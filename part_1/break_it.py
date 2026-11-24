"""1.4 — A model that never says it's done. Without MAX_TURNS this loop runs until your card declines."""
import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
from agent import MAX_TURNS, Agent
from common import Notice, ToolCall, drive, sandbox
from fake import FakeModel
from scripts import SCRIPTS

model = FakeModel(SCRIPTS, delay=0)
agent = Agent(model, sandbox(reset=True))
events = asyncio.run(drive(agent, "keep going forever"))

calls = sum(isinstance(e, ToolCall) for e in events)
print(f"model calls: {calls}, input tokens billed: {agent.tokens[0]:,}")
print(f"stopped by: {next(e.text for e in events if isinstance(e, Notice))}")
print(f"\nEvery call re-sends the whole transcript, so input cost grows with the square of the turns.")
print(f"The script had 1000 steps. MAX_TURNS={MAX_TURNS} is the only reason this ended.")
