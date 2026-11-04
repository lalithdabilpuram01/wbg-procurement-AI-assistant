"""Readable text out of a streamed answer: Claude writes the answer as JSON ({"claims": [{"text": ..., ...}], ...}); this
passes on the characters of every claim's "text" as they arrive, one paragraph per claim (as the answer card shows them),
and nothing else (no ids, keys or punctuation of the JSON). A small incremental scanner: it never needs the whole reply.
"""
from __future__ import annotations

from typing import Callable

PARAGRAPH = "\n\n"
ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/", "b": "", "f": "", "r": ""}


class ClaimTextStream:
    """Feed it raw JSON chunks; it calls `emit` with the characters of each claim's "text" value as they arrive."""
    def __init__(self, emit: Callable[[str], None]):
        """Keep `emit` and the parser state: bracket stack, string and escape flags, and the current key."""
        self._emit = emit
        self._stack: list[str] = []
        self._in_string = self._escape = False
        self._unicode: str | None = None
        self._role = "other"                   # key | text | other: what the current string is
        self._key: list[str] = []
        self._last_key = ""
        self._expect_key = self._after_colon = False
        self._claims = 0

    def feed(self, chunk: str) -> None:
        for ch in chunk:
            self._string_char(ch) if self._in_string else self._structure_char(ch)

    # ---------------------------------------------------------------------------------------------- inside "..."
    def _string_char(self, ch: str) -> None:
        """Handle one character inside a JSON string, decoding escapes; a malformed \\u escape is dropped."""
        if self._unicode is not None:
            self._unicode += ch
            if len(self._unicode) == 4:
                self._out(chr(int(self._unicode, 16)) if all(c in "0123456789abcdefABCDEF" for c in self._unicode) else "")
                self._unicode = None
        elif self._escape:
            self._escape = False
            if ch == "u":
                self._unicode = ""
            else:
                self._out(ESCAPES.get(ch, ch))
        elif ch == "\\":
            self._escape = True
        elif ch == '"':
            self._in_string = False
            if self._role == "key":
                self._last_key = "".join(self._key)
        else:
            self._out(ch)

    def _out(self, text: str) -> None:
        """Collect decoded string text: into the current key name, or out to `emit` when inside a "text" value."""
        if self._role == "key":
            self._key.append(text)
        elif self._role == "text" and text:
            self._emit(text)

    # --------------------------------------------------------------------------------------------- the structure
    def _structure_char(self, ch: str) -> None:
        """Track nesting, keys and colons outside strings, and decide what the next string is (key, text or other)."""
        if ch in "{[":
            self._stack.append(ch)
            self._expect_key = ch == "{"
        elif ch in "}]":
            if self._stack:
                self._stack.pop()
        elif ch == ":":
            self._after_colon, self._expect_key = True, False
        elif ch == ",":
            self._expect_key = bool(self._stack) and self._stack[-1] == "{"
            self._after_colon = False
        elif ch == '"':
            self._in_string = True
            if self._expect_key and self._stack and self._stack[-1] == "{":
                self._role, self._key = "key", []
            elif self._after_colon and self._last_key == "text":
                self._role = "text"
                if self._claims:                       # a blank line between claims, none before the first
                    self._emit(PARAGRAPH)
                self._claims += 1
            else:
                self._role = "other"
            self._after_colon = False
