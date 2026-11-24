import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]
import agent as agent_module
import tools
from agent import Agent
from common import REPO, Notice, ToolCall, ToolResult, drive
from fake import FakeModel, validate
from scripts import SCRIPTS
from tools import Context, ToolError


@pytest.fixture
def root(tmp_path):
    shutil.copytree(REPO / "workspace", tmp_path / "ws")
    return tmp_path / "ws"


def run(ctx, name, **args):
    return asyncio.run(tools.run(ctx, name, args))


def test_fixes_the_bug_with_one_edit(root):
    events = asyncio.run(drive(Agent(FakeModel(SCRIPTS, delay=0), root), "fix the failing test"))
    assert [e.name for e in events if isinstance(e, ToolCall)] == ["Bash", "Read", "Edit", "Bash"]
    assert subprocess.run([sys.executable, "test_cart.py"], cwd=root).returncode == 0


def test_a_failing_tool_is_a_result_and_the_session_survives(root):
    agent = Agent(FakeModel(SCRIPTS, delay=0), root)
    events = asyncio.run(drive(agent, "what's in config.py?"))
    results = [e for e in events if isinstance(e, ToolResult)]
    assert results[0].is_error and "does not exist" in results[0].output
    asyncio.run(drive(agent, "ok, try settings.py"))
    validate(agent.messages + [{"role": "user", "content": "next"}])


def test_edit_needs_a_read_first(root):
    with pytest.raises(ToolError, match="Read cart.py before"):
        run(Context(root), "Edit", path="cart.py", old_string="discount", new_string="x")


def test_edit_refuses_a_file_that_changed_since_it_was_read(root):
    ctx = Context(root)
    run(ctx, "Read", path="cart.py")
    path = root / "cart.py"
    path.write_text(path.read_text() + "# someone else\n")
    os.utime(path, ns=(1, 1))
    with pytest.raises(ToolError, match="changed since"):
        run(ctx, "Edit", path="cart.py", old_string="subtotal - discount", new_string="x")


def test_edit_must_match_exactly_once(root):
    ctx = Context(root)
    run(ctx, "Read", path="cart.py")
    with pytest.raises(ToolError, match="only part of a line"):
        run(ctx, "Edit", path="cart.py", old_string="discount", new_string="off")
    with pytest.raises(ToolError, match="not found"):
        run(ctx, "Edit", path="cart.py", old_string="    3\treturn", new_string="x")
    (root / "twice.py").write_text("x = 1\nx = 1\n")
    run(ctx, "Read", path="twice.py")
    with pytest.raises(ToolError, match="matches 2 places"):
        run(ctx, "Edit", path="twice.py", old_string="x = 1", new_string="x = 2")
    edited = run(ctx, "Edit", path="cart.py", old_string="return subtotal - discount", new_string="return subtotal")
    assert edited.endswith("    4\t    return subtotal")


def test_write_creates_new_files_but_needs_a_read_to_overwrite(root):
    ctx = Context(root)
    assert "Wrote 1 line" in run(ctx, "Write", path="notes/todo.txt", content="hi\n")
    with pytest.raises(ToolError, match="Read cart.py"):
        run(ctx, "Write", path="cart.py", content="")


def test_read_numbers_lines_and_pages_long_files(root):
    (root / "long.txt").write_text("\n".join(f"line {i}" for i in range(1, 1001)))
    first = run(Context(root), "Read", path="long.txt")
    assert first.splitlines()[0] == "    1\tline 1"
    assert "600 more lines. Read again with offset=401" in first
    assert run(Context(root), "Read", path="long.txt", offset=999).splitlines()[0] == "  999\tline 999"


def test_long_output_keeps_head_and_tail(root):
    out = run(Context(root), "Bash", command="seq 1 20000")
    assert out.startswith("1\n") and out.rstrip().endswith("20000") and "characters omitted" in out


def test_failing_command_is_an_error_with_its_exit_code(root):
    with pytest.raises(ToolError, match=r"exit code 3"):
        run(Context(root), "Bash", command="echo oops; exit 3")


def test_search_tools_stay_inside_the_project(root):
    assert run(Context(root), "Glob", pattern="**/*.py").splitlines() == ["cart.py", "test_cart.py"]
    assert "cart.py:4:" in run(Context(root), "Grep", pattern=r"subtotal -")
    with pytest.raises(ToolError, match="outside the project"):
        run(Context(root), "Read", path="../../etc/passwd")


def test_bad_calls_say_how_to_fix_them(root):
    with pytest.raises(ToolError, match="no tool called Delete"):
        run(Context(root), "Delete", path="x")
    with pytest.raises(ToolError, match="missing path; no such parameter file"):
        run(Context(root), "Read", file="cart.py")


def test_denied_is_an_error_result_and_changes_nothing(root):
    events = asyncio.run(drive(Agent(FakeModel(SCRIPTS, delay=0), root), "fix the failing test", approve=False))
    assert [e.is_error for e in events if isinstance(e, ToolResult)] == [True]
    assert "subtotal - discount" in (root / "cart.py").read_text()


def test_max_turns(root, monkeypatch):
    monkeypatch.setattr(agent_module, "MAX_TURNS", 5)
    events = asyncio.run(drive(Agent(FakeModel(SCRIPTS, delay=0), root), "keep going forever"))
    assert isinstance(events[-1], Notice)
