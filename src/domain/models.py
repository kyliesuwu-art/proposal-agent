"""与存储和解析器解耦的通用文档领域模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass(frozen=True)
class DocumentIdentity:
    """稳定文档身份与内容版本；不保存本机绝对路径。"""

    source_key: str
    document_id: str
    content_hash: str
    version_id: str


@dataclass
class DocumentMetadata:
    """可扩展业务元数据，未知字段放入 ``custom`` 保留。"""

    document_type: str | None = None
    proposal_types: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    project_stage: str | None = None
    confidence: float | None = None
    custom: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImageAsset:
    """属于某个页面的图片资产；路径应保持为项目内相对路径。"""

    path: str
    caption: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_legacy_dict(cls, value: dict[str, Any]) -> ImageAsset:
        return cls(
            path=str(value.get("path", "")),
            caption=str(value.get("caption", "")),
            metadata={key: item for key, item in value.items() if key not in {"path", "caption"}},
        )


@dataclass
class ContentBlock:
    """解析器保留的原始内容块及其可选文本表示。"""

    block_type: str
    text: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, value: dict[str, Any]) -> ContentBlock:
        return cls(
            block_type=str(value.get("type", "unknown")),
            text=str(value.get("text", value.get("table_caption", "")) or ""),
            raw=dict(value),
        )


@dataclass
class Page:
    """格式无关的文档页面；PPTX slide 只是它的一种来源。"""

    page_number: int
    title: str = ""
    content: str = ""
    blocks: list[ContentBlock] = field(default_factory=list)
    images: list[ImageAsset] = field(default_factory=list)
    indexable: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_legacy_dict(cls, value: dict[str, Any]) -> Page:
        raw_blocks = value.get("raw_blocks") or []
        return cls(
            page_number=int(value.get("page_number", value.get("slide_number", 0))),
            title=str(value.get("title", "")),
            content=str(value.get("content", "")),
            blocks=[ContentBlock.from_raw(block) for block in raw_blocks if isinstance(block, dict)],
            images=[ImageAsset.from_legacy_dict(image) for image in value.get("images", []) if isinstance(image, dict)],
            indexable=bool(value.get("indexable", True)),
            metadata={key: item for key, item in value.items() if key not in {"slide_number", "page_number", "title", "content", "raw_blocks", "images", "indexable", "source_file"}},
        )


@dataclass
class SourceDocument:
    """来源文件的格式无关描述。"""

    source_key: str
    filename: str
    media_type: str
    identity: DocumentIdentity | None = None
    metadata: DocumentMetadata = field(default_factory=DocumentMetadata)


@dataclass
class ParseResult:
    """通用解析输出，同时提供旧 ``list[dict]`` 的兼容视图。"""

    document: SourceDocument
    pages: list[Page]
    metadata: dict[str, Any] = field(default_factory=dict)
    _legacy_pages: list[dict[str, Any]] = field(default_factory=list, repr=False)

    @classmethod
    def from_legacy_pages(cls, source_file: str, media_type: str, legacy_pages: list[dict[str, Any]]) -> ParseResult:
        return cls(
            document=SourceDocument(source_key=source_file, filename=source_file.rsplit("/", 1)[-1], media_type=media_type),
            pages=[Page.from_legacy_dict(page) for page in legacy_pages],
            _legacy_pages=legacy_pages,
        )

    def to_legacy_pages(self) -> list[dict[str, Any]]:
        """返回兼容现有 Chroma/Pipeline 字段的页面字典，不改变原字典。"""
        return self._legacy_pages

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self._legacy_pages)

    def __len__(self) -> int:
        return len(self._legacy_pages)

    def __bool__(self) -> bool:
        return bool(self._legacy_pages)
