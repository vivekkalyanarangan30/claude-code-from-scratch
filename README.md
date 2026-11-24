# Claude Code from Scratch — Harness Engineering by Hand (Python)

The model is a stateless function: messages in, one message out. It cannot read a file, run a
command, remember the last turn or stop itself. Everything that makes it an agent is the harness —
a `while` loop and the system design that keeps that loop safe, durable, interruptible and cheap.
You don't need to train a model to build one. You need to be a good software engineer.

> "A decent model with a great harness beats a great model with a bad harness."
> — Addy Osmani, [Agent Harness Engineering](https://addyosmani.com/blog/agent-harness-engineering/)

Every lesson answers one question: which model failure does this piece of the harness fix?

It ends with an always-on agent: the same loop run with nobody watching, on a cron schedule, as a
background process or triggered by a file, with a permission policy that never waits for a person, a
lock against overlapping runs, a token budget and a report for every run.

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt   # httpx, prompt_toolkit, pytest
ollama pull qwen3:1.7b && ollama create cc-qwen3 -f Modelfile   # optional, 1.4 GB, needs a recent Ollama

.venv/bin/python part_1/main.py                                  # cc-qwen3 if Ollama has it, else the fake
MODEL=fake .venv/bin/python part_1/main.py                       # force the scripted model
MODEL=anthropic:claude-sonnet-5-5 .venv/bin/python part_1/main.py
```

Then type `fix the failing test`. The banner says which model you got and why. `--reset` restores
`sandbox/` from `workspace/`; `--cwd DIR` points it at a real project.

The course is taught on **qwen3:1.7b through Ollama**: free, local, small enough for any laptop.
`Modelfile` turns it into `cc-qwen3`: a 16k context window (Ollama's default silently drops the
start of long prompts), temperature 0, top_k 1 and a fixed seed. `local.py` talks to it, and like
`tui.py` and `fake.py` it is a stand-in, not a lesson: Ollama's own Messages endpoint drops qwen3's
tool calls when thinking is off, and with thinking on a 1.7B model can think for minutes. So
`local.py` renders qwen3's chat format itself, lets it think for at most 256 tokens a step
(`THINK_BUDGET`), and parses its tool calls. It also empties Ollama's prompt cache when a
conversation starts, because a reused cache changes the arithmetic enough to change the path.

The result replays: the same prompt in the same part gives the same transcript, run after run, on
this machine. [RUNBOOK.md](RUNBOOK.md) has the exact prompts and keystrokes for every part, each one
replayed. [COACH_SCRIPT.pdf](COACH_SCRIPT.pdf) is the same course as a teaching script: what to run, what
appears, what to say and which code made the difference (source: `COACH_SCRIPT.html`). Another machine,
Ollama version or edit to a prompt can take a different path, so rehearse.

With Part 1's harness qwen fails: it runs `pytest`, which isn't installed, tries `pip`, which the
harness disables, then reads a file that doesn't exist and the turn crashes. With project context
(Part 7) it runs the right test command and explains the right fix, but tries to change the test
instead, and a deny rule stops it. With a stop hook (Part 8) it is sent back with the failing output
and writes the correct fix. The tests in `workspace/` include a 50%-off case, so a fix that only
satisfies 10%-off-200 fails.

No Ollama? Nothing breaks. `MODEL=fake` is this course's `TINY=1`: a scripted model that speaks the
Messages API and rejects exactly what the real API rejects, so every lesson runs with no key, no
network and the same output every time. Tests and `verify.sh` always use it. No SDK, no framework —
raw HTTP, the way `llm_from_scratch` used no Trainer API.

`tui.py` is the screen, shared by every part and not part of the course: it renders events and
forwards keys. Each part is a complete agent behind it — `diff -r part_3 part_4` **is** lesson 4.
Each part's `break_it.py` breaks the previous part; then we fix it. `./verify.sh` runs everything.

## Part 0 — The screen and the function
- **0.1** `main.py`: the screen with a puppet behind it. It looks like an agent; there is no model. Five events — `Text`, `ToolCall`, `Ask`, `ToolResult`, `Notice` — are the whole contract between screen and agent
- **0.2** `one_call.py`: the model is a function. Ask its name twice; it only remembers if you re-send the history
- **0.3** `tool_call.py`: a tool call on the wire. The model asks and stops; nothing ran
- **0.4** `fake.py`: the scripted model, and why it must be exactly as strict as the real API

## Part 1 — The loop
- **1.1** `agent.py`: call → run each `tool_use` → append `tool_result` → call again → stop when it stops asking
- **1.2** `tools.py`: `Read`, `Write`, `Bash` — a JSON schema the model reads plus a function you run. Writes and commands ask first
- **1.3** The transcript is the only state: `/clear` it and the agent forgets everything
- **1.4** `break_it.py`: a model that never says it's done. `MAX_TURNS`
- Try: `fix the failing test`. Then Esc while a command runs, and type anything. The 400 you get is Part 4

## Part 2 — Tool design is API design for an LLM reader
`break_it.py`: Part 1 reads a file that doesn't exist; the exception kills the turn and every later request is a 400.
- **2.1** Schemas are prompts: every description says when to use the tool and how to recover
- **2.2** Errors are results, not exceptions: `ToolError` messages tell the model what to do instead
- **2.3** `Edit`: replace whole lines, matched exactly once; refuse a match in the middle of a line, and show the lines as they now read
- **2.4** Read before edit: refuse to change a file the model hasn't read, or one that changed since
- **2.5** Output budgets: numbered lines, `offset`, head and tail kept, "N characters omitted"
- **2.6** `Glob` and `Grep` as bounded, read-only tools, not `find` through Bash
- **2.7** `tools.py` is a registry: schema, handler, `read_only` and `concurrency_safe` in one place
- Try: `what's in config.py?`, then `ok, try settings.py`

## Part 3 — Streaming
`break_it.py`: the first words of a long answer take 2.0 s in Part 2 and 0.3 s streamed. Same model, same total time.
- **3.1** `sse.py`: server-sent events parsed by hand, line by line
- **3.2** `Reply`: tool input arrives as JSON fragments and is unusable until its block closes
- **3.3** The screen already drew pieces; Part 2 just never sent any
- **3.4** `-p PROMPT --json`: headless mode is a second subscriber to the same events
- **3.5** Thinking streams first, before any answer; the screen shows it in grey
- Try: `explain the bug in cart.py`

## Part 4 — Interruptions
`break_it.py`: Esc during `sleep 8` in Part 3 takes 7 s to land, leaves `sleep` running, and the next message is a 400.
- **4.1** Killing the shell isn't enough: its child keeps the output pipe open, so cleanup waits for it
- **4.2** The invariant: every `tool_use` gets a `tool_result`. On cancel, open calls get "interrupted by user"
- **4.3** A half-streamed answer is kept, so the model knows what it already said
- **4.4** Steering: type while it works; the agent reads it at the next tool boundary
- **4.5** Commands that never end: `timeout`, and `run_in_background` with a log file
- Try: `run something slow` then Esc; `count to five` then type `stop counting`; `start the dev server`

## Part 5 — Permissions
`break_it.py`: a poisoned README gets a key-stealing command proposed; a prefix rule would wave it through; a judge that reads tool output is talked into allowing it.
- **5.1** `permissions.py`: one gate, allow / deny / ask, before every side effect
- **5.2** Rules per tool, path and command (`Bash(git status:*)`, `Edit(src/**)`), saved by "don't ask again". `workspace/.agent/settings.json` denies edits to the tests, so a model can't pass them by rewriting them
- **5.3** Compound commands are split and every part checked; `$(...)`, backticks and redirections mean ask
- **5.4** Modes, cycled with shift+tab: plan (read-only), accept-edits, auto
- **5.5** `--inject` plants the poisoned README. The gate stops it; a system prompt can't
- **5.6** `judge.py`: auto mode asks a second model, showing it only the user's words and the action. Unsure or broken means ask
- **5.7** Defense in depth, on slides: every layer is a heuristic, and their holes are in different places. The next layer is an OS sandbox
- Try: `python part_5/main.py --inject`, then `set up the project`; shift+tab to plan mode, then `plan the fix`

## Part 6 — Persistence and durable execution
`break_it.py`: kill -9 in the middle of a deploy. Part 5 has nothing to resume. Part 6 resumes and the model checks instead of guessing.
- **6.1** `session.py`: the session is an append-only JSONL log; `--resume` replays it
- **6.2** Write-ahead: an intent record before every tool, a result record after
- **6.3** An intent with no result becomes "outcome unknown", never a silent re-run
- **6.4** Atomic writes (temp, fsync, rename), and a reader that drops a torn last line
- **6.5** `/rewind`: restore the files the last turn changed and drop it from the conversation. Commands aren't undone
- Try: `fix the failing test`, then `/rewind`; quit mid-command and start again with `--resume`

## Part 7 — Context management
`break_it.py`: a 12-batch audit in a 9,000-token window. Part 6 fails with "prompt is too long"; Part 7 clears, compacts and finishes. Then a timestamp in the system prompt throws the cache away.
- **7.1** A fuel gauge in the status bar; `/context` breaks it down
- **7.2** `context.py`: the system prompt is assembled once: environment, git, `AGENTS.md`
- **7.3** Cache marks on the system prompt and the newest message. Append-only is cheap; editing the start is not
- **7.4** Past half the window, old tool output is cleared first
- **7.5** Past 80%, compaction: a summary, the user's words and the todo list start a fresh transcript
- **7.6** `TodoWrite`: a tool with no side effect except working memory that survives compaction
- Try: `audit the logs`, `/context`, `/compact`

## Part 8 — Long-running agents: the Ralph loop
`break_it.py`: a three-feature job. After one feature the model says "All features are complete." Two tests still fail.
- **8.1** `ralph.sh`: the purest form, the same prompt and a fresh agent forever. The files remember
- **8.2** `project/`: `features.json`, `PROGRESS.md` and the git log are the hand-off between passes. The loop writes them from the test results; deny rules keep the agent out of them
- **8.3** `hooks.py`: a stop hook. When the model stops, run the tests; if they fail, send the task back (`-p ... --until CMD`)
- **8.4** `ralph.py`: a fresh agent per pass, given one failing feature, its check and a stop hook on that check; a git commit after every pass; done when every check passes. A failing check says what it got and what it expected
- Try: `MODEL=fake python part_8/ralph.py --reset`, then `git -C sandbox_ralph log --oneline`

## Part 9 — Reliability and cost
`break_it.py`: a 529 right after a command ran. Retrying the turn runs the command twice; retrying the request doesn't.
- **9.1** `retry.py`: backoff with full jitter on 429, 529, 5xx and dropped connections; never on a 400
- **9.2** Retry boundaries: only before the first byte of a reply, never around a turn
- **9.3** `--budget`: tokens spent stop the loop, the grown-up `MAX_TURNS`
- **9.4** `openai_compat.py`: OpenAI-style chat completions behind the same interface. Thinking blocks and `is_error` don't survive; tool arguments may not be JSON
- Try: `FAKE_FAULTS=529,529 MODEL=fake python part_9/main.py`, then `fix the failing test`; `MODEL=openai:cc-qwen3`

## Part 10 — Concurrency and subagents
`break_it.py`: finding one missing line in twelve logs leaves 22,064 tokens in Part 9's context and 1,597 in Part 10's. Then two agents edit one file.
- **10.1** Parallel tools: consecutive read-only calls run together, everything else alone, results in order
- **10.2** The `Agent` tool: this same loop with a fresh transcript and read-only tools, returning one summary
- **10.3** Two agents, one file: Part 2's read-before-edit check refuses the second edit
- **10.4** Esc cancels the parent and every subagent it started
- Try: `explore the project`

## Part 11 — Always-on agents: cron, schedules and triggers
Scheduled agents, background agents, agents that run 24/7: the same loop, started by a clock or an
event instead of a person. `break_it.py`: Part 10 on a schedule. With nobody to answer, headless mode
can only refuse every call or allow every call, the curl in a poisoned README included. And when the
09:00 run is still going at 09:05, two agents edit one file and the second still says "Fixed".
- **11.1** `always_on.py --once`: one unattended run, for a scheduler outside the agent. A `crontab`
  line, a launchd agent, a systemd timer or a scheduled CI job (GitHub Actions `on: schedule`) does the
  timing: `*/30 * * * * cd ~/proj && .venv/bin/python part_11/always_on.py --once "fix the failing test" --allow ...`
- **11.2** `cron.py`: cron syntax from scratch: five fields, `*/15`, ranges, lists, `@daily`, and the
  trap where day-of-month and day-of-week match if either does. `--cron EXPR` and `--every 10m` keep
  one process running as a daemon. The next slot is worked out after each run, so slots missed by an
  overrun or a sleeping laptop are skipped, not fired all at once
- **11.3** `dont-ask` mode: with nobody watching, every question becomes a no that the model reads as a
  result. `--allow RULE` lists what a run may do; deny rules still win; read-only tools still run
- **11.4** `Lock`: one run per folder, held with `flock`, which the OS releases if the process dies. A
  run that finds it held is skipped and says so
- **11.5** Event triggers: `--watch inbox/` runs once for each file dropped in. A webhook handler that
  writes a file there is all a webhook trigger needs. Each file comes from someone who isn't
  watching; the allow list, not the prompt, is what stops it. A file moves to `inbox/done/` after its
  run, so a crash means it runs again, never zero times
- **11.6** A report for every run: `.agent/runs/<time>.md` and a line in `runs/index.jsonl` with the
  status (`done`, `needs you`, `skipped`, `error`), what was denied, the tokens spent and the check's
  result. `--budget` caps tokens per run, `--until CMD` is Part 8's stop hook, and `--notify CMD` gets
  the summary on stdin (`osascript`, `mail`, a script that posts to chat)
- Try: `MODEL=fake python part_11/always_on.py --once --reset "fix the failing test" --allow "Bash(python3 test_cart.py)" --allow "Edit(cart.py)" --until "python3 test_cart.py"`, then `cat sandbox/.agent/runs/*.md`

## extras/
Depth, once the parts make sense. Planned, not built yet: each will run standalone.

| file | shows |
|---|---|
| `lsp_diagnostics.py` | feed type errors back after every edit |
| `repo_map.py` | a ranked map of symbols so the model knows where to look |
| `apply_patch.py` | a multi-file patch format as an alternative to exact-string edit |
| `worktrees.py` | parallel agents, one git worktree each |
| `docker_sandbox.py` | the Bash tool runs in a container with no network, no keys and only the project mounted; the agent keeps both |
| `client_server.py` | the agent core as a server, the REPL as a client |
| `hooks.py` | the loop as an event bus: external processes, JSON on stdin, an exit code that blocks |
| `mcp_client.py` | MCP from scratch: JSON-RPC over stdio, `initialize` → `tools/list` → `tools/call` |
| `skills.py` | names in context, bodies loaded on demand |
| `state_machine.py` | the same agent as an explicit graph of deterministic nodes around model calls — loops vs graphs |

## How the shipped ones choose

Same problems, different answers. Each row gets a public source before recording.

| problem | Claude Code | Codex CLI | Aider | opencode |
|---|---|---|---|---|
| editing | exact-string replace, read required first | `apply_patch` format | several edit formats | — |
| safety | permission rules + modes + hooks | OS sandbox | git commit per change | — |
| finding code | grep / glob tools, subagents | shell | tree-sitter repo map | LSP |
| shape | CLI, Ink TUI | Rust CLI | Python CLI | client / server |
| unattended | `-p` headless, `dontAsk` mode, `/loop`, scheduled routines, GitHub Actions | `codex exec` | `--message` with `--yes-always` | `opencode run` |
