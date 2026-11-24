"""What the fake model says in Part 2. A real model is a drop-in: MODEL=anthropic:claude-sonnet-5-5."""
from fake import call, say

BUG = "    return subtotal - discount\n"
FIX = "    return subtotal * (1 - discount / 100)\n"

SCRIPTS = {
    "fix": ("fix the failing test", [
        [say("Let me run the tests first."), call("Bash", command="python3 test_cart.py")],
        [say("The discount assertion fails. Let me look at `total`."), call("Read", path="cart.py")],
        [say("`discount` is a percentage but it's subtracted as money. One line to change."),
         call("Edit", path="cart.py", old_string=BUG, new_string=FIX)],
        [call("Bash", command="python3 test_cart.py")],
        [say("Fixed: `total()` now applies the discount as a percentage, and the tests pass.")],
    ]),
    "config.py": ("what's in config.py?", [
        [call("Read", path="config.py")],
        [say("There's no config.py. Let me see what files the project has."), call("Glob", pattern="**/*.py")],
        [say("There's no config.py here. The project has `cart.py` (the checkout logic) and "
             "`test_cart.py`. Did you mean one of those?")],
    ]),
    "settings.py": ("ok, try settings.py", [
        [call("Read", path="settings.py")],
        [say("No settings.py either. The only Python files are `cart.py` and `test_cart.py`.")],
    ]),
    "where": ("where is total used?", [
        [call("Grep", pattern=r"total\(", glob="*.py")],
        [say("`total()` is defined in cart.py, called by `receipt()` in the same file, and tested in test_cart.py.")],
    ]),
    "explain": ("explain the bug in cart.py", [
        [say("`total()` takes `discount` as a percentage: the docstring says so, and the test expects 10 off "
             "200 to be 180. But the code subtracts it as money, so 10 percent off 200 comes out as 190. "
             "Small numbers hide it: with a subtotal of 100, both readings give 90, which is why a test with "
             "round numbers can pass while the bug is still there. The fix is one line: multiply by "
             "(1 - discount / 100) instead of subtracting.")],
    ]),
    "slow": ("run something slow", [
        [say("Starting a long job."), call("Bash", command="sleep 8 && echo done")],
        [say("The job finished.")],
    ]),
    "forever": ("keep going forever", [[call("Bash", command="echo still going")]] * 1000),
}
