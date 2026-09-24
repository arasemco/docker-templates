"""Plain stdin/stdout prompts for the interactive `app` command. Input and
output are injectable, so tests drive it with a scripted answer list."""

from __future__ import annotations

import sys


class Aborted(Exception):
    """Input ended (EOF / Ctrl-D) before every question was answered."""


class Prompter:
    def __init__(self, input_fn=None, out=None):
        self._input = input_fn or (lambda prompt: input(prompt))
        self.out = out or sys.stdout

    def say(self, text: str = "") -> None:
        print(text, file=self.out)

    def _read(self, prompt: str) -> str:
        try:
            return self._input(prompt).strip()
        except EOFError as e:
            raise Aborted from e

    def choose(
        self, title: str, options: list[str], *, none_label: str | None = None
    ) -> int | None:
        """Numbered menu. Returns the chosen index, or None for the
        `none_label` entry (listed as 0) when one is given."""
        self.say(title)
        if none_label is not None:
            self.say(f"   0) {none_label}")
        for n, option in enumerate(options, 1):
            self.say(f"  {n:2}) {option}")
        low = 0 if none_label is not None else 1
        while True:
            answer = self._read(f"Select [{low}-{len(options)}]: ")
            if answer.isdigit() and low <= int(answer) <= len(options):
                n = int(answer)
                return None if n == 0 else n - 1
            self.say(f"  enter a number from {low} to {len(options)}")

    def confirm(self, question: str, *, default: bool = True) -> bool:
        hint = "[Y/n]" if default else "[y/N]"
        while True:
            answer = self._read(f"{question} {hint} ").lower()
            if not answer:
                return default
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            self.say("  answer y or n")

    def ask(self, question: str, *, default: str | None = None, required: bool = False) -> str:
        """Free-text answer. Empty input returns `default` (or "" when there
        is none); a required question repeats until it gets a value."""
        suffix = f" [{default}]" if default else ""
        while True:
            answer = self._read(f"{question}{suffix}: ")
            if answer:
                return answer
            if default:
                return default
            if not required:
                return ""
            self.say("  a value is required")
