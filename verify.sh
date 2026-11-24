#!/usr/bin/env bash
# Runs every part's tests and non-interactive lessons on the fake model. Exit 0 means the course works.
set -u
cd "$(dirname "$0")"
PY=${PY:-.venv/bin/python}
export MODEL=fake FAKE_DELAY=0
status=0
check() {
    if output=$("$@" 2>&1); then printf 'PASS %s\n' "$*"; else printf 'FAIL %s\n%s\n' "$*" "$output"; status=1; fi
}
check "$PY" -m pytest -q -p no:cacheprovider tests
check "$PY" part_0/one_call.py
check "$PY" part_0/tool_call.py
for n in $(seq 1 11); do
    check "$PY" -m pytest -q -p no:cacheprovider "part_$n/tests"
    check "$PY" "part_$n/break_it.py"
done
check "$PY" part_8/ralph.py --reset
exit "$status"
