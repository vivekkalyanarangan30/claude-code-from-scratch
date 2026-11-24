"""6.1–6.4 — The session as an append-only log. An intent is written before a tool runs, its result after."""
import json
import os
import time
import uuid
from pathlib import Path

UNKNOWN = ("Outcome unknown: the session stopped while this was running. It may or may not have happened. "
           "Check before running it again.")
NOT_RUN = "Not run: the session stopped before this started."


def atomic_write(path: Path, text: str):
    """Temp file, fsync, rename: after a crash the file is either the old version or the new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Log:
    def __init__(self, root: Path, path: Path | None = None):
        folder = root / ".agent" / "sessions"
        folder.mkdir(parents=True, exist_ok=True)
        self.path = path or folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.jsonl"

    def append(self, kind: str, **data):
        """One JSON line, flushed and fsynced before we carry on: if it isn't on disk, it didn't happen."""
        with open(self.path, "a") as f:
            f.write(json.dumps({"kind": kind, **data}) + "\n")
            f.flush()
            os.fsync(f.fileno())


def read(path: Path) -> list[dict]:
    """Every complete record. A crash mid-write leaves a torn last line; it is dropped, not fatal."""
    records = []
    for line in path.read_text().splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            break
    return records


def latest(root: Path) -> Path | None:
    logs = sorted((root / ".agent" / "sessions").glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


def replay(records: list[dict]) -> tuple[list, list | None]:
    """Rebuild the transcript. Returns (messages, results to append for tool calls that never got one)."""
    messages, intents, results = [], set(), {}
    for r in records:
        if r["kind"] == "message":
            messages.append(r["message"])
        elif r["kind"] == "intent":
            intents.add(r["id"])
        elif r["kind"] == "result":
            results[r["id"]] = r
        elif r["kind"] == "rewind":
            messages = messages[:r["at"]]
    last = messages[-1] if messages else None
    if not last or last["role"] != "assistant":
        return messages, None
    missing = []
    for block in (b for b in last["content"] if b["type"] == "tool_use"):
        if block["id"] in results:
            done = results[block["id"]]
            missing.append({"type": "tool_result", "tool_use_id": block["id"], "content": done["output"],
                            **({"is_error": True} if done["is_error"] else {})})
        else:
            missing.append({"type": "tool_result", "tool_use_id": block["id"], "is_error": True,
                            "content": UNKNOWN if block["id"] in intents else NOT_RUN})
    return messages, missing or None
