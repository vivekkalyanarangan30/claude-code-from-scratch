"""8.2 — Hand the agent a three-feature job. It says it's finished after one."""
import asyncio
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import hooks
from agent import Agent
from common import REPO, Notice, drive, load_part
from fake import FakeModel
from scripts import SCRIPTS

part_7 = load_part(7, "agent")


def fresh_project():
    root = REPO / "sandbox_ralph"
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(HERE / "project", root)
    return root, (root / "PROMPT.md").read_text()


def tests(root):
    out = subprocess.run([sys.executable, "test_cart.py"], cwd=root, capture_output=True, text=True).stdout
    return " · ".join(out.splitlines())


print("── Part 7, one run")
root, prompt = fresh_project()
agent = part_7.Agent(FakeModel(SCRIPTS, delay=0), root)
agent.gate.mode = "accept-edits"
asyncio.run(drive(agent, prompt, approve=False))
print(f"  model: \"{agent.messages[-1]['content'][-1]['text']}\"\n  tests: {tests(root)}")

print("\n── Part 8, a stop hook: the tests decide when it's done")
root, prompt = fresh_project()
agent = Agent(FakeModel(SCRIPTS, delay=0), root)
agent.gate.mode = "accept-edits"
agent.stop_hook = hooks.until(root, "python3 test_cart.py", prompt, max_iterations=5)
agent.max_turns = 100
events = asyncio.run(drive(agent, prompt, approve=False))
sent_back = sum(isinstance(e, Notice) and e.text.startswith("stop hook") for e in events)
print(f"  sent back {sent_back} times · model: \"{agent.messages[-1]['content'][-1]['text']}\"\n  tests: {tests(root)}")
print("\nThe stop hook keeps one long context. part_8/ralph.py does the same with a fresh agent every pass.")
