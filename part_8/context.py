"""7.1–7.5 — Context is a budget: count it, assemble it once, keep its prefix stable, and shrink it when full."""
import copy
import json
import platform
import subprocess
from pathlib import Path

CLEARED = "[old tool output cleared to save context]"
SUMMARIZE = ("Summarize this conversation so you can continue the work after it is cleared: what the user asked, "
             "what you did and found, which files you changed, and what is left. Be brief and specific.")
COMPACTED = "This conversation was compacted to save context. Continue the task from this summary."


def estimate(*parts) -> int:
    """About four characters per token: good enough to decide when to act, not to bill."""
    return sum(len(p if isinstance(p, str) else json.dumps(p)) for p in parts) // 4


def system_prompt(root: Path, base: str) -> str:
    """Built once per session, and no date: a prompt that changes daily replays differently daily."""
    lines = [base, "", "# Environment", f"- Working directory: {root}", f"- Platform: {platform.system()}"]
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=root, capture_output=True, text=True)
    is_repo = (root / ".git").exists() and branch.returncode == 0
    lines.append(f"- Git: on branch {branch.stdout.strip()}" if is_repo else "- Git: not a repository")
    for folder in reversed([root, *root.parents]):
        memory = folder / "AGENTS.md"
        if memory.is_file():
            lines += ["", f"# Project instructions from {memory}", memory.read_text().strip()]
    return "\n".join(lines)


def cache_marks(system: str, messages: list) -> tuple[list, list]:
    """Mark the end of the system prompt and of the newest message: everything before a mark can be cached."""
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    marked = copy.deepcopy(messages)
    if marked:
        last = marked[-1]
        if isinstance(last["content"], str):
            last["content"] = [{"type": "text", "text": last["content"]}]
        last["content"][-1]["cache_control"] = {"type": "ephemeral"}
    return system_blocks, marked


def clear_old_results(messages: list, keep: int = 3) -> int:
    """Replace all but the newest `keep` tool outputs with a placeholder. Returns how many were cleared."""
    results = [b for m in messages if isinstance(m["content"], list) for b in m["content"]
               if b.get("type") == "tool_result" and b["content"] != CLEARED]
    stale = results[:-keep] if keep else results
    for block in stale:
        block["content"] = CLEARED
    return len(stale)


def render_todos(todos: list) -> str:
    marks = {"pending": "☐", "in_progress": "◐", "completed": "☒"}
    return "\n".join(f"{marks.get(t['status'], '☐')} {t['content']}" for t in todos) or "(empty)"
