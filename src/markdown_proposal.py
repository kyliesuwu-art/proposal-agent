"""Small, in-memory workflow for one evidence-grounded Markdown proposal."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from src.config import RAG_DB_PATH
from src.query_result import QueryResult, SearchHit
from src.retrieval_fields import extract_retrieval_fields


class ProposalLLM(Protocol):
    def generate(self, prompt: str, system_prompt: str = "") -> str: ...


class ProposalGenerationError(RuntimeError):
    """A safe, stage-labelled failure for the one-file proposal workflow."""

    def __init__(self, stage: str, cause: BaseException, *, detail: str = "") -> None:
        self.stage = stage
        self.cause = cause
        suffix = f" ({detail})" if detail else ""
        super().__init__(f"proposal stage {stage} failed: {type(cause).__name__}: {cause}{suffix}")


class ProposalQualityError(ValueError):
    """A deterministic, user-visible proposal quality gate failure."""


class ProposalWriteError(RuntimeError):
    """A publication failure with a safe operation name, never raw content."""

    def __init__(self, operation: str, cause: BaseException) -> None:
        self.operation = operation
        self.cause = cause
        super().__init__(f"proposal publication failed at {operation}: {type(cause).__name__}")


@dataclass(frozen=True)
class ProposalRunResult:
    path: Path
    quality_status: str
    warnings: list[str]
    metrics: dict


_DRAFT_NOTICE = "> ⚠️ 当前文档为自动生成草稿，尚有质量检查项需要人工确认。\n\n"


@dataclass(frozen=True)
class SectionPlan:
    section_id: str
    heading: str
    level: int
    writing_goal: str
    retrieval_queries: list[str]
    image_intent: str = "optional"


@dataclass(frozen=True)
class ProposalPlan:
    title: str
    sections: list[SectionPlan]


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    file_name: str
    page_number: int
    text: str
    images: list[dict]
    title: str = ""
    role: str = "unknown"


@dataclass(frozen=True)
class ReviewIssue:
    affected_section_ids: list[str]
    issue_type: str
    description: str
    revision_instruction: str
    severity: str = "warning"


@dataclass(frozen=True)
class ReviewParseResult:
    """Validated review feedback plus safe telemetry for advisory omissions."""

    issues: list[ReviewIssue]
    items_received: int
    items_skipped_advisory: int
    warnings: list[str]


class ReviewSchemaError(ValueError):
    """A schema error whose repairability is explicit to the review workflow."""

    def __init__(self, message: str, *, repairable: bool) -> None:
        self.repairable = repairable
        super().__init__(message)


@dataclass(frozen=True)
class _SectionDraft:
    """One independently rendered section; image ownership is always canonical."""

    plan: SectionPlan
    raw_body: str
    evidence: list[EvidenceItem]
    images: dict[str, tuple[EvidenceItem, dict]]
    # These are runtime-only records.  Keeping them on the section prevents an
    # image selected for one section being appended after the document has been
    # concatenated and accidentally rendered under another H2.
    usable_images: dict[str, tuple[EvidenceItem, dict]] = field(default_factory=dict)
    selected_images: dict[str, str] = field(default_factory=dict)
    rendered_image_blocks: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class _ImageAsset:
    section_id: str
    image_id: str
    selected_by: str
    source: Path
    relative_path: str
    caption: str
    evidence: EvidenceItem


_PLAN_SYSTEM = """你是技术方案规划助手。仅根据用户需求规划 3 至 8 个主要章节；不得套用固定模板。
只输出 JSON，格式为 {title, sections:[{section_id,heading,level,writing_goal,retrieval_queries,image_intent}]}；image_intent 只能为 none、optional、preferred。
section_id 必须唯一；检索词必须具体、可直接检索。优先考虑总体架构、源网荷储/配电数字化、虚拟电厂调度等核心主题是否值得插图；没有可靠图片也可为 none，不能为凑数要求插图。用户明确列出的交付项必须在最终标题结构中逐项可识别：可以作为主章节，或在合理归并的主章节中使用明确的 H3 标题，不能只隐含在正文。章节名称、写作目标和检索词必须面向当前项目范围；不得因为知识库可能命中而扩展用户未要求的物业、停车、租户收费、普通楼宇或非能源子系统。证据概览只帮助形成更贴近资料的大纲，不能扩大用户范围；资料不足的章节仍可保留，并在写作阶段标记【待确认】。"""
_WRITE_SYSTEM = """你是技术方案撰写助手。只输出当前章节正文，不输出标题或整篇方案。
只能使用给出的证据；技术参数必须有证据 ID [S数字] 支持。只能引用允许的 [S数字]，不能输出文件名、页码、cache 路径或图片路径。图片只能用允许的 [IMG数字]，仅在确有帮助处插入。资料不足写【待确认】。
必须明确区分：用户输入条件、当前项目已知事实、地方政策参考、同类案例数据、产品能力资料、设计建议和待确认内容。不要生成“待确认项”独立章节、同名标题或加粗的“待确认内容/事项/明确内容”视觉标题；系统会在文末统一汇总，只在相关正文中标记具体待确认事实。异地政策只能称为政策参考，案例参数只能称为案例数据，产品能力只能称为可选能力；行业标准适用性须结合最终系统和并网条件确认。案例的总投资、分项金额或报价明细最多作为一句“案例参考，需独立测算”，不得写入本项目投资估算主体。设计建议必须以“【设计建议】”“建议”“可考虑”或“是否采用需结合待确认参数论证”明确标记，不能把建议、资料能力或案例经验写成本项目既定参数或承诺。除非用户明确要求或与本项目能源计量、负荷调控有直接必要联系且有证据支持，不得扩展停车、车位、租户缴费、物业预付费、水表管理或普通智慧楼宇功能。"""
_REVIEW_SYSTEM = """审查完整技术方案。只输出 JSON：{issues:[{affected_section_ids,issue_type,description,revision_instruction,severity}]}。
检查遗漏、重复、无证据参数、无关引用、矛盾、空话、应标待确认的结论及无关图片；不要重写正文。
特别检查 policy_scope_leakage、case_as_project_fact、product_capability_as_committed、unsupported_project_assumption、standard_applicability_unclear：不得把异地政策、同类案例、产品手册能力或未确认标准适用性写成本项目事实、指标或承诺。案例中的总投资、分项金额或报价明细不得成为“本项目投资估算”主体；最多保留一句“案例参考，需独立测算”，其余删除。还要检查 cross_section_repetition、terminology_inconsistency、transition_break、contradiction、section_boundary_violation、conclusion_mismatch，及 scope_drift（用户未要求的物业、停车、租户收费、水表、预付费或普通智慧楼宇功能应删除或收缩）、uncited_knowledge_fact、unmarked_design_advice 与待确认视觉伪标题；发现时必须给出受影响章节、严重程度和可执行指令。"""
_REVISE_SYSTEM = """只输出修订后的当前章节正文。你收到的是带内部证据 ID 和图片 ID 的原始正文；必须保留或使用允许的 [S数字]/[IMG数字]，不能输出文件名、页码、cache 路径或图片路径。
只能使用给出的证据和图片 ID；不得编造参数，资料不足写【待确认】。不要生成“待确认项”独立章节、同名标题或加粗的“待确认内容/事项/明确内容”视觉标题；系统会在文末统一汇总。必须区分用户输入条件、政策参考、案例数据、产品能力和设计建议；不能把它们写成本项目已确定事实或承诺。任何政策、案例、产品或技术事实必须保留对应证据 ID；设计建议必须明确标记为建议。删除用户未要求且与能源计量或负荷调控没有直接必要联系的物业、停车、租户收费、预付费、水表或普通智慧楼宇功能，不要仅添加免责声明。行业标准适用性须结合最终系统和并网条件确认。"""
_ID_RE = re.compile(r"\[(S\d+|IMG\d+)\]")
_VISIBLE_CITATION_RE = re.compile(r"\[来源:\s*[^\]]+\]")
_RAW_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+\S", re.M)
_BAD_BOLD_HEADING_RE = re.compile(r"^\s*\*\*#{1,6}\s+", re.M)
_BAD_IMAGE_ESCAPE_RE = re.compile(r"!\[[^\]]*\]\\\(")
_BAD_LIST_ESCAPE_RE = re.compile(r"^\s*\d+\\\.\s+", re.M)
_BAD_UNORDERED_LIST_ESCAPE_RE = re.compile(r"^\s*\\-\s+", re.M)
_FINAL_IMAGE_RE = re.compile(r"(?<!\\)!\[[^\]\n]*\]\((assets/[A-Za-z0-9._/-]+)\)")
_BAD_DOUBLE_BOLD_RE = re.compile(r"\*\*\\\*\\\*.+?\\\*\\\*\*\*")
_CONFIRM_RE = re.compile(r"[^\n。！？；]*【待确认】[^\n。！？；]*[。！？；]?")
_CONFIRMATION_PSEUDO_LABEL_RE = re.compile(
    r"(?m)^\s*(?:[-*+]\s+)?\*\*(?:待确认|待明确)(?:内容|事项)\*\*\s*[:：]?\s*"
)
_VISIBLE_SOURCE_RE = re.compile(r"\[来源:\s*([^,\]]+?),\s*第\s*(\d+)\s*页\]")


def _json(raw: str) -> dict:
    cleaned = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.I | re.S)
    if fenced:
        cleaned = fenced.group(1)
    try:
        value, _end = json.JSONDecoder().raw_decode(cleaned[cleaned.index("{"):])
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("LLM 未返回可解析的 JSON 对象") from exc
    if not isinstance(value, dict):
        raise ValueError("LLM JSON 顶层必须是对象")
    return value


def _plan_from(raw: str) -> ProposalPlan:
    value = _json(raw)
    title, sections = value.get("title"), value.get("sections")
    if not isinstance(title, str) or not title.strip() or not isinstance(sections, list) or not 3 <= len(sections) <= 8:
        raise ValueError("大纲必须有标题和 3 至 8 个章节")
    planned: list[SectionPlan] = []
    seen: set[str] = set()
    for item in sections:
        if not isinstance(item, dict):
            raise ValueError("章节必须是对象")
        section_id = item.get("section_id")
        queries = item.get("retrieval_queries")
        if (not isinstance(section_id, str) or not section_id or section_id in seen
                or not all(isinstance(item.get(key), str) and item[key].strip() for key in ("heading", "writing_goal"))
                or not isinstance(item.get("level"), int) or not isinstance(queries, list)
                or not queries or not all(isinstance(query, str) and query.strip() for query in queries)):
            raise ValueError("章节字段不符合要求")
        seen.add(section_id)
        image_intent = item.get("image_intent", "optional")
        if image_intent not in {"none", "optional", "preferred"}:
            raise ValueError("章节 image_intent 必须为 none、optional 或 preferred")
        planned.append(SectionPlan(section_id, item["heading"].strip(), item["level"], item["writing_goal"].strip(), list(dict.fromkeys(queries)), image_intent))
    return ProposalPlan(title.strip(), planned)


def _review_from(raw: str, valid_sections: set[str]) -> ReviewParseResult:
    try:
        issues = _json(raw).get("issues")
    except ValueError as exc:
        raise ReviewSchemaError(str(exc), repairable=True) from exc
    if not isinstance(issues, list):
        raise ReviewSchemaError("审查结果必须包含 issues 列表", repairable=True)
    parsed: list[ReviewIssue] = []
    warnings: list[str] = []
    skipped_advisory = 0
    for index, item in enumerate(issues):
        affected = item.get("affected_section_ids") if isinstance(item, dict) else None
        if affected is None and isinstance(item, dict) and item.get("section_id") in valid_sections:
            affected = [item["section_id"]]
        instruction = item.get("revision_instruction", item.get("instruction")) if isinstance(item, dict) else None
        description = item.get("description", instruction) if isinstance(item, dict) else None
        severity = item.get("severity", "warning") if isinstance(item, dict) else None
        explicit_severity = item.get("severity") if isinstance(item, dict) else None
        valid = (isinstance(item, dict) and isinstance(affected, list) and bool(affected)
                 and all(section in valid_sections for section in affected)
                 and isinstance(item.get("issue_type"), str) and bool(item["issue_type"].strip())
                 and isinstance(instruction, str) and bool(instruction.strip())
                 and isinstance(description, str) and bool(description.strip())
                 and isinstance(severity, str) and bool(severity.strip()))
        if not valid:
            keys = sorted(item) if isinstance(item, dict) else []
            normalized_severity = explicit_severity.strip().lower() if isinstance(explicit_severity, str) else ""
            detail = f"index={index}; fields={','.join(keys) or 'non_object'}"
            # Only an explicitly classified advisory item may be omitted.  An
            # unknown, blocking, or critical issue must fail visibly instead.
            if normalized_severity in {"advisory", "info", "warning"}:
                skipped_advisory += 1
                warnings.append(f"review_advisory_item_skipped：{detail}")
                continue
            raise ReviewSchemaError(f"审查问题字段不符合要求 ({detail})", repairable=False)
        parsed.append(ReviewIssue(list(dict.fromkeys(affected)), item["issue_type"], description, instruction, severity))
    return ReviewParseResult(parsed, len(issues), skipped_advisory, warnings)


def _evidence_role(hit: SearchHit) -> str:
    """Use explicit retrieval metadata first; conservative inference is only a fallback."""
    explicit = str(hit.metadata.get("evidence_role", "")).lower()
    if explicit in {"user_input", "policy", "case", "product", "standard", "unknown"}:
        return explicit
    source = f"{hit.source_file} {hit.title} {hit.content[:300]}"
    if re.search(r"政策|实施方案|通知|办法", source):
        return "policy"
    if re.search(r"案例|业绩|项目介绍", source):
        return "case"
    if re.search(r"产品手册|产品资料|说明书", source):
        return "product"
    if re.search(r"GB/?T|标准|规程|导则", source, re.I):
        return "standard"
    return "unknown"


def _evidence(hits: list[SearchHit]) -> list[EvidenceItem]:
    result, seen = [], set()
    for hit in hits:
        identity = (hit.source_file, hit.page_number)
        if identity not in seen:
            seen.add(identity)
            result.append(EvidenceItem(f"S{len(result) + 1}", hit.source_file, hit.page_number, hit.content, hit.images, hit.title, _evidence_role(hit)))
    return result


def _pre_retrieval_queries(request: str) -> list[str]:
    """Use the request plus bounded local terms; this never calls an LLM."""
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9-]{1,}|[\u4e00-\u9fff]{2,12}", request)
    terms = list(dict.fromkeys(x for x in tokens if x not in {"方案", "项目", "建设", "需要", "用户"}))[:8]
    return list(dict.fromkeys([request, *terms]))


def _evidence_overview(hits: list[SearchHit], *, max_pages: int = 16, max_chars: int = 6000) -> str:
    """Bounded, portable planning context: never source paths or full page text."""
    lines: list[str] = []
    seen: set[tuple[str, int]] = set()
    used = 0
    for hit in hits:
        identity = (hit.source_file, hit.page_number)
        if identity in seen or len(lines) >= max_pages:
            continue
        seen.add(identity)
        fields = extract_retrieval_fields(hit.title, hit.content)
        summary = re.sub(r"\s+", " ", hit.content).strip()[:240]
        line = (f"- 文件：{_display_name(hit.source_file)}；页码：{hit.page_number}；页标题：{hit.title[:80] or '无'}；"
                f"章节：{fields.section_title[:80] or '无'}；关键词：{','.join(fields.keywords[:8]) or '无'}；"
                f"实体：{','.join(fields.entities[:6]) or '无'}；参数：{','.join(fields.parameters[:6]) or '无'}；"
                f"摘要：{summary}；图片：{len(hit.images)} 张")
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


def _document_brief(plan: ProposalPlan, request: str, index: int) -> str:
    current = plan.sections[index]
    outline = "；".join(f"{item.heading}（{item.writing_goal}）" for item in plan.sections)
    adjacent = []
    if index:
        adjacent.append(f"前一章：{plan.sections[index - 1].heading}（{plan.sections[index - 1].writing_goal}）")
    if index + 1 < len(plan.sections):
        adjacent.append(f"后一章：{plan.sections[index + 1].heading}（{plan.sections[index + 1].writing_goal}）")
    return (f"文档标题：{plan.title}\n用户需求与目标读者：{request}\n全部章节及职责：{outline}\n"
            f"当前位置：第 {index + 1}/{len(plan.sections)} 章 {current.heading}（{current.writing_goal}）；插图意图：{current.image_intent}\n"
            f"{'；'.join(adjacent) or '无相邻章节'}\n全局术语和口径：{', '.join(_pre_retrieval_queries(request)[1:]) or '以用户需求为准'}\n"
            "范围边界：仅覆盖用户明确需求；不要重复其他章节职责，相邻章节正文不是新证据。")


def _display_name(file_name: str) -> str:
    """Show only a portable source basename; identities retain the original key."""
    return Path(file_name.replace("\\", "/")).name


def _caption(item: EvidenceItem, image: dict) -> str:
    value = str(image.get("caption") or image.get("description") or item.title or "").strip()
    if value and value != "资料图片":
        return value
    # A page title is a conservative description when MinerU did not supply a caption.
    if item.title.strip():
        return item.title.strip()
    for line in item.text.splitlines():
        heading = re.sub(r"^[#*\s]+|[*\s]+$", "", line).strip()
        if 4 <= len(heading) <= 80:
            return heading
    return ""


def _image_catalog(evidence: list[EvidenceItem]) -> dict[str, tuple[EvidenceItem, dict]]:
    catalog = {}
    for item in evidence:
        derived_added = False
        for image in item.images:
            has_native_caption = bool(str(image.get("caption") or image.get("description") or "").strip())
            # An unlabeled page can offer one conservatively described figure, never a gallery.
            if not has_native_caption and derived_added:
                continue
            if image.get("path") and _caption(item, image):
                catalog[f"IMG{len(catalog) + 1}"] = (item, image)
                derived_added = derived_added or not has_native_caption
    return catalog


def _evidence_prompt(evidence: list[EvidenceItem], images: dict[str, tuple[EvidenceItem, dict]]) -> str:
    lines = [f"[{item.evidence_id}] 证据角色：{item.role}；文件：{_display_name(item.file_name)}；页码：{item.page_number}\n正文：{item.text}" for item in evidence]
    for image_id, (item, image) in images.items():
        lines.append(f"[{image_id}] 图注：{_caption(item, image)}；来源：{_display_name(item.file_name)}，第{item.page_number}页")
    return "\n\n".join(lines) or "（没有可用证据；必须使用【待确认】。）"


def _normalise_markdown(text: str) -> str:
    """Deterministically repair known structural escapes outside fenced code blocks."""
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines(keepends=True):
        if re.match(r"^\s*(```|~~~)", line):
            in_fence = not in_fence
            lines.append(line)
            continue
        if in_fence:
            lines.append(line)
            continue
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        line = line[:-len(ending)] if ending else line
        # U+00A0 is not a stable indentation character in Markdown renderers.
        line = line.replace("\u00a0", " ")
        line = re.sub(r"^\s*\*\*(#{1,6}\s+.+?)\*\*\s*$", r"\1", line)
        line = re.sub(r"\*\*\\\*\\\*(.+?)\\\*\\\*\*\*", r"**\1**", line)
        line = re.sub(r"^([ \t]*\d+)\\\.\s+", r"\1. ", line)
        line = re.sub(r"^([ \t]*)\\-\s+", r"\1- ", line)
        # This only targets an otherwise complete Markdown image token.
        line = re.sub(r"(!\[[^\]]*\])\\\(([^)]*)\\\)", r"\1(\2)", line)
        lines.append(line + ending)
    return "".join(lines)


def _validate_ids(body: str, evidence: list[EvidenceItem], images: dict[str, tuple[EvidenceItem, dict]]) -> None:
    if _VISIBLE_CITATION_RE.search(body):
        raise ValueError("章节原文不得直接输出可见来源；必须使用 [S数字]")
    if _RAW_IMAGE_RE.search(body):
        raise ValueError("章节原文不得直接输出图片路径；必须使用 [IMG数字]")
    allowed = {item.evidence_id for item in evidence} | set(images)
    unknown = sorted({value for value in _ID_RE.findall(body) if value not in allowed})
    if unknown:
        raise ValueError(f"存在本章不可用的引用或图片 ID：{', '.join(unknown)}")


def _source_path(image_root: Path, stored_path: str) -> Path:
    root = image_root.resolve()
    path = Path(stored_path)
    candidate = path.resolve() if path.is_absolute() else (root / path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ProposalQualityError(f"图片路径越出知识库根目录：{stored_path}")
    if not candidate.is_file():
        raise ProposalQualityError(f"图片文件不存在：{stored_path}")
    return candidate


def _with_draft(draft: _SectionDraft, *, raw_body: str | None = None,
                usable_images: dict[str, tuple[EvidenceItem, dict]] | None = None,
                selected_images: dict[str, str] | None = None,
                rendered_image_blocks: list[dict] | None = None) -> _SectionDraft:
    """Copy a section without losing its canonical image ownership records."""
    return _SectionDraft(
        draft.plan,
        draft.raw_body if raw_body is None else raw_body,
        draft.evidence,
        draft.images,
        draft.usable_images if usable_images is None else usable_images,
        draft.selected_images if selected_images is None else selected_images,
        draft.rendered_image_blocks if rendered_image_blocks is None else rendered_image_blocks,
    )


def _asset_plan(sections: dict[str, _SectionDraft], image_root: Path) -> dict[tuple[str, str], _ImageAsset]:
    assets: dict[tuple[str, str], _ImageAsset] = {}
    number = 1
    for section_id, draft in sections.items():
        for image_id in _ID_RE.findall(draft.raw_body):
            if not image_id.startswith("IMG") or (section_id, image_id) in assets:
                continue
            item, image = draft.images[image_id]
            source = _source_path(image_root, str(image["path"]))
            suffix = source.suffix.lower()
            if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
                raise ProposalQualityError(f"不支持的图片格式：{source.name}")
            assets[(section_id, image_id)] = _ImageAsset(
                section_id, image_id, draft.selected_images.get(image_id, "llm"), source,
                f"assets/image-{number:03d}{suffix}", _caption(item, image), item,
            )
            number += 1
    return assets


def _drop_unavailable_images(sections: dict[str, _SectionDraft], image_root: Path, warnings: list[str]) -> None:
    """Remove only broken image markers; their surrounding readable text remains."""
    for section_id, draft in list(sections.items()):
        body = draft.raw_body
        for image_id in _ID_RE.findall(body):
            if not image_id.startswith("IMG"):
                continue
            try:
                _source_path(image_root, str(draft.images[image_id][1]["path"]))
            except (KeyError, ProposalQualityError) as exc:
                body = body.replace(f"[{image_id}]", "")
                warnings.append(f"图片 {image_id} 已移除：{exc}")
        if body != draft.raw_body:
            sections[section_id] = _with_draft(draft, raw_body=body)


def _image_record(section_id: str, image_id: str, item: EvidenceItem, image: dict, *,
                  selected_by: str = "", reason: str = "") -> dict:
    """Portable image telemetry; never put a cache path in a user-visible run log."""
    return {"section_id": section_id, "candidate_section_id": section_id,
            "intended_section_id": section_id, "image_id": image_id,
            "source_file": _display_name(item.file_name), "page_number": item.page_number,
            "caption": _caption(item, image), "selected_by": selected_by, "reason": reason}


def _image_candidates(sections: dict[str, _SectionDraft], image_root: Path, removals: list[dict]) -> list[dict]:
    candidates: list[dict] = []
    for section_id, draft in sections.items():
        for image_id, (item, image) in draft.images.items():
            try:
                _source_path(image_root, str(image["path"]))
            except (KeyError, ProposalQualityError) as exc:
                removals.append(_image_record(section_id, image_id, item, image, reason=str(exc)))
                continue
            candidates.append(_image_record(section_id, image_id, item, image))
    return candidates


def _image_priority(section: SectionPlan) -> int:
    heading = f"{section.heading} {section.writing_goal}"
    if re.search(r"总体|架构", heading):
        return 0
    if re.search(r"源网荷储|配电|变电", heading):
        return 1
    if re.search(r"虚拟电厂|调度", heading):
        return 2
    return 3


def _effective_image_intent(section: SectionPlan) -> str:
    """Older planners may omit image_intent; core energy chapters are preferred by default."""
    return "preferred" if section.image_intent == "optional" and _image_priority(section) < 3 else section.image_intent


def _place_fallback_image(draft: _SectionDraft, image_id: str, item: EvidenceItem, image: dict) -> _SectionDraft:
    """Place a fallback figure after its closest same-page evidence paragraph.

    The image never leaves ``draft``.  Exact evidence identity is the primary
    anchor; caption/section-word overlap merely breaks ties between paragraphs
    which cite that same source page.
    """
    marker = f"[{image_id}]"
    if marker in draft.raw_body:
        return draft
    paragraphs = re.split(r"(\n\s*\n)", draft.raw_body)
    candidates: list[tuple[int, int]] = []
    needles = set(re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}",
                             f"{draft.plan.heading} {_caption(item, image)}"))
    for index in range(0, len(paragraphs), 2):
        paragraph = paragraphs[index]
        cited = [evidence for evidence in _ID_RE.findall(paragraph) if evidence.startswith("S")]
        same_page = any(
            evidence.evidence_id == evidence_id
            and evidence.file_name == item.file_name
            and evidence.page_number == item.page_number
            for evidence_id in cited for evidence in draft.evidence
        )
        if same_page:
            words = set(re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", paragraph))
            candidates.append((len(needles & words), index))
    if candidates:
        _score, index = max(candidates, key=lambda value: (value[0], -value[1]))
        paragraphs[index] = f"{paragraphs[index].rstrip()}\n\n{marker}"
        return _with_draft(draft, raw_body="".join(paragraphs))
    return _with_draft(draft, raw_body=f"{draft.raw_body.rstrip()}\n\n{marker}")


def _fallback_image_relevance(section: SectionPlan, item: EvidenceItem, image: dict) -> int:
    """Prefer a caption/page-title that shares meaningful terms with its section."""
    def terms(value: str) -> set[str]:
        ascii_terms = re.findall(r"[A-Za-z0-9]{2,}", value.lower())
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]", value))
        return set(ascii_terms) | {chinese[index:index + 2] for index in range(len(chinese) - 1)}
    return len(terms(f"{section.heading} {section.writing_goal}")
               & terms(f"{_caption(item, image)} {item.title}"))


def _apply_image_budget(sections: dict[str, _SectionDraft], image_root: Path, warnings: list[str], telemetry: dict) -> None:
    """Select images by canonical section, covering preferred sections before extras."""
    removals: list[dict] = telemetry.setdefault("removed_images", [])
    telemetry["image_intents"] = {section_id: _effective_image_intent(draft.plan) for section_id, draft in sections.items()}
    telemetry["raw_image_records"] = [_image_record(section_id, image_id, item, image)
                                        for section_id, draft in sections.items()
                                        for image_id, (item, image) in draft.images.items()]
    candidates = _image_candidates(sections, image_root, removals)
    telemetry["usable_images"] = candidates
    usable_by_section: dict[str, dict[str, tuple[EvidenceItem, dict]]] = {}
    for candidate in candidates:
        section_id, image_id = candidate["section_id"], candidate["image_id"]
        item, image = sections[section_id].images[image_id]
        usable_by_section.setdefault(section_id, {})[image_id] = (item, image)
    for section_id, draft in list(sections.items()):
        sections[section_id] = _with_draft(draft, usable_images=usable_by_section.get(section_id, {}), selected_images={}, rendered_image_blocks=[])
    unavailable = {(item["section_id"], item["image_id"]) for item in removals if item.get("image_id")}
    for section_id, image_id in unavailable:
        draft = sections.get(section_id)
        if draft and f"[{image_id}]" in draft.raw_body:
            sections[section_id] = _with_draft(draft, raw_body=draft.raw_body.replace(f"[{image_id}]", ""))
            warnings.append(f"图片 {image_id} 已移除：候选文件不可用")
    telemetry["candidate_images"] = candidates
    candidate_keys = {(x["section_id"], x["image_id"]) for x in candidates}
    model_selected = {(section_id, image_id)
                      for section_id, draft in sections.items()
                      for image_id in _ID_RE.findall(draft.raw_body)
                      if image_id.startswith("IMG") and (section_id, image_id) in candidate_keys}
    kept: dict[tuple[str, str], str] = {}
    seen_source: set[tuple[str, int, str]] = set()
    per_section: set[str] = set()

    def choose(section_id: str, *, allow_fallback: bool) -> bool:
        """Choose one image without ever changing its owning section_id."""
        if section_id in per_section or len(kept) >= 5:
            return False
        draft = sections[section_id]
        ordered = sorted(
            draft.usable_images.items(),
            key=lambda value: (
                (section_id, value[0]) not in model_selected,
                -_fallback_image_relevance(draft.plan, value[1][0], value[1][1]), value[0],
            ),
        )
        for image_id, (item, image) in ordered:
            by_model = (section_id, image_id) in model_selected
            if not by_model and not allow_fallback:
                continue
            if not by_model and re.search(r"logo|二维码|页眉|装饰", _caption(item, image), re.I):
                continue
            source_key = (item.file_name, item.page_number, str(image.get("path", "")))
            if source_key in seen_source:
                continue
            if not by_model:
                draft = _place_fallback_image(draft, image_id, item, image)
                sections[section_id] = draft
            kept[(section_id, image_id)] = "LLM" if by_model else "code_fallback"
            seen_source.add(source_key)
            per_section.add(section_id)
            return True
        return False

    # Coverage is intentional, not an accidental side effect of a global score:
    # each eligible preferred section is considered before any optional extra.
    preferred_ids = [section_id for section_id, draft in sections.items()
                     if _effective_image_intent(draft.plan) == "preferred"]
    for section_id in preferred_ids:
        choose(section_id, allow_fallback=True)
    for section_id, draft in sorted(sections.items(), key=lambda value: (_image_priority(value[1].plan), value[0])):
        if _effective_image_intent(draft.plan) != "preferred":
            choose(section_id, allow_fallback=False)

    # Remove only unselected model markers.  A code fallback marker belongs to
    # the same section and is retained above, so no full-document append exists.
    for section_id, draft in list(sections.items()):
        body = draft.raw_body
        for image_id in _ID_RE.findall(body):
            if not image_id.startswith("IMG") or (section_id, image_id) not in candidate_keys:
                continue
            if (section_id, image_id) in kept:
                continue
            item, image = draft.images[image_id]
            source_key = (item.file_name, item.page_number, str(image.get("path", "")))
            reason = ("同一章节最多保留一张图片" if section_id in per_section
                      else "同一来源文件、页码和图片不得重复" if source_key in seen_source
                      else "全文图片预算上限为 5" if len(kept) >= 5
                      else "核心章节覆盖优先")
            body = body.replace(f"[{image_id}]", "")
            removals.append(_image_record(section_id, image_id, item, image, reason=reason))
        selected_for_section = {image_id: selected_by for (owner, image_id), selected_by in kept.items() if owner == section_id}
        sections[section_id] = _with_draft(sections[section_id], raw_body=body, selected_images=selected_for_section)
    telemetry["selected_images"] = [
        _image_record(section_id, image_id, item, image, selected_by=selected_by)
        for (section_id, image_id), selected_by in kept.items()
        for item, image in [sections[section_id].images[image_id]]
    ]
    for section_id, draft in sections.items():
        if _effective_image_intent(draft.plan) == "preferred" and any(item["section_id"] == section_id for item in candidates) and not any(item["section_id"] == section_id for item in telemetry["selected_images"]):
            warnings.append(f"图片：章节“{draft.plan.heading}”存在合格候选但未插入图片")
    if not candidates:
        warnings.append("no_usable_images：全文没有合格图片候选")
    elif len(candidates) >= 3 and not telemetry["selected_images"]:
        telemetry["image_pipeline_failure"] = True
    elif len(telemetry["selected_images"]) < 3:
        warnings.append("图片覆盖不足：存在足够合格候选但全文未达到 3 张目标")


def _role_and_scope_errors(sections: dict[str, _SectionDraft]) -> list[str]:
    errors: list[str] = []
    commitment = re.compile(r"本项目(?:拟建设|将|必须|应当|应|需|具备|实现)|既定(?:指标|参数)|硬性承诺")
    boundary = re.compile(r"参考|仅供|可考虑|【设计建议】|用户输入条件|【待确认】")
    building = re.compile(r"水表|水电|空调|办公时段|室内温湿度|无人空转|物业收费|租户收费")
    for draft in sections.values():
        for paragraph in re.split(r"\n\s*\n", draft.raw_body):
            ids = [value for value in _ID_RE.findall(paragraph) if value.startswith("S")]
            roles = {item.role for item in draft.evidence if item.evidence_id in ids}
            if commitment.search(paragraph) and roles & {"policy", "case", "product", "standard", "unknown"} and not boundary.search(paragraph):
                errors.append(f"证据角色越界：章节“{draft.plan.heading}”将 {', '.join(sorted(roles))} 证据写为项目承诺")
            if "case" in roles and re.search(r"投资估算|投资", draft.plan.heading) and len(re.findall(r"\d+(?:\.\d+)?\s*万元", paragraph)) >= 2:
                errors.append(f"案例金额污染：章节“{draft.plan.heading}”包含多项案例投资金额，不能作为本项目估算主体")
            if building.search(paragraph) and (draft.plan.level <= 2 or re.search(r"核心功能|核心模块", draft.plan.heading)) and "可选扩展（非本期核心范围）" not in paragraph:
                errors.append(f"范围越界：章节“{draft.plan.heading}”将非工业园区能源核心内容作为核心模块")
    return errors


def _render_body(raw_body: str, draft: _SectionDraft, assets: dict[tuple[str, str], _ImageAsset], *, preview: bool) -> tuple[str, set[str], list[dict]]:
    raw_body = _normalise_markdown(raw_body)
    _validate_ids(raw_body, draft.evidence, draft.images)
    evidence_by_id = {item.evidence_id: item for item in draft.evidence}
    used: set[str] = set()
    rendered_blocks: list[dict] = []

    def replace(match: re.Match[str]) -> str:
        value = match.group(1)
        if value.startswith("S"):
            item = evidence_by_id[value]
            used.add(value)
            return f"[来源: {_display_name(item.file_name)}, 第 {item.page_number} 页]"
        asset = assets[(draft.plan.section_id, value)]
        if asset.section_id != draft.plan.section_id:
            raise ProposalQualityError(
                f"image_placement_mismatch：图片 {asset.image_id} 的章节归属与当前章节不一致"
            )
        used.add(asset.evidence.evidence_id)
        if preview:
            return f"*图：{asset.caption}。来源：{_display_name(asset.evidence.file_name)}，第{asset.evidence.page_number}页。*"
        rendered_blocks.append({"section_id": asset.section_id, "image_id": asset.image_id,
                                "relative_path": asset.relative_path, "selected_by": asset.selected_by})
        return f"![{asset.caption}]({asset.relative_path})\n\n图片来源：{_display_name(asset.evidence.file_name)}，第 {asset.evidence.page_number} 页"

    return _ID_RE.sub(replace, raw_body).strip(), used, rendered_blocks


def _confirmation_heading(line: str) -> bool:
    match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
    if not match:
        return False
    value = re.sub(r"^(?:\d+(?:\.\d+)*\.?|第?[一二三四五六七八九十百]+)[、.．]?\s*", "", match.group(1)).strip()
    return value in {"待确认项", "待确认内容", "待确认事项", "待明确内容", "待明确事项"}


def _extract_confirmation_section(body: str) -> tuple[str, str]:
    """Remove a model-owned confirmation section and retain its content for aggregation."""
    body = _CONFIRMATION_PSEUDO_LABEL_RE.sub("", body)
    kept: list[str] = []
    extracted: list[str] = []
    in_confirmation = False
    for line in body.splitlines():
        if _confirmation_heading(line):
            in_confirmation = True
            continue
        if in_confirmation and _HEADING_RE.match(line):
            in_confirmation = False
        (extracted if in_confirmation else kept).append(line)
    return "\n".join(kept).strip(), "\n".join(extracted).strip()


def _confirmation_items(body: str) -> list[str]:
    items: list[str] = []
    for match in _CONFIRM_RE.findall(body):
        item = re.sub(r"\*\*", "", match)
        item = re.sub(r"^(?:(?:待确认|待明确)(?:内容|事项)?|待确认项)\s*[:：]", "", item.strip())
        item = re.sub(r"\s+", " ", item).strip(" -；。")
        subject = item.replace("【待确认】", "").strip(" ：:，,。")
        if (not subject
                or re.fullmatch(r"(?:具体)?(?:参数|内容|事项)(?:待确认|需确认)?", subject)):
            continue
        key = re.sub(r"[【】待确认\s，,。：:；;]", "", item)
        duplicate = next((old for old in items if key in re.sub(r"[【】待确认\s，,。：:；;]", "", old)
                          or re.sub(r"[【】待确认\s，,。：:；;]", "", old) in key), None)
        if duplicate is None:
            items.append(item)
        elif len(key) > len(re.sub(r"[【】待确认\s，,。：:；;]", "", duplicate)):
            items[items.index(duplicate)] = item
    return items


def _confirmation_section_items(body: str) -> list[str]:
    """Treat non-empty list/prose lines beneath a removed confirmation heading as marked items."""
    marked = []
    for line in body.splitlines():
        value = re.sub(r"^\s*(?:[-*+]\s+|\d+[.]\s+)?", "", line).strip()
        if value:
            marked.append(value if "【待确认】" in value else f"{value.rstrip('。！？；')}【待确认】")
    return _confirmation_items("\n".join(marked))


_REQUEST_CONFIRMATION_ITEMS = (
    ("园区面积", "园区面积"),
    ("负荷曲线", "负荷曲线"),
    ("变压器容量", "变压器容量"),
    ("配电电压", "配电电压"),
    ("光伏可安装面积", "光伏可安装面积"),
    ("设备容量", "设备容量"),
    ("投资预算", "投资预算"),
    ("电价", "电价"),
    ("并网条件", "并网条件"),
    ("建设周期", "建设周期"),
)

_CONFIRMATION_CATEGORIES = (
    ("园区边界与负荷", (("园区面积", "园区面积【待确认】"), ("负荷曲线", "负荷曲线【待确认】"), ("可调负荷", "可调负荷规模及响应能力【待确认】"))),
    ("配电与并网", (("变压器容量", "变压器容量【待确认】"), ("配电电压", "配电电压【待确认】"), ("接线", "现状接线及供配电边界【待确认】"), ("并网条件", "并网条件【待确认】"))),
    ("源储充与设备资源", (("光伏可安装面积", "光伏可安装面积【待确认】"), ("设备容量", "设备容量【待确认】"), ("储能", "储能及可调资源配置【待确认】"))),
    ("电价与市场条件", (("电价", "峰、平、谷电价及基本电费计费方式【待确认】"), ("基本电费", "峰、平、谷电价及基本电费计费方式【待确认】"))),
    ("投资与实施边界", (("投资预算", "投资预算【待确认】"), ("建设周期", "建设周期【待确认】"), ("分期", "分期实施与运营边界【待确认】"))),
)


def _requested_confirmation_items(request: str) -> list[str]:
    """Use an explicit, complete user parameter list as the single final checklist."""
    items = [label for needle, label in _REQUEST_CONFIRMATION_ITEMS if needle in request]
    return [f"{item}【待确认】" for item in items] if len(items) == len(_REQUEST_CONFIRMATION_ITEMS) else []


def _confirmation_checklist(request: str, confirmations: list[str]) -> list[tuple[str, list[str]]]:
    """Convert repeated prose markers into stable, short business questions."""
    source = f"{request}\n{' '.join(confirmations)}"
    grouped: list[tuple[str, list[str]]] = []
    for heading, candidates in _CONFIRMATION_CATEGORIES:
        items: list[str] = []
        for needle, label in candidates:
            if needle in source and label not in items:
                items.append(label)
        if items:
            grouped.append((heading, items))
    grouped_by_heading = dict(grouped)
    # The final checklist is a stable project questionnaire, not a copy of prose markers.
    return [(heading, grouped_by_heading.get(heading, [candidates[0][1]]))
            for heading, candidates in _CONFIRMATION_CATEGORIES]


def _render(plan: ProposalPlan, sections: dict[str, _SectionDraft], *, image_root: Path, request: str = "", preview: bool = False) -> tuple[str, dict[tuple[str, str], _ImageAsset]]:
    assets = _asset_plan(sections, image_root)
    lines = [f"# {plan.title}", ""]
    used_evidence: dict[tuple[str, int], EvidenceItem] = {}
    confirmations: list[str] = []
    for number, section in enumerate(plan.sections, 1):
        draft = sections.get(section.section_id)
        if not draft:
            continue
        body, used, rendered_blocks = _render_body(draft.raw_body, draft, assets, preview=preview)
        sections[section.section_id] = _with_draft(draft, rendered_image_blocks=rendered_blocks)
        duplicate_title = re.compile(rf"(?m)^\s*第\s*{number}\s*章\s*{re.escape(section.heading)}\s*$")
        body = duplicate_title.sub("", body).strip()
        body, section_confirmations = _extract_confirmation_section(body)
        lines.extend([f"## {number}. {section.heading}", "", body, ""])
        for item in draft.evidence:
            if item.evidence_id in used:
                used_evidence[(item.file_name, item.page_number)] = item
        confirmations.extend(_confirmation_items(body))
        confirmations.extend(_confirmation_section_items(section_confirmations))
    checklist = _confirmation_checklist(request, confirmations)
    if checklist:
        lines.extend(["## 待确认项", ""])
        for heading, items in checklist:
            lines.extend([f"### {heading}", ""])
            lines.extend(f"- {item}" for item in items)
            lines.append("")
        lines.append("")
    lines.extend(["## 来源与依据", ""])
    if used_evidence:
        grouped: dict[str, list[int]] = {}
        for item in used_evidence.values():
            grouped.setdefault(_display_name(item.file_name), []).append(item.page_number)
        lines.extend(f"- {name}：第 {', '.join(map(str, sorted(set(pages))))} 页" for name, pages in grouped.items())
    else:
        lines.append("（本方案没有使用可引用资料。）")
    return "\n".join(lines).rstrip() + "\n", assets


def _strict_image_links(markdown: str) -> list[str]:
    """Count only real, stable assets Markdown tokens; escaped lookalikes do not count."""
    return [match.group(1) for match in _FINAL_IMAGE_RE.finditer(markdown)]


def _source_manifest(markdown: str, assets: dict[tuple[str, str], _ImageAsset]) -> dict:
    """Build a portable stable source/page and figure provenance sidecar."""
    source_keys: list[tuple[str, int]] = []
    for filename, page in _VISIBLE_SOURCE_RE.findall(markdown):
        key = (_display_name(filename.strip()), int(page))
        if key not in source_keys:
            source_keys.append(key)
    images: list[dict] = []
    for asset in assets.values():
        key = (_display_name(asset.evidence.file_name), asset.evidence.page_number)
        if key not in source_keys:
            source_keys.append(key)
        images.append({
            "figure_id": asset.image_id,
            "image_id": asset.image_id,
            "source_id": f"S{source_keys.index(key) + 1}",
            "source_file": key[0],
            "page": key[1],
            "section_id": asset.section_id,
            "caption": asset.caption,
            "asset_path": asset.relative_path,
            "selected_by": asset.selected_by,
        })
    return {
        "schema_version": 1,
        "sources": [{"source_id": f"S{i + 1}", "filename": name, "pages": [page]}
                    for i, (name, page) in enumerate(source_keys)],
        "images": images,
    }


def _image_placement_report(markdown: str, plan: ProposalPlan,
                            assets: dict[tuple[str, str], _ImageAsset]) -> dict:
    """Re-read rendered Markdown and verify each asset remains under its own H2.

    The H2 mapping is generated from the plan's canonical IDs, never inferred
    with a title substring match or a full-document search.
    """
    section_headers = {f"## {number}. {section.heading}": section.section_id
                       for number, section in enumerate(plan.sections, 1)}
    by_path = {asset.relative_path: asset for asset in assets.values()}
    selected_by_section: dict[str, list[dict]] = {section.section_id: [] for section in plan.sections}
    final_by_section: dict[str, list[dict]] = {section.section_id: [] for section in plan.sections}
    for asset in assets.values():
        selected_by_section.setdefault(asset.section_id, []).append(
            {"image_id": asset.image_id, "relative_path": asset.relative_path,
             "selected_by": asset.selected_by}
        )
    actual_section_id: str | None = None
    final_images: list[dict] = []
    mismatches: list[dict] = []
    for line in markdown.splitlines():
        if line in section_headers:
            actual_section_id = section_headers[line]
            continue
        for match in _FINAL_IMAGE_RE.finditer(line):
            relative_path = match.group(1)
            asset = by_path.get(relative_path)
            record = {
                "relative_path": relative_path,
                "image_id": asset.image_id if asset else "",
                "intended_section_id": asset.section_id if asset else "",
                "actual_section_id": actual_section_id,
                "selected_by": asset.selected_by if asset else "",
            }
            final_images.append(record)
            if actual_section_id:
                final_by_section.setdefault(actual_section_id, []).append(record)
            if asset is None or actual_section_id != asset.section_id:
                mismatches.append(record)
    coverage = {
        section.section_id: bool(final_by_section.get(section.section_id))
        for section in plan.sections
        if _effective_image_intent(section) == "preferred"
    }
    return {
        "selected_images_by_section": selected_by_section,
        "final_images_by_section": final_by_section,
        "final_images": final_images,
        "image_section_coverage": coverage,
        "image_placement_mismatches": mismatches,
    }


def _requested_headings(request: str) -> list[str]:
    """Extract explicit numbered requirements only; do not mistake ordinary prose for headings."""
    requirements = []
    for value in re.findall(r"(?m)^\s*\d+[.、]\s*([^\n；;]+)", request):
        value = value.strip().rstrip("。；;")
        if value and value not in requirements:
            requirements.append(value)
    return requirements


def _quality_gate(markdown: str, output_path: Path, assets: dict[tuple[str, str], _ImageAsset], request: str) -> list[str]:
    errors = []
    headings = [line.lstrip("#").strip() for line in markdown.splitlines() if _HEADING_RE.match(line)]
    h1_lines = [line for line in markdown.splitlines() if re.match(r"^#\s+\S", line)]
    if not markdown.startswith("# ") or len(h1_lines) != 1 or not headings:
        errors.append("缺少合法 H1 标题")
    if (_BAD_BOLD_HEADING_RE.search(markdown) or _BAD_IMAGE_ESCAPE_RE.search(markdown)
            or _BAD_LIST_ESCAPE_RE.search(markdown) or _BAD_UNORDERED_LIST_ESCAPE_RE.search(markdown)
            or _BAD_DOUBLE_BOLD_RE.search(markdown)):
        errors.append("存在无效的结构化 Markdown 转义")
    if _ID_RE.search(markdown):
        errors.append("残留内部证据或图片 ID")
    if ("cache/" in markdown or "cache\\" in markdown or re.search(r"[A-Za-z]:\\", markdown)
            or re.search(r"(?:^|[\s(])/(?:home|tmp|Users|var|mnt)/", markdown)):
        errors.append("显示了内部缓存路径或机器绝对路径")
    visible = bool(_VISIBLE_CITATION_RE.search(markdown))
    source_part = markdown.split("## 来源与依据", 1)[-1]
    if visible and "（本方案没有使用可引用资料。）" in source_part:
        errors.append("正文存在引用但来源列表为空")
    confirmation_headings = [line for line in markdown.splitlines() if _confirmation_heading(line)]
    if len(confirmation_headings) > 1 or any(line != "## 待确认项" for line in confirmation_headings):
        errors.append("存在重复或非标准的待确认项标题")
    if any(line.strip() in {"- 【待确认】", "- 【待确认】。", "- 待确认内容：", "- 待确认事项："}
           for line in markdown.splitlines()):
        errors.append("存在空待确认项")
    if _CONFIRMATION_PSEUDO_LABEL_RE.search(markdown):
        errors.append("正文残留待确认视觉伪标题")
    required_confirmations = _requested_confirmation_items(request)
    if required_confirmations:
        confirmation_part = markdown.split("## 待确认项", 1)[-1].split("## 来源与依据", 1)[0]
        rendered_confirmations = [line.strip()[2:].strip() for line in confirmation_part.splitlines() if line.strip().startswith("- ")]
        if set(rendered_confirmations) != set(required_confirmations) or len(rendered_confirmations) != len(set(rendered_confirmations)):
            errors.append("待确认项未完整规范覆盖用户明确参数")
    for asset in assets.values():
        if not asset.source.is_file():
            errors.append(f"图片文件不存在：{asset.relative_path}")
    requested = _requested_headings(request)
    missing = [item for item in requested if not any(item in heading for heading in headings)]
    if missing:
        errors.append(f"用户明确要求的章节缺少标题覆盖：{', '.join(missing)}")
    return errors


def _quality_gate_diagnostics(
    markdown: str, *, output_path: Path, gate_warnings: list[str], fatal_errors: list[str],
    placement: dict,
) -> dict:
    """Return a prompt-free snapshot of the pre-publication quality decision.

    The final Markdown is deliberately kept in memory until the gate passes. A
    failing run must still be diagnosable, but logging its body would turn a run
    log into an accidental proposal copy. Record only deterministic checks and
    aggregate counts instead.
    """
    headings = [line for line in markdown.splitlines() if _HEADING_RE.match(line)]
    citations = _VISIBLE_CITATION_RE.findall(markdown)
    figures = _strict_image_links(markdown)
    confirmations = _confirmation_items(markdown)
    role_prefixes = ("证据角色越界", "案例金额污染", "范围越界")
    warning_rule_prefixes = (
        ("缺少合法 H1 标题", "missing_valid_h1"),
        ("存在无效的结构化 Markdown 转义", "invalid_markdown_structure"),
        ("残留内部证据或图片 ID", "internal_ids"),
        ("显示了内部缓存路径或机器绝对路径", "internal_paths"),
        ("正文存在引用但来源列表为空", "citation_without_sources"),
        ("存在重复或非标准的待确认项标题", "noncanonical_confirmation_heading"),
        ("存在空待确认项", "empty_confirmation_item"),
        ("正文残留待确认视觉伪标题", "confirmation_pseudo_label"),
        ("待确认项未完整规范覆盖用户明确参数", "missing_requested_confirmations"),
        ("图片文件不存在", "missing_image_file"),
        ("用户明确要求的章节缺少标题覆盖", "missing_requested_heading"),
        ("最终 Markdown 有效图片链接与资产计划数量不一致", "image_link_asset_count_mismatch"),
        ("图片章节覆盖不足", "insufficient_image_section_coverage"),
        ("图片：章节", "preferred_section_missing_image"),
    )
    warning_checks = [
        {"rule": next((rule for prefix, rule in warning_rule_prefixes if warning.startswith(prefix)), "markdown_quality_violation"),
         "expected": "absent", "actual": "present", "passed": False}
        for warning in gate_warnings
    ]
    checks: list[dict[str, object]] = [
        {"rule": "final_markdown_quality_warnings", "expected": 0,
         "actual": len(gate_warnings), "passed": not gate_warnings},
        {"rule": "role_and_scope_errors", "expected": 0,
         "actual": len([item for item in fatal_errors if item.startswith(role_prefixes)]),
         "passed": not any(item.startswith(role_prefixes) for item in fatal_errors)},
        {"rule": "image_pipeline_failure", "expected": False,
         "actual": any(item.startswith("image_pipeline_failure") for item in fatal_errors),
         "passed": not any(item.startswith("image_pipeline_failure") for item in fatal_errors)},
        {"rule": "image_placement_mismatch", "expected": 0,
         "actual": len(placement["image_placement_mismatches"]),
         "passed": not placement["image_placement_mismatches"]},
        {"rule": "internal_ids_in_final_markdown", "expected": 0,
         "actual": len(_ID_RE.findall(markdown)), "passed": not _ID_RE.search(markdown)},
        {"rule": "internal_paths_in_final_markdown", "expected": False,
         "actual": bool("cache/" in markdown or "cache\\" in markdown or re.search(r"[A-Za-z]:\\", markdown)),
         "passed": not ("cache/" in markdown or "cache\\" in markdown or re.search(r"[A-Za-z]:\\", markdown))},
    ] + warning_checks
    return {
        "event": "quality_gate_checks",
        "checked_artifact": f"in_memory_final_markdown_prepublication:{output_path.name}",
        "draft_character_count": len(markdown),
        "heading_count": len(headings),
        "citation_count": len(citations),
        "figure_count": len(figures),
        "pending_confirmation_count": len(confirmations),
        "quality_gate_checks": checks,
        "quality_gate_failed_checks": [check for check in checks if not check["passed"]],
        # Messages are deterministic rule results, never model output,
        # prompts, credentials, or Markdown body text.
        "quality_gate_warning_messages": gate_warnings,
        "quality_gate_error_messages": fatal_errors,
    }


def _sync_assets(stage_root: Path, assets: dict[tuple[str, str], _ImageAsset]) -> list[dict]:
    """Copy into a private staging directory before touching published assets."""
    copied: list[dict] = []
    for asset in assets.values():
        destination = stage_root / asset.relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset.source, destination)
        if not destination.is_file():
            raise ProposalQualityError(f"图片复制后不存在：{asset.relative_path}")
        copied.append({"section_id": asset.section_id, "intended_section_id": asset.section_id,
                       "image_id": asset.image_id, "selected_by": asset.selected_by,
                       "relative_path": asset.relative_path,
                       "source_file": _display_name(asset.evidence.file_name),
                       "page_number": asset.evidence.page_number, "caption": asset.caption})
    return copied


def _final_markdown(plan: ProposalPlan, sections: dict[str, _SectionDraft], *, image_root: Path, output_path: Path, request: str) -> tuple[str, dict[tuple[str, str], _ImageAsset], list[str], dict]:
    """Render, deterministically normalize, and classify a readable review draft."""
    markdown, assets = _render(plan, sections, image_root=image_root, request=request)
    markdown = _normalise_markdown(markdown)
    warnings = _quality_gate(markdown, output_path, assets, request)
    placement = _image_placement_report(markdown, plan, assets)
    image_links = _strict_image_links(markdown)
    if len(image_links) != len(assets):
        warnings.append("最终 Markdown 有效图片链接与资产计划数量不一致")
    if len(image_links) >= 3 and len({item["actual_section_id"] for item in placement["final_images"]}) == 1:
        warnings.append("图片章节覆盖不足：三张或以上图片全部位于同一章节")
    for section_id, covered in placement["image_section_coverage"].items():
        draft = sections[section_id]
        if draft.usable_images and not covered:
            warnings.append(f"图片：章节“{draft.plan.heading}”存在合格候选但最终无图")
    if warnings:
        markdown = _DRAFT_NOTICE + markdown
    return markdown, assets, warnings, placement


def diagnose_markdown_proposal(path: Path, output_path: Path | None = None) -> Path:
    """Create a read-only, portable diagnostic report for an already-rendered proposal."""
    markdown = path.read_text(encoding="utf-8")
    output_path = output_path or path.with_name("proposal.diagnosis.md")
    issues = _quality_gate(markdown, path, {}, "")
    normalised = _normalise_markdown(markdown)
    image_links = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown)
    lines = ["# Proposal 诊断报告", "", f"- 文件：{path.name}", "- 诊断方式：只读，不调用检索、LLM 或外部服务。", "",
             "## 结构与质量", ""]
    if markdown != normalised:
        lines.append("- 发现可确定性归一化的 Markdown 结构问题。")
    lines.extend(f"- {item}" for item in issues) if issues else lines.append("- 未发现基础结构质量问题。")
    lines.extend(["", "## 图片链路", "", f"- 最终 Markdown 图片链接：{len(image_links)} 张。"])
    if not image_links:
        lines.append("- 历史产物未包含图片 Markdown；旧日志未记录规划意图、候选、选择或移除原因，无法回溯断点。")
    else:
        for link in image_links:
            lines.append(f"- {link}：{'存在' if (path.parent / link).is_file() else '缺失'}")
    lines.extend(["", "## 待确认项", "", f"- 正文待确认标记：{markdown.count('【待确认】')} 处。",
                  f"- 标准待确认项标题：{len([line for line in markdown.splitlines() if line == '## 待确认项'])} 个。",
                  "- 旧稿的业务归并质量只能依据最终文本判断；下次运行将记录抽取与归并指标。", "",
                  "## 后续可回溯性", "", "- candidate_images：历史运行未记录，无法回溯。",
                  "- selected_images：历史运行未记录，无法回溯。",
                  "- copied_images：历史运行未记录，无法回溯。",
                  "- removed_images / removal_reasons：历史运行未记录，无法回溯。", ""])
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def _write(path: Path, markdown: str, assets: dict[tuple[str, str], _ImageAsset]) -> dict:
    """Publish Markdown and assets together; retain old successful artefacts on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".proposal-stage-", dir=path.parent))
    backup: Path | None = None
    operation = "stage_markdown"
    try:
        staged_markdown = stage / path.name
        staged_markdown.write_text(markdown, encoding="utf-8")
        operation = "sync_assets"
        copied = _sync_assets(stage, assets)
        operation = "validate_staged_markdown"
        rendered = staged_markdown.read_text(encoding="utf-8")
        image_links = _strict_image_links(rendered)
        errors = []
        if _ID_RE.search(rendered):
            errors.append("最终暂存 Markdown 残留内部图片或证据 ID")
        for link in image_links:
            candidate = (stage / link).resolve()
            if not link.startswith("assets/") or ".." in Path(link).parts or not candidate.is_file():
                errors.append(f"最终暂存 Markdown 图片链接无效：{link}")
        staged_assets = stage / "assets"
        staged_files = sorted(path.relative_to(stage).as_posix() for path in staged_assets.rglob("*") if path.is_file()) if staged_assets.exists() else []
        if len(image_links) != len(copied) or set(image_links) != set(staged_files):
            errors.append("最终暂存 Markdown 图片链接、复制图片与 assets 文件数量不一致")
        if errors:
            raise ProposalQualityError("；".join(errors))
        operation = "build_sources_manifest"
        sources_manifest = _source_manifest(rendered, assets)
        staged_sources = stage / "proposal.sources.json"
        operation = "stage_sources_manifest"
        staged_sources.write_text(json.dumps(sources_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for asset in assets.values():
            if not (stage / asset.relative_path).is_file():
                raise ProposalQualityError(f"图片相对路径无法解析：{asset.relative_path}")
        published_assets = path.parent / "assets"
        if published_assets.exists():
            operation = "merge_existing_assets"
            # Preserve all existing user assets by staging a full copy before replacement.
            merged = stage / "assets-merged"
            shutil.copytree(published_assets, merged)
            if staged_assets.exists():
                for source in staged_assets.iterdir():
                    shutil.copy2(source, merged / source.name)
            shutil.rmtree(staged_assets, ignore_errors=True)
            merged.rename(staged_assets)
        if published_assets.exists():
            operation = "backup_existing_assets"
            backup = path.parent / f".assets-backup-{uuid.uuid4().hex}"
            published_assets.rename(backup)
        if staged_assets.exists():
            operation = "publish_assets"
            staged_assets.rename(published_assets)
        try:
            operation = "publish_markdown"
            os.replace(staged_markdown, path)
            operation = "publish_sources_manifest"
            os.replace(staged_sources, path.with_name("proposal.sources.json"))
        except Exception:
            if published_assets.exists() and backup is not None:
                shutil.rmtree(published_assets, ignore_errors=True)
                backup.rename(published_assets)
            raise
        if backup is not None:
            shutil.rmtree(backup)
        return {"copied_images": copied, "final_markdown_images": image_links, "final_asset_files": staged_files,
                "sources_path": "proposal.sources.json", "sources": sources_manifest["sources"],
                "image_sources": sources_manifest["images"]}
    except Exception as exc:
        raise ProposalWriteError(operation, exc) from exc
    finally:
        if backup is not None and backup.exists() and not (path.parent / "assets").exists():
            backup.rename(path.parent / "assets")
        shutil.rmtree(stage, ignore_errors=True)


def _validated_raw(llm: ProposalLLM, raw: str, draft: _SectionDraft, *, stage: str, repair_prompt: str, system_prompt: str, warnings: list[str], call: Callable[..., str]) -> str:
    try:
        _validate_ids(_normalise_markdown(raw), draft.evidence, draft.images)
        return _normalise_markdown(raw)
    except ValueError as exc:
        try:
            repaired = call(f"{repair_prompt}：{exc}。只修复 ID 后返回章节正文。\n原文：\n{raw}", system_prompt=system_prompt, purpose="section_reference_repair")
            _validate_ids(_normalise_markdown(repaired), draft.evidence, draft.images)
            return _normalise_markdown(repaired)
        except Exception as repair_error:  # noqa: BLE001
            raise ProposalGenerationError(stage, repair_error) from repair_error


def generate_markdown_proposal(
    request: str, output_path: Path, *, llm: ProposalLLM,
    retriever: Callable[[list[str]], QueryResult], image_root: Path | None = None,
    return_result: bool = False, progress: Callable[[str, dict], None] | None = None,
) -> Path | ProposalRunResult:
    """Generate one Markdown proposal; readable content is retained as a warned draft."""
    image_root = Path(image_root or RAG_DB_PATH)
    warnings: list[str] = []
    metrics = {"planning_llm_calls": 0, "planning_repair_calls": 0, "section_writing_calls": 0,
               "section_reference_repair_calls": 0, "review_calls": 0, "review_repair_calls": 0,
               "review_items_received": 0, "review_items_valid": 0, "review_items_skipped_advisory": 0,
               "review_repair_attempted": False, "review_warnings": [], "section_revision_calls": 0,
               "retrieval_query_count": 0, "embedding_api_calls": "unknown", "stage_durations": {},
               "images": {"image_intents": {}, "raw_image_records": [], "candidate_images": [],
                          "usable_images": [], "selected_images": [], "copied_images": [],
                          "removed_images": [], "removal_reasons": [], "final_markdown_images": [],
                          "final_asset_files": [], "selected_images_by_section": {},
                          "final_images_by_section": {}, "image_section_coverage": {},
                          "image_placement_mismatches": []}}
    started = time.monotonic()
    def stage(name: str, started_at: float) -> None:
        metrics["stage_durations"][name] = round(time.monotonic() - started_at, 6)
        if progress is not None:
            progress(name, {"event": "stage_completed", "elapsed_seconds": metrics["stage_durations"][name]})
    def call(prompt: str, *, system_prompt: str, purpose: str) -> str:
        metrics[purpose] = metrics.get(purpose, 0) + 1
        if progress is not None:
            progress("model_call", {"event": "model_call_started", "purpose": purpose})
        call_started = time.monotonic()
        try:
            response = llm.generate(prompt, system_prompt=system_prompt)
        except Exception as exc:
            if progress is not None:
                progress("model_call", {"event": "model_call_failed", "purpose": purpose,
                                         "elapsed_seconds": round(time.monotonic() - call_started, 6),
                                         "error_type": type(exc).__name__})
            raise
        if progress is not None:
            progress("model_call", {"event": "model_call_completed", "purpose": purpose,
                                     "elapsed_seconds": round(time.monotonic() - call_started, 6)})
        return response

    pre_started = time.monotonic()
    overview = ""
    try:
        pre_queries = _pre_retrieval_queries(request)
        metrics["retrieval_query_count"] += len(pre_queries)
        overview = _evidence_overview(retriever(pre_queries).hits)
    except Exception as exc:  # Pre-retrieval must never prevent planning.
        warnings.append(f"pre_retrieval：{type(exc).__name__}: {exc}；已退化为仅根据需求规划")
    stage("pre_retrieval", pre_started)
    try:
        planning_started = time.monotonic()
        prompt = f"用户需求：\n{request}"
        if overview:
            prompt += f"\n\n证据概览（仅供贴近资料，不得扩大范围）：\n{overview}"
        plan = _plan_from(call(prompt, system_prompt=_PLAN_SYSTEM, purpose="planning_llm_calls"))
        stage("planning", planning_started)
    except (ValueError, json.JSONDecodeError) as initial_error:
        try:
            repaired = call(f"以下大纲 JSON 不符合格式：{initial_error}。请仅修复 JSON。\n用户需求：{request}", system_prompt=_PLAN_SYSTEM, purpose="planning_repair_calls")
            plan = _plan_from(repaired)
        except Exception as repair_error:  # noqa: BLE001
            raise ProposalGenerationError("planning", repair_error, detail=f"initial format error: {initial_error}") from repair_error
    except Exception as exc:  # noqa: BLE001
        raise ProposalGenerationError("planning", exc) from exc

    sections: dict[str, _SectionDraft] = {}
    for index, section in enumerate(plan.sections):
        try:
            retrieval_started = time.monotonic()
            metrics["retrieval_query_count"] += len(section.retrieval_queries)
            evidence = _evidence(retriever(section.retrieval_queries).hits)
            stage(f"retrieval:{section.section_id}", retrieval_started)
        except Exception as exc:  # noqa: BLE001
            raise ProposalGenerationError(f"retrieval:{section.section_id}", exc) from exc
        draft = _SectionDraft(section, "", evidence, _image_catalog(evidence))
        prompt = f"受控全局文档上下文：\n{_document_brief(plan, request, index)}\n\n当前章节：{section.heading}\n写作目标：{section.writing_goal}\n允许证据：\n{_evidence_prompt(evidence, draft.images)}"
        try:
            writing_started = time.monotonic()
            raw = call(prompt, system_prompt=_WRITE_SYSTEM, purpose="section_writing_calls")
            stage(f"writing:{section.section_id}", writing_started)
        except Exception as exc:  # noqa: BLE001
            raise ProposalGenerationError(f"writing:{section.section_id}", exc) from exc
        raw = _validated_raw(llm, raw, draft, stage=f"citation_validation:{section.section_id}", repair_prompt="你的章节包含无效 ID 或直接路径", system_prompt=_WRITE_SYSTEM, warnings=warnings, call=call)
        draft = _with_draft(draft, raw_body=raw)
        sections[section.section_id] = draft

    try:
        review_started = time.monotonic()
        _apply_image_budget(sections, image_root, warnings, metrics["images"])
        readable, _assets = _render(plan, sections, image_root=image_root, request=request, preview=True)
        review_raw = call(f"章节 ID：{[s.section_id for s in plan.sections]}\n完整方案（仅供审查，不得重写）：\n{readable}", system_prompt=_REVIEW_SYSTEM, purpose="review_calls")
        try:
            review = _review_from(review_raw, set(sections))
        except ReviewSchemaError as initial_error:
            if not initial_error.repairable:
                raise
            metrics["review_repair_attempted"] = True
            warnings.append(f"review_format_repair_attempted：{type(initial_error).__name__}")
            repaired = call(
                f"上次审查 JSON 无法解析或顶层结构不符合要求（{initial_error}）。"
                "请仅按既定 JSON schema 修复格式，保留所有有效审查结论。\n原始审查输出：\n"
                f"{review_raw}",
                system_prompt=_REVIEW_SYSTEM,
                purpose="review_repair_calls",
            )
            review = _review_from(repaired, set(sections))
        metrics["review_items_received"] = review.items_received
        metrics["review_items_valid"] = len(review.issues)
        metrics["review_items_skipped_advisory"] = review.items_skipped_advisory
        metrics["review_warnings"] = review.warnings
        warnings.extend(review.warnings)
        issues = review.issues
        stage("review", review_started)
    except Exception as exc:  # noqa: BLE001
        raise ProposalGenerationError("review", exc) from exc
    affected = list(dict.fromkeys(section_id for issue in issues for section_id in issue.affected_section_ids))
    for section_id in affected:
        old = sections[section_id]
        instructions = [issue.revision_instruction for issue in issues if section_id in issue.affected_section_ids]
        try:
            revision_started = time.monotonic()
            raw = call(f"受控相邻章节上下文：\n{_document_brief(plan, request, [x.section_id for x in plan.sections].index(section_id))}\n当前章节：{old.plan.heading}\n当前原始正文（保留内部 ID）：\n{old.raw_body}\n审查意见：{instructions}\n允许证据：\n{_evidence_prompt(old.evidence, old.images)}", system_prompt=_REVISE_SYSTEM, purpose="section_revision_calls")
            stage(f"revision:{section_id}", revision_started)
        except Exception as exc:  # noqa: BLE001
            raise ProposalGenerationError(f"revision:{old.plan.section_id}", exc) from exc
        raw = _validated_raw(llm, raw, old, stage=f"citation_validation:{old.plan.section_id}", repair_prompt="修订稿包含无效 ID 或直接路径", system_prompt=_REVISE_SYSTEM, warnings=warnings, call=call)
        sections[section_id] = _with_draft(old, raw_body=raw)
    try:
        assembly_started = time.monotonic()
        _apply_image_budget(sections, image_root, warnings, metrics["images"])
        markdown, assets, gate_warnings, placement = _final_markdown(plan, sections, image_root=image_root, output_path=output_path, request=request)
        metrics["images"].update(placement)
        warnings.extend(gate_warnings)
        fatal_errors = _role_and_scope_errors(sections)
        if metrics["images"].get("image_pipeline_failure"):
            fatal_errors.append("image_pipeline_failure：合格图片候选不少于 3 张但最终未选择图片")
        if placement["image_placement_mismatches"]:
            fatal_errors.append("image_placement_mismatch：最终 Markdown 图片归属与 intended_section_id 不一致")
        if _ID_RE.search(markdown):
            fatal_errors.append("最终 Markdown 残留内部证据或图片 ID")
        if ("cache/" in markdown or "cache\\" in markdown or re.search(r"[A-Za-z]:\\", markdown)):
            fatal_errors.append("最终 Markdown 显示了内部缓存路径或机器绝对路径")
        diagnostics = _quality_gate_diagnostics(
            markdown, output_path=output_path, gate_warnings=gate_warnings,
            fatal_errors=fatal_errors, placement=placement,
        )
        if progress is not None:
            progress("quality_gate", diagnostics)
        if fatal_errors:
            raise ProposalQualityError("；".join(fatal_errors))
        if warnings and not markdown.startswith(_DRAFT_NOTICE):
            markdown = _DRAFT_NOTICE + markdown
        stage("assembly", assembly_started)
        stage("quality_gate", assembly_started)
    except ProposalQualityError as exc:
        raise ProposalGenerationError("quality_gate", exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise ProposalGenerationError("markdown_write", exc) from exc
    try:
        metrics["images"]["removal_reasons"] = [item["reason"] for item in metrics["images"]["removed_images"]]
        published = _write(output_path, markdown, assets)
        metrics["images"].update(published)
        metrics["sources_path"] = published["sources_path"]
        metrics["sources"] = published["sources"]
    except Exception as exc:  # noqa: BLE001
        raise ProposalGenerationError("markdown_write", exc) from exc
    metrics["total_generation_llm_calls"] = sum(metrics[key] for key in ("planning_llm_calls", "planning_repair_calls", "section_writing_calls", "section_reference_repair_calls", "review_calls", "review_repair_calls", "section_revision_calls"))
    metrics["stage_durations"]["docx_render"] = 0.0
    metrics["stage_durations"]["total"] = round(time.monotonic() - started, 6)
    status = "PASS" if not warnings else "DRAFT_WITH_WARNINGS"
    metrics["quality_passed_checks"] = max(0, 10 - len(gate_warnings))
    metrics["quality_status"] = status
    result = ProposalRunResult(output_path, status, warnings, metrics)
    return result if return_result else output_path
