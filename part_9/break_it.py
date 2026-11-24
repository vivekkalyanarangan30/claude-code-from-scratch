"""9.2 — The API returns 529 right after a command ran. Retry the turn, or retry the request?"""
import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import retry
from agent import Agent
from common import APIError, Notice, drive, load_part, sandbox
from fake import FakeModel
from scripts import SCRIPTS

retry.BASE = 0.05
part_8 = load_part(8, "agent")


async def retry_the_turn(agent, prompt, attempts=3):
    """What a try/except around the whole turn does."""
    for attempt in range(attempts):
        try:
            return await drive(agent, prompt)
        except APIError as exc:
            print(f"  turn failed: {exc} · running the turn again")


def commits(root):
    return (root / "commits.log").read_text().splitlines()


print("── Part 8 + a retry around the turn")
root = sandbox(reset=True)
asyncio.run(retry_the_turn(part_8.Agent(FakeModel(SCRIPTS, delay=0, faults=[0, 529]), root), "commit the change"))
print(f"  commits.log: {commits(root)}\n")

print("── Part 9: retry around the request")
root = sandbox(reset=True)
events = asyncio.run(drive(Agent(FakeModel(SCRIPTS, delay=0, faults=[0, 529, 529]), root), "commit the change"))
for notice in (e.text for e in events if isinstance(e, Notice)):
    print(f"  ⎿  {notice}")
print(f"  commits.log: {commits(root)}\n")

print("── 9.3 A budget instead of hoping")
agent = Agent(FakeModel(SCRIPTS, delay=0), sandbox(reset=True))
agent.budget = 20_000
events = asyncio.run(drive(agent, "keep going forever"))
print(f"  ⎿  {[e.text for e in events if isinstance(e, Notice)][-1]}")
print("\nRe-sending a request is safe: the model saw nothing. Re-running a turn repeats every command it ran.")
