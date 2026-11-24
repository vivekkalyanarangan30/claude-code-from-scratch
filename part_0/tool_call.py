"""0.3 — A tool call on the wire. The model asks; it runs nothing. Everything after the reply is your code."""
import json
import sys

from wire import MODEL, post, said

print(f"model: {MODEL}\n")

READ = {
    "name": "Read",
    "description": "Read a text file from the project. Path is relative to the project root.",
    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
}
body = {"max_tokens": 1024, "tools": [READ],
        "system": "You work inside a project folder. Use your tools to look at files; never ask the user.",
        "messages": [{"role": "user", "content": "what is in cart.py?"}]}
print(">>> tools:\n" + json.dumps(READ, indent=2))
reply = post(body)
print("<<< " + json.dumps(reply, indent=2))

use = next((b for b in reply["content"] if b["type"] == "tool_use"), None)
if use is None:
    sys.exit(f"stop_reason={reply['stop_reason']!r}: it answered instead of asking: {said(reply)[:200]}")
print(f"\nstop_reason={reply['stop_reason']!r}: the model stopped and is waiting for {use['name']}"
      f"({use['input']}).\nNo file was opened. To continue, YOU open it and send back:")
print(json.dumps({"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": use["id"], "content": "def total(items, discount=0): ..."}]}, indent=2))
print("\nDo that in a loop and you have an agent. That's Part 1.")
