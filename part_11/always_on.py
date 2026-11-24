"""11.1–11.5 — An always-on agent: a trigger wakes it, a lock keeps it to one run at a time, a policy answers
for you, a budget caps it, and a report says what happened.

python part_11/always_on.py --once TASK                one run, then exit: crontab, launchd, systemd or CI does the timing
python part_11/always_on.py --every 10m TASK           stay running: a run, a sleep, a run
python part_11/always_on.py --cron "0 9 * * 1-5" TASK  stay running on a cron schedule
python part_11/always_on.py --watch inbox              a run for every file dropped into inbox/
Each --allow RULE is something it may do with nobody watching, e.g. --allow "Bash(python3 test_cart.py)".
"""
import argparse
import asyncio
import datetime as dt
import fcntl
import itertools
import json
import os
import pathlib
import re
import secrets
import shlex
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import cron
import hooks
import providers
from agent import Agent
from common import Ask, Notice, ToolCall, ToolResult, pick_model, sandbox

LOCK, RUNS = ".agent/always_on.lock", ".agent/runs"
INBOX_NAME, INBOX_LIMIT = re.compile(r"^[A-Za-z0-9][\w.-]{0,79}$"), 20_000
REQUEST = "Request from inbox/{name} (its author is not watching):\n\n{text}"
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class Busy(Exception):
    pass


class Lock:
    """One run per folder at a time. The OS drops a flock when its process dies, so a crash never leaves it held."""

    def __init__(self, root):
        self.path = root / LOCK

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(self.path, "a+")
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.file.close()
            raise Busy(f"the previous run (pid {self.path.read_text().strip() or '?'}) still holds {LOCK}") from None
        self.file.truncate(0)
        self.file.write(str(os.getpid()))
        self.file.flush()
        return self

    def __exit__(self, *_):
        fcntl.flock(self.file, fcntl.LOCK_UN)
        self.file.close()


def interval(text: str) -> float:
    match = re.fullmatch(r"(\d{1,6})([smhd])", text.strip())
    if not match or int(match[1]) == 0:
        raise argparse.ArgumentTypeError(f"{text!r}: use a number and a unit, like 30s, 10m, 2h or 1d")
    return int(match[1]) * UNITS[match[2]]


def label(call) -> str:
    subject = call.input.get("command") or call.input.get("path") or json.dumps(call.input)
    return f"{call.name}({str(subject)[:70]})"


async def attempt(root, model, task, allow, budget, until) -> dict:
    """One unattended agent turn. Nothing here can wait for a person."""
    agent = Agent(model, root)
    agent.gate.mode = "dont-ask"
    agent.gate.rules["allow"] = [*agent.gate.rules["allow"], *allow]
    agent.budget = budget
    if until:
        agent.stop_hook, agent.max_turns = hooks.until(root, until, task, 3), 100
    events, error = [], None
    try:
        async for event in agent.turn(task):
            events.append(event)
            if isinstance(event, Ask):
                event.answer.set_result("no")
    except Exception as exc:  # an API that stays down still gets a report, not a silent dead process
        error = f"{type(exc).__name__}: {exc}"
    calls = {e.id: e for e in events if isinstance(e, ToolCall)}
    denied = [label(calls[e.id]) for e in events
              if isinstance(e, ToolResult) and e.is_error and e.output.startswith("Not run:") and e.id in calls]
    notices = [e.text for e in events if isinstance(e, Notice)]
    passed = None
    if until:
        try:
            passed = subprocess.run(until, shell=True, cwd=root, capture_output=True, timeout=600).returncode == 0
        except subprocess.TimeoutExpired:
            passed = False
    cut_short = any(n.startswith(("budget of", "stopped after")) for n in notices)
    status = "error" if error else "needs you" if denied or cut_short or passed is False else "done"
    last = next((m for m in reversed(agent.messages) if m["role"] == "assistant"), {"content": []})
    said = " ".join(b.get("text", "") for b in last["content"] if isinstance(b, dict)).strip()
    return {"status": status, "said": said, "tool_calls": len(calls), "denied": denied,
            "tokens": sum(agent.tokens), "check": until, "check_passed": passed, "notices": notices, "error": error}


def write_report(root, report) -> pathlib.Path:
    """A Markdown report per run, written atomically, and one JSON line per run in runs/index.jsonl."""
    runs = root / RUNS
    runs.mkdir(parents=True, exist_ok=True)
    path = runs / f"{report['started'][:19].replace(':', '')}-{secrets.token_hex(2)}.md"
    lines = [f"# {report['status']} · {report['started']}", "", f"- trigger: {report['source']}",
             f"- task: {report['task']}"]
    if report["status"] == "skipped":
        lines.append(f"- why: {report['why']}")
    else:
        lines += [f"- tool calls: {report['tool_calls']} · tokens: {report['tokens']:,}",
                  f"- denied: {', '.join(report['denied']) or 'nothing'}"]
        if report["check"]:
            lines.append(f"- check `{report['check']}`: {'passed' if report['check_passed'] else 'FAILED'}")
        lines += [f"- {n}" for n in report["notices"]] + [f"- error: {report['error']}"] * bool(report["error"])
        lines += ["", "## Last thing the agent said", "", report["said"] or "(nothing)"]
    with tempfile.NamedTemporaryFile("w", dir=runs, delete=False) as tmp:
        tmp.write("\n".join(lines) + "\n")
    os.replace(tmp.name, path)
    with open(runs / "index.jsonl", "a") as index:
        index.write(json.dumps({**report, "report": path.name}) + "\n")
    return path


def summary(report) -> str:
    if report["status"] == "skipped":
        return f"skipped: {report['why']}"
    denied = f" · denied {len(report['denied'])}: {report['denied'][0]}" if report["denied"] else ""
    return f"{report['status']} · {report['tool_calls']} tool calls · {report['tokens']:,} tokens{denied}"


async def run_once(root, model, task, allow=(), budget=50_000, until=None, notify=None, source="schedule") -> dict:
    """Take the lock or skip, run, write the report, tell someone. Returns the report."""
    started = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        with Lock(root):
            report = await attempt(root, model, task, allow, budget, until)
    except Busy as busy:
        report = {"status": "skipped", "why": str(busy)}
    report |= {"started": started, "source": source, "task": task[:200]}
    report["report"] = str(write_report(root, report).relative_to(root))
    if notify:
        try:
            subprocess.run(shlex.split(notify), input=f"{summary(report)}\n{task[:200]}\n", text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"notify failed: {exc}", file=sys.stderr)
    return report


async def every(seconds, run, runs=None):
    for n in itertools.count(1):
        await run()
        if runs and n >= runs:
            return
        print(f"next run in {seconds:g}s")
        await asyncio.sleep(seconds)


async def on_schedule(expr, run, runs=None, now=dt.datetime.now):
    # The next slot is worked out after each run, so an overrun or a sleeping laptop skips missed slots
    # instead of firing them all at once.
    for n in itertools.count(1):
        when = cron.next_run(expr, now())
        print(f"next run {when:%a %d %b %H:%M}")
        await asyncio.sleep(max(0.0, (when - now()).total_seconds()))
        await run()
        if runs and n >= runs:
            return


def arrivals(inbox: pathlib.Path) -> list[pathlib.Path]:
    """New requests: small regular files with plain names. Symlinks, folders and dotfiles are left alone."""
    return sorted(p for p in inbox.iterdir() if INBOX_NAME.match(p.name) and not p.is_symlink()
                  and p.is_file() and p.stat().st_size <= INBOX_LIMIT)


async def watch(inbox, run, runs=None, poll=2.0):
    """A run per file. The file moves to done/ after its run, so a crash means it runs again, never zero times."""
    done = inbox / "done"
    done.mkdir(parents=True, exist_ok=True)
    handled = 0
    print(f"watching {inbox} for new files")
    while True:
        for path in arrivals(inbox):
            await run(REQUEST.format(name=path.name, text=path.read_text(errors="replace")), f"inbox/{path.name}")
            os.replace(path, done / path.name)
            handled += 1
            if runs and handled >= runs:
                return
        await asyncio.sleep(poll)


def main():
    parser = argparse.ArgumentParser(description="An always-on agent: --once for cron, or --every, --cron, --watch.")
    trigger = parser.add_mutually_exclusive_group(required=True)
    trigger.add_argument("--once", action="store_true", help="one run, then exit (crontab, launchd, systemd, CI)")
    trigger.add_argument("--every", type=interval, metavar="10m", help="a run now, then one every interval")
    trigger.add_argument("--cron", metavar="EXPR", help='a cron schedule, e.g. "*/15 * * * *" or @daily')
    trigger.add_argument("--watch", type=pathlib.Path, metavar="DIR", help="a run for every new file in DIR")
    parser.add_argument("task", nargs="?", help="what every run should do (with --watch, each file is the task)")
    parser.add_argument("--cwd", type=pathlib.Path, help="work in this directory instead of sandbox/")
    parser.add_argument("--reset", action="store_true", help="restore sandbox/ from workspace/ first")
    parser.add_argument("--allow", action="append", default=[], metavar="RULE", help="allowed with nobody watching")
    parser.add_argument("--budget", type=int, default=50_000, help="tokens one run may spend (default 50,000)")
    parser.add_argument("--until", metavar="CMD", help="a stop hook, and the check the report records")
    parser.add_argument("--notify", metavar="CMD", help="run CMD after every run, with the summary on stdin")
    parser.add_argument("--runs", type=int, help="stop after this many runs")
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)
    if not args.watch and not args.task:
        parser.error("say what each run should do, e.g. \"fix the failing test\"")
    if args.cron:
        try:
            cron.parse(args.cron)
        except ValueError as exc:
            parser.error(str(exc))

    root = args.cwd.resolve() if args.cwd else sandbox(reset=args.reset)
    spec, reason = pick_model()
    print(f"always-on · {spec} · {reason} · {root}")
    print(f"allowed with nobody watching: {', '.join(args.allow) or 'nothing beyond read-only tools'}")

    async def run(task=args.task, source="schedule"):
        print(f"━━ {dt.datetime.now():%H:%M:%S} · {source} · {task.splitlines()[0][:60]}")
        report = await run_once(root, providers.make(spec), task, args.allow, args.budget, args.until,
                                args.notify, source)
        print(f"━━ {summary(report)} · {report['report']}")

    if args.once:
        job = run()
    elif args.every:
        job = every(args.every, run, args.runs)
    elif args.cron:
        job = on_schedule(args.cron, run, args.runs)
    else:
        job = watch(args.watch.resolve(), run, args.runs)
    try:
        asyncio.run(job)
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
