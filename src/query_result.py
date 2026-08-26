"""查询流程的稳定输出模型，供 CLI 与未来交付渠道复用。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SearchHit:
    """一次检索中可供生成与引用的页面。"""

    source_file: str
    page_number: int
    title: str
    content: str
    distance: float | None
    images: list[dict[str, Any]] = field(default_factory=list)
    context_role: str = "primary"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_adjacent(self) -> bool:
        """兼容旧调用方对相邻补充页的判断。"""
        return self.context_role in {"adjacent", "supporting"}


@dataclass(frozen=True)
class Citation:
    """可验证的引用身份；文件名与页码共同构成唯一来源。"""

    source_file: str
    page_number: int
    title: str = ""
    images: tuple[dict[str, Any], ...] = ()

    def display(self) -> str:
        """统一的人类可读引用格式。"""
        return f"[来源: {self.source_file}, 第 {self.page_number} 页]"


@dataclass
class QueryResult:
    """一次完整查询的渠道无关结果。"""

    original_query: str
    rewritten_queries: list[str]
    hits: list[SearchHit]
    proposal_markdown: str
    citations: list[Citation]
    warnings: list[str] = field(default_factory=list)
    retrieval_metadata: dict[str, Any] = field(default_factory=dict)
