"""0.2 — The model is a function: a list of messages in, one message out, nothing remembered."""
import json

from wire import MODEL, post, said

print(f"model: {MODEL}\n")

first = {"max_tokens": 1024, "messages": [{"role": "user", "content": "my name is Vivek"}]}
print(">>> POST /v1/messages\n" + json.dumps(first, indent=2))
reply = post(first)
print("<<< 200\n" + json.dumps(reply, indent=2))

second = {"max_tokens": 1024, "messages": [{"role": "user", "content": "what is my name?"}]}
print("\n>>> a second, separate request\n" + json.dumps(second, indent=2))
print("<<< " + said(post(second)))

replayed = {"max_tokens": 1024, "messages": [
    first["messages"][0],
    {"role": "assistant", "content": reply["content"]},
    {"role": "user", "content": "what is my name?"},
]}
print(f"\n>>> the same question, with the history re-sent ({len(replayed['messages'])} messages)")
print("<<< " + said(post(replayed)))
print("\nThe model kept nothing between calls. Whoever holds the list holds the memory - that's the harness.")
