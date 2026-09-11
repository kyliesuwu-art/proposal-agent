"""Deterministic, structure-preserving conversion for client deliverables.

The review Markdown remains the fact and provenance source. This module only
removes review-only annotations from a downstream copy; it never invents a
project value to replace an unknown one.
"""
from __future__ import annotations
import re

_CITATION = re.compile(r"\[来源:\s*[^\]]+\]")
_IMAGE_SOURCE = re.compile(r"^图片来源：.*$")
_DRAFT = re.compile(r"^>\s*⚠️.*$")
_STATUS = re.compile(r"【(?:待确认|设计建议|可选能力)】")
_SOURCES_HEADING = re.compile(r"^##\s+(?:来源与依据|参考资料)\s*$")


def _clean_sentence(value: str) -> str:
    """Remove review annotations without mechanically substituting prose."""
    value = _CITATION.sub("", value)
    value = _STATUS.sub("", value)
    value = re.sub(r"\s{2,}", " ", value).strip(" \t，；")
    if "待确认" in value or "待核定" in value:
        return ""
    return value.replace("建议建议", "建议")


def formal_markdown(value: str) -> tuple[str, list[str]]:
    """Return clean Markdown plus deduplicated design-input conditions.

    Images and headings are preserved so downstream renderers, rather than a
    text substitution, decide whether an image is embedded.
    """
    lines: list[str] = []
    inputs: list[str] = []
    in_sources = False
    in_image_sources = False
    previous_heading = ""
    for raw in value.splitlines():
        stripped = raw.strip()
        plain_marker = stripped.replace("*", "").strip()
        if _SOURCES_HEADING.match(stripped):
            in_sources = True
            continue
        if in_sources:
            continue
        if stripped.startswith("### 图片来源"):
            in_image_sources = True
            continue
        if in_image_sources and stripped.startswith("## "):
            in_image_sources = False
        if in_image_sources:
            continue
        if (stripped in {"---", "***", "___"} or _IMAGE_SOURCE.match(plain_marker)
                or plain_marker.startswith("图片来源") or plain_marker.startswith("来源：")
                or _DRAFT.match(stripped)):
            continue
        if "【待确认】" in raw:
            candidate = _clean_sentence(raw.replace("【待确认】", ""))
            if candidate:
                candidate = re.sub(r"^\s*(?:[-*+] |\d+[.)] )", "", candidate)
                if candidate not in inputs:
                    inputs.append(candidate)
            continue
        cleaned = _clean_sentence(raw)
        if re.match(r"^#{2,3}\s+", cleaned):
            heading_value = re.sub(r"^#+\s+", "", cleaned).strip()
            if heading_value == previous_heading:
                continue
            previous_heading = heading_value
        if cleaned or not stripped:
            lines.append(cleaned)
    while lines and not lines[-1].strip():
        lines.pop()
    if inputs:
        lines.extend(["", "## 深化设计输入条件", "", "系统容量、负荷边界、接入条件及相关运行参数将在现场勘察、负荷数据核定和深化设计阶段确定。"])
    return "\n".join(lines), inputs


def formal_text(value: str) -> str:
    """Compatibility helper for a single line or a complete Markdown string."""
    return formal_markdown(value)[0]


def forbidden_delivery_tokens(value: str) -> list[str]:
    needles = ("[来源:", "图片来源：", "来源：", "[S", "【待确认】", "【设计建议】", "【可选能力】", "自动生成草稿", "尚有质量检查项需要人工确认", "![", "assets/", "---")
    return [needle for needle in needles if needle in value]
