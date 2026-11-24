"""8.3 — Backpressure: a stop hook that lets a check, not the model, decide when the work is done."""
import asyncio
import sys


def until(root, check: str, prompt, max_iterations: int):
    """A stop hook: run `check` when the model stops; if it fails, send the task back with its output.

    `prompt` is the task, or a function returning it (on screen, whatever the user typed last)."""
    sent = 0

    async def hook(final_text):
        nonlocal sent
        proc = await asyncio.create_subprocess_shell(check, cwd=root, stdout=asyncio.subprocess.PIPE,
                                                     stderr=asyncio.subprocess.STDOUT)
        output = (await proc.communicate())[0].decode(errors="replace")
        if proc.returncode == 0:
            return None
        if sent >= max_iterations:
            print(f"stop hook: `{check}` still fails after {sent} re-runs; giving up", file=sys.stderr)
            return None
        sent += 1
        task = prompt() if callable(prompt) else prompt
        return f"{task}\n\n(The check `{check}` still fails, so you are not done:\n{output[-2000:]})"
    return hook
