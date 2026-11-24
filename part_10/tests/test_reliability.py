import asyncio
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]
import openai_compat
import retry
from agent import Agent
from common import REPO, APIError, Notice, drive
from fake import FakeModel
from scripts import SCRIPTS


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr(retry, "BASE", 0.001)


@pytest.fixture
def root(tmp_path):
    shutil.copytree(REPO / "workspace", tmp_path / "ws")
    return tmp_path / "ws"


def test_backoff_is_jittered_capped_and_respects_retry_after():
    waits = [retry.delay(10) for _ in range(50)]
    assert all(0 <= w <= retry.CAP for w in waits) and len(set(waits)) > 1
    assert retry.delay(0, retry_after=7) == 7


def test_what_gets_retried():
    assert retry.retryable(APIError(529, "overloaded")) and retry.retryable(APIError(None, "reset"))
    assert not retry.retryable(APIError(400, "invalid_request_error"))


def test_a_529_after_a_command_ran_does_not_run_it_twice(root):
    events = asyncio.run(drive(Agent(FakeModel(SCRIPTS, delay=0, faults=[0, 529, 529]), root), "commit the change"))
    assert (root / "commits.log").read_text().splitlines() == ["fix discount"]
    assert sum(isinstance(e, Notice) and "retrying" in e.text for e in events) == 2


def test_a_400_is_never_retried(root):
    model = FakeModel(SCRIPTS, delay=0, faults=[400])
    with pytest.raises(APIError, match="400"):
        asyncio.run(drive(Agent(model, root), "fix the failing test"))


def test_retries_run_out(root):
    with pytest.raises(APIError, match="529"):
        asyncio.run(drive(Agent(FakeModel(SCRIPTS, delay=0, faults=[529] * 10), root), "fix the failing test"))


def test_the_budget_stops_the_loop(root):
    agent = Agent(FakeModel(SCRIPTS, delay=0), root)
    agent.budget = 10_000
    events = asyncio.run(drive(agent, "keep going forever"))
    assert "budget" in events[-1].text and sum(agent.tokens) < 15_000


def test_openai_translation_round_trip():
    messages = [{"role": "user", "content": "fix it"},
                {"role": "assistant", "content": [{"type": "thinking", "thinking": "hmm"},
                                                  {"type": "tool_use", "id": "c1", "name": "Read", "input": {"path": "a"}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "nope", "is_error": True}]}]
    chat, functions = openai_compat.to_openai("sys", messages, [{"name": "Read", "description": "d", "input_schema": {}}])
    assert [m["role"] for m in chat] == ["system", "user", "assistant", "tool"]
    assert chat[2]["tool_calls"][0]["function"]["arguments"] == '{"path": "a"}' and "thinking" not in str(chat)
    assert chat[3]["content"] == "Error: nope" and functions[0]["function"]["name"] == "Read"


def test_what_leaks_through_arguments_that_are_not_json():
    body = {"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [
        {"id": "c1", "function": {"name": "Read", "arguments": "{path: cart.py"}}]}}]}
    message = openai_compat.from_openai(body)
    assert message["stop_reason"] == "tool_use"
    assert message["content"][0]["input"] == {"_unparsed_arguments": "{path: cart.py"}
