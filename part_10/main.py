"""python part_10/main.py [--reset] [--resume] [--inject] [--cwd DIR] [-p PROMPT [--json] [--yes] [--until CMD]]"""
import argparse
import asyncio
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import headless
import hooks
import providers
import session
import tui
from agent import Agent
from common import pick_model, sandbox, with_faults
from permissions import MODES

parser = argparse.ArgumentParser()
parser.add_argument("--reset", action="store_true", help="restore sandbox/ from workspace/")
parser.add_argument("--resume", action="store_true", help="continue the latest session in this folder")
parser.add_argument("--inject", action="store_true", help="plant a poisoned README and a fake key in sandbox/")
parser.add_argument("--cwd", type=pathlib.Path, help="work in this directory instead of sandbox/")
parser.add_argument("--mode", choices=MODES, default="default", help="permission mode to start in")
parser.add_argument("-p", "--print", dest="prompt", help="run one prompt without the screen, then exit (- reads stdin)")
parser.add_argument("--json", action="store_true", help="with -p: print every event as a JSON line")
parser.add_argument("--yes", action="store_true", help="with -p: approve every tool call (careful)")
parser.add_argument("--until", metavar="CMD", help="a stop hook: keep working until CMD exits 0")
parser.add_argument("--budget", type=int, help="stop the loop once this many tokens are spent")
parser.add_argument("--max-iterations", type=int, default=5, help="with --until: how many times to send it back")
args = parser.parse_args()

root = args.cwd.resolve() if args.cwd else sandbox(reset=args.reset or args.inject)
if args.inject:
    shutil.copytree(HERE.parent / "part_5" / "inject", root, dirs_exist_ok=True)
spec, reason = pick_model()
agent = Agent(with_faults(providers.make(spec)), root)
if args.resume and (path := session.latest(root)):
    agent, unknown = Agent.resume(providers.make(spec), root, path)
    print(f"resumed {path.name}: {len(agent.messages)} messages"
          + (f", {unknown} tool call(s) with no recorded outcome" if unknown else ""))
elif args.resume:
    print("no session to resume in this folder")
agent.gate.mode = args.mode
agent.budget = args.budget
prompt = sys.stdin.read() if args.prompt == "-" else args.prompt
if args.until:
    agent.stop_hook = hooks.until(root, args.until, prompt or (lambda: agent.requests[-1]), args.max_iterations)
    agent.max_turns = 25 * (args.max_iterations + 1)
if args.prompt:
    asyncio.run(headless.run(agent, prompt, args.json, args.yes))
else:
    tui.run(agent, "part 10 · concurrency and subagents", root, f"{spec} · {reason}")
