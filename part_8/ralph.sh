#!/usr/bin/env bash
# 8.1 — Ralph in its purest form: the same prompt, a fresh agent, forever. The files remember. Ctrl-C stops it.
# Run part_8/ralph.py --reset once first to create sandbox_ralph/.
cd "$(dirname "$0")/.."
while :; do
    .venv/bin/python part_8/main.py --cwd sandbox_ralph --mode accept-edits -p - < sandbox_ralph/PROMPT.md
done
