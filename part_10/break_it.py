"""10.2–10.3 — Exploring fills the context of whoever does the reading. And two agents, one file."""
import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import tools
from agent import Agent
from common import drive, load_part, sandbox
from fake import FakeModel
from scripts import SCRIPTS

part_9 = load_part(9, "agent")

def with_logs():
    """Twelve days of logs, 300 lines each; one line is missing from day 7."""
    root = sandbox(reset=True)
    (root / "logs").mkdir()
    for day in range(1, 13):
        lines = [f"day{day:02} line {n}" for n in range(1, 301) if not (day == 7 and n == 150)]
        (root / "logs" / f"day{day:02}.log").write_text("\n".join(lines) + "\n")
    return root


print("── Find the one missing line in twelve log files")
for label, cls, prompt in (("Part 9, reads all twelve itself", part_9.Agent, "find the gap in the logs yourself"),
                           ("Part 10, three subagents     ", Agent, "find the gap in the logs")):
    agent = cls(FakeModel(SCRIPTS, delay=0), with_logs())
    events = asyncio.run(drive(agent, prompt))
    print(f"  {label}: {agent.used():>6,} tokens left in its context · "
          f"\"{agent.messages[-1]['content'][-1]['text']}\"")
print("  Same answer. The subagents read the logs in their own windows; the parent kept three summaries.\n")

print("── 10.3 Two agents edit cart.py")


async def two_editors(root):
    a, b = tools.Context(root), tools.Context(root)
    await tools.run(a, "Read", {"path": "cart.py"})
    await tools.run(b, "Read", {"path": "cart.py"})
    await tools.run(a, "Edit", {"path": "cart.py", "old_string": "return subtotal - discount",
                                "new_string": "return subtotal * (1 - discount / 100)"})
    print("  agent A: edited cart.py")
    try:
        await tools.run(b, "Edit", {"path": "cart.py", "old_string": "    return subtotal",
                                    "new_string": "    return round(subtotal"})
    except tools.ToolError as exc:
        print(f"  agent B: {exc}")
asyncio.run(two_editors(sandbox(reset=True)))
print("  Part 2's read-before-edit check is what stops B from writing over A. Giving each agent its own\n"
      "  checkout (a git worktree) avoids the conflict altogether.")
