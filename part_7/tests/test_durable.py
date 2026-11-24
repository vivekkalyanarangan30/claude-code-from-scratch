import asyncio
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]
import session
from agent import Agent
from common import REPO, drive
from fake import FakeModel, validate
from scripts import SCRIPTS


@pytest.fixture
def root(tmp_path):
    shutil.copytree(REPO / "workspace", tmp_path / "ws")
    return tmp_path / "ws"


def fresh(root):
    return Agent(FakeModel(SCRIPTS, delay=0), root)


def test_resume_rebuilds_the_same_transcript(root):
    agent = fresh(root)
    asyncio.run(drive(agent, "fix the failing test"))
    again, unknown = Agent.resume(FakeModel(SCRIPTS, delay=0), root, agent.log.path)
    assert again.messages == agent.messages and unknown == 0


def test_a_torn_last_line_is_dropped_not_fatal(root):
    agent = fresh(root)
    asyncio.run(drive(agent, "fix the failing test"))
    with open(agent.log.path, "a") as f:
        f.write('{"kind": "message", "mess')
    assert len(session.read(agent.log.path)) == len(agent.log.path.read_text().splitlines()) - 1


def crashed_log(root, with_intent):
    """A log that ends where a process died: an assistant tool call, maybe an intent, no result."""
    log = session.Log(root)
    call = {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git push"}}
    log.append("turn", at=0, prompt="push it")
    log.append("message", message={"role": "user", "content": "push it"})
    log.append("message", message={"role": "assistant", "content": [call]})
    if with_intent:
        log.append("intent", id="t1", name="Bash", input=call["input"])
    return log.path


def test_an_intent_without_a_result_is_outcome_unknown(root):
    agent, unknown = Agent.resume(FakeModel(SCRIPTS, delay=0), root, crashed_log(root, with_intent=True))
    assert unknown == 1 and agent.messages[-1]["content"][0]["content"] == session.UNKNOWN
    validate(agent.messages + [{"role": "user", "content": "go on"}])


def test_a_call_that_never_started_is_not_run(root):
    agent, _ = Agent.resume(FakeModel(SCRIPTS, delay=0), root, crashed_log(root, with_intent=False))
    assert agent.messages[-1]["content"][0]["content"] == session.NOT_RUN


def test_intent_is_logged_before_the_tool_runs(root):
    agent = fresh(root)
    asyncio.run(drive(agent, "fix the failing test"))
    kinds = [r["kind"] for r in session.read(agent.log.path)]
    first_intent = kinds.index("intent")
    assert kinds[first_intent + 1] == "result"


def test_rewind_restores_files_and_conversation(root):
    agent = fresh(root)
    asyncio.run(drive(agent, "what's in config.py?"))
    kept = list(agent.messages)
    asyncio.run(drive(agent, "fix the failing test"))
    assert "subtotal * (1" in (root / "cart.py").read_text()
    note = agent.rewind("")
    assert "restored cart.py" in note
    assert "subtotal - discount" in (root / "cart.py").read_text()
    assert agent.messages == kept
    again, _ = Agent.resume(FakeModel(SCRIPTS, delay=0), root, agent.log.path)
    assert again.messages == kept


def test_atomic_write_leaves_no_temp_files(root):
    session.atomic_write(root / "out.txt", "hello")
    assert (root / "out.txt").read_text() == "hello"
    assert [p.name for p in root.iterdir() if p.name.endswith(".tmp")] == []
