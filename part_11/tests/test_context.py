import asyncio
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]
import context
import tools
from agent import Agent
from common import REPO, Notice, drive
from fake import FakeModel, FakeAPIError
from scripts import SCRIPTS


@pytest.fixture
def root(tmp_path):
    shutil.copytree(REPO / "workspace", tmp_path / "ws")
    return tmp_path / "ws"


def test_system_prompt_carries_environment_and_agents_md(root):
    system = context.system_prompt(root, "BASE")
    assert system.startswith("BASE") and f"Working directory: {root}" in system
    assert "pytest is not installed" in system


def test_the_fake_rejects_a_prompt_bigger_than_its_window(root):
    with pytest.raises(FakeAPIError, match="prompt is too long"):
        asyncio.run(FakeModel(SCRIPTS, delay=0, window=10).complete("x" * 100, [{"role": "user", "content": "hi"}], []))


def test_append_only_turns_read_the_prefix_from_cache(root):
    agent = Agent(FakeModel(SCRIPTS, delay=0), root)
    asyncio.run(drive(agent, "fix the failing test"))
    assert agent.cache[0] > agent.cache[1] > 0


def test_clearing_keeps_the_newest_results():
    messages = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": str(i), "content": f"out {i}"}]}
                for i in range(5)]
    assert context.clear_old_results(messages, keep=3) == 2
    assert [m["content"][0]["content"] for m in messages] == [context.CLEARED] * 2 + ["out 2", "out 3", "out 4"]


def test_a_long_task_survives_a_small_window(root):
    agent = Agent(FakeModel(SCRIPTS, delay=0, window=9_000), root)
    events = asyncio.run(drive(agent, "audit the logs"))
    notices = [e.text for e in events if isinstance(e, Notice)]
    assert any(n.startswith("cleared") for n in notices) and any(n.startswith("compacted") for n in notices)
    assert "all 12 batches" in agent.messages[-1]["content"][-1]["text"]


def test_compaction_keeps_the_request_and_the_todo_list(root):
    agent = Agent(FakeModel(SCRIPTS, delay=0), root)
    asyncio.run(drive(agent, "audit the logs"))
    agent.ctx.todos = [{"content": "ship it", "status": "pending"}]
    asyncio.run(agent.compact())
    restart = agent.messages[0]["content"]
    assert len(agent.messages) == 1 and "audit the logs" in restart and "☐ ship it" in restart


def test_todo_write_validates_its_input(root):
    ctx = tools.Context(root)
    assert asyncio.run(tools.run(ctx, "TodoWrite", {"todos": [{"content": "a", "status": "completed"}]})) == "☒ a"
    with pytest.raises(tools.ToolError, match="list of"):
        asyncio.run(tools.run(ctx, "TodoWrite", {"todos": "do stuff"}))
