# Phase 1 Release Acceptance — Live Calls, Isolation and Remaining Gates

Status: design and evidence record. No billable DashScope call and no formal
database access was performed while producing this document. All offline tests
continue to use fake LLM/fake retriever and pytest temporary directories.

## 1. Embedding compatibility verdict

### 1.1 Evidence
- Offline tests build disposable indexes with
  `src.hybrid_v2.LocalHashEmbeddingFunction`, whose default dimension is 256.
  Its docstring already states it is for offline cache acceptance only, not
  production QA. Vectors are deterministic SHA-256 token-bucket hashes.
- Production retrieval uses
  `src.adapters.vector_store.DashScopeEmbeddingFunction`, which calls
  DashScope `text-embedding-v3` without a `dimensions` argument, so the service
  default of 1024 dimensions applies.
- The formal collection `electrical_pages_v2` in
  `runtime_data/word_test_db/chroma_v2/chroma.sqlite3` is registered with
  `dimension = 1024` (read-only SQLite inspection, no Chroma client opened).

### 1.2 Conclusions
- Dimension mismatch: 256 (hash) versus 1024 (real). Chroma rejects a query
  vector whose width differs from the collection width, so a hash-built index
  cannot answer a real `text-embedding-v3` query at all.
- Semantic space mismatch: even with equal width, hash embeddings encode token
  overlap, not meaning. Cosine neighbours would be lexical artefacts and prove
  nothing about semantic recall.
- The existing isolated hash databases therefore MUST NOT be reused for real
  semantic retrieval acceptance. Real embedding acceptance requires a freshly
  built, independently rooted index whose vectors all come from
  `text-embedding-v3`.
- The hash-based integration tests remain valid for workflow, RRF plumbing,
  citation, quality gate and renderer contracts; they are not evidence of
  embedding quality.

## 2. Path isolation (`RAG_DB_PATH` and related variables)

Controlled by `RAG_DB_PATH` (default `runtime_data/word_test_db`):
- Hybrid V2 root: `<root>/chroma_v2` and `<root>/hybrid_lexical.sqlite3`
  through `HybridTestStore(test_db, ...)`, reached from
  `pipeline.retrieve_evidence(..., db=RAG_DB_PATH)`.
- Legacy V1 slide store default root in `adapters/vector_store.py`
  (constructor `persist_dir` still overrides it).
- Cache batch root in `cache_batch.py`: `embedding_cache.sqlite3`,
  `cache_ingest_manifest.sqlite3`, `sidecars/` and `assets/`.
- Default image root in `markdown_proposal.py` when no explicit root is passed.

NOT controlled by `RAG_DB_PATH`, each with its own environment override:
- `INGEST_MANIFEST_PATH` (default project-root `ingest_manifest.csv`).
- `IMAGES_DIR`, `DEBUG_ZIPS_DIR`, `DEBUG_MINERU_RESULT_DIR`.
- The WeCom agent SQLite in `wecom_bot.py` is a separate service database and
  is unrelated to RAG retrieval.

Consequence: a fully isolated live run must set `RAG_DB_PATH` to a dedicated
temporary root and additionally override the manifest/debug variables whenever
the exercised code path touches them. `tests/test_config_paths.py` already
locks each override independently and must stay green.

## 3. Read-only index open behaviour

`HybridTestStore(..., readonly=True)`:
- The SQLite FTS database is opened with URI `mode=ro`; it cannot write.
- Chroma is still opened through `chromadb.PersistentClient` plus
  `get_collection`. A PersistentClient can run migrations/maintenance and touch
  SQLite/HNSW files even for reads. Per AGENTS.md, real verification must run
  against a full temporary copy, never by opening the original library
  directly.
- When no embedding function is supplied (the production
  `retrieve_evidence` path), `get_collection` defaults to
  `DashScopeEmbeddingFunction()`. Constructing it only builds an OpenAI client
  object and reads `DASHSCOPE_API_KEY`; it performs no network I/O and no write.
- A network call happens only when Chroma needs an embedding for a
  `query_texts` search. Offline tests neutralise this by monkeypatching
  `src.hybrid_v2.DashScopeEmbeddingFunction` to the local hash function.

## 4. Live test designs (opt-in, independent resources, not executed here)

Both tests must be skipped by default and enabled only by an explicit approval
environment variable, mirroring the existing `PPT_MODEL_LIVE_APPROVED=1`
convention. They must never run in the default `pytest tests` invocation.

### 4.1 Real DashScope LLM smoke test — implemented `tests/live/test_dashscope_llm_smoke.py` (skips by default)
- Gate: `pytest.mark.skipif(os.environ.get("DASHSCOPE_LIVE_APPROVED") != "1")`.
- Preconditions: `DASHSCOPE_API_KEY` present; `dashscope_settings()` passes;
  otherwise skip with a clear reason. No key value is ever printed.
- Resources: no database, no retrieval, no file under the formal tree. All
  outputs go to `tmp_path`; the LLM is called with a short deterministic prompt.
- Calls: exactly one chat completion against the configured model. Assert the
  response is a non-empty string and that redaction helpers leave no key
  fragment in any logged diagnostic.
- Budget guard: hard cap of one request; fail the test if more than one call is
  observed. No retries against the live service in the smoke path.

### 4.2 Real Embedding + RAG test — implemented `tests/live/test_dashscope_embedding_rag.py` (skips by default)
- Gate: same explicit approval variable plus a requirement that
  `RAG_DB_PATH` points inside a pytest-created temporary directory; refuse to
  start if it resolves to the default formal root or any pre-existing library.
- Corpus: a tiny fixed set of representative pages (single digits) defined
  inside the test, written through the normal `HybridTestStore.add_version`
  write path with the real `DashScopeEmbeddingFunction` into the temp root.
- Dimension contract: assert the collection metadata dimension equals the
  served embedding width (expected 1024) before querying; this fails fast if
  the service default changes.
- Queries: a small fixed list of semantic queries, one embedding batch each
  (respecting the ten-text batch limit). Assert known relevant pages rank above
  deliberately unrelated pages and that lexical FTS plus RRF still return
  structured `retrieval` reasons.
- Isolation/teardown: close the store, assert every touched path stays within
  the temp root, then discard it. The formal `runtime_data/word_test_db` is
  never opened; if a PersistentClient-managed copy is needed for the read
  phase, copy the freshly built temp index, do not copy the formal library.
- Repeatability: reruns create a new temp root; no persistent cache or manifest
  outside it is read or written.

## 5. Remaining release acceptance items

- Real DashScope LLM acceptance: not yet executed (no billable call in this round); gated test implemented in section 4.1, skipped unless DASHSCOPE_LIVE_APPROVED=1.
- Real Embedding/RAG acceptance: not yet executable against the hash indexes;
  gated test implemented in section 4.2, using an independent 1024-dimension temp index; skipped unless DASHSCOPE_LIVE_APPROVED=1.
- DOCX Office visual acceptance: only OOXML structural validation currently
  runs (python-docx open, render report checks).
  `test_visual_layout_requires_office_renderer` is a placeholder that is
  skipped without LibreOffice (`soffice` is not on PATH in this environment).
- PPTX Office visual acceptance: same status; python-pptx structural checks and
  geometry audit exist, but no rendered-page visual pass has run.
- New/old workflow compatibility: `proposal` keeps its own CLI path and FATAL
  contract; `generate` composes the same generator/renderers. Covered offline by
  `test_proposal_workflow.py`, `test_proposal_workflow_integration.py` and
  `test_structured_query_results.py`.
- Logging, exception and file publication safety: run logs stay prompt-free and
  credential-free; the FATAL field parity fix is covered by
  `test_workflow_fatal_log_keeps_quality_gate_contract`,
  `test_workflow_fatal_log_keeps_publication_write_contract` and `test_workflow_fatal_log_never_leaks_plain_exception_payload`. Offline suite after the FATAL parity fix: 503 passed, 7 skipped (5 baseline skips plus 2 opt-in live gates).
