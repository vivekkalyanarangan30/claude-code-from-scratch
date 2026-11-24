import asyncio
import json
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]
import tools
from agent import Agent
from common import REPO, Ask, Notice, ToolCall, ToolResult, drive
from fake import FakeModel
from permissions import Gate, split_command
from scripts import EXFILTRATE, SCRIPTS

BASH, EDIT, READ = tools.TOOLS["Bash"], tools.TOOLS["Edit"], tools.TOOLS["Read"]


@pytest.fixture
def root(tmp_path):
    shutil.copytree(REPO / "workspace", tmp_path / "ws")
    shutil.copytree(HERE / "inject", tmp_path / "ws", dirs_exist_ok=True)
    return tmp_path / "ws"


def gate_with(root, allow=(), deny=(), mode="default"):
    gate = Gate(root)
    gate.rules = {"allow": list(allow), "deny": list(deny)}
    gate.mode = mode
    return gate


def test_compound_commands_are_split_and_odd_ones_refused():
    assert split_command("git status && npm test | tee log") == [["git", "status"], ["npm", "test"], ["tee", "log"]]
    assert split_command("echo $(whoami)") is None
    assert split_command("echo hi > out.txt") is None
    assert split_command("echo 'unterminated") is None


def test_every_part_of_a_command_must_be_allowed(root):
    gate = gate_with(root, allow=["Bash(git status:*)"])
    assert gate.check(BASH, {"command": "git status -s"})[0] == "allow"
    assert gate.check(BASH, {"command": EXFILTRATE})[0] == "ask"


def test_deny_rules_win_even_for_read_only_tools(root):
    gate = gate_with(root, allow=["Read"], deny=["Read(api_key.txt)"])
    assert gate.check(READ, {"path": "api_key.txt"})[0] == "deny"
    assert gate.check(READ, {"path": "./cart.py"})[0] == "allow"


def test_modes(root):
    assert gate_with(root, mode="plan").check(EDIT, {"path": "cart.py"})[0] == "deny"
    assert gate_with(root, mode="plan").check(READ, {"path": "cart.py"})[0] == "allow"
    assert gate_with(root, mode="accept-edits").check(EDIT, {"path": "cart.py"})[0] == "allow"
    assert gate_with(root, mode="accept-edits").check(BASH, {"command": "ls"})[0] == "ask"


def test_dont_ask_again_saves_a_rule_to_disk(root):
    agent = Agent(FakeModel(SCRIPTS, delay=0), root)

    async def go():
        async for event in agent.turn("fix the failing test"):
            if isinstance(event, Ask):
                event.answer.set_result("always" if event.always else "yes")
    asyncio.run(go())
    saved = json.loads((root / ".agent" / "settings.json").read_text())["permissions"]["allow"]
    assert saved == ["Bash(python3 test_cart.py)", "Edit"]
    assert Gate(root).check(BASH, {"command": "python3 test_cart.py"})[0] == "allow"


def test_the_injected_command_is_asked_about_and_a_no_runs_nothing(root):
    events = asyncio.run(drive(Agent(FakeModel(SCRIPTS, delay=0), root), "set up the project", approve=False))
    assert [e.input.get("command") for e in events if isinstance(e, ToolCall)][1] == EXFILTRATE
    assert sum(isinstance(e, Ask) for e in events) == 1
    assert "denied" in [e for e in events if isinstance(e, ToolResult)][-1].output


def test_plan_mode_reads_but_changes_nothing(root):
    agent = Agent(FakeModel(SCRIPTS, delay=0), root)
    agent.set_mode("plan")
    events = asyncio.run(drive(agent, "plan the fix"))
    assert not any(isinstance(e, Ask) for e in events)
    assert "subtotal - discount" in (root / "cart.py").read_text()
    assert "plan mode" in [e for e in events if isinstance(e, ToolResult)][-1].output


def test_auto_mode_lets_the_judge_answer_before_you(root):
    agent = Agent(FakeModel(SCRIPTS, delay=0), root)
    agent.set_mode("auto")
    events = asyncio.run(drive(agent, "fix the failing test"))
    assert not any(isinstance(e, Ask) for e in events)
    assert "the judge allowed this" in [e.text for e in events if isinstance(e, Notice)][0]


def test_auto_mode_asks_you_when_the_judge_says_ask(root):
    agent = Agent(FakeModel(SCRIPTS, delay=0), root)
    agent.set_mode("auto")
    events = asyncio.run(drive(agent, "set up the project", approve=False))
    assert sum(isinstance(e, Ask) for e in events) == 1


def test_a_judge_that_fails_means_ask(root):
    class Broken:
        name = "broken"

        def stream(self, *args):
            raise RuntimeError("judge offline")
    from judge import judge
    assert asyncio.run(judge(Broken(), ["fix it"], "Bash", {"command": "ls"})) == "ask"
