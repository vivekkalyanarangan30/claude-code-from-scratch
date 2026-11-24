"""python part_2/main.py [--reset] [--cwd DIR]   ·   MODEL=fake | anthropic:<model> | ollama:<model>"""
import argparse
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import providers
import tui
from agent import Agent
from common import pick_model, sandbox

parser = argparse.ArgumentParser()
parser.add_argument("--reset", action="store_true", help="restore sandbox/ from workspace/")
parser.add_argument("--cwd", type=pathlib.Path, help="work in this directory instead of sandbox/")
args = parser.parse_args()

root = args.cwd.resolve() if args.cwd else sandbox(reset=args.reset)
spec, reason = pick_model()
tui.run(Agent(providers.make(spec), root), "part 2 · tool design", root, f"{spec} · {reason}")
