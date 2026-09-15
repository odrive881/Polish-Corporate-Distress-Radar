# 0003 — A3: RDF financial document retrieval (Playwright), completing Phase 1

**Stage:** A3 + B (AGENT_SPEC.md §6A, §6B) — the last piece of Phase 1 (AGENT_SPEC.md §10: "Acquisition for 20 hand-picked entities; raw documents in MinIO, manifest in Postgres").

## Why

Plan 0002 delivered A1 (17-entity seed), A2 (GUS BIR1 identity validation), B1 (raw store), and B2 (manifest) — verified clean in its close-out. The one open item was A3: retrieving the actual financial statements and accompanying documents from RDF, which plan 0002's probe (ADR 0007) found blocked behind an Imperva Incapsula WAF on every host.

ADR 0007 is now `accepted`: the user contacted KRS support, explained the project's option C (a human-paced Playwright tier), and got confirmation that 3 documents a minute with a non-invasive automation script is allowed. This plan builds exactly the adapter ADR 0007 specifies, with the confirmed rate folded in from the start rather than the ADR's earlier "1–2 entities/minute" placeholder. Once this lands, Phase 1's deliverable is complete for the 17-entity seed.

## Out of scope (do not do these here)

- A4 (KRZ/MSiG legal events), A5 (NBP/BDL reference data) — later phases.
- C1–C3 (parsing the downloaded documents into the canonical model) — Phase 2. This plan only gets bytes into MinIO with lineage; it does not read their contents.
- Growing the seed universe beyond the existing 17 entities.
- A full v1-universe backfill (a few thousand entities). This plan targets the 17-entity Phase 1 seed only; a production-scale backfill is a later, explicit operation, sized and scheduled separately (L, §6L).
- Anything from ADR 0007 options a or b (sanctioned bulk/partner access) — only revisited if option C fails in practice.

## Steps

### A. Manual HAR capture (prerequisite, no code)

Before writing any adapter code: a human opens `rdf-przegladarka.ms.gov.pl` in an ordinary browser, looks up one seed KRS (e.g. the first entry in `config/segments/construction_sme_v1_seed.yaml`), and saves a DevTools HAR of the search → filing list → one document download. This is normal manual use of the public UI, at human pace, well under the confirmed rate.

- Answers the still-open unknowns from ADR 0007/0004: the filing-list response shape (JSON/XML/HTML), the download URL shape, and whether the RDF submission date (`known_from`) is exposed in the list response or only discoverable per-document.
- Store the HAR **sanitised** — cookies, `visid_incap_*`/`incap_ses_*` session tokens, and any other session identifiers stripped — under `tests/fixtures/rdf/`, alongside a short `README.md` noting the KRS looked up and the capture date.
- This HAR is the source for the fake `FilingBrowser` fixtures in step E and for deciding the exact fields `filing_index` needs (step F may need adjusting once the real shape is known — treat the schema below as a starting point, not final).

### B. Dependencies

- Add `playwright` to `[project].dependencies` in `pyproject.toml` (it's named in the locked stack, AGENT_SPEC §3 "Browser fallback: Playwright", but never added). Run `make lock`.
- `uv run playwright install chromium --with-deps` — pulls system libraries under WSL; document this in the module docstring and in `README.md` setup steps, since it's a new local prerequisite beyond `make install`.
- No new runtime settings dependency beyond what's already in `pyproject.toml`.

### C. Settings (`src/distress_radar/settings.py`)

- Lower `rdf_requests_per_minute` default from `6` to `3`, matching the KRS support confirmation (ADR 0007).
- Update its docstring/comment: the limit is now **per RDF request** (filing-list open + each document download), not per entity — correct the "neither source publishes a rate limit" comment, since RDF's is now confirmed, informally, at 3/minute.
- `.env.example`: update the `RDF_REQUESTS_PER_MINUTE` comment to state the confirmed limit and cite ADR 0007.
- Add `RDF_DAILY_REQUEST_CAP` (optional, sensible default e.g. `500`) if the circuit breaker (step D) is implemented as a hard daily cap rather than purely a consecutive-failure counter — decide during implementation which is simpler to test; both are described in ADR 0007, only one needs a setting.

### D. `acquisition/document_retrieval.py` (A3)

Mirrors the shape of `regon_client.py` (A2): a narrow Protocol for what the adapter needs from the transport, a real implementation, and a fake for tests.

- **`FilingBrowser` Protocol:**
  - `search(krs: str) -> list[FilingListEntry]` — opens the entity's filing list, returns parsed entries (document ref/URL, document type, fiscal year, submission date if the list exposes it).
  - `download(entry: FilingListEntry) -> bytes` — downloads one document.
  - Both raise `TransientSourceError` / `PermanentSourceError` / `ContentCheckFailed` per the shared taxonomy in `base.py`.
- **Playwright implementation** (`PlaywrightFilingBrowser`):
  - One serial browser, one context, launched once per run (not per entity) — matches ADR 0007's "no parallel contexts" limit.
  - Drives `rdf-przegladarka.ms.gov.pl` the way a person would: open → search by KRS → open filing list → download each document (`page.expect_download()` or the context's `APIRequestContext`, sharing the browser session — no cookie export to `httpx`).
  - Captures the SPA's own XHR/JSON responses via `page.on("response")` for the filing list rather than scraping the DOM (sturdier; per ADR 0007).
  - Promotes the probe notebook's `detect_gate` Incapsula-block-page check (`notebooks/exploration/rdf_access_probe.py`) into a `ContentCheck` here — reused on every page load and every download via the existing `ContentCheckFailed` seam in `base.py`. A WAF page is always a failure, never a document.
  - Randomised think-time between UI actions (e.g. 2–5s jitter), separate from the rate-limiter tokens.
- **Fake implementation** (`FakeFilingBrowser`, test-only): returns canned entries/bytes from the step-A fixtures; used by `test_document_retrieval.py`.
- **Raw-first (invariant 2):** the filing-list response and every downloaded document go to `put_raw` before any parsing of their content — only the list *shape* (not its contents) is inspected to build `FilingListEntry` objects, matching how A2 treats the BIR1 XML.
- **Sidecar metadata:** `RawDocumentMeta.source = "rdf"`; note `fetch_tier: playwright` and the document URL, per ADR 0007.
- **Pacing:** `postgres_limiter` (already in `base.py`) built from `SourcePolicy(name="rdf", requests_per_minute=settings.rdf_requests_per_minute)`. One token consumed per RDF network action — filing-list open or document download — not per entity.
- **Circuit breaker:** track consecutive `ContentCheckFailed`/CAPTCHA outcomes across the run; after N (e.g. 3), stop the whole run rather than continuing entity-by-entity, and surface this clearly (log + non-zero exit / Dagster asset failure) rather than silently finishing with partial results.
- **Failure taxonomy** (segment/document-level, quarantined rather than dropped — invariant 4):
  - Timeouts, transient network errors → `TransientSourceError`, bounded retries (existing `tenacity` policy in `base.py`).
  - Unresolved challenge, CAPTCHA, or a WAF block that survives retries → `PermanentSourceError` (`rdf_access_blocked`). The entity stays unresolved for a later run, like A2's source errors.
  - Entity has no filings in RDF → `quarantine` row, reason `no_rdf_filings`, not an error.

### E. Manifest additions (`acquisition/manifest.py`)

New table, added to `SCHEMA_DDL` alongside the existing ones, same idempotent-DDL / `ON CONFLICT DO NOTHING` pattern:

```sql
CREATE TABLE IF NOT EXISTS filing_index (
    krs               char(10) NOT NULL REFERENCES entity_master (krs),
    document_ref      text NOT NULL,      -- RDF's own id/URL for the document
    document_type     text NOT NULL,      -- statement, auditor_report, management_report, resolution, correction, ...
    fiscal_year       int,                -- null if not resolvable from the list alone
    submission_date   date,               -- RDF's date -> known_from; null if unobservable per step A's findings
    sha256            text REFERENCES raw_documents (sha256),  -- null until downloaded
    discovered_at     timestamptz NOT NULL,
    ingestion_run_id  text NOT NULL,
    PRIMARY KEY (krs, document_ref)
)
```

- Typed insert function `insert_filing_index_entries`, `ON CONFLICT DO NOTHING`, following `insert_universe_candidates`'s pattern.
- `raw_documents` / `raw_document_fetches` are reused unchanged for the downloaded bytes (source = `"rdf"`), consistent with ADR 0006's decision to keep one raw-object model across adapters.
- Add `filing_index` to `table_counts()`.
- **Adjust nullability of `fiscal_year`/`submission_date` once step A's HAR shows what the list response actually exposes** — the schema above assumes they might not both be present in the list itself; if RDF's list is richer, tighten the columns to `NOT NULL` instead of leaving this as a known gap.

### F. Dagster wiring (`dagster_defs/assets/acquisition.py`, `dagster_defs/definitions.py`)

- New asset `filing_index`: depends on `entity_master`, wraps `FilingBrowser.search` for every resolved entity not yet indexed (mirrors A2's `unresolved_candidates` pattern — a `filing_index`-side "entities with no filing_index rows and no A3 quarantine row" query keeps re-materialization a no-op).
- New asset `raw_filing_documents`: depends on `filing_index`, wraps `FilingBrowser.download` for every `filing_index` row with `sha256 IS NULL`, then updates that row's `sha256` once downloaded.
- `ConfigurableResource` for the Playwright browser (wraps browser launch/teardown), added to `definitions.py` next to the existing Postgres/object-store/BIR1 resources — no new files, per plan 0002's precedent.
- Docstrings state inputs, outputs, and partition scheme (unpartitioned, same as the existing acquisition assets), per AGENT_SPEC §8.

### G. Docs

- `CLAUDE.md` § "Known moving targets": already updated (this change) to point at ADR 0007's accepted decision instead of "on hold". No further edit needed here unless findings change again during implementation.
- `README.md` status line: update once this plan's Definition of Done is met, to describe Phase 1 as complete and name plan 0003's close-out, following plan 0002's close-out format.
- Note in both: re-run `notebooks/exploration/rdf_access_probe.py` before a production-scale backfill, since the WAF posture and the informal rate confirmation could both change without notice (ADR 0007).

## Tests (`tests/acquisition/`, no network — AGENT_SPEC §8)

- `test_document_retrieval.py`:
  - `FakeFilingBrowser` fed the sanitised HAR-derived fixtures from step A.
  - Filing-list parsing into `FilingListEntry`, including `known_from` extraction (or its absence, if step A shows the list doesn't carry it — then `known_from` must be resolved per-document instead, and this test documents that path).
  - Raw-first ordering: bytes hit `put_raw` before any content-dependent logic runs.
  - `ContentCheckFailed` routing on a synthetic WAF-block-page fixture.
  - Circuit breaker trips after N consecutive failures and stops cleanly.
  - No-filings entity → `quarantine` row with `no_rdf_filings`, not an exception.
- `test_manifest.py` (`@pytest.mark.integration`, live Postgres): `filing_index` DDL is idempotent; re-inserts are no-ops; `table_counts()` includes it.
- Optional `@pytest.mark.integration` test drives real Playwright against local HTML served through `page.route` (e.g. a saved copy of the sanitised HAR replayed locally) — **never against live RDF**, keeping `make test-integration` network-free per AGENT_SPEC §8.
- Register any new pytest markers needed in `pyproject.toml` (the `integration` marker already exists from plan 0002).

## Definition of done

- [ ] Step A's sanitised HAR captured and committed under `tests/fixtures/rdf/`.
- [ ] `playwright` added to `pyproject.toml`; `make lock` run; `chromium` installed locally.
- [ ] `settings.rdf_requests_per_minute` defaults to `3`, documented as per-request in `.env.example`.
- [ ] `document_retrieval.py`, `FilingBrowser` Protocol, Playwright + fake implementations, `filing_index` manifest table, and Dagster assets implemented and tested.
- [ ] `make check` green (ruff, pyright, pytest — no network).
- [ ] `make test-integration` green with `make dev-up`.
- [ ] Dagster run materializes `filing_index` and `raw_filing_documents` against compose services for the 17-entity seed; document counts and any quarantine rows recorded in this plan's close-out, matching plan 0002's format.
- [ ] Re-materializing adds no new raw objects or manifest rows (idempotence, invariant 5).
- [ ] `README.md` and `CLAUDE.md` updated to reflect Phase 1 completion.

## Next plan

Phase 2 (AGENT_SPEC §10): C1–C2, parsing the downloaded financial statements into `financial_statements_canonical`, replacing the prototype `parsing/mapping_engine.py`, against the golden fixtures already in `tests/fixtures/` plus whatever new ones the RDF documents downloaded here provide.
