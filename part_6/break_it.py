"""6.2 — kill -9 in the middle of a deploy. What does each part know when it starts again?"""
import asyncio
import os
import pathlib
import signal
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import session
from agent import Agent
from common import ToolResult, drive, load_part, sandbox
from fake import FakeModel
from scripts import SCRIPTS


def deploy_and_kill(part):
    """Run part N's agent in a child process, kill -9 it mid-deploy, and show what's left."""
    root = sandbox(reset=True)
    proc = subprocess.Popen([sys.executable, __file__, "--child", str(part)], start_new_session=True,
                            env={**os.environ, "FAKE_DELAY": "0"}, stdout=subprocess.DEVNULL)
    log, deadline = root / "deploy.log", time.time() + 15
    while not log.exists() and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(0.5)
    os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()
    print(f"  killed it. deploy.log: {log.read_text().split() if log.exists() else 'missing'}")
    return root


if sys.argv[1:2] == ["--child"]:
    child = load_part(int(sys.argv[2]), "agent").Agent(FakeModel(SCRIPTS), sandbox())
    asyncio.run(drive(child, "deploy the release"))
    sys.exit()

print("── Part 5")
root = deploy_and_kill(5)
print(f"  on disk afterwards: {sorted(p.name for p in (root / '.agent').glob('*')) or 'nothing'}"
      " — the conversation lived in memory\n")

print("── Part 6")
root = deploy_and_kill(6)
path = session.latest(root)
print(f"  on disk afterwards: {path.name}, {len(session.read(path))} records")
agent, unknown = Agent.resume(FakeModel(SCRIPTS, delay=0), root, path)
print(f"  resumed: {unknown} tool call with no recorded result → \"{agent.messages[-1]['content'][0]['content'][:60]}…\"")
print("  > did the deploy finish?")
for event in asyncio.run(drive(agent, "did the deploy finish?")):
    if isinstance(event, ToolResult):
        print(f"  ⎿  {event.output.strip()}")
print(f"  ⏺ {agent.messages[-1]['content'][-1]['text']}")
print("\nThe log can't say whether the command ran. It can say that nobody knows, so the model checks.")
