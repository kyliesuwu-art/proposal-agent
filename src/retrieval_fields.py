"""Pure-local, explainable fields for V2 page retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass


# Deliberately small and editable.  Unknown terms remain verbatim tokens.
DOMAIN_SYNONYMS: dict[str, tuple[str, ...]] = {
    "PCS": ("储能变流器", "power conversion system"),
    "BESS": ("电池储能系统", "battery energy storage system"),
    "SVG": ("静止无功发生器",),
    "STATCOM": ("静止同步补偿器",),
    "EMS": ("能量管理系统",),
    "SCADA": ("监控与数据采集系统",),
}

_ABBREVIATION = re.compile(r"\b[A-Z][A-Z0-9-]{1,}\b")
# ``\b`` is unsafe beside Chinese text because Unicode treats Han characters as
# word characters (for example ``10kV母线``).  Bound only ASCII identifier chars.
_VOLTAGE = re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?\s*(?:kV|KV)(?![A-Za-z0-9])", re.I)
_PARAMETER = re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?\s*(?:MW|kW|MWh|kWh|kvar|kVar)(?![A-Za-z0-9])", re.I)
_MODEL = re.compile(r"\b(?=[A-Za-z0-9-]{4,}\b)(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d)[A-Za-z0-9-]+\b")
_PROJECT = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]{2,}(?:项目|工程|电站|园区)")
_SECTION = re.compile(r"(?:^|\n)\s*(?:第[一二三四五六七八九十\d]+[章节]|\d+(?:\.\d+){0,3})\s*([^\n]{2,80})")


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))


@dataclass(frozen=True)
class RetrievalFields:
    retrieval_text: str
    title: str
    section_title: str
    keywords: list[str]
    entities: list[str]
    parameters: list[str]


def extract_retrieval_fields(title: str, content: str, context_summary: str = "") -> RetrievalFields:
    """Extract conservative searchable fields without calling an LLM or API."""
    title = title.strip()
    content = content.strip()
    combined = "\n".join(part for part in (title, content) if part)
    section_matches = _SECTION.findall(combined)
    section_title = section_matches[0].strip() if section_matches else title
    abbreviations = _ABBREVIATION.findall(combined)
    voltages = [item.replace(" ", "") for item in _VOLTAGE.findall(combined)]
    parameters = [item.replace(" ", "") for item in _PARAMETER.findall(combined)]
    models = _MODEL.findall(combined)
    projects = _PROJECT.findall(combined)
    aliases = [canonical for canonical, values in DOMAIN_SYNONYMS.items() if any(value.casefold() in combined.casefold() for value in (canonical, *values))]
    keywords = _unique([title, section_title, *abbreviations, *voltages, *parameters, *aliases])
    entities = _unique([*aliases, *models, *projects])
    # Keep embeddings clean: no LLM context, keyword repetition, or metadata serialization.
    return RetrievalFields(content, title, section_title, keywords, entities, _unique(parameters))
