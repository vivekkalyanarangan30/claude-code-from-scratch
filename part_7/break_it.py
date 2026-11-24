"""7.1, 7.3 — A long task in a small window, and a system prompt that changes every call."""
import asyncio
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
from agent import Agent
from common import Notice, ToolCall, drive, load_part, sandbox
from fake import FakeModel
from scripts import SCRIPTS

WINDOW = 9_000
part_6 = load_part(6, "agent")

print(f"── A 12-batch log audit in a {WINDOW:,}-token window")
for label, cls in (("Part 6", part_6.Agent), ("Part 7", Agent)):
    agent = cls(FakeModel(SCRIPTS, delay=0, window=WINDOW), sandbox(reset=True))
    try:
        events = asyncio.run(drive(agent, "audit the logs"))
        calls = sum(isinstance(e, ToolCall) for e in events)
        print(f"  {label}: finished after {calls} tool calls")
        notices = [e.text for e in events if isinstance(e, Notice)]
        cleared = [n for n in notices if n.startswith("cleared")]
        if cleared:
            print(f"    ⎿  {cleared[0]}\n    ⎿  … cleared again before {len(cleared) - 1} more calls")
        for notice in (n for n in notices if not n.startswith("cleared")):
            print(f"    ⎿  {notice}")
        print(f"    ⏺ {agent.messages[-1]['content'][-1]['text']}")
    except Exception as exc:
        print(f"  {label}: ✗ {exc}")


class Fidgety(Agent):
    """Puts the current time in the system prompt before every call."""

    async def fit(self):
        self.system = f"Current time: {time.time()}\n{self.system.split(chr(10), 1)[-1]}"
        async for notice in super().fit():
            yield notice


print("\n── 7.3 The same 5-call fix, cached")
for label, cls in (("append-only            ", Agent), ("time in the system prompt", Fidgety)):
    agent = cls(FakeModel(SCRIPTS, delay=0), sandbox(reset=True))
    asyncio.run(drive(agent, "fix the failing test"))
    fresh = agent.tokens[0] - agent.cache[0] - agent.cache[1]
    cost = fresh + 1.25 * agent.cache[1] + 0.1 * agent.cache[0]
    print(f"  {label}: {agent.cache[0]:>6,} read from cache, {agent.cache[1]:>6,} written · "
          f"billed like {cost:>7,.0f} input tokens")
print("\nA cache read costs a tenth of an input token and a cache write 1.25×. The cache only helps if the\n"
      "prefix stays byte-for-byte the same, which is why the transcript is append-only.")
