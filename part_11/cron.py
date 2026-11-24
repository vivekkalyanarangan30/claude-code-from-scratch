"""11.2 — Cron syntax: minute hour day-of-month month day-of-week. When does a schedule fire next?"""
import datetime as dt

FIELDS = [("minute", 0, 59), ("hour", 0, 23), ("day of month", 1, 31), ("month", 1, 12), ("day of week", 0, 6)]
SHORTHANDS = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *"}


def parse_field(text: str, name: str, low: int, high: int) -> set[int]:
    """`*`, `5`, `1-5`, `*/15`, `9-17/2` and lists of them, as the set of values they allow."""
    values = set()
    for part in text.split(","):
        spec, _, step = part.partition("/")
        try:
            if spec == "*":
                start, end = low, high
            elif "-" in spec:
                start, end = map(int, spec.split("-"))
            else:
                start = int(spec)
                end = high if step else start
            every = int(step) if step else 1
        except ValueError:
            raise ValueError(f"{name}: {part!r} is not a number, a range or */n") from None
        if not low <= start <= end <= high or every < 1:
            raise ValueError(f"{name}: {part!r} is outside {low}-{high}")
        values.update(range(start, end + 1, every))
    return values


def parse(expr: str) -> list[set[int]]:
    fields = SHORTHANDS.get(expr.strip(), expr).split()
    if len(fields) != 5:
        raise ValueError(f"{expr!r}: cron needs five fields: minute hour day-of-month month day-of-week")
    return [parse_field(text, *spec) for text, spec in zip(fields, FIELDS)]


def matches(fields, when: dt.datetime) -> bool:
    minute, hour, day, month, weekday = fields
    day_ok, weekday_ok = when.day in day, (when.weekday() + 1) % 7 in weekday  # cron counts Sunday as 0
    # Cron's oldest trap: when both day fields are restricted, either one matching is enough.
    both = len(day) < 31 and len(weekday) < 7
    return (when.minute in minute and when.hour in hour and when.month in month
            and (day_ok or weekday_ok if both else day_ok and weekday_ok))


def next_run(expr: str, after: dt.datetime) -> dt.datetime:
    """The first minute strictly after `after` that the expression allows."""
    fields = parse(expr)
    when = after.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    limit = when + dt.timedelta(days=366 * 5)
    while when < limit:
        if when.month not in fields[3]:
            when = (when.replace(day=1, hour=0, minute=0) + dt.timedelta(days=32)).replace(day=1)
        elif not matches([set(range(60)), set(range(24)), *fields[2:]], when):
            when = when.replace(hour=0, minute=0) + dt.timedelta(days=1)
        elif when.hour not in fields[1]:
            when = when.replace(minute=0) + dt.timedelta(hours=1)
        elif when.minute not in fields[0]:
            when += dt.timedelta(minutes=1)
        else:
            return when
    raise ValueError(f"{expr!r} never fires")
