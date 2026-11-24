import sys

from cart import receipt, total

CHECKS = {
    "discount": ("total([(200.0, 1)], discount=10)", lambda: total([(200.0, 1)], discount=10), 180.0),
    "rounding": ("total([(0.1, 3)])", lambda: total([(0.1, 3)]), 0.3),
    "empty": ("receipt([])", lambda: receipt([]), "empty cart"),
}


def failure(call, run, want):
    """None if the check passes, else what went wrong, worded for whoever has to fix it."""
    try:
        got = run()
    except Exception as exc:
        return f"{call} raised {type(exc).__name__}: {exc}"
    return None if got == want else f"{call} returned {got!r}, expected {want!r}"


names = [name for name in sys.argv[1:] if name in CHECKS] or list(CHECKS)
failed = 0
for name in names:
    problem = failure(*CHECKS[name])
    failed += problem is not None
    print(f"FAIL {name}: {problem}" if problem else f"PASS {name}")
raise SystemExit(failed)
