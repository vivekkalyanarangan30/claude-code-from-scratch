"""What the fake model says in Part 1. A real model is a drop-in: MODEL=anthropic:claude-sonnet-5-5."""
from fake import call, say

FIXED = '''def total(items, discount=0):
    """Return the order total for (price, qty) items, with `discount` percent off."""
    subtotal = sum(price * qty for price, qty in items)
    return subtotal * (1 - discount / 100)


def receipt(items, discount=0):
    lines = [f"{qty} x {price:.2f}" for price, qty in items]
    return "\\n".join(lines + [f"total: {total(items, discount):.2f}"])
'''

SCRIPTS = {
    "fix": ("fix the failing test", [
        [say("Let me run the tests first."), call("Bash", command="python3 test_cart.py")],
        [say("The discount assertion fails. Let me look at `total`."), call("Read", path="cart.py")],
        [say("`discount` is a percentage but it's subtracted as money. Fixing that."),
         call("Write", path="cart.py", content=FIXED)],
        [call("Bash", command="python3 test_cart.py")],
        [say("Fixed: `total()` now applies the discount as a percentage, and the tests pass.")],
    ]),
    "slow": ("run something slow", [
        [say("Starting a long job."), call("Bash", command="sleep 8 && echo done")],
        [say("The job finished.")],
    ]),
    "forever": ("keep going forever", [[call("Bash", command="echo still going")]] * 1000),
}
