"""The screen. Not part of the course: it renders events and forwards keys, and every part reuses it.

Contract with an agent:
  agent.turn(prompt)   async generator of common.* events          (required)
  agent.status()       text for the bottom bar                       (optional)
  agent.steer(text)    take a message typed mid-turn                 (optional, else it queues)
  agent.commands       {"name": fn(arg) -> str | None} slash commands (optional)
  agent.cycle_mode()   shift+tab: switch permission mode, returns its name (optional)
"""
import asyncio
import pathlib
import time

from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.application import get_app_or_none
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style

from common import Ask, Notice, Text, Thinking, ToolCall, ToolResult

STYLE = Style.from_dict({
    "dot": "#d97757", "tool": "bold", "dim": "#888888", "err": "#e06c75", "ok": "#98c379",
    "user": "#888888 italic", "think": "#6e6e6e italic", "box": "#d97757", "key": "bold #d97757", "bottom-toolbar": "noreverse #888888",
})
SPINNER = "·✢✳✶✻✽"
PREVIEW_LINES = 3


def out(*parts):
    # Explicit \r: some terminals are left without newline translation while a prompt is active.
    print_formatted_text(FormattedText([("", "\r"), *parts]), style=STYLE, end="\r\n")


def label(name, args):
    first = next((v for v in args.values() if isinstance(v, str)), "")
    first = first.splitlines()[0] if first else ""
    return f"{name}({first[:70] + '…' if len(first) > 70 else first})"


class TUI:
    def __init__(self, agent, title, cwd, model=None):
        self.agent, self.title, self.cwd, self.model = agent, title, cwd, model
        self.task, self.ask, self.queue = None, None, []
        self.started, self.buffer, self.block_open, self.thought = 0.0, "", False, ""
        self.session = PromptSession(key_bindings=self.keys(), bottom_toolbar=self.toolbar,
                                     style=STYLE, refresh_interval=0.12)

    def keys(self):
        kb = KeyBindings()
        asking = Condition(lambda: self.ask is not None)

        for key, choice in (("1", "yes"), ("2", "always"), ("3", "no")):
            @kb.add(key, filter=asking, eager=True)
            def _(event, choice=choice):
                self.answer(choice)

        @kb.add("escape", eager=True)
        def _(event):
            if self.ask:
                self.answer("no")
            elif self.task:
                self.task.cancel()

        @kb.add("s-tab")
        def _(event):
            if hasattr(self.agent, "cycle_mode"):
                out(("class:dim", f"  ⎿  {self.agent.cycle_mode()} mode"))

        @kb.add("c-c")
        def _(event):
            if self.task:
                self.task.cancel()
            elif event.app.current_buffer.text:
                event.app.current_buffer.reset()
            else:
                event.app.exit(exception=EOFError)
        return kb

    def toolbar(self):
        if self.ask:
            middle = [("class:key", "2"), ("", " always  ")] if self.ask.always else []
            last = "3" if self.ask.always else "2"
            return FormattedText([("class:key", " 1"), ("", " yes  "), *middle, ("class:key", last), ("", " no (esc)")])
        if self.task:
            spin = SPINNER[int(time.time() * 8) % len(SPINNER)]
            queued = f" · {len(self.queue)} queued" if self.queue else ""
            return FormattedText([("class:dot", f" {spin} "),
                                  ("", f"Working… ({time.time() - self.started:.0f}s · esc to interrupt{queued})")])
        status = getattr(self.agent, "status", lambda: "")()
        hint = "shift+tab mode · /help" if hasattr(self.agent, "cycle_mode") else "/help for commands"
        return FormattedText([("class:dim", f" {status}   {hint}")])

    def banner(self):
        cwd = str(self.cwd).replace(str(pathlib.Path.home()), "~")
        rows = [[("class:dot", "✻ "), ("bold", self.title)], [("class:dim", f"  cwd: {cwd}")]]
        if self.model:
            rows.append([("class:dim", f"  model: {self.model}")])
        width = max(sum(len(t) for _, t in row) for row in rows) + 2
        out(("class:box", "╭" + "─" * width + "╮"))
        for row in rows:
            pad = width - 1 - sum(len(t) for _, t in row)
            out(("class:box", "│ "), *row, ("", " " * pad), ("class:box", "│"))
        out(("class:box", "╰" + "─" * width + "╯"))
        out(("", ""))

    # ---- rendering -------------------------------------------------------------------------
    def render(self, event):
        if isinstance(event, Thinking):
            self.thought += event.text
            *lines, self.thought = self.thought.split("\n")
            for line in (l for l in lines if l.strip()):
                out(("class:think", f"  ✻ {line}"))
            return
        if self.thought.strip():
            out(("class:think", f"  ✻ {self.thought}"))
        self.thought = ""
        if isinstance(event, Text):
            self.buffer += event.text
            *lines, self.buffer = self.buffer.split("\n")
            for line in lines:
                self.text_line(line)
            return
        self.flush()
        if isinstance(event, ToolCall):
            out(("", ""))
            out(("class:dot", "⏺ "), ("class:tool", label(event.name, event.input)))
        elif isinstance(event, ToolResult):
            shown = (event.display or event.output).rstrip("\n").splitlines() or ["(no output)"]
            style = "class:err" if event.is_error else "class:dim"
            for i, line in enumerate(shown[:PREVIEW_LINES]):
                out(("class:dim", "  ⎿  " if i == 0 else "     "), (style, line[:200]))
            if len(shown) > PREVIEW_LINES:
                out(("class:dim", f"     … +{len(shown) - PREVIEW_LINES} lines"))
        elif isinstance(event, Ask):
            self.ask = event
            out(("class:box", "  ╭─ "), ("bold", event.question))
            out(("class:box", "  │ "), ("class:key", "1"), ("", " Yes"))
            if event.always:
                out(("class:box", "  │ "), ("class:key", "2"), ("", f" Yes, and don't ask again for {event.always}"))
            out(("class:box", "  ╰ "), ("class:key", "3" if event.always else "2"),
                ("", " No, and tell the agent what to do instead (esc)"))
        elif isinstance(event, Notice):
            out(("class:dim", f"  ⎿  {event.text}"))

    def text_line(self, line):
        if not self.block_open and not line.strip():
            return
        if not self.block_open:
            out(("", ""))
        out(("class:dot", "  " if self.block_open else "⏺ "), ("", line))
        self.block_open = True

    def flush(self):
        if self.buffer:
            self.text_line(self.buffer)
        self.buffer, self.block_open = "", False

    def answer(self, choice):
        if not self.ask or self.ask.answer.done():
            return
        if choice == "always" and not self.ask.always:
            choice = "no"
        self.ask.answer.set_result(choice)
        self.ask = None
        out(("class:dim", {"yes": "  ⎿  allowed", "always": "  ⎿  allowed, won't ask again",
                           "no": "  ⎿  denied"}[choice]))

    # ---- turns ----------------------------------------------------------------------------
    def start(self, prompt):
        self.started = time.time()
        self.task = asyncio.create_task(self.run_turn(prompt))

    async def run_turn(self, prompt):
        try:
            async for event in self.agent.turn(prompt):
                self.render(event)
        except asyncio.CancelledError:
            self.flush()
            out(("class:err", "  ⎿  Interrupted · what should the agent do instead?"))
        except Exception as exc:
            self.flush()
            out(("class:err", f"  ⎿  Error: {exc}"))
        finally:
            self.flush()
            self.task, self.ask = None, None
            out(("", ""))
            if self.queue:
                self.start(self.queue.pop(0))
            elif app := get_app_or_none():
                app.invalidate()

    def command(self, line):
        name, _, arg = line[1:].partition(" ")
        commands = {"help": self.help, **getattr(self.agent, "commands", {})}
        if name in ("exit", "quit"):
            raise EOFError
        if name not in commands:
            out(("class:err", f"  Unknown command /{name}. Try /help."))
            return
        result = commands[name](arg.strip())
        if result:
            out(("class:dim", f"  ⎿  {result}"))

    def help(self, _arg):
        names = ["/help", "/exit", *[f"/{n}" for n in getattr(self.agent, "commands", {})]]
        return "commands: " + "  ".join(names) + "\n     esc interrupts · enter while working queues a message"

    async def main(self):
        self.banner()
        with patch_stdout(raw=True):
            while True:
                try:
                    line = (await self.session.prompt_async(FormattedText([("class:dot", "> ")]))).strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not line:
                    continue
                try:
                    if line.startswith("/"):
                        self.command(line)
                    elif self.task and hasattr(self.agent, "steer"):
                        self.agent.steer(line)
                        out(("class:dim", "  ⎿  noted, the agent will see this at its next step"))
                    elif self.task:
                        self.queue.append(line)
                    else:
                        self.start(line)
                except EOFError:
                    break
            if self.task:
                self.task.cancel()


def run(agent, title, cwd, model=None):
    asyncio.run(TUI(agent, title, cwd, model).main())
