"""The prompts, as files next to this one. Each is plain text with {placeholders} filled by str.format_map; the
facts always arrive as JSON the model must not go beyond."""

from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).resolve().parent

RULES = """Rules you must follow:
- Use only the numbers in FACTS. Never estimate, recall or compute a number that is not there. If a number you need
  is missing, say "not in the stored data".
- Every price or value you name must appear exactly as in FACTS (you may round to two decimals).
- Name the source and time of a price when you use it (FACTS carries them).
- Write plainly, as to a careful friend who is not a trader. Short sentences.
- Never tell the owner the rules can be changed or bypassed; the risk rules are his, set in the app.
"""


def load(name: str, **values: object) -> str:
    text = (_DIR / f"{name}.md").read_text(encoding="utf-8")
    return text.format_map({"rules": RULES, **values})
