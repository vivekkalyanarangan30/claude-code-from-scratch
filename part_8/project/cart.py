def total(items, discount=0):
    """Return the order total for (price, qty) items, with `discount` percent off."""
    subtotal = sum(price * qty for price, qty in items)
    return subtotal - discount


def receipt(items, discount=0):
    lines = [f"{qty} x {price:.2f}" for price, qty in items]
    return "\n".join(lines + [f"total: {total(items, discount):.2f}"])
