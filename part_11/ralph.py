"""8.1, 8.4 — The Ralph loop: a fresh agent every pass, the state in files, a commit after every pass.

python part_8/ralph.py [--reset] [--max-passes N]      works in sandbox_ralph/, a copy of part_8/project/
"""
import argparse
import asyncio
import json
import pathlib
import re
import shlex
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import headless
import hooks
import providers
from agent import Agent
from common import REPO, drive, pick_model

GIT = ["git", "-c", "user.name=ralph", "-c", "user.email=ralph@localhost"]
SEND_BACKS = 3
THIS_PASS = """
This pass: make "{id}" pass ({description}).
Its check, `python3 test_cart.py {id}`, says right now:
{output}"""


def setup(root: pathlib.Path, reset: bool):
    if reset and root.exists():
        shutil.rmtree(root)
    if not root.exists():
        shutil.copytree(HERE / "project", root)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run([*GIT, "add", "-A"], cwd=root, check=True)
        subprocess.run([*GIT, "commit", "-qm", "start"], cwd=root, check=True)


def tests(root, *names) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "test_cart.py", *names], cwd=root, capture_output=True, text=True)


def check(root) -> tuple[bool, str]:
    result = tests(root)
    return result.returncode == 0, " · ".join(line.split(":")[0] for line in result.stdout.splitlines())


def record(root, after_pass: int = 0):
    """The tests, not the model, write the ledger. Returns the first feature that still fails, or None."""
    passing = set(re.findall(r"^PASS (\w+)$", tests(root).stdout, re.M))
    path = root / "features.json"
    features = json.loads(path.read_text())
    for feature in features:
        feature["passes"] = feature["id"] in passing
    path.write_text("[\n" + ",\n".join("  " + json.dumps(f) for f in features) + "\n]\n")
    if after_pass:
        line = " · ".join(f"{f['id']} {'PASS' if f['passes'] else 'FAIL'}" for f in features)
        with open(root / "PROGRESS.md", "a") as progress:
            progress.write(f"- pass {after_pass}: {line}\n")
    return next((f for f in features if not f["passes"]), None)


def commit(root, message) -> bool:
    subprocess.run([*GIT, "add", "-A"], cwd=root, check=True)
    return subprocess.run([*GIT, "commit", "-qm", message], cwd=root, capture_output=True).returncode == 0


async def one_pass(root, model, feature, quiet):
    """A fresh agent with one feature to do, and a stop hook that holds it to that feature's check."""
    output = tests(root, feature["id"]).stdout.strip()
    prompt = (root / "PROMPT.md").read_text() + THIS_PASS.format(output=output, **feature)
    agent = Agent(model, root)
    agent.gate.mode = "accept-edits"
    agent.stop_hook = hooks.until(root, f"python3 test_cart.py {shlex.quote(feature['id'])}", prompt, SEND_BACKS)
    agent.max_turns = 60
    if quiet:
        await drive(agent, prompt, approve=False)
    else:
        await headless.run(agent, prompt)
    return " ".join(b.get("text", "") for b in agent.messages[-1]["content"] if isinstance(b, dict))


async def loop(root, model, max_passes, quiet=False):
    """Run passes until every feature's test passes. Returns the number of passes used."""
    feature = record(root)
    for n in range(1, max_passes + 1):
        if feature is None:
            break
        print(f"\n━━ pass {n} · fresh context · {feature['id']}")
        said = await one_pass(root, model, feature, quiet)
        worked_on, feature = feature["id"], record(root, n)
        committed = "committed" if commit(root, f"ralph pass {n}: {worked_on}") else "nothing to commit"
        print(f"━━ pass {n} done · model: \"{said.strip()[:70]}\" · tests: {check(root)[1]} · {committed}")
    else:
        n = max_passes + 1
    if feature is None:
        print(f"━━ complete after {n - 1} passes: every feature's test passes")
        return n - 1
    print(f"━━ stopped after {max_passes} passes; {feature['id']} still fails")
    return max_passes


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="start again from part_8/project/")
    parser.add_argument("--max-passes", type=int, default=10)
    args = parser.parse_args()
    root = REPO / "sandbox_ralph"
    setup(root, args.reset)
    spec, reason = pick_model()
    print(f"ralph · {spec} · {reason} · {root}")
    asyncio.run(loop(root, providers.make(spec), args.max_passes))
    print("\n" + (root / "PROGRESS.md").read_text().strip())
