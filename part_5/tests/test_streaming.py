import asyncio
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]
import sse
from common import REPO, sse as encode, stream_events
from fake import call, say

MESSAGE = {"id": "m", "content": [say("Reading it now."), {**call("Read", path="cart.py"), "id": "t1"}],
           "stop_reason": "tool_use", "usage": {"input_tokens": 10, "output_tokens": 5}}


async def lines_of(events):
    for event in events:
        for line in encode(event):
            yield line


async def assemble(events):
    reply, seen = sse.Reply(), []
    async for event in sse.events(lines_of(events)):
        reply.feed(event)
        seen.append((event["type"], reply.blocks.get(1, {}).get("input")))
    return reply, seen


def test_stream_reassembles_the_same_message():
    reply, _ = asyncio.run(assemble(stream_events(MESSAGE)))
    assert reply.content == MESSAGE["content"] and reply.stop_reason == "tool_use"


def test_tool_input_is_not_usable_until_its_block_closes():
    _, seen = asyncio.run(assemble(stream_events(MESSAGE)))
    inputs = [inp for kind, inp in seen if kind == "content_block_delta" and inp is not None]
    assert inputs and all(inp == {} for inp in inputs)
    assert seen[-3] == ("content_block_stop", {"path": "cart.py"})


def test_ollama_reports_input_tokens_split_with_the_cache():
    reply = sse.Reply()
    reply.feed({"type": "message_start", "message": {"usage": {"input_tokens": 65, "output_tokens": 0}}})
    reply.feed({"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                "usage": {"input_tokens": 1, "cache_read_input_tokens": 164, "output_tokens": 117}})
    assert reply.input_tokens == 165


def test_headless_json_is_a_second_subscriber(tmp_path):
    import shutil
    shutil.copytree(REPO / "workspace", tmp_path / "ws")
    out = subprocess.run([sys.executable, str(HERE / "main.py"), "--cwd", str(tmp_path / "ws"),
                          "-p", "where is total used?", "--json"], capture_output=True, text=True,
                         env={"MODEL": "fake", "FAKE_DELAY": "0", "PATH": "/usr/bin:/bin"}, check=True)
    kinds = [json.loads(line)["type"] for line in out.stdout.splitlines()]
    assert kinds[:2] == ["ToolCall", "ToolResult"] and set(kinds[2:]) == {"Text"}
