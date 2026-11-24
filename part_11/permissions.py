"""5.1–5.4, 11.3 — The gate. Every tool call is allowed, denied or asked about before anything happens."""
import fnmatch
import json
import os
import shlex
import tempfile

MODES = ["default", "accept-edits", "plan", "auto", "dont-ask"]
NOBODY_HERE = "dont-ask mode: nobody is here to approve this, and no allow rule covers it"
FILE_TOOLS = {"Read", "Edit", "Write"}
EDITS = {"Edit", "Write"}
SEPARATORS = {"&&", "||", ";", "|", "&", ";;", "|&"}
UNCHECKABLE = ("$(", "`", "<(", ">(", "${")


def split_command(command: str) -> list[list[str]] | None:
    """Split a shell line into its simple commands. None means it can't be checked, so a human decides."""
    if any(mark in command for mark in UNCHECKABLE):
        return None
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=";&|<>")
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    commands, current = [], []
    for token in tokens:
        if token in SEPARATORS:
            if current:
                commands.append(current)
            current = []
        elif set(token) <= set(";&|<>"):
            return None
        else:
            current.append(token)
    if current:
        commands.append(current)
    return commands or None


def parse(rule: str) -> tuple[str, str | None]:
    name, _, rest = rule.partition("(")
    return name, rest[:-1] if rest.endswith(")") else None


def matches(rule: str, name: str, subject) -> bool:
    """Rules look like Bash(git status:*), Bash(python3 test_cart.py), Edit, Edit(src/**), Read(.env)."""
    rule_name, spec = parse(rule)
    if rule_name != name:
        return False
    if spec is None:
        return True
    if name == "Bash":
        if spec.endswith(":*"):
            prefix = shlex.split(spec[:-2])
            return subject[:len(prefix)] == prefix
        return subject == shlex.split(spec)
    return subject is not None and fnmatch.fnmatch(subject, spec)


class Gate:
    def __init__(self, root):
        self.root, self.mode = root, "default"
        self.path = root / ".agent" / "settings.json"
        self.rules = {"allow": [], "deny": []}
        if self.path.exists():
            self.rules.update(json.loads(self.path.read_text()).get("permissions", {}))

    def subjects(self, name, args):
        if name == "Bash":
            return split_command(str(args.get("command", "")))
        if name in FILE_TOOLS:
            return [os.path.normpath(str(args.get("path", "")))]
        return [None]

    def check(self, tool, args) -> tuple[str, str]:
        """Return (decision, reason). Deny rules win, then modes, then allow rules; anything else asks."""
        subjects = self.subjects(tool.name, args)
        for rule in self.rules["deny"]:
            if subjects and any(matches(rule, tool.name, s) for s in subjects):
                return "deny", f"denied by the rule {rule}"
        if tool.read_only:
            return "allow", "read-only"
        if self.mode == "plan":
            return "deny", "plan mode allows read-only tools only. Finish the plan; the user switches modes to apply it"
        if subjects is None:
            return self.ask("this command can't be split safely")
        if self.mode == "accept-edits" and tool.name in EDITS:
            return "allow", "accept-edits mode"
        if all(any(matches(rule, tool.name, s) for rule in self.rules["allow"]) for s in subjects):
            return "allow", "allowed by a rule"
        return self.ask("")

    def ask(self, reason) -> tuple[str, str]:
        """With nobody watching, a question is a no: the model gets the reason back as a result."""
        return ("deny", NOBODY_HERE) if self.mode == "dont-ask" else ("ask", reason)

    def suggest(self, tool, args) -> str | None:
        """The rule that "don't ask again" would save. None when no single rule is safe to offer."""
        if tool.name in EDITS:
            return tool.name
        commands = self.subjects(tool.name, args) if tool.name == "Bash" else None
        if not commands or len(commands) != 1:
            return None
        words = commands[0]
        return f"Bash({shlex.join(words)})" if len(words) <= 3 else f"Bash({shlex.join(words[:2])}:*)"

    def allow(self, rule: str):
        if rule not in self.rules["allow"]:
            self.rules["allow"].append(rule)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=self.path.parent, delete=False) as tmp:
            json.dump({"permissions": self.rules}, tmp, indent=2)
        os.replace(tmp.name, self.path)

    def cycle(self) -> str:
        self.mode = MODES[(MODES.index(self.mode) + 1) % len(MODES)]
        return self.mode
