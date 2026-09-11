"""Shared, deliberately small contract for delivered proposal image blocks."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

_BLOCK = re.compile(r"^!\[([^\]\r\n]*)\]\((assets/[A-Za-z0-9._/-]+)\)$")
_ANY_ASSET = re.compile(r"!\[[^\]\r\n]*\]\(([^)\s]+)\)")
_ORPHAN = re.compile(r"!\[[^\]\r\n]*\](?!\()")
_MARKER = re.compile(r"\[(IMG\d+)\]")
_WRAPPED_MARKER = re.compile(r"!\[[^\]\r\n]*\]\s*\[(IMG\d+)\]")


@dataclass(frozen=True)
class ImageBlock:
    line_number: int
    caption: str
    asset_path: str


class MarkdownImageError(ValueError):
    pass


def normalise_caption(value: str) -> str:
    """Produce a one-line literal caption, never Markdown syntax."""
    value = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", value or "")
    value = re.sub(r"[\[\]]", "", value.replace("\r", " ").replace("\n", " "))
    return re.sub(r"\s+", " ", value).strip() or "图片"


def normalise_internal_image_markers(text: str, selected_ids: set[str] | None = None) -> str:
    """Strip model wrappers and make each selected IMG marker a standalone block.

    Fenced code is literal.  Marker duplicates are removed only when the marker
    is selected, preserving ordinary text and unknown-marker validation.
    """
    output: list[str] = []
    seen: set[str] = set()
    in_fence = False
    for original in text.splitlines():
        if re.match(r"^\s*(```|~~~)", original):
            in_fence = not in_fence; output.append(original); continue
        if in_fence:
            output.append(original); continue
        line = _WRAPPED_MARKER.sub(lambda m: f"[{m.group(1)}]", original)
        parts = _MARKER.split(line)
        if len(parts) == 1:
            output.append(line); continue
        fragments: list[str] = []
        for index, part in enumerate(parts):
            if index % 2 == 0:
                if part.strip(): fragments.append(part.strip())
                continue
            marker = part
            if selected_ids is not None and marker in selected_ids and marker in seen:
                continue
            if selected_ids is None or marker in selected_ids:
                seen.add(marker)
            fragments.append(f"[{marker}]")
        output.extend(fragments)
    return "\n".join(output).strip() + "\n"


def image_blocks(markdown: str) -> list[ImageBlock]:
    """Return only complete, code-block-external standalone image blocks."""
    blocks: list[ImageBlock] = []
    in_fence = False
    for number, raw in enumerate(markdown.splitlines(), 1):
        if re.match(r"^\s*(```|~~~)", raw):
            in_fence = not in_fence; continue
        if in_fence: continue
        match = _BLOCK.fullmatch(raw.strip())
        if match: blocks.append(ImageBlock(number, match.group(1), match.group(2)))
    return blocks


def image_syntax_errors(markdown: str) -> list[str]:
    """Flag every image-like asset token that is not a legal shared block."""
    errors: list[str] = []; in_fence = False
    for number, raw in enumerate(markdown.splitlines(), 1):
        if re.match(r"^\s*(```|~~~)", raw): in_fence = not in_fence; continue
        if in_fence: continue
        line = raw.strip()
        legal = _BLOCK.fullmatch(line)
        if "assets/" in raw and _ANY_ASSET.search(raw) and not legal:
            errors.append(f"第 {number} 行：assets 图片必须是独立完整图片块")
        elif _ORPHAN.search(raw):
            errors.append(f"第 {number} 行：存在没有路径的孤立图片图注")
    return errors


def normalise_delivered_image_blocks(markdown: str, *, remove_model_wrappers: bool = False) -> str:
    """Repair only an unambiguous run of model image wrappers before one asset.

    This is intentionally conservative: an orphan caption anywhere else remains
    a structural error rather than being silently discarded as prose.
    """
    lines = markdown.splitlines(); output: list[str] = []; in_fence = False; index = 0
    wrapper = re.compile(r"^!\[[^\]\r\n]*\]$")
    prefixed = re.compile(r"^(?:!\[[^\]\r\n]*\])+(\!\[[^\]\r\n]*\]\(assets/[A-Za-z0-9._/-]+\))$")
    while index < len(lines):
        line = lines[index]
        if re.match(r"^\s*(```|~~~)", line): in_fence = not in_fence; output.append(line); index += 1; continue
        direct = prefixed.fullmatch(line.strip()) if not in_fence else None
        if direct and _BLOCK.fullmatch(direct.group(1)):
            output.append(direct.group(1)); index += 1; continue
        if in_fence or not wrapper.fullmatch(line.strip()): output.append(line); index += 1; continue
        start = index
        while index < len(lines) and wrapper.fullmatch(lines[index].strip()): index += 1
        if index < len(lines):
            match = prefixed.fullmatch(lines[index].strip())
            if match and _BLOCK.fullmatch(match.group(1)):
                output.append(match.group(1)); index += 1; continue
        if not remove_model_wrappers:
            output.extend(lines[start:index])
    return "\n".join(output) + ("\n" if markdown.endswith("\n") else "")


def safe_asset_path(markdown_path: Path, asset_path: str) -> Path | None:
    if not asset_path.startswith("assets/") or Path(asset_path).is_absolute() or ".." in Path(asset_path).parts:
        return None
    root = markdown_path.parent.resolve(); candidate = (root / asset_path).resolve()
    try: candidate.relative_to(root)
    except ValueError: return None
    return candidate if candidate.is_file() else None
