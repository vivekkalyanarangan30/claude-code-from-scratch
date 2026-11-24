"""The one HTTP call the course is built on. Without Ollama, a scripted fake answers in the same shape."""
import asyncio
import os
import pathlib
import sys

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from common import OLLAMA, pick_model
from fake import FakeModel, call, say

MODEL = pick_model()[0]


def recall_name(messages):
    told = [m["content"] for m in messages if m["role"] == "user" and "my name is" in str(m["content"])]
    return [say(f"Your name is {told[-1].split()[-1]}." if told else "I don't know your name - you haven't told me.")]


PUPPET_SCRIPTS = {
    "my name is": ("my name is Vivek", [[say("Nice to meet you, Vivek!")]]),
    "what is my name": ("what is my name?", [recall_name]),
    "cart.py": ("what is in cart.py?", [[call("Read", path="cart.py")]]),
}


def post(body: dict) -> dict:
    """POST /v1/messages and return the JSON reply."""
    kind, _, name = MODEL.partition(":")
    if kind == "fake":
        return asyncio.run(FakeModel(PUPPET_SCRIPTS, delay=0).complete(
            body.get("system", ""), body["messages"], body.get("tools", [])))
    if kind == "anthropic":
        url, key = "https://api.anthropic.com", os.environ["ANTHROPIC_API_KEY"]
    else:
        url, key = OLLAMA, "ollama"
    reply = httpx.post(f"{url}/v1/messages", json={**body, "model": name, "temperature": 0}, timeout=300,
                       headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
    reply.raise_for_status()
    body = reply.json()
    body["content"] = body.get("content") or []  # Ollama sends null for an empty reply
    return body


def said(reply: dict) -> str:
    """The reply's text blocks joined; real models may also send thinking blocks."""
    return " ".join(b["text"] for b in reply["content"] if b["type"] == "text").strip()
