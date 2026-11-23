"""Whitespace-tolerant check that a quote actually occurs in a page."""

from __future__ import annotations

import re


def normalize_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def quote_in_text(quote: str, text: str) -> bool:
    needle = normalize_ws(quote)
    haystack = normalize_ws(text)
    if len(needle) < 8:
        return False
    if needle in haystack:
        return True
    return needle.casefold() in haystack.casefold()
