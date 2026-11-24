import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]
import hooks
import ralph
import tools
from agent import Agent
from common import Notice, drive
from fake import FakeModel, say
from scripts import SCRIPTS


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    ralph.setup(root, reset=True)
    return root


def test_ralph_finishes_every_feature_with_a_fresh_agent_per_pass(project, capsys):
    passes = asyncio.run(ralph.loop(project, FakeModel(SCRIPTS, delay=0), max_passes=6, quiet=True))
    assert passes == 3 and ralph.check(project)[0]
    log = subprocess.run(["git", "log", "--oneline"], cwd=project, capture_output=True, text=True).stdout
    assert len(log.splitlines()) == 4
    assert all(f["passes"] for f in json.loads((project / "features.json").read_text()))
    assert (project / "PROGRESS.md").read_text().count("- pass ") == 3


def test_saying_it_is_done_records_nothing(project, capsys):
    liar = FakeModel({"you are one pass of a loop": ("", [[say("All features are complete.")]])}, delay=0)
    assert asyncio.run(ralph.loop(project, liar, max_passes=2, quiet=True)) == 2
    assert not any(f["passes"] for f in json.loads((project / "features.json").read_text()))
    assert "discount still fails" in capsys.readouterr().out


def test_the_ledger_and_the_tests_are_off_limits_to_the_agent(project):
    agent = Agent(FakeModel(SCRIPTS, delay=0), project)
    agent.gate.mode = "accept-edits"
    for name in ("features.json", "PROGRESS.md", "test_cart.py"):
        assert agent.gate.check(tools.TOOLS["Edit"], {"path": name})[0] == "deny"


def test_the_stop_hook_sends_the_task_back_until_the_check_passes(project):
    prompt = (project / "PROMPT.md").read_text()
    agent = Agent(FakeModel(SCRIPTS, delay=0), project)
    agent.gate.mode = "accept-edits"
    agent.stop_hook = hooks.until(project, "python3 test_cart.py", prompt, max_iterations=5)
    agent.max_turns = 100
    events = asyncio.run(drive(agent, prompt, approve=False))
    assert sum(isinstance(e, Notice) and e.text.startswith("stop hook") for e in events) == 2
    assert ralph.check(project)[0]


def test_the_stop_hook_gives_up_after_max_iterations(project):
    hook = hooks.until(project, "exit 1", "try again", max_iterations=1)
    assert asyncio.run(hook("done")).startswith("try again")
    assert asyncio.run(hook("done")) is None


def test_headless_reads_the_prompt_from_stdin(project):
    out = subprocess.run([sys.executable, str(HERE / "main.py"), "--cwd", str(project), "-p", "-"],
                         input="where is total used?", capture_output=True, text=True,
                         env={"MODEL": "fake", "FAKE_DELAY": "0", "PATH": "/usr/bin:/bin"}, check=True)
    assert "defined in cart.py" in out.stdout
