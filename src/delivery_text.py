"""Deterministic review-Markdown to formal-delivery text conversion."""
from __future__ import annotations
import re

_CITATION = re.compile(r"\[来源:\s*[^\]]+\]")
_IMAGE_SOURCE = re.compile(r"^图片来源：.*$", re.M)
_DRAFT = re.compile(r"^>\s*⚠️.*(?:\n|$)", re.M)

def formal_text(value: str) -> str:
    value = re.sub(r"(?ms)^## 来源与依据\s*$.*\Z", "", value)
    value = _CITATION.sub("", value)
    value = _IMAGE_SOURCE.sub("", value)
    value = re.sub(r"图片来源[^\n。；]*[。；]?", "", value)
    value = re.sub(r"来源：[^\n]*", "", value)
    value = _DRAFT.sub("", value)
    value = value.replace("【设计建议】", "建议").replace("【可选能力】", "可根据项目需求配置")
    value = re.sub(r"【待确认】", "将在现场勘察及深化设计阶段确定", value)
    return re.sub(r"[ \t]+\n", "\n", value).strip()

def forbidden_delivery_tokens(value: str) -> list[str]:
    needles = ("[来源:", "图片来源：", "来源：", "[S", "【待确认】", "【设计建议】", "【可选能力】", "自动生成草稿", "尚有质量检查项需要人工确认")
    return [needle for needle in needles if needle in value]
