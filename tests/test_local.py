import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import local

TOOLS = [{"name": "Read", "description": "Read a file.", "input_schema": {"type": "object", "properties": {}}}]


def ids():
    n = iter(range(1, 100))
    return lambda: f"t{next(n)}"


def test_render_follows_qwen3_chat_format():
    messages = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": [{"type": "text", "text": "Looking."},
                                                  {"type": "tool_use", "id": "t1", "name": "Read", "input": {"path": "a"}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "nope", "is_error": True}]}]
    prompt = local.render("sys", messages, TOOLS)
    assert prompt.startswith("<|im_start|>system\nsys\n\n# Tools")
    assert 'Looking.\n<tool_call>\n{"name": "Read", "arguments": {"path": "a"}}\n</tool_call><|im_end|>' in prompt
    assert "<tool_response>\nError: nope\n</tool_response>" in prompt
    assert prompt.endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n")


def test_parse_splits_text_from_tool_calls():
    raw = 'Checking.\n<tool_call>\n{"name": "Read", "arguments": {"path": "cart.py"}}\n</tool_call>'
    assert local.parse(raw, ids()) == [{"type": "text", "text": "Checking."},
                                       {"type": "tool_use", "id": "t1", "name": "Read", "input": {"path": "cart.py"}}]


def test_a_broken_tool_call_stays_visible_as_text():
    content = local.parse("<tool_call>{not json</tool_call>", ids())
    assert content == [{"type": "text", "text": "<tool_call>{not json</tool_call>"}]


def test_a_half_written_tag_is_held_back_from_the_screen():
    assert local.visible_length("Reading it now. <tool") == len("Reading it now. ")
    assert local.visible_length("all good") == len("all good")
    assert local.visible_length("x <tool_call>{...") == 2
