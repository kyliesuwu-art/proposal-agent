"""Offline acceptance checks on an explicitly disposable temporary index copy."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

import chromadb

from src.v3_hnsw_rebuild import _read_integrity


def _check(name, status, basis, findings=None):
    return {"name": name, "status": status, "basis": basis, "findings": findings or []}


def _safe_root(value):
    root = Path(value).resolve(strict=True)
    temp = Path(tempfile.gettempdir()).resolve(strict=True)
    if root == temp or not root.is_dir() or not root.is_relative_to(temp):
        raise ValueError("检查目标必须是系统临时目录内的可丢弃副本")
    return root


def inspect_copy(path, *, disposable_copy=False, attestation=None):
    """Inspect only a caller-identified disposable copy; never use a live index."""
    checks = []
    limitations = ["Chroma PersistentClient may modify the disposable copy; this is not a strictly read-only client.",
                   "Vector contents, ANN quality, FTS index internals, source authenticity and snapshot quiescence are not proven.",
                   "ZIP collisions are limited to cache manifest entries; unrecorded archives and original source identity are not independently checked.",
                   "Existing indexes have no authoritative active-version registry; hashes and scores do not establish the current version."]
    def blocked(name, basis, findings):
        checks.append(_check(name, "BLOCKED", basis, findings))

    try:
        if not disposable_copy:
            raise ValueError("必须明确声明 --disposable-copy")
        root = _safe_root(path)
        required = [root / "hybrid_lexical.sqlite3", root / "chroma_v2" / "chroma.sqlite3", root / "cache_ingest_manifest.sqlite3"]
        def walk_error(error):
            raise error
        for base, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
            if any((Path(base) / name).is_symlink() for name in [*dirs, *files]):
                raise ValueError("检查副本不得包含符号链接")
        missing = [p.name for p in required if not p.is_file()]
        if missing:
            blocked("required_files", "lexical SQLite, Chroma SQLite and cache manifest", ["missing: " + ", ".join(missing)])
            raise RuntimeError("required files unavailable")
        checks.append(_check("required_files", "PASS", "lexical SQLite, Chroma SQLite and cache manifest"))
        integrity = {p.name: _read_integrity(p) for p in required}
        if any(value != "ok" for value in integrity.values()):
            blocked("sqlite_integrity", "PRAGMA integrity_check, mode=ro", [str(integrity)])
            raise RuntimeError("SQLite integrity failed")
        checks.append(_check("sqlite_integrity", "PASS", "PRAGMA integrity_check, mode=ro", [str(integrity)]))

        with closing(sqlite3.connect((root / "hybrid_lexical.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            table = db.execute("SELECT sql FROM sqlite_master WHERE name='page_fts'").fetchone()
            if not table or "using fts5" not in table[0].lower():
                raise ValueError("page_fts is not an FTS5 virtual table")
            rows = db.execute("SELECT page_id,document_id,version_id,source_key,page_number,payload FROM pages").fetchall()
            fts_rows = db.execute("SELECT page_id,title,section_title,keywords,entities,parameters,content FROM page_fts").fetchall()
        checks.append(_check("schema", "PASS", "required pages columns and FTS5 table/columns"))
        pages = {}
        doc_versions, doc_sources, source_docs = defaultdict(set), defaultdict(set), defaultdict(set)
        bad = 0
        for row in rows:
            data = json.loads(row["payload"])
            pid, doc, ver, source, number = (row[k] for k in ("page_id", "document_id", "version_id", "source_key", "page_number"))
            if (not all(isinstance(v, str) and v for v in (pid, doc, ver, source)) or not isinstance(number, int) or number < 1
                    or pid != f"{doc}:{ver}:p{number}" or any(data.get(k) != row[k] for k in ("page_id", "document_id", "version_id", "source_key", "page_number"))
                    or data.get("source_file") != source or data.get("slide_number") != number
                    or any(data.get("identity_metadata", {}).get(k) != row[k] for k in ("document_id", "version_id", "source_key", "page_number"))
                    or source.startswith(("/", "\\")) or ":" in source or ".." in source.replace("\\", "/").split("/")):
                bad += 1
            pages[pid] = data
            doc_versions[doc].add(ver); doc_sources[doc].add(source); source_docs[source].add(doc)
        multi = sum(len(v) > 1 for v in doc_versions.values())
        conflicts = sum(len(v) > 1 for v in doc_sources.values()) + sum(len(v) > 1 for v in source_docs.values())
        checks.append(_check("page_identity_versions", "BLOCKED" if bad or multi or conflicts or not pages else "PASS",
                             "pages primary key, payload identity and document/version/source cardinality",
                             [f"pages={len(pages)}, invalid={bad}, multi_version_docs={multi}, identity_conflicts={conflicts}"]))
        fts = Counter(row["page_id"] for row in fts_rows)
        fts_bad = 0
        for row in fts_rows:
            data = pages.get(row["page_id"])
            if data and any(row[k] != (" ".join(data.get(k, [])) if k in {"keywords", "entities", "parameters"} else data.get(k, "")) for k in ("title", "section_title", "keywords", "entities", "parameters", "content")):
                fts_bad += 1
        missing_fts, orphan_fts = set(pages) - set(fts), set(fts) - set(pages)
        duplicates = sum(n - 1 for n in fts.values() if n > 1)
        checks.append(_check("fts_pages", "BLOCKED" if missing_fts or orphan_fts or duplicates or fts_bad else "PASS",
                             "FTS page_id set, multiplicity and stored indexed fields versus payload",
                             [f"missing={len(missing_fts)}, orphan={len(orphan_fts)}, duplicate={duplicates}, mismatched={fts_bad}"]))

        # This check reads IDs and metadata only. Supplying an unrelated
        # embedding function conflicts with the persisted collection config.
        collection = chromadb.PersistentClient(path=str(root / "chroma_v2")).get_collection("electrical_pages_v2", embedding_function=None)
        count = collection.count()
        vectors = {}
        for offset in range(0, count, 500):
            batch = collection.get(limit=500, offset=offset, include=["metadatas"])
            for identifier, metadata in zip(batch["ids"], batch["metadatas"]):
                if identifier in vectors:
                    raise ValueError("duplicate Chroma ID during pagination")
                vectors[identifier] = metadata or {}
        if len(vectors) != count:
            raise ValueError("incomplete Chroma pagination")
        vector_missing, vector_orphans = set(pages) - set(vectors), set(vectors) - set(pages)
        mismatch = sum(any(vectors[pid].get(key) != pages[pid].get(key) for key in ("document_id", "version_id", "source_key", "page_number")) for pid in set(pages) & set(vectors))
        checks.append(_check("chroma_pages", "BLOCKED" if vector_missing or vector_orphans or mismatch else "PASS",
                             "paginated Chroma IDs and metadata versus SQLite pages",
                             [f"chroma={count}, missing={len(vector_missing)}, orphan={len(vector_orphans)}, metadata_mismatch={mismatch}"]))

        with closing(sqlite3.connect((root / "cache_ingest_manifest.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
            entries = db.execute("SELECT zip_path,zip_hash,source_key,document_id,version_id,status FROM cache_documents").fetchall()
        zip_sources = defaultdict(set)
        zip_ids = defaultdict(set)
        zip_hash_sources = defaultdict(set)
        incomplete = 0
        missing_indexed = 0
        for zip_path, zip_hash, source, doc, version, status in entries:
            if not all((zip_path, zip_hash, source, doc, version)) or status not in {"completed", "duplicate"}:
                incomplete += 1
                continue
            zip_sources[source].add((zip_path, zip_hash)); zip_ids[source].add(doc)
            zip_hash_sources[zip_hash].add(source)
            if status == "completed" and version not in doc_versions.get(doc, set()):
                missing_indexed += 1
        collisions = sum(len(values) > 1 for values in zip_sources.values())
        aliases = sum(len(values) > 1 for values in zip_hash_sources.values())
        cross_conflicts = sum(bool(source_docs.get(source) and source_docs[source] != docs) for source, docs in zip_ids.items())
        checks.append(_check("zip_identity", "BLOCKED" if collisions or cross_conflicts or incomplete or missing_indexed else "WARNING" if aliases else "PASS",
                             "cache_documents recorded ZIP paths/hashes/source keys and indexed document IDs",
                             [f"entries={len(entries)}, collision_keys={collisions}, cross_conflicts={cross_conflicts}, incomplete={incomplete}, completed_missing_index={missing_indexed}, duplicate_hash_sources={aliases}"]))

        if attestation is None:
            blocked("version_attestation", "explicit human-confirmed manifest, not inferred from hashes or scores", ["no attestation provided"])
        else:
            evidence_path = Path(attestation).resolve(strict=True)
            if not evidence_path.is_relative_to(root):
                raise ValueError("确认清单必须位于临时检查副本内")
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            approved = evidence.get("documents")
            valid = (isinstance(approved, dict) and isinstance(evidence.get("confirmed_by"), str) and bool(evidence["confirmed_by"].strip())
                     and isinstance(evidence.get("basis"), str) and bool(evidence["basis"].strip())
                     and evidence.get("zip_inventory_complete") is True and set(approved) == set(doc_versions)
                     and all(len(versions) == 1 and approved[doc] in versions for doc, versions in doc_versions.items()))
            checks.append(_check("version_attestation", "PASS" if valid else "BLOCKED", "caller-supplied human attestation; authenticity not independently verified", ["explicit document/version and ZIP inventory attestation " + ("matches" if valid else "missing or inconsistent")]))
            limitations.append("Attestation is a caller declaration; independent authority, ZIP scope and real-world source identity cannot be verified by this tool.")
    except Exception as exc:
        blocked("inspection_execution", "fail closed on missing evidence, schema, I/O, Chroma or unexpected errors", [f"{type(exc).__name__}: {exc}"])
    expected = ("required_files", "sqlite_integrity", "schema", "page_identity_versions", "fts_pages", "chroma_pages", "zip_identity", "version_attestation")
    completed = {check["name"] for check in checks}
    for name in expected:
        if name not in completed:
            blocked(name, "not checked because a required earlier step failed", ["NOT_CHECKED"])
            limitations.append(f"{name} was not checked")
    status = "BLOCKED" if any(x["status"] == "BLOCKED" for x in checks) else "WARNING" if any(x["status"] == "WARNING" for x in checks) else "PASS"
    return {"status": status, "can_enter_next_stage": status != "BLOCKED", "checks": checks, "unable_to_check": limitations}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("copy", help="disposable index copy under the system temporary directory")
    parser.add_argument("--disposable-copy", action="store_true", required=True)
    parser.add_argument("--attestation", help="human attestation JSON inside the disposable copy")
    args = parser.parse_args()
    report = inspect_copy(args.copy, disposable_copy=args.disposable_copy, attestation=args.attestation)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["status"] == "BLOCKED" else 0


if __name__ == "__main__":
    sys.exit(main())
