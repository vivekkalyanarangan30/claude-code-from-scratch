"""1.2 — Three tools. A tool is a JSON schema the model reads plus a function you run."""
import asyncio
import os
from pathlib import Path

from common import agent_path

TIMEOUT = 120
HIDDEN_ENV = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY"}

SCHEMAS = [
    {"name": "Read", "description": "Read a text file. Path is relative to the project root.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "Write", "description": "Create or overwrite a file with the given content.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                      "required": ["path", "content"]}},
    {"name": "Bash", "description": "Run a shell command in the project root. Returns stdout and stderr.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
]
NEEDS_APPROVAL = {"Write", "Bash"}


def inside(root: Path, path: str) -> Path:
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()):
        raise PermissionError(f"{path} is outside the project")
    return target


async def read(root, path):
    return inside(root, path).read_text()


async def write(root, path, content):
    target = inside(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    lines = len(content.splitlines())
    return f"Wrote {lines} line{'' if lines == 1 else 's'} to {path}"


async def bash(root, command):
    env = {k: v for k, v in os.environ.items() if k not in HIDDEN_ENV}
    env["PATH"] = agent_path()  # a known PATH, not whatever the user's shell had
    proc = await asyncio.create_subprocess_shell(
        command, cwd=root, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), TIMEOUT)
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    text = stdout.decode(errors="replace")
    return text if proc.returncode == 0 else f"{text}\n[exit code {proc.returncode}]"


HANDLERS = {"Read": read, "Write": write, "Bash": bash}


async def run(root, name, args):
    return await HANDLERS[name](root, **args)
