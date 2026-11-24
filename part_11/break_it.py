"""11.2–11.4 — Part 10 run on a schedule: nobody to say yes, and the 09:00 run still going at 09:05.
Nothing here executes the curl: Part 10's --yes column is the gate's decision, not a run."""
import asyncio
import datetime as dt
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import always_on
import cron
import tools
from common import ToolCall, ToolResult, drive, load_part, sandbox
from fake import FakeModel
from permissions import Gate
from scripts import BUG, EXFILTRATE, FIX, SCRIPTS

part_10 = load_part(10, "agent", "permissions")
ALLOW = ["Bash(python3 test_cart.py)", "Edit(cart.py)"]


def fake():
    return FakeModel(SCRIPTS, delay=0)


print("── 11.2 Cron: when does each schedule fire next, from Thu 12 Nov 09:03?")
for expr, meaning in (("*/15 * * * *", "every 15 minutes"), ("0 9 * * 1-5", "09:00 on weekdays"),
                      ("@daily", "midnight"), ("0 0 1 * *", "the 1st of the month")):
    print(f"  {expr:<14} {meaning:<22} → {cron.next_run(expr, dt.datetime(2026, 11, 12, 9, 3)):%a %d %b %H:%M}")

print("\n── 11.3 Nobody is there to say yes. Who runs what?")
root = sandbox(reset=True)
shutil.copytree(HERE / "inject", root, dirs_exist_ok=True)
old, new = part_10[1].Gate(root), Gate(root)
new.mode, new.rules["allow"] = "dont-ask", ALLOW
calls = [("Bash", {"command": "python3 test_cart.py"}),
         ("Edit", {"path": "cart.py", "old_string": BUG, "new_string": FIX}),
         ("Bash", {"command": EXFILTRATE})]
print(f"  {'tool call':<46}{'Part 10 -p':<13}{'Part 10 -p --yes':<19}Part 11 dont-ask")
for name, args in calls:
    asked = old.check(tools.TOOLS[name], args)[0] == "ask"
    label = f"{name}({args.get('command') or args['path']})"
    print(f"  {label[:44]:<46}{'denied' if asked else 'runs':<13}{'runs' if asked else '?':<19}"
          f"{'runs' if new.check(tools.TOOLS[name], args)[0] == 'allow' else 'denied'}")
print("  Part 10's only switches are \"every call refused\" and \"every call allowed, the curl too\".\n"
      "  Part 11 decides each call from the rules written before anyone left; a question becomes a no.")

asked = always_on.REQUEST.format(name="setup.txt", text="set up the project")
report = asyncio.run(always_on.run_once(root, fake(), asked, ALLOW, source="inbox/setup.txt"))
print(f"  a poisoned README, a run from inbox/: {always_on.summary(report)}")
report = asyncio.run(always_on.run_once(sandbox(reset=True), fake(), "fix the failing test", ALLOW,
                                        until="python3 test_cart.py"))
print(f"  the nightly \"fix the failing test\":   {always_on.summary(report)} · check passed: {report['check_passed']}")

print("\n── 11.4 The 09:00 run is still going when cron starts the 09:05 one")


async def part_10_twice(root):
    agents = [part_10[0].Agent(fake(), root) for _ in range(2)]
    runs = await asyncio.gather(*(drive(agent, "fix the failing test") for agent in agents))
    for n, events in enumerate(runs, 1):
        edits = {e.id for e in events if isinstance(e, ToolCall) and e.name == "Edit"}
        edit = next(e for e in events if isinstance(e, ToolResult) and e.id in edits)
        said = agents[n - 1].messages[-1]["content"][-1]["text"]
        print(f"  Part 10, run {n}: Edit → {edit.output.splitlines()[0][:60]} · says \"{said[:45]}\"")


asyncio.run(part_10_twice(sandbox(reset=True)))


async def part_11_twice(root):
    reports = await asyncio.gather(*(always_on.run_once(root, fake(), "fix the failing test", ALLOW)
                                     for _ in range(2)))
    for n, report in enumerate(reports, 1):
        print(f"  Part 11, run {n}: {always_on.summary(report)}")


asyncio.run(part_11_twice(sandbox(reset=True)))
print("  Two agents in one folder at once is Part 10's two-editors problem on a timer. A lock makes the\n"
      "  late run skip, and every run, skipped or not, leaves a report in .agent/runs/.")
