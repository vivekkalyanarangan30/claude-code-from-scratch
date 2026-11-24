"""4.2, 4.5 — Tools that can be killed cleanly: process groups, timeouts, background jobs."""
import asyncio
import atexit
import fnmatch
import os
import re
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from common import agent_path

TIMEOUT = 120
MAX_OUTPUT = 20_000          # characters a tool may return; longer output loses its middle
MAX_LINES = 400              # lines per Read; the rest needs an offset
MAX_MATCHES = 100            # Glob and Grep results
SKIP_DIRS = {".git", ".agent", "__pycache__", ".venv", "node_modules"}
HIDDEN_ENV = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY"}


class ToolError(Exception):
    """A mistake the model can fix. Its message goes back to the model as an error result."""


@dataclass
class Context:
    """What tools share within one conversation: the project root and the file versions the model has seen."""
    root: Path
    seen: dict = field(default_factory=dict)


@dataclass
class Tool:
    name: str
    description: str
    params: dict
    required: list
    handler: Callable
    read_only: bool = False
    concurrency_safe: bool = False

    @property
    def schema(self):
        return {"name": self.name, "description": self.description,
                "input_schema": {"type": "object", "properties": self.params, "required": self.required}}


def inside(root: Path, path: str) -> Path:
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ToolError(f"{path} is outside the project. Use a path relative to the project root.")
    return target


def clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    half = MAX_OUTPUT // 2
    return f"{text[:half]}\n… {len(text) - MAX_OUTPUT:,} characters omitted …\n{text[-half:]}"


def check_fresh(ctx: Context, target: Path, path: str):
    """Refuse to change a file the model hasn't read, or one that changed since it did."""
    if not target.exists():
        return
    if target not in ctx.seen:
        raise ToolError(f"Read {path} before changing it.")
    if target.stat().st_mtime_ns != ctx.seen[target]:
        raise ToolError(f"{path} changed since you last read it. Read it again, then retry.")


def save(ctx: Context, target: Path, text: str):
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    ctx.seen[target] = target.stat().st_mtime_ns


async def read(ctx, path, offset=1, limit=MAX_LINES):
    target = inside(ctx.root, path)
    if not target.is_file():
        raise ToolError(f"{path} does not exist. Use Glob to see which files do.")
    lines = target.read_text(errors="replace").splitlines()
    ctx.seen[target] = target.stat().st_mtime_ns
    start = max(int(offset), 1) - 1
    chunk = lines[start:start + int(limit)]
    body = "\n".join(f"{n:>5}\t{line}" for n, line in enumerate(chunk, start=start + 1))
    rest = len(lines) - start - len(chunk)
    if rest > 0:
        body += f"\n… {rest} more lines. Read again with offset={start + len(chunk) + 1}."
    return clip(body) or "(empty file)"


async def write(ctx, path, content):
    target = inside(ctx.root, path)
    check_fresh(ctx, target, path)
    save(ctx, target, content)
    lines = len(content.splitlines())
    return f"Wrote {lines} line{'' if lines == 1 else 's'} to {path}"


def whole_lines(text: str, start: int, end: int) -> bool:
    """True if text[start:end] runs from the start of a line (indentation aside) to the end of one."""
    line_start = text.rfind("\n", 0, start) + 1
    return not text[line_start:start].strip() and (end == len(text) or text[end - 1] == "\n" or text[end] == "\n")


async def edit(ctx, path, old_string, new_string, replace_all=False):
    target = inside(ctx.root, path)
    if not target.is_file():
        raise ToolError(f"{path} does not exist. Use Write to create a new file.")
    check_fresh(ctx, target, path)
    text = target.read_text()
    found = [m.start() for m in re.finditer(re.escape(old_string), text)] if old_string else []
    if not found:
        raise ToolError(f"old_string was not found in {path}. Copy it exactly from Read's output, "
                        "without the line numbers.")
    found = [at for at in found if whole_lines(text, at, at + len(old_string))]
    if not found:
        raise ToolError(f"old_string matches only part of a line in {path}. Copy whole lines from Read's output.")
    if len(found) > 1 and not replace_all:
        raise ToolError(f"old_string matches {len(found)} places in {path}. Include more surrounding lines "
                        "so it matches exactly one, or set replace_all.")
    for at in reversed(found if replace_all else found[:1]):
        text = text[:at] + new_string + text[at + len(old_string):]
    save(ctx, target, text)
    first = text[:found[0]].count("\n") + 1
    shown = text.splitlines()[first - 1:first - 1 + max(1, new_string.count("\n") + 1)]
    now = "\n".join(f"{n:>5}\t{line}" for n, line in enumerate(shown, start=first))
    return f"Edited {path}. It now reads:\n{now}"


def walk(root: Path):
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            yield (Path(directory) / name).relative_to(root).as_posix()


async def glob(ctx, pattern):
    hits = [p for p in walk(ctx.root) if fnmatch.fnmatch(p, pattern) or fnmatch.fnmatch(p, pattern.removeprefix("**/"))]
    more = f"\n… {len(hits) - MAX_MATCHES} more" if len(hits) > MAX_MATCHES else ""
    return "\n".join(hits[:MAX_MATCHES]) + more if hits else f"No files match {pattern}."


async def grep(ctx, pattern, glob="*"):
    if len(pattern) > 200:
        raise ToolError("pattern is too long; keep it under 200 characters.")
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ToolError(f"invalid regular expression: {exc}")
    hits = []
    for rel in walk(ctx.root):
        if not fnmatch.fnmatch(rel, glob) and not fnmatch.fnmatch(Path(rel).name, glob):
            continue
        path = ctx.root / rel
        if path.stat().st_size > 1_000_000:
            continue
        for n, line in enumerate(path.read_text(errors="replace").splitlines(), start=1):
            if regex.search(line[:500]):
                hits.append(f"{rel}:{n}: {line.strip()[:200]}")
    more = f"\n… {len(hits) - MAX_MATCHES} more matches" if len(hits) > MAX_MATCHES else ""
    return "\n".join(hits[:MAX_MATCHES]) + more if hits else f"No matches for {pattern}."


BACKGROUND: dict[str, asyncio.subprocess.Process] = {}


def kill_group(proc):
    """Kill the shell and everything it started: they share a process group because of start_new_session."""
    if proc.returncode is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@atexit.register
def stop_background():
    for proc in BACKGROUND.values():
        kill_group(proc)


async def bash(ctx, command, timeout=TIMEOUT, run_in_background=False):
    env = {k: v for k, v in os.environ.items() if k not in HIDDEN_ENV}
    env["PATH"] = agent_path()  # a known PATH, not whatever the user's shell had
    if run_in_background:
        job = f"bg{len(BACKGROUND) + 1}"
        log = ctx.root / ".agent" / "bg" / f"{job}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "wb") as sink:
            BACKGROUND[job] = await asyncio.create_subprocess_shell(
                command, cwd=ctx.root, env=env, stdout=sink, stderr=sink, start_new_session=True)
        return (f"Started in the background as {job} (pid {BACKGROUND[job].pid}). Its output goes to "
                f"{log.relative_to(ctx.root)}; Read that file to check on it. It stops when the agent exits.")
    proc = await asyncio.create_subprocess_shell(
        command, cwd=ctx.root, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=True)
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), min(float(timeout), 600))
    except asyncio.TimeoutError:
        raise ToolError(f"Timed out after {float(timeout):g}s and was killed. If it is a server or a watcher, "
                        "run it with run_in_background.")
    finally:
        kill_group(proc)
    text = clip(stdout.decode(errors="replace"))
    if proc.returncode != 0:
        raise ToolError(f"{text}\n[exit code {proc.returncode}]".strip())
    return text


PATH = {"type": "string", "description": "File path relative to the project root, e.g. src/app.py"}
TOOLS = {t.name: t for t in [
    Tool("Read", "Read a file. Returns numbered lines (number, tab, text). Read a file before you Edit it.",
         {"path": PATH,
          "offset": {"type": "integer", "description": "First line to read, from 1. Only for long files."},
          "limit": {"type": "integer", "description": f"How many lines. Default {MAX_LINES}."}},
         ["path"], read, read_only=True, concurrency_safe=True),
    Tool("Edit", "Replace whole lines in a file. old_string is one or more complete lines copied from Read's "
                 "output without the line numbers, and must match exactly one place. Prefer this to Write.",
         {"path": PATH, "old_string": {"type": "string", "description": "Exact text to replace."},
          "new_string": {"type": "string", "description": "Replacement text."},
          "replace_all": {"type": "boolean", "description": "Replace every match. Default false."}},
         ["path", "old_string", "new_string"], edit),
    Tool("Write", "Create a new file, or replace a whole file you have Read. Use Edit for small changes.",
         {"path": PATH, "content": {"type": "string", "description": "The complete file content."}},
         ["path", "content"], write),
    Tool("Glob", "List project files matching a pattern, e.g. **/*.py. Use this to find files.",
         {"pattern": {"type": "string", "description": "Glob pattern relative to the project root."}},
         ["pattern"], glob, read_only=True, concurrency_safe=True),
    Tool("Grep", "Search inside files for a regular expression, e.g. def total. Returns file:line: text. "
                 "To see a whole file, use Read.",
         {"pattern": {"type": "string", "description": "Python regular expression."},
          "glob": {"type": "string", "description": "Only search files matching this, e.g. *.py."}},
         ["pattern"], grep, read_only=True, concurrency_safe=True),
    Tool("Bash", "Run a shell command in the project root and return its output. A failing command "
                 "returns its output and exit code as an error. Don't use it to read or search files.",
         {"command": {"type": "string", "description": "The command to run."},
          "timeout": {"type": "number", "description": f"Seconds before it is killed. Default {TIMEOUT}, max 600."},
          "run_in_background": {"type": "boolean", "description": "For servers and watchers that never exit: "
                                "start it, return at once, and write its output to a log file."}},
         ["command"], bash),
]}
SCHEMAS = [t.schema for t in TOOLS.values()]
NEEDS_APPROVAL = {name for name, t in TOOLS.items() if not t.read_only}


async def run(ctx, name, args):
    """Run one tool call. Every mistake the model can make raises ToolError with what to do instead."""
    tool = TOOLS.get(name)
    if tool is None:
        raise ToolError(f"There is no tool called {name}. Available: {', '.join(TOOLS)}.")
    if not isinstance(args, dict):
        raise ToolError(f"{name} takes a JSON object of arguments.")
    missing = [p for p in tool.required if p not in args]
    unknown = [p for p in args if p not in tool.params]
    if missing or unknown:
        raise ToolError(f"{name} needs {', '.join(tool.required)}"
                        + (f"; missing {', '.join(missing)}" if missing else "")
                        + (f"; no such parameter {', '.join(unknown)}" if unknown else "") + ".")
    return await tool.handler(ctx, **args)


def describe(exc: Exception) -> str:
    """An exception as the model should see it."""
    if isinstance(exc, ToolError):
        return str(exc)
    if isinstance(exc, asyncio.TimeoutError):
        return "The command timed out and was killed."
    return f"{type(exc).__name__}: {exc}"
