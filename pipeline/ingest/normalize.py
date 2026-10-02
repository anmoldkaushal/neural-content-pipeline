"""Repairs the two extraction artifacts real client PDFs produce, before anything reads the text:

- letter-spaced display headings ("T H E  S I T U A T I O N" -> "THE SITUATION"), which no prompt
  or search reliably recognises as a heading;
- one word per line ("What\\n \\nis\\n \\nthe"), which some exporters emit for every word and which
  roughly doubles the size of the text a model has to read.

Ordinary text passes through unchanged."""
from __future__ import annotations

import re

_LETTER_SPACED = re.compile(r"^(?:\S ){2,}\S$")  # three or more single characters, single-spaced
_SPACED_PAIR = re.compile(r"^(?:\S )+\S$")  # "I T": allowed beside a longer spaced run
# A share of "word\n \nword" separators above this means the exporter split every word.
_WORD_PER_LINE_SHARE = 0.3
_MIN_WORD_GAPS = 20  # and enough of them that a short snippet with one blank line is left alone


def _join_letter_spaced(line: str) -> str:
    stripped = line.strip()
    segments = [s for s in re.split(r" {2,}", stripped) if s]
    if not segments or not any(_LETTER_SPACED.match(s) for s in segments):
        return line
    if not all(_SPACED_PAIR.match(s) or " " not in s for s in segments):
        return line  # mixed with ordinary words: not a display heading
    return " ".join(s.replace(" ", "") for s in segments)


def _rejoin_words(text: str) -> str:
    newlines = text.count("\n") or 1
    gaps = text.count("\n \n")
    if gaps < _MIN_WORD_GAPS or gaps / newlines < _WORD_PER_LINE_SHARE:
        return text

    def gap(match: re.Match[str]) -> str:
        breaks = match.group().count("\n")
        return " " if breaks == 2 else "\n\n"

    # Runs of whitespace holding newlines: exactly two is a word gap, three or more a paragraph.
    return re.sub(r"(?:[ \t]*\n){2,}[ \t]*", gap, text)


def normalize(text: str) -> str:
    if not text:
        return text
    text = _rejoin_words(text)
    text = "\n".join(_join_letter_spaced(line) for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()
