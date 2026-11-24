"""python part_3/main.py [--reset] [--cwd DIR] [-p PROMPT [--json] [--yes]]   ·   MODEL=fake | anthropic:<m> | ollama:<m>"""
import argparse
import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import headless
import providers
import tui
from agent import Agent
from common import pick_model, sandbox

parser = argparse.ArgumentParser()
parser.add_argument("--reset", action="store_true", help="restore sandbox/ from workspace/")
parser.add_argument("--cwd", type=pathlib.Path, help="work in this directory instead of sandbox/")
parser.add_argument("-p", "--print", dest="prompt", help="run one prompt without the screen, then exit")
parser.add_argument("--json", action="store_true", help="with -p: print every event as a JSON line")
parser.add_argument("--yes", action="store_true", help="with -p: approve every tool call (careful)")
args = parser.parse_args()

root = args.cwd.resolve() if args.cwd else sandbox(reset=args.reset)
spec, reason = pick_model()
agent = Agent(providers.make(spec), root)
if args.prompt:
    asyncio.run(headless.run(agent, args.prompt, args.json, args.yes))
else:
    tui.run(agent, "part 3 · streaming", root, f"{spec} · {reason}")
