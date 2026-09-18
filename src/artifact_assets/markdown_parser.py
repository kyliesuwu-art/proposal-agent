"""Small Markdown image extractor scoped to task delivery syntax.

It deliberately ignores fenced code and does not attempt to parse arbitrary
Markdown constructs. Inline images and an optional quoted title are supported.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import unquote


_IMAGE = re.compile(r"!\[([^\]\r\n]*)\]\(\s*(?:<([^>\r\n]+)>|([^\s)]+(?:\s+[^\s)]+)*?))\s*\)")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class MarkdownImageReference:
    raw_text: str
    reference: str
    alt_text: str
    line_number: int
    normalized_reference: str


def _split_destination(value: str) -> str:
    # A title is optional and only stripped when its delimiter is unambiguous.
    match = re.match(r'^(.*?)(?:\s+(?:"[^"]*"|\'[^\']*\'|\([^)]*\)))$', value.strip())
    return (match.group(1) if match else value).strip()


def normalize_reference(reference: str) -> str:
    return unquote(reference).replace("\\", "/")


def extract_markdown_images(markdown: str) -> list[MarkdownImageReference]:
    found: list[MarkdownImageReference] = []
    in_fence = False
    for line_number, line in enumerate(markdown.splitlines(), 1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in _IMAGE.finditer(line):
            reference = (match.group(2) or _split_destination(match.group(3))).strip()
            found.append(MarkdownImageReference(
                raw_text=match.group(0), reference=reference, alt_text=match.group(1),
                line_number=line_number, normalized_reference=normalize_reference(reference),
            ))
    return found
