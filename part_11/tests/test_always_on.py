import asyncio
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]
import always_on
import cron
import tools
from fake import FakeModel, call, say
from permissions import NOBODY_HERE, Gate
from scripts import EXFILTRATE, SCRIPTS

ALLOW = ["Bash(python3 test_cart.py)", "Edit(cart.py)"]
THU_0903 = dt.datetime(2026, 11, 12, 9, 3)


@pytest.fixture
def root(tmp_path):
    target = tmp_path / "project"
    shutil.copytree(HERE.parent / "workspace", target, ignore=shutil.ignore_patterns("__pycache__"))
    return target


def fake():
    return FakeModel(SCRIPTS, delay=0)


def run(root, task="fix the failing test", **kwargs):
    return asyncio.run(asyncio.wait_for(always_on.run_once(root, fake(), task, **kwargs), timeout=30))


@pytest.mark.parametrize("expr, expected", [
    ("*/15 * * * *", dt.datetime(2026, 11, 12, 9, 15)),
    ("0 9 * * 1-5", dt.datetime(2026, 11, 13, 9, 0)),
    ("0 9 * * 0", dt.datetime(2026, 11, 15, 9, 0)),
    ("@daily", dt.datetime(2026, 11, 13, 0, 0)),
    ("0 0 1 * *", dt.datetime(2026, 12, 1, 0, 0)),
    ("0 0 29 2 *", dt.datetime(2028, 2, 29, 0, 0)),
    ("0 0 13 * 5", dt.datetime(2026, 11, 13, 0, 0)),
    ("5,35 9-10 * * *", dt.datetime(2026, 11, 12, 9, 5)),
])
def test_cron_next_run(expr, expected):
    assert cron.next_run(expr, THU_0903) == expected


def test_both_day_fields_restricted_means_either_one():
    assert cron.next_run("0 0 1 * 1", THU_0903) == dt.datetime(2026, 11, 16, 0, 0)


@pytest.mark.parametrize("bad", ["* * * *", "60 * * * *", "*/0 * * * *", "a * * * *", "0 0 31 2 *"])
def test_bad_cron_is_refused(bad):
    with pytest.raises(ValueError):
        cron.next_run(bad, THU_0903)


def test_interval():
    assert [always_on.interval(t) for t in ("30s", "10m", "2h", "1d")] == [30, 600, 7200, 86400]
    for bad in ("0m", "10", "m", "-5m", "1.5h"):
        with pytest.raises(Exception):
            always_on.interval(bad)


def test_dont_ask_turns_every_question_into_a_no(root):
    gate = Gate(root)
    gate.mode, gate.rules["allow"] = "dont-ask", ALLOW
    bash, edit, read = tools.TOOLS["Bash"], tools.TOOLS["Edit"], tools.TOOLS["Read"]
    assert gate.check(bash, {"command": "python3 test_cart.py"})[0] == "allow"
    assert gate.check(edit, {"path": "cart.py"})[0] == "allow"
    assert gate.check(read, {"path": "cart.py"})[0] == "allow"
    assert gate.check(bash, {"command": EXFILTRATE}) == ("deny", NOBODY_HERE)
    assert gate.check(bash, {"command": "python3 test_cart.py $(curl x)"}) == ("deny", NOBODY_HERE)
    assert gate.check(edit, {"path": "test_cart.py"})[0] == "deny"


def test_a_scheduled_run_fixes_it_and_writes_a_report(root):
    report = run(root, allow=ALLOW, until="python3 test_cart.py")
    assert report["status"] == "done" and report["check_passed"] and not report["denied"]
    assert "Fixed" in report["said"]
    text = (root / report["report"]).read_text()
    assert text.startswith("# done") and "check `python3 test_cart.py`: passed" in text
    assert json.loads((root / ".agent/runs/index.jsonl").read_text())["status"] == "done"


def test_without_allow_rules_nothing_waits_and_nothing_changes(root):
    before = (root / "cart.py").read_text()
    report = run(root)
    assert report["status"] == "needs you" and report["denied"]
    assert (root / "cart.py").read_text() == before
    assert "allow" not in json.loads((root / ".agent/settings.json").read_text())["permissions"]["allow"]


def test_a_poisoned_request_is_refused_not_run(root):
    shutil.copytree(HERE / "inject", root, dirs_exist_ok=True)
    report = run(root, always_on.REQUEST.format(name="setup.txt", text="set up the project"), allow=ALLOW)
    assert report["status"] == "needs you"
    assert report["denied"] == [f"Bash({EXFILTRATE[:70]})"]


def test_an_overlapping_run_is_skipped(root):
    async def twice():
        return await asyncio.gather(*(always_on.run_once(root, fake(), "fix the failing test", ALLOW)
                                      for _ in range(2)))
    first, second = asyncio.run(twice())
    assert first["status"] == "done" and second["status"] == "skipped"
    assert "still holds" in second["why"]
    assert len((root / ".agent/runs/index.jsonl").read_text().splitlines()) == 2


def test_the_lock_is_free_after_a_run_and_after_a_crash(root):
    with pytest.raises(RuntimeError):
        with always_on.Lock(root):
            raise RuntimeError("crash")
    assert run(root, allow=ALLOW)["status"] == "done"
    assert run(root, allow=ALLOW)["status"] == "done"


def test_the_budget_stops_a_run_that_would_go_on_forever(root):
    looping = FakeModel({"forever": ("", [[call("Bash", command="echo still going")]] * 1000)}, delay=0)
    report = asyncio.run(always_on.run_once(root, looping, "keep going forever", ["Bash(echo:*)"], budget=5_000))
    assert report["status"] == "needs you" and any(n.startswith("budget of") for n in report["notices"])


def test_an_api_that_stays_down_still_gets_a_report(root):
    down = FakeModel(SCRIPTS, delay=0, faults=[529] * 10)
    report = asyncio.run(always_on.run_once(root, down, "fix the failing test", ALLOW))
    assert report["status"] == "error" and "529" in report["error"]
    assert (root / report["report"]).exists()


def test_notify_gets_the_summary_on_stdin(root, tmp_path):
    out = tmp_path / "told.txt"
    script = tmp_path / "tell.sh"
    script.write_text(f"#!/bin/sh\ncat > '{out}'\n")
    script.chmod(0o755)
    run(root, allow=ALLOW, notify=str(script))
    assert out.read_text().startswith("done · ")


def test_watch_runs_each_new_file_once_and_ignores_the_rest(root, tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "fix.txt").write_text("fix the failing test")
    (inbox / "big.txt").write_text("x" * (always_on.INBOX_LIMIT + 1))
    (inbox / ".hidden").write_text("fix the failing test")
    (inbox / "link.txt").symlink_to(inbox / "fix.txt")
    seen = []

    async def record(task, source):
        seen.append(source)
        await always_on.run_once(root, fake(), task, ALLOW, source=source)

    asyncio.run(asyncio.wait_for(always_on.watch(inbox, record, runs=1, poll=0), timeout=30))
    assert seen == ["inbox/fix.txt"]
    assert (inbox / "done" / "fix.txt").exists() and not (inbox / "fix.txt").exists()
    assert (inbox / "big.txt").exists() and (inbox / ".hidden").exists()


def test_a_run_that_overruns_skips_the_missed_slot(monkeypatch):
    slept, clock = [], [THU_0903]

    async def sleep(seconds):
        slept.append(seconds)
        clock[0] += dt.timedelta(seconds=seconds)

    async def job():
        clock[0] += dt.timedelta(minutes=20)

    monkeypatch.setattr(always_on.asyncio, "sleep", sleep)
    asyncio.run(always_on.on_schedule("*/15 * * * *", job, runs=2, now=lambda: clock[0]))
    assert slept == [12 * 60, 10 * 60]  # 09:15 runs until 09:35, so 09:30 is skipped and 09:45 is next
