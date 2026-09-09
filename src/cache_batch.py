"""Read-only inspection for a directory of existing MinerU debug ZIP caches.

This module deliberately delegates recovery validation to ``recover_debug_zip`` so
the batch path and the existing V2 cache path share identity and page recovery.
It does not create a database, extract assets, or contact MinerU.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import zipfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.cache_recovery import SUPPORTED_CACHE_EXTENSIONS, recover_debug_zip
from src.hybrid_v2 import HybridTestStore
from src.retrieval_fields import extract_retrieval_fields
from src.adapters.vector_store import DashScopeEmbeddingFunction
from src.adapters.parser import INDEX_SCHEMA_VERSION, PARSER_VERSION
from src.config import RAG_DB_PATH, PROJECT_ROOT


_IMAGE_EXTENSIONS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
EMBEDDING_MODEL_ID = "text-embedding-v3"
_STAGES = ("cache_recovered", "sidecar_saved", "assets_extracted", "lexical_indexed", "embedded", "completed")


@dataclass
class CacheInspection:
    zip_path: str
    zip_hash: str
    size_bytes: int
    origin_extension: str | None
    page_count: int | None
    source_key: str | None
    document_id: str | None
    version_id: str | None
    recoverable: bool
    full_markdown: bool
    content_list: bool
    layout: bool
    origin_file: bool
    image_asset_count: int
    image_block_count: int
    missing_referenced_images: list[str]
    error: str | None = None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_content_list(archive: zipfile.ZipFile, names: list[str]) -> tuple[list[dict[str, Any]] | None, str | None]:
    candidate = next((name for name in names if name.endswith("content_list.json")), None)
    if candidate is None:
        return None, None
    try:
        value = json.loads(archive.read(candidate).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"content_list.json 无法解析: {type(exc).__name__}"
    if not isinstance(value, list):
        return None, "content_list.json 不是列表"
    return [item for item in value if isinstance(item, dict)], None


def inspect_cache_zip(zip_path: str | Path, *, root: str | Path) -> CacheInspection:
    """Inspect one cache archive without extracting or changing it."""
    path, root_path = Path(zip_path), Path(root)
    relative = path.relative_to(root_path).as_posix()
    digest = _sha256(path)
    defaults = dict(
        zip_path=relative, zip_hash=digest, size_bytes=path.stat().st_size,
        origin_extension=None, page_count=None, source_key=None, document_id=None,
        version_id=None, recoverable=False, full_markdown=False, content_list=False,
        layout=False, origin_file=False, image_asset_count=0, image_block_count=0,
        missing_referenced_images=[], error=None,
    )
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            lower_names = [name.lower() for name in names]
            origins = [name for name in names if "_origin." in name]
            content, content_error = _read_content_list(archive, names)
            image_assets = [name for name in names if Path(name).suffix.lower() in _IMAGE_EXTENSIONS and name not in origins]
            defaults.update(
                full_markdown=any(name.endswith("full.md") for name in lower_names),
                content_list=content is not None,
                layout=any("layout" in Path(name).name.lower() and name.lower().endswith(".json") for name in names),
                origin_file=bool(origins),
                image_asset_count=len(image_assets),
            )
            if origins:
                defaults["origin_extension"] = Path(origins[0]).suffix.lower() or None
            if content is not None:
                page_indexes = [block.get("page_idx") for block in content if isinstance(block.get("page_idx"), int)]
                defaults["page_count"] = max(page_indexes) + 1 if page_indexes else 0
                image_paths = [str(block.get("img_path", "")) for block in content if block.get("type") == "image" and block.get("img_path")]
                defaults["image_block_count"] = len(image_paths)
                available = set(names)
                defaults["missing_referenced_images"] = sorted(value for value in image_paths if value not in available)
            if content_error:
                defaults["error"] = content_error
    except zipfile.BadZipFile:
        defaults["error"] = "ZIP 文件损坏或不是有效 ZIP"
        return CacheInspection(**defaults)

    extension = defaults["origin_extension"]
    if not defaults["origin_file"]:
        defaults["error"] = "缓存缺少 _origin.<ext>"
    elif extension not in SUPPORTED_CACHE_EXTENSIONS:
        defaults["error"] = f"缓存原始格式不受支持: {extension}"
    elif not defaults["content_list"] and defaults["error"] is None:
        defaults["error"] = "缓存缺少 content_list.json"
    elif defaults["missing_referenced_images"]:
        defaults["error"] = "缓存引用的图片文件缺失"
    else:
        try:
            recovered = recover_debug_zip(path, recover_pages=False)
        except Exception as exc:  # Same recovery path as the formal cache batch.
            defaults["error"] = f"恢复失败: {type(exc).__name__}: {exc}"
        else:
            defaults.update(
                recoverable=True,
                source_key=recovered.identity.source_key,
                document_id=recovered.identity.document_id,
                version_id=recovered.identity.version_id,
                # Page count above comes directly from content_list.page_idx.  Avoid
                # rendering pages during dry-run just to count them.
            )
    return CacheInspection(**defaults)


def inspect_cache_directory(directory: str | Path, *, target_db: str | Path) -> dict[str, Any]:
    """Build a JSON-serializable dry-run report. The target is never opened."""
    root = Path(directory).resolve()
    if not root.is_dir():
        raise ValueError(f"缓存目录不存在: {root}")
    archives = sorted(path for path in root.rglob("*.zip") if path.is_file())
    records = [inspect_cache_zip(path, root=root) for path in archives]
    by_hash: dict[str, list[str]] = defaultdict(list)
    by_source: dict[str, list[str]] = defaultdict(list)
    for record in records:
        by_hash[record.zip_hash].append(record.zip_path)
        if record.source_key:
            by_source[record.source_key].append(record.zip_path)
    missing = {
        "full_markdown": sum(not item.full_markdown for item in records),
        "content_list": sum(not item.content_list for item in records),
        "layout": sum(not item.layout for item in records),
        "origin_file": sum(not item.origin_file for item in records),
        "referenced_images": sum(len(item.missing_referenced_images) for item in records),
    }
    type_counts = Counter(item.origin_extension or "unknown" for item in records)
    failures = [{"zip_path": item.zip_path, "reason": item.error} for item in records if item.error]
    return {
        "mode": "dry-run", "input_directory": str(root), "target_db": str(Path(target_db).resolve()),
        "zip_total": len(records), "recoverable_total": sum(item.recoverable for item in records),
        "missing": missing, "file_types": dict(sorted(type_counts.items())),
        "duplicate_zips": {key: value for key, value in by_hash.items() if len(value) > 1},
        "duplicate_source_keys": {key: value for key, value in by_source.items() if len(value) > 1},
        "failures": failures, "archives": [asdict(item) for item in records],
        "write_plan": {"state_manifest": "<target_db>/cache_ingest_manifest.sqlite3", "raw_sidecars": "<target_db>/sidecars/<document_id>/<version_id>/", "image_assets": "<target_db>/assets/<document_id>/<version_id>/", "embedding_cache": "<target_db>/embedding_cache.sqlite3", "note": "dry-run did not create these paths or open the target database"},
    }


def _safe_target_db(value: str | Path) -> Path:
    target = Path(value).resolve()
    protected = {PROJECT_ROOT.resolve(), (PROJECT_ROOT / "debug_zips").resolve(),
                 (PROJECT_ROOT / "files").resolve(), (PROJECT_ROOT / "templates").resolve()}
    old_names = {"chroma_db", "chroma_db_backup_20260728", "v2_test_db"}
    if target in protected or target.name in old_names:
        raise ValueError("不安全的 --db 目标：不得指向项目根目录、资料目录、模板目录或旧库")
    return target


def select_trial_candidates(inspections: list[CacheInspection], *, limit: int,
                            page_limit: int | None = 200) -> list[CacheInspection]:
    """Choose compact canonical samples while guaranteeing each real input type."""
    canonical: dict[str, CacheInspection] = {}
    for item in sorted(inspections, key=lambda value: value.zip_path):
        if item.recoverable:
            canonical.setdefault(item.zip_hash, item)
    candidates = sorted(canonical.values(), key=lambda value: (value.page_count or 0, value.zip_path))
    selected: list[CacheInspection] = []
    total_pages = 0
    for extension in (".pdf", ".docx", ".pptx"):
        candidate = next((item for item in candidates if item.origin_extension == extension and item not in selected), None)
        if candidate is not None and len(selected) < limit:
            selected.append(candidate)
            total_pages += candidate.page_count or 0
    for candidate in candidates:
        pages = candidate.page_count or 0
        if candidate in selected or len(selected) >= limit or (page_limit is not None and total_pages + pages > page_limit):
            continue
        selected.append(candidate)
        total_pages += pages
    return selected


def _safe_member_path(member: str) -> Path:
    candidate = Path(member.replace("\\", "/"))
    if candidate.is_absolute() or candidate.drive or ".." in candidate.parts:
        raise ValueError(f"ZIP 成员路径不安全: {member}")
    return candidate


class EmbeddingCache:
    """Persistent vector cache scoped by model identifier and normalized retrieval text."""

    def __init__(self, root: Path) -> None:
        self._db = sqlite3.connect(root / "embedding_cache.sqlite3")
        self._db.execute("CREATE TABLE IF NOT EXISTS embeddings (model_id TEXT NOT NULL, text_hash TEXT NOT NULL, vector_json TEXT NOT NULL, PRIMARY KEY(model_id, text_hash))")
        self._db.commit()
        self.hits = 0
        self.misses = 0

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def _hash(text: str) -> str:
        normalized = " ".join(text.split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def vectors(self, texts: list[str], embedding_function: Any, *, batch_size: int) -> list[list[float]]:
        values: list[list[float] | None] = [None] * len(texts)
        pending: list[tuple[int, str, str]] = []
        for index, text in enumerate(texts):
            text_hash = self._hash(text)
            row = self._db.execute("SELECT vector_json FROM embeddings WHERE model_id=? AND text_hash=?", (EMBEDDING_MODEL_ID, text_hash)).fetchone()
            if row:
                values[index] = json.loads(row[0])
                self.hits += 1
            else:
                pending.append((index, text_hash, text))
                self.misses += 1
        # Current DashScope client permits at most 10 inputs; caller's batch-size is
        # respected as an upper bound but never sent beyond that API constraint.
        actual_batch = max(1, min(batch_size, 10))
        for start in range(0, len(pending), actual_batch):
            batch = pending[start:start + actual_batch]
            vectors = embedding_function([item[2] for item in batch])
            if len(vectors) != len(batch):
                raise RuntimeError("Embedding 客户端返回向量数量不匹配")
            with self._db:
                for (index, text_hash, _), vector in zip(batch, vectors):
                    clean = [float(value) for value in vector]
                    values[index] = clean
                    self._db.execute("INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?)", (EMBEDDING_MODEL_ID, text_hash, json.dumps(clean)))
        return [value for value in values if value is not None]


class CacheBatchIngestor:
    """Staged cache ZIP ingestion into one explicitly named, isolated candidate DB."""

    def __init__(self, source_dir: str | Path, target_db: str | Path, *, embedding_function: Any | None = None) -> None:
        self.source_dir = Path(source_dir).resolve()
        if not self.source_dir.is_dir():
            raise ValueError(f"缓存目录不存在: {self.source_dir}")
        self.root = _safe_target_db(target_db)
        self.root.mkdir(parents=True, exist_ok=True)
        self.embedding_function = embedding_function or DashScopeEmbeddingFunction()
        self.state = sqlite3.connect(self.root / "cache_ingest_manifest.sqlite3")
        self.state.row_factory = sqlite3.Row
        self.state.execute("""CREATE TABLE IF NOT EXISTS cache_documents (
            zip_path TEXT PRIMARY KEY, zip_hash TEXT NOT NULL, source_key TEXT, document_id TEXT,
            version_id TEXT, status TEXT NOT NULL, stage TEXT, page_count INTEGER, duplicate_of TEXT,
            error TEXT, embedding_calls INTEGER NOT NULL DEFAULT 0,
            parser_version TEXT, index_schema_version TEXT
        )""")
        columns = {row[1] for row in self.state.execute("PRAGMA table_info(cache_documents)")}
        for column in ("parser_version", "index_schema_version"):
            if column not in columns:
                self.state.execute(f"ALTER TABLE cache_documents ADD COLUMN {column} TEXT")
        self.state.commit()
        self.cache = EmbeddingCache(self.root)
        self.store = HybridTestStore(self.root, embedding_function=self.embedding_function)

    def close(self) -> None:
        self.store.close()
        self.cache.close()
        self.state.close()

    def _state(self, record: CacheInspection, *, status: str, stage: str | None, error: str | None = None,
               duplicate_of: str | None = None, embedding_calls: int = 0) -> None:
        with self.state:
            self.state.execute("""INSERT INTO cache_documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(zip_path) DO UPDATE SET zip_hash=excluded.zip_hash, source_key=excluded.source_key,
                document_id=excluded.document_id, version_id=excluded.version_id, status=excluded.status,
                stage=excluded.stage, page_count=excluded.page_count, duplicate_of=excluded.duplicate_of,
                error=excluded.error, embedding_calls=excluded.embedding_calls,
                parser_version=excluded.parser_version, index_schema_version=excluded.index_schema_version""", (
                record.zip_path, record.zip_hash, record.source_key, record.document_id, record.version_id,
                status, stage, record.page_count, duplicate_of, error, embedding_calls,
                PARSER_VERSION, INDEX_SCHEMA_VERSION,
            ))

    def _prior(self, record: CacheInspection) -> sqlite3.Row | None:
        return self.state.execute("SELECT * FROM cache_documents WHERE zip_path=?", (record.zip_path,)).fetchone()

    def _save_sidecar_and_assets(self, record: CacheInspection, pages: list[dict]) -> list[dict]:
        assert record.document_id and record.version_id
        sidecar_root = self.root / "sidecars" / record.document_id / record.version_id
        asset_root = self.root / "assets" / record.document_id / record.version_id
        sidecar_root.mkdir(parents=True, exist_ok=True)
        asset_root.mkdir(parents=True, exist_ok=True)
        archive_path = self.source_dir / record.zip_path
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            selected = [name for name in names if name.endswith("content_list.json") or name.lower().endswith("full.md") or ("layout" in Path(name).name.lower() and name.lower().endswith(".json"))]
            index: dict[str, str] = {}
            for member in selected:
                safe = _safe_member_path(member)
                destination = sidecar_root / safe.name
                destination.write_bytes(archive.read(member))
                index[member] = destination.relative_to(self.root).as_posix()
            (sidecar_root / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
            for page in pages:
                image_blocks = [block for block in page.get("raw_blocks", [])
                                if block.get("type") in {"image", "chart", "equation"}
                                and (block.get("img_path") or block.get("image_path"))]
                updated: list[dict] = []
                for index_no, image in enumerate(page.get("images", [])):
                    if index_no >= len(image_blocks):
                        continue
                    member = str(image_blocks[index_no].get("img_path") or image_blocks[index_no].get("image_path"))
                    safe = _safe_member_path(member)
                    if member not in names:
                        raise ValueError(f"ZIP 图片成员缺失: {member}")
                    extension = safe.suffix or ".jpg"
                    destination = asset_root / f"page_{page['page_number']}_{index_no}{extension}"
                    destination.write_bytes(archive.read(member))
                    updated.append({**image, "path": destination.relative_to(self.root).as_posix()})
                page["images"] = updated
        return pages

    def _records_for(self, identity, pages: list[dict]) -> tuple[list[dict], list[str]]:
        records = [self.store._record(identity, page, "proposal") for page in pages if page.get("indexable", True)]
        return records, [record["retrieval_text"] for record in records]

    def ingest(self, *, limit: int, batch_size: int) -> dict[str, Any]:
        if limit < 1 or batch_size < 1:
            raise ValueError("--limit 和 --batch-size 必须大于 0")
        report = inspect_cache_directory(self.source_dir, target_db=self.root)
        inspected = [CacheInspection(**item) for item in report["archives"]]
        canonical_by_hash: dict[str, CacheInspection] = {}
        for item in inspected:
            if item.recoverable and item.zip_hash not in canonical_by_hash:
                canonical_by_hash[item.zip_hash] = item
        # The trial preview uses page_limit=200.  Operational --limit intentionally
        # has no page cap so the documented full-resume command can reach all
        # canonical documents.
        selected = select_trial_candidates(inspected, limit=limit, page_limit=None)
        outcomes: list[dict[str, Any]] = []
        # Preserve aliases, including those outside the limited canonical trial, but do not index them.
        for item in inspected:
            canonical = canonical_by_hash.get(item.zip_hash)
            if canonical and canonical.zip_path != item.zip_path:
                self._state(item, status="duplicate", stage="completed", duplicate_of=canonical.zip_path)
        for item in selected:
            prior = self._prior(item)
            if prior and prior["status"] == "completed" and prior["zip_hash"] == item.zip_hash:
                outcomes.append({"zip_path": item.zip_path, "status": "skipped", "reason": "hash 未变化且已完成"})
                continue
            try:
                recovered = recover_debug_zip(self.source_dir / item.zip_path)
                self._state(item, status="running", stage="cache_recovered")
                pages = self._save_sidecar_and_assets(item, recovered.pages)
                self._state(item, status="running", stage="sidecar_saved")
                self._state(item, status="running", stage="assets_extracted")
                records, texts = self._records_for(recovered.identity, pages)
                before_misses = self.cache.misses
                vectors = self.cache.vectors(texts, self.embedding_function, batch_size=batch_size)
                written = self.store.add_version(recovered.identity, pages, "proposal", embeddings=vectors, batch_size=batch_size)
                self._state(item, status="running", stage="lexical_indexed", embedding_calls=self.cache.misses - before_misses)
                self._state(item, status="running", stage="embedded", embedding_calls=self.cache.misses - before_misses)
                self._state(item, status="completed", stage="completed", embedding_calls=self.cache.misses - before_misses)
                outcomes.append({"zip_path": item.zip_path, "status": "completed", "pages": written, "embedding_cache_misses": self.cache.misses - before_misses})
            except Exception as exc:
                stage = self._prior(item)["stage"] if self._prior(item) else None
                self._state(item, status="failed", stage=stage, error=f"{type(exc).__name__}: {exc}")
                outcomes.append({"zip_path": item.zip_path, "status": "failed", "stage": stage, "error": f"{type(exc).__name__}: {exc}"})
        summary = {
            "mode": "resume", "target_db": str(self.root), "limit": limit, "batch_size": batch_size,
            "selected_canonical": [{"zip_path": item.zip_path, "origin_extension": item.origin_extension, "page_count": item.page_count} for item in selected],
            "outcomes": outcomes, "embedding_cache": {"hits": self.cache.hits, "misses": self.cache.misses},
        }
        (self.root / "cache_ingest_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
