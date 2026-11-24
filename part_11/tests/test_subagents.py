import asyncio
import shutil
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]
import tools
from agent import Agent, batches
from common import REPO, ToolResult, drive
from fake import FakeModel, call, say, validate
from scripts import SCRIPTS


@pytest.fixture
def root(tmp_path):
    shutil.copytree(REPO / "workspace", tmp_path / "ws")
    return tmp_path / "ws"


def block(name, n):
    return {"type": "tool_use", "id": f"t{n}", "name": name, "input": {}}


def test_reads_are_batched_writes_run_alone_and_order_is_kept():
    decided = [(block(name, i), "allow", "") for i, name in enumerate(["Read", "Grep", "Edit", "Read", "Glob"])]
    groups = [[b["name"] for b, _, _ in group] for group in batches(decided)]
    assert groups == [["Read", "Grep"], ["Edit"], ["Read", "Glob"]]


def test_a_denied_read_does_not_join_a_batch():
    decided = [(block("Read", 1), "allow", ""), (block("Read", 2), "deny", "no"), (block("Read", 3), "allow", "")]
    assert [len(g) for g in batches(decided)] == [1, 1, 1]


def test_subagents_run_at_the_same_time(root):
    agent = Agent(FakeModel(SCRIPTS, delay=0.2), root)
    start = time.perf_counter()
    events = asyncio.run(drive(agent, "explore the project"))
    elapsed = time.perf_counter() - start
    summaries = [e.output for e in events if isinstance(e, ToolResult)]
    assert len(summaries) == 3 and all("subagent" in s for s in summaries)
    assert elapsed < 3.0


def test_a_subagent_cannot_write_or_spawn(root):
    SCRIPTS["sneaky"] = ("", [[call("Edit", path="cart.py", old_string="a", new_string="b"),
                               call("Agent", description="x", prompt="y")], [say("tried")]])
    try:
        child = Agent(FakeModel(SCRIPTS, delay=0), root, allowed={"Read", "Glob", "Grep"})
        events = asyncio.run(drive(child, "sneaky"))
        errors = [e.output for e in events if isinstance(e, ToolResult)]
        assert all("can only use Glob, Grep, Read" in e for e in errors) and len(errors) == 2
    finally:
        del SCRIPTS["sneaky"]


def test_cancel_reaches_every_subagent(root):
    agent = Agent(FakeModel(SCRIPTS, delay=0.3), root)

    async def go():
        task = asyncio.create_task(drive(agent, "explore the project"))
        await asyncio.sleep(1.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert asyncio.run(go()) == []
    validate(agent.messages + [{"role": "user", "content": "go on"}])


def test_two_editors_one_file(root):
    async def go():
        a, b = tools.Context(root), tools.Context(root)
        await tools.run(a, "Read", {"path": "cart.py"})
        await tools.run(b, "Read", {"path": "cart.py"})
        await tools.run(a, "Edit", {"path": "cart.py", "old_string": "return subtotal - discount", "new_string": "return subtotal"})
        await tools.run(b, "Edit", {"path": "cart.py", "old_string": "return subtotal", "new_string": "return 0"})
    with pytest.raises(tools.ToolError, match="changed since"):
        asyncio.run(go())
