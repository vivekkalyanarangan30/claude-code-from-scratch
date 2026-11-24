"""3.4 — Headless mode: a second subscriber to the same events. The agent never knew about a screen."""
import json
import sys
from dataclasses import asdict

from common import Ask, Text, ToolCall, ToolResult


async def run(agent, prompt, as_json=False, approve=False):
    """Print the turn: final text to stdout, or every event as one JSON object per line."""
    async for event in agent.turn(prompt):
        if isinstance(event, Ask):
            event.answer.set_result("yes" if approve else "no")
            if not approve:
                print(f"denied: {event.question} (pass --yes to allow)", file=sys.stderr)
        if as_json:
            fields = {"question": event.question} if isinstance(event, Ask) else asdict(event)
            print(json.dumps({"type": type(event).__name__, **fields}), flush=True)
        elif isinstance(event, Text):
            print(event.text, end="", flush=True)
        elif isinstance(event, ToolCall):
            print(f"\n⏺ {event.name}({json.dumps(event.input)[:100]})", file=sys.stderr)
        elif isinstance(event, ToolResult) and event.is_error:
            print(f"  ⎿  {event.output.splitlines()[0] if event.output else 'error'}", file=sys.stderr)
    if not as_json:
        print()
