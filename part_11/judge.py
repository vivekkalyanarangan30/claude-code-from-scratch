"""5.6 — Auto mode: a second model call judges each action against what the user actually asked for."""
import json
import re

import sse

PROMPT = """You decide whether a coding agent may run an action without asking its user.

What the user asked, in their own words:
{requests}

The action the agent wants to run:
{action}

Answer allow only if the action is clearly needed for what the user asked and stays inside the project:
no network access, no reading secrets, nothing destructive. If you are not sure, answer ask.
Reply with one word: allow or ask."""


async def judge(model, requests: list[str], name: str, args: dict) -> str:
    """Return "allow" or "ask". The judge never sees tool output, which is where injected text lives."""
    action = f"{name} {json.dumps(args)}"
    prompt = PROMPT.format(requests="\n".join(f"- {r}" for r in requests), action=action)
    try:
        reply = sse.Reply()
        async for event in sse.events(model.stream("You review actions for safety.",
                                                   [{"role": "user", "content": prompt}], [])):
            reply.feed(event)
        words = " ".join(b["text"] for b in reply.content if b["type"] == "text").strip().lower()
    except Exception:
        return "ask"
    return "allow" if re.fullmatch(r"\W*allow\W*", words) else "ask"
