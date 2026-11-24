import asyncio
import shutil
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]
import tools
from agent import INTERRUPTED, Agent
from common import REPO, Ask, Text, ToolResult, drive
from fake import FakeModel, validate
from scripts import SCRIPTS


@pytest.fixture
def root(tmp_path):
    shutil.copytree(REPO / "workspace", tmp_path / "ws")
    return tmp_path / "ws"


async def cancel_after(agent, prompt, seconds, on_event=None):
    seen = []

    async def consume():
        async for event in agent.turn(prompt):
            seen.append(event)
            if isinstance(event, Ask):
                event.answer.set_result("yes")
            if on_event:
                on_event(event)
    task = asyncio.create_task(consume())
    await asyncio.sleep(seconds)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    return seen


def test_cancel_during_a_tool_answers_it(root):
    agent = Agent(FakeModel(SCRIPTS, delay=0), root)
    asyncio.run(cancel_after(agent, "run something slow", 0.5))
    assert agent.messages[-1]["content"][0]["content"] == INTERRUPTED
    validate(agent.messages + [{"role": "user", "content": "are you still there?"}])


def test_cancel_kills_everything_the_command_started(root):
    agent = Agent(FakeModel(SCRIPTS, delay=0), root)
    SCRIPTS["marker"] = ("", [[{"type": "tool_use", "name": "Bash", "input": {"command": "sleep 1; touch marker"}}]])
    try:
        asyncio.run(cancel_after(agent, "marker", 0.3))
        time.sleep(1.5)
        assert not (root / "marker").exists()
    finally:
        del SCRIPTS["marker"]


def test_cancel_while_streaming_keeps_what_was_said(root):
    agent = Agent(FakeModel(SCRIPTS, delay=0.3), root)

    async def go():
        task = asyncio.current_task()
        words = []

        def cancel_soon(event):
            if isinstance(event, Text):
                words.append(event)
                if len(words) == 3:
                    asyncio.get_running_loop().call_later(0.1, task.cancel)
        try:
            await _drive_with(agent, "explain the bug in cart.py", cancel_soon)
        except asyncio.CancelledError:
            pass
    asyncio.run(go())
    said = agent.messages[-1]
    assert said["role"] == "assistant" and said["content"][0]["text"].startswith("`total()`")
    assert len(said["content"][0]["text"]) < 300
    validate(agent.messages + [{"role": "user", "content": "go on"}])


def test_steering_changes_course_at_the_next_tool_boundary(root):
    agent = Agent(FakeModel(SCRIPTS, delay=0), root)

    def steer(event):
        if isinstance(event, ToolResult) and event.output.strip() == "1":
            agent.steer("stop counting please")
    events = asyncio.run(_drive_with(agent, "count to five", steer))
    assert "I got to 1" in "".join(e.text for e in events if isinstance(e, Text))


async def _drive_with(agent, prompt, hook):
    events = []
    async for event in agent.turn(prompt):
        events.append(event)
        if isinstance(event, Ask):
            event.answer.set_result("yes")
        hook(event)
    return events


def test_commands_that_never_end_time_out(root):
    with pytest.raises(tools.ToolError, match="Timed out after 0.5s"):
        asyncio.run(tools.run(tools.Context(root), "Bash", {"command": "sleep 5", "timeout": 0.5}))


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")  # asyncio GCs the job's transport late
def test_background_jobs_return_at_once_and_log(root):
    async def go():
        ctx = tools.Context(root)
        started = await tools.run(ctx, "Bash", {"command": "echo hello; sleep 30", "run_in_background": True})
        await asyncio.sleep(0.3)
        log = next((root / ".agent" / "bg").glob("*.log")).read_text()
        tools.stop_background()
        for proc in tools.BACKGROUND.values():
            await proc.wait()
        tools.BACKGROUND.clear()
        return started, log
    started, log = asyncio.run(go())
    assert "Started in the background" in started and "hello" in log


def test_part_2_lessons_still_hold(root):
    events = asyncio.run(drive(Agent(FakeModel(SCRIPTS, delay=0), root), "what's in config.py?"))
    assert [e.is_error for e in events if isinstance(e, ToolResult)] == [True, False]
