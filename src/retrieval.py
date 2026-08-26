"""不依赖 Chroma 或 LLM 的检索排序与上下文选择规则。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


# 这是刻意保持很小的业务词表：仅覆盖当前库里反复出现的方案类别；未知类型
# 不会被强行归类。每个 canonical 值的第一个 alias 用于可选的精确 metadata 查询。
TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "光储充": ("光储充", "光储充一体化", "工商业光储充", "光伏储能充电"),
    "光伏": ("光伏", "分布式光伏"),
    "储能": ("储能", "储能系统"),
    "源网荷储": ("源网荷储", "源网荷储一体化"),
    "风电": ("风电", "大型风电", "风电场"),
    "数字化运维": ("数字化运维", "智能运维"),
    "配电": ("配电", "变配电", "配电系统"),
    "自动化": ("自动化", "电力自动化"),
}

# “related” 只用于很小的、业务上有明显交集的类别，且加分显著低于向量距离。
RELATED_TYPE_PAIRS = frozenset({
    frozenset(("光伏", "光储充")),
    frozenset(("储能", "光储充")),
    frozenset(("源网荷储", "光储充")),
    frozenset(("自动化", "数字化运维")),
})

TYPE_MATCH_RULES = (
    "exact: 同义词表归一化后类别相同",
    "related: 仅对光伏/储能/源网荷储与光储充、自动化与数字化运维的已列关系加分",
    "none: 未标注、未知或不属于上述关系",
)


def _compact(value: str | None) -> str:
    return re.sub(r"[\s_\-—/]+", "", (value or "").lower())


def normalize_proposal_type(value: str | None) -> str | None:
    """将已知中文标签归一化；未知/空值保持为 ``None``。"""
    compact = _compact(value)
    if not compact:
        return None
    aliases = sorted(
        ((alias, canonical) for canonical, values in TYPE_ALIASES.items() for alias in values),
        key=lambda item: len(_compact(item[0])),
        reverse=True,
    )
    for alias, canonical in aliases:
        if _compact(alias) in compact:
            return canonical
    return None


def explicit_requested_type(question: str) -> tuple[str | None, str | None]:
    """仅从用户原问题识别明确类型，避免把 LLM 猜测当成硬过滤条件。"""
    canonical = normalize_proposal_type(question)
    if not canonical:
        return None, None
    for alias in TYPE_ALIASES[canonical]:
        if _compact(alias) in _compact(question):
            return canonical, alias
    return None, None


def type_match(requested_type: str | None, candidate_type: str | None) -> tuple[str, str]:
    """返回 ``exact`` / ``related`` / ``none`` 及可解释原因。"""
    requested = normalize_proposal_type(requested_type)
    candidate = normalize_proposal_type(candidate_type)
    if not requested or not candidate:
        return "none", "missing_or_unknown_type"
    if requested == candidate:
        return "exact", f"normalized:{requested}"
    if frozenset((requested, candidate)) in RELATED_TYPE_PAIRS:
        return "related", f"related:{requested}<->{candidate}"
    return "none", f"different:{requested}!={candidate}"


def metadata_filter_values(canonical_type: str | None, matched_alias: str | None) -> list[str]:
    """给 Chroma 精确 metadata 查询用的保守候选值。"""
    if not canonical_type:
        return []
    values = [matched_alias, *TYPE_ALIASES[canonical_type]]
    return list(dict.fromkeys(value for value in values if value))


def extract_keywords(text: str) -> set[str]:
    """轻量中文/英文关键词切分，用于小幅 rerank 和相邻页关联判断。"""
    compact = _compact(text)
    tokens = set(re.findall(r"[a-z0-9]{2,}", compact))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", compact))
    tokens.update(chinese[index:index + 2] for index in range(max(0, len(chinese) - 1)))
    return {token for token in tokens if token not in {"方案", "项目", "系统", "建设", "提供", "需求"}}


def keyword_overlap(left: str, right: str) -> tuple[float, list[str]]:
    """返回 Dice 风格的关键词重合比例及最多五个匹配关键词。"""
    left_terms = extract_keywords(left)
    right_terms = extract_keywords(right)
    shared = sorted(left_terms & right_terms)
    if not left_terms or not right_terms:
        return 0.0, []
    return len(shared) / max(len(left_terms), 1), shared[:5]


def rerank_candidates(
    candidates: Iterable[dict[str, Any]],
    query_text: str,
    requested_type: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """以向量距离为主、关键词和类型为辅，对候选页去重并重排。"""
    deduped: dict[tuple[str, int], dict[str, Any]] = {}
    for original in candidates:
        record = dict(original)
        identity = (str(record["source_file"]), int(record["slide_number"]))
        distance = record.get("distance")
        distance_value = float(distance) if distance is not None else 9999.0
        existing = deduped.get(identity)
        if existing is None or distance_value < float(existing.get("distance") or 9999.0):
            deduped[identity] = record

    ranked = []
    for record in deduped.values():
        distance = record.get("distance")
        distance_value = max(float(distance), 0.0) if distance is not None else 9999.0
        vector_score = 1.0 / (1.0 + distance_value)
        searchable_text = "\n".join(
            str(record.get(field, "")) for field in ("title", "content", "context_summary")
        )
        overlap, matched_keywords = keyword_overlap(query_text, searchable_text)
        match, match_reason = type_match(requested_type, record.get("proposal_type"))
        type_bonus = {"exact": 0.03, "related": 0.015, "none": 0.0}[match]
        score = vector_score + min(overlap, 1.0) * 0.08 + type_bonus
        record["retrieval"] = {
            "rerank_score": round(score, 6),
            "vector_score": round(vector_score, 6),
            "distance": distance,
            "keyword_overlap": round(overlap, 6),
            "matched_keywords": matched_keywords,
            "type_match": match,
            "type_match_reason": match_reason,
        }
        ranked.append(record)

    ranked.sort(key=lambda record: (-record["retrieval"]["rerank_score"], record.get("distance") or 9999.0))
    return ranked[:limit]


def select_supporting_context(
    primary_records: list[dict[str, Any]],
    adjacent_by_primary: dict[tuple[str, int], list[dict[str, Any]]],
    query_text: str,
    *,
    max_pages_per_file: int = 4,
    max_total_pages: int = 14,
    max_total_characters: int = 16_000,
    short_content_characters: int = 180,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按相关性和预算补充相邻页，返回主命中和 supporting 页。"""
    primaries = [dict(record) for record in primary_records]
    selected = {(record["source_file"], record["slide_number"]) for record in primaries}
    file_counts: dict[str, int] = {}
    used_characters = 0
    for record in primaries:
        file_counts[record["source_file"]] = file_counts.get(record["source_file"], 0) + 1
        used_characters += len(str(record.get("title", ""))) + len(str(record.get("content", "")))

    supporting: list[dict[str, Any]] = []
    for primary in primaries:
        primary_identity = (primary["source_file"], primary["slide_number"])
        primary_text = f"{primary.get('title', '')}\n{primary.get('content', '')}"
        primary_is_short = len(str(primary.get("content", "")).strip()) <= short_content_characters
        for adjacent in adjacent_by_primary.get(primary_identity, []):
            candidate = dict(adjacent)
            identity = (candidate["source_file"], candidate["slide_number"])
            if identity in selected or candidate["source_file"] != primary["source_file"]:
                continue
            relation_text = f"{candidate.get('title', '')}\n{candidate.get('content', '')}"
            overlap, terms = keyword_overlap(f"{query_text}\n{primary_text}", relation_text)
            if not primary_is_short and not terms:
                continue
            if len(selected) >= max_total_pages or file_counts.get(candidate["source_file"], 0) >= max_pages_per_file:
                continue
            candidate_size = len(str(candidate.get("title", ""))) + len(str(candidate.get("content", "")))
            if used_characters + candidate_size > max_total_characters:
                continue
            candidate["supporting_reason"] = (
                "primary_content_short" if primary_is_short else f"shared_keywords:{','.join(terms)}"
            )
            candidate["supporting_keyword_overlap"] = round(overlap, 6)
            supporting.append(candidate)
            selected.add(identity)
            file_counts[candidate["source_file"]] = file_counts.get(candidate["source_file"], 0) + 1
            used_characters += candidate_size
    return primaries, supporting
