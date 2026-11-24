import asyncio
import pathlib
import shutil
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]
import agent as agent_module
import tools
from agent import Agent
from common import REPO, Notice, ToolCall, drive
from fake import FakeAPIError, FakeModel, validate
from scripts import SCRIPTS


@pytest.fixture
def root(tmp_path):
    shutil.copytree(REPO / "workspace", tmp_path / "ws")
    return tmp_path / "ws"


def test_fixes_the_bug(root):
    events = asyncio.run(drive(Agent(FakeModel(SCRIPTS, delay=0), root), "fix the failing test"))
    assert [e.name for e in events if isinstance(e, ToolCall)] == ["Bash", "Read", "Write", "Bash"]
    assert subprocess.run([sys.executable, "test_cart.py"], cwd=root).returncode == 0


def test_transcript_is_the_only_state(root):
    agent = Agent(FakeModel(SCRIPTS, delay=0), root)
    asyncio.run(drive(agent, "fix the failing test"))
    assert [m["role"] for m in agent.messages] == ["user", "assistant"] * 5
    validate(agent.messages + [{"role": "user", "content": "next"}])


def test_denied_tool_changes_nothing(root):
    asyncio.run(drive(Agent(FakeModel(SCRIPTS, delay=0), root), "fix the failing test", approve=False))
    assert "subtotal - discount" in (root / "cart.py").read_text()


def test_max_turns(root, monkeypatch):
    monkeypatch.setattr(agent_module, "MAX_TURNS", 5)
    events = asyncio.run(drive(Agent(FakeModel(SCRIPTS, delay=0), root), "keep going forever"))
    assert isinstance(events[-1], Notice)


def test_paths_stay_inside_the_project(root):
    with pytest.raises(PermissionError):
        tools.inside(root, "../../etc/passwd")


def test_fake_is_as_strict_as_the_api():
    dangling = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]},
                {"role": "user", "content": "are you there?"}]
    with pytest.raises(FakeAPIError, match="without `tool_result`"):
        validate(dangling)
