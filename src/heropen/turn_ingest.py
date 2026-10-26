"""主动读取本机客户端会话记录，把真实完整话轮补交给轮次存储。

背景：heropen 是被动 MCP server，原生设计是「宿主每轮回复后调用
auto_save_turn」。当宿主没有接钩子时，整条自动累计链路就空转（接收数恒为 0）。
本模块不依赖宿主配合：直接读取客户端已经写在磁盘上的会话记录，提取真实轮次。

原则：
- 只读客户端记录文件，绝不改写客户端配置、不安装钩子、不动客户端数据。
- 只用记录里原生存在的会话与轮次标识作幂等键，因此重复运行不会重复计数。
- 不生成、不补写、不猜测内容：用户输入与助手回复都取自记录原文。
- 注入块（system-reminder / 浏览器上下文等）不是用户说的话，必须剔除；
  若一轮除了注入块没有真实用户输入，则整轮跳过，宁缺毋滥。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from . import turn_storage

# 已被识别为「注入块」的标签前缀：这些是宿主塞入的上下文，不是用户发言。
_INJECTION_TAG = re.compile(
    r"^\s*<\s*(system-reminder|in-app-browser-context|local-command"
    r"|command-|tool-|ide_|attachment|task-notification|user_info"
    r"|identity_context|project_context|connector-status"
    r"|memory_and_skills_reminder|craft_mode|additional-data"
    r"|wecom_wedrive_references|rule-reminder)",
    re.IGNORECASE,
)
_INLINE_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
# WorkBuddy 把真实用户输入包在 <user_query> 里，其余都是注入上下文。
_USER_QUERY = re.compile(r"<user_query>(.*?)</user_query>", re.DOTALL)
# 客户端注入的附件元数据：整块只是本机路径，不含任何人类语言。
_IMAGE_PATH_TAG = re.compile(r"<image_local_path>.*?</image_local_path>", re.DOTALL)
# 附件元数据行，如 "Attached application/octet-stream: /path/to/file"。
_ATTACH_LINE = re.compile(
    r"^\s*Attached\s+[\w.+-]+/[\w.+-]+:\s*\S+\s*$", re.MULTILINE)
MAX_TURNS_PER_RUN = 500
# 与 auto_save_turn 的 MAX_TURN_CHARS 一致：单轮用户＋助手合计上限，超限明确拒绝。
MAX_TURN_CHARS = 1_000_000
# ZCode 用 finishReason 表示这一条模型调用是否收尾；只有收尾才是完整回复。
ZCODE_COMPLETE = "stop"
# WorkBuddy 用 message.status 表示；只有 completed 才是完整回复。
WORKBUDDY_COMPLETE = "completed"


class IncompleteTurn(ValueError):
    """该轮次尚无可确认的完整回复，本轮跳过而不是猜测。"""


class TurnTooLong(ValueError):
    """用户＋助手合计超过上限，明确拒绝而不静默截断。"""


@dataclass(frozen=True)
class Turn:
    """One complete conversation turn as recorded on disk."""

    conversation_id: str
    turn_id: str
    user: str
    assistant: str
    started_at: str
    agent: str


def _message_texts(message) -> list[str]:
    content = message.get("content")
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [part.get("text", "") for part in content if isinstance(part, dict)]
    return []


def _strip_attachment_metadata(text: str) -> str:
    """Remove client-injected attachment metadata, keep any real words.

    Two kinds of attachment text appear in user messages, and they must not be
    treated the same:

    - **Machine metadata with no human language**: a bare local path
      (``<image_local_path>…</image_local_path>``) or an
      "Attached <mime>: <path>" line. These are stripped, so a block that was
      nothing but a path disappears.
    - **The user's own attachment reference** (``@image#1:foo.png``). This is a
      filename the user attached, not internal metadata, and it is often glued
      to a real request ("@image#1:shot.png 给这张图加墨镜"). Removing it — or
      dropping the whole block for containing "an image" — would silently lose
      what the user asked for, so it is left alone.

    Only the first kind is removed here; the surrounding words always survive.
    """
    text = _IMAGE_PATH_TAG.sub("", text)
    text = _ATTACH_LINE.sub("", text)
    return text.strip()


def _clean_user_text(message) -> str:
    """Real human input only: drop injected blocks, keep what the user typed.

    A user message may carry several text blocks. All real blocks are kept, in
    their original order, joined with blank lines so two blocks never run
    together and change meaning. Injection-only blocks are dropped; when a
    block wraps real input in a marker (WorkBuddy uses <user_query>) only the
    marker's contents are taken, otherwise an inline reminder is stripped and
    the remainder kept. Attachment metadata is stripped within each block, so a
    block that was nothing but a local path disappears while a block that mixed
    an attachment reference with a real request survives intact. Picking one
    "best" block would silently lose the rest.
    """
    kept: list[str] = []
    for text in _message_texts(message):
        if not isinstance(text, str) or not text.strip():
            continue
        queries = _USER_QUERY.findall(text)
        if queries:
            candidate = "\n".join(q.strip() for q in queries if q.strip())
        else:
            candidate = _INLINE_REMINDER.sub("", text).strip()
        candidate = _strip_attachment_metadata(candidate)
        if not candidate or _INJECTION_TAG.match(candidate):
            # Pure context (or nothing left after stripping): not user speech.
            continue
        kept.append(candidate)
    # No truncation here: a silent cut would be recorded as a "complete" turn.
    # Length is validated against the same ceiling as auto_save_turn elsewhere.
    return "\n\n".join(kept)


def read_zcode_turns(rollout_dir: Path, *, include_subagents: bool = False) -> list[Turn]:
    """Extract real turns from ZCode's model-io rollout transcripts.

    Each line is one model call. One user turn can span several calls (tool
    loops) that share a turnId, and only the call whose finishReason is "stop"
    is the finished reply — intermediate "tool-calls" snapshots must never be
    stored, or the final text would later conflict with what was saved. When no
    finished call exists the turn is simply not produced yet; it will appear on
    a later scan once the reply completes. Identity comes straight from the
    file: sessionId becomes the conversation id and turnId the turn id, which
    makes re-runs idempotent.
    """
    turns: dict[tuple[str, str], Turn] = {}
    for path in sorted(rollout_dir.glob("model-io-*.jsonl")):
        if not include_subagents and "subagent" in path.name:
            continue
        try:
            handle = path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("type") != "model_io":
                    continue
                conversation_id = row.get("sessionId")
                turn_id = row.get("turnId")
                if not isinstance(conversation_id, str) or not conversation_id:
                    continue
                if not isinstance(turn_id, str) or not turn_id:
                    continue
                request = row.get("request")
                response = row.get("response")
                if not isinstance(request, dict) or not isinstance(response, dict):
                    continue
                # Only a finished call counts as the final reply.
                if response.get("finishReason") != ZCODE_COMPLETE:
                    continue
                assistant = response.get("text")
                if not isinstance(assistant, str) or not assistant.strip():
                    continue
                # The user text must come from THIS turn's own user message.
                # Do not walk back to an earlier turn's text when this one had
                # only injected content, or a pair would be invented.
                user = ""
                messages = request.get("messages")
                if isinstance(messages, list):
                    for message in reversed(messages):
                        if isinstance(message, dict) and message.get("role") == "user":
                            user = _clean_user_text(message)
                            break
                started = row.get("startedAt")
                turns[(conversation_id, turn_id)] = Turn(
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    user=user,
                    assistant=assistant.strip(),
                    started_at=started if isinstance(started, str) else "",
                    agent="zcode",
                )
    return list(turns.values())


def _entry_date(started_at: str) -> str:
    """Local calendar date of the turn, so entries group by the day they happened."""
    if started_at:
        try:
            parsed = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            return parsed.astimezone().date().isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).astimezone().date().isoformat()


def ingest_turns(turns: list[Turn], *, apply: bool = False) -> dict:
    """Hand turns to the turn store. Read-only unless apply is True.

    conversation_id is the session id straight from the record and turn_id is
    the record's own turn id. Both are native and stable, so accumulation stays
    within one conversation (the ten-turn window is not scattered) and re-runs
    are idempotent.

    Progress is tracked by skipping turns the store already has, so the
    per-run ceiling bounds *new* work rather than re-reading the same earliest
    records forever: a history larger than the ceiling completes across several
    runs instead of stalling. A turn that fails is reported in "errors" and is
    left unprocessed (it is not marked as done), so the next run retries it
    rather than skipping it silently.
    """
    summary = {
        "apply": apply, "considered": len(turns), "skipped_incomplete": 0,
        "skipped_invalid": 0, "already_stored": 0, "deferred": 0,
        "submitted": 0, "duplicate": 0, "buffered": 0, "saved": 0,
        "failed": 0, "conflicts": 0, "errors": [], "conversations": {},
    }
    ordered = sorted(turns, key=lambda t: (t.started_at or "", t.turn_id))
    # Known turns come from the store itself, so a restarted process resumes
    # exactly where it left off without a side-channel cursor file. The digest
    # is kept too: an id alone must not be trusted, because the same native id
    # can later carry changed text (an intermediate snapshot, a client
    # revision). Identical content is skipped silently; different content is
    # reported as a conflict instead of vanishing into "already stored".
    stored: dict[str, dict] = {}
    new_count = 0
    for turn in ordered:
        if not turn.user.strip() or not turn.assistant.strip():
            # No real human input, or no confirmed final reply yet.
            summary["skipped_incomplete"] += 1
            continue
        if len(turn.user) + len(turn.assistant) > MAX_TURN_CHARS:
            # Refuse explicitly: a silent cut would be stored as "complete".
            summary["skipped_invalid"] += 1
            summary["errors"].append({
                "conversation_id": turn.conversation_id, "turn_id": turn.turn_id,
                "reason": (f"用户＋助手合计 {len(turn.user) + len(turn.assistant)} 字符，"
                           f"超过上限 {MAX_TURN_CHARS}，已拒绝且未截断"),
            })
            continue
        if turn.agent not in stored:
            stored[turn.agent] = turn_storage.stored_turn_digests(turn.agent)
        known = stored[turn.agent].get((turn.conversation_id, turn.turn_id))
        if known is not None:
            if known == turn_storage.turn_digest(turn.user, turn.assistant):
                # Byte-identical: genuinely unchanged, costs no new-turn quota.
                summary["already_stored"] += 1
                continue
            # Same id, different text: report it and leave the original alone.
            summary["failed"] += 1
            summary["conflicts"] += 1
            summary["errors"].append({
                "conversation_id": turn.conversation_id, "turn_id": turn.turn_id,
                "reason": "同一会话/轮次 ID 已存在但正文不同，已保留原记忆并报告冲突",
                "kind": "conflict",
            })
            continue
        if new_count >= MAX_TURNS_PER_RUN:
            # Ceiling bounds new submissions; remaining turns wait for the next
            # run instead of starving behind already-stored records.
            summary["deferred"] = summary.get("deferred", 0) + 1
            continue
        new_count += 1
        summary["submitted"] += 1
        entry = summary["conversations"].setdefault(
            turn.conversation_id,
            {"submitted": 0, "duplicate": 0, "saved": 0,
             "received_turns": 0, "pending_turns": 0})
        entry["submitted"] += 1
        if not apply:
            continue
        try:
            result = turn_storage.save_turn(
                turn.agent,
                content=turn.assistant,
                user_content=turn.user,
                entry_date=_entry_date(turn.started_at),
                conversation_id=turn.conversation_id,
                turn_id=turn.turn_id,
            )
        except ValueError as exc:
            # Keep the failure traceable; do not mark the turn as handled.
            summary["failed"] += 1
            summary["errors"].append({
                "conversation_id": turn.conversation_id, "turn_id": turn.turn_id,
                "reason": f"{type(exc).__name__}: {exc}",
            })
            continue
        if result["duplicate"]:
            summary["duplicate"] += 1
            entry["duplicate"] += 1
        if result["saved"]:
            summary["saved"] += result["saved"]
            entry["saved"] += result["saved"]
        if result["buffered"]:
            summary["buffered"] += 1
        entry["received_turns"] = result["received_turns"]
        entry["pending_turns"] = result["pending_turns"]
        # Track it so a repeat within the same run stays consistent.
        stored[turn.agent][(turn.conversation_id, turn.turn_id)] = \
            turn_storage.turn_digest(turn.user, turn.assistant)
    return summary


def read_workbuddy_turns(projects_dir: Path, *,
                         include_subagents: bool = False) -> list[Turn]:
    """Extract real turns from WorkBuddy session transcripts.

    WorkBuddy writes one JSONL per session under ~/.workbuddy/projects/<project>/.
    Rows are messages with a role and a globally unique id; there is no turn id,
    so each user message's own id becomes the turn key and the session id the
    conversation key — both native, so re-runs stay idempotent. A turn runs from
    a real user message to the assistant reply that follows it.
    """
    turns: list[Turn] = []
    # Sessions live at projects/<project>/<session>.jsonl while subagent
    # transcripts sit one level deeper under a subagents/ directory, so walk
    # recursively and filter by path rather than assuming a fixed depth.
    for path in sorted(projects_dir.rglob("*.jsonl")):
        if not include_subagents and ("subagents" in path.parts
                                      or "subagent" in path.name):
            continue
        try:
            handle = path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        pending: dict | None = None
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("type") != "message":
                    continue
                role = row.get("role")
                session_id = row.get("sessionId")
                if not isinstance(session_id, str) or not session_id:
                    continue
                if role == "user":
                    # Keep the previous turn only once it has a real user text.
                    if pending is not None and pending["assistant"] and pending["user"]:
                        turns.append(_workbuddy_turn(pending))
                    pending = {"user": _clean_user_text(row), "assistant": "",
                               "id": row.get("id"), "session": session_id,
                               "ts": row.get("timestamp")}
                elif role == "assistant" and pending is not None:
                    # Only a completed reply is final; an "incomplete" row is a
                    # partial snapshot and must not be stored as the full turn.
                    if row.get("status") != WORKBUDDY_COMPLETE:
                        continue
                    text = _assistant_text(row)
                    if text:
                        pending["assistant"] = text
        if pending is not None and pending["assistant"] and pending["user"]:
            turns.append(_workbuddy_turn(pending))
    return turns


def _assistant_text(row) -> str:
    parts = _message_texts(row)
    # No truncation: an oversized field is rejected by ingest_turns with an
    # explicit reason rather than silently shortened.
    return "\n".join(p for p in parts if p.strip()).strip()


def _workbuddy_turn(pending: dict) -> Turn:
    started = pending.get("ts")
    if isinstance(started, (int, float)):
        # WorkBuddy timestamps are epoch milliseconds.
        started = datetime.fromtimestamp(started / 1000, tz=timezone.utc).isoformat()
    return Turn(
        conversation_id=pending["session"],
        turn_id=str(pending["id"]),
        user=pending["user"],
        assistant=pending["assistant"],
        started_at=started if isinstance(started, str) else "",
        agent="workbuddy",
    )


def default_workbuddy_projects() -> Path:
    return Path.home() / ".workbuddy/projects"


def default_zcode_rollout() -> Path:
    return Path.home() / ".zcode/cli/rollout"


def ingest_workbuddy(*, apply: bool = False, projects_dir: Path | None = None,
                     include_subagents: bool = False) -> dict:
    """Convenience wrapper: read WorkBuddy transcripts and hand them to the store."""
    directory = projects_dir or default_workbuddy_projects()
    turns = read_workbuddy_turns(directory, include_subagents=include_subagents)
    summary = ingest_turns(turns, apply=apply)
    summary["source"] = str(directory)
    return summary


def ingest_zcode(*, apply: bool = False, rollout_dir: Path | None = None,
                 include_subagents: bool = False) -> dict:
    """Convenience wrapper: read ZCode transcripts and hand them to the store."""
    directory = rollout_dir or default_zcode_rollout()
    turns = read_zcode_turns(directory, include_subagents=include_subagents)
    summary = ingest_turns(turns, apply=apply)
    summary["source"] = str(directory)
    return summary