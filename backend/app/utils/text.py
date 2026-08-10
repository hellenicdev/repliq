"""Text normalization helpers shared by search and future segmentation."""

import re

_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")


def normalize_text(text: str) -> str:
    """Lowercase, drop apostrophes and punctuation, collapse whitespace."""
    text = text.lower().replace("'", "")
    text = _NON_ALNUM.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokenize(text: str) -> list[str]:
    return normalize_text(text).split()
