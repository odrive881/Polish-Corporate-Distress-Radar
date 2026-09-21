# 0006 — Phase 3b: the C3 PDF tier

**Stage:** Phase 3 (AGENT_SPEC.md §10), second of three plans: C3 (§6C3), the PDF route into the canonical model. Plan 0005 covers the remaining XML structure versions; plan 0007 covers SQLMesh `quarantine` and `dq_mart`.

**Order:** after plan 0005 (whose vocabulary it needs), and — since the 2026-09-21 deferral below — no earlier than Phase 4, when MSiG forces a PDF text layer to exist anyway. Plan 0007 no longer waits for it.

## Status: deferred (2026-09-21)

Not cancelled, and not blocked: the steps below stay as written and are what to execute when a trigger
fires. What did not survive review is the *justification for building it now*, which rested on one document.

### Why it is deferred

Evidence, from the document and the entity behind it:

1. **The figures are already in the warehouse.** The FY2024 XML filing carries FY2023 as its `prior_year`
   column — 200 line items, the same small-form line set the PDF renders. Nothing structural is lost: the
   small form declares no cash-flow and no equity-changes statement to begin with.
2. **There is no point-in-time window where the PDF is the only source.** The FY2023 PDF and the FY2024 XML
   were filed **on the same day, 2025-05-28**. The usual argument for a PDF tier — that the rendering is the
   only thing knowable at some `as_of_date` — does not apply to this document.
3. **Both filings are post-outcome.** The entity is `0000181328`, BS CONSTRUCTION, *w upadłości*, declared
   **2025-05-20** (`config/segments/construction_sme_v1_seed.yaml`). Both statements were filed eight days
   after the declaration, so under §4.7 neither can enter a feature vector predicting that event. For this
   entity's label the PDF carries **no usable modelling signal**; the last usable filing is FY2022
   (`known_from` 2023-07-15), already parsed.
4. **The filing-behaviour signal needs no parsing at all.** "Filed FY2023 two years late, as a printout,
   days after going bankrupt" is exactly the §6H filing-behaviour pattern — and submission dates and
   document types are already in `filing_index`. Opening the PDF adds nothing to it.

**What is actually lost by deferring:** an independent copy of FY2023 as originally filed, which would let
`prior_year_consistency` check the FY2024 comparatives against it. That is a data-quality nicety on one
entity-year. The notes inside the PDF are out of scope until Phase 7 either way.

**What the deferral does not change:** the file stays recorded as `needs_pdf_tier` in `parsed_documents`,
visible and counted. Nothing is silently dropped (invariant 4), and `dq_mart` (plan 0007) will report it.

### The cheaper substitute, and its cost

Phase 5 (H) should be allowed to source a missing fiscal year from **the next filing's prior-year column,
carrying that later filing's `known_from`**. It is point-in-time-correct by construction, and the
period-adjacency machinery already exists in `prior_year_consistency`. For this entity it recovers FY2023 in
full.

It is not free, and it is a rule with two sharp edges, not a one-liner:

- `period_start`/`period_end` on a fact are the *filing's* period, so a consumer must resolve a prior-year
  column to the adjacent earlier period, the way `prior_year_consistency` pairs filings — never by
  `fiscal_year - 1`.
- A feature built this way must be dated by the later filing's `known_from`, which is what keeps it honest;
  a comparative column is *not* evidence that existed when the original year closed.
- Where both sources exist, the filed statement wins. The comparative column is a fallback, and which one a
  feature used belongs in its lineage.

### What C3 is still needed for, and when

- **MSiG pre-2021 insolvency notices (Phase 4)** — the *labels*. There is no substitute anywhere. This is
  what should force the PDF text layer into existence.
- **Notes attachments (`Plik`) for text signals** — Phase 7, stage G.
- **Management reports for average employment** — the blocked §4.4 decision (`docs/data_inventory.md` gap 8).

All three are **text extraction**. The expensive, brittle part of this plan — a per-body label map over ~109
small-form labels, with no XSD to validate against and a different rendering per filer — serves only
statements, the one case that has a substitute. So: build the router, tier 1 and the personal-data scan when
Phase 4 needs MSiG, and add the statement label map only if a trigger below fires.

### Triggers — build the statement tier when any of these is true

1. **A live entity's most recent statement is a PDF**, with no later filing to supply comparatives. That is
   the recency a score needs, and the substitute cannot reach it. The seed has none: this entity filed
   FY2024 and FY2025 afterwards.
2. **The backfill beyond the 17-entity seed** turns up PDF-only years that are *not* recoverable from a
   later filing, at a share that would distort the universe — or that concentrates in entities with a
   distress hint, which would bias the positive class. Measure it: count `needs_pdf_tier` in
   `parsed_documents` and join each to a later filing for the same entity.
3. **Phase 4 has built the PDF text layer for MSiG**, at which point the incremental cost here is the label
   map and its fixture alone.

### Two findings to carry into the build

- **The header does not determine the body.** Step B says "detect the structure from the header, exactly as
  C1 does for XML". That is still right for the *version*, but plan 0005 step D found that a `JednostkaMala`
  envelope may carry either the small-form statements or the full-form ones, chosen per statement, with an
  identical header — and the in-scope PDF's header (`SFJMAZ`, `1-2`) is exactly that case. Which body was
  rendered has to come from the rendered labels, so the label map is per body, not per structure version, and
  decision 7's `small-2018-v1-2-pdf` suffix does not by itself say which one was read. The XML side settled
  on body alternatives in the spec plus the body written into `source_element_path`; the `tier` column step A
  adds to `parsed_documents` is the natural place to record it here.
- **This document is the best fixture the project will ever get for a PDF extractor**, and that is an
  argument for using it *when* the tier is built, not for building it now: the FY2024 filing's prior-year
  column is an authoritative, line-by-line oracle for what the rendering must yield. Step D's hand-check is
  strictly weaker — see the amendment in step D.
- **The vocabulary this plan needs already exists**: `jednostka_mala`, the small-form chart codes, and ten
  short-form golden fixtures, including `small_2018_v1_2_mala_kalk_2021`, the same form and schema as the PDF.

## Why

One stored seed download cannot be parsed as XML: KRS `0000181328`, FY2023, recorded `needs_pdf_tier` by C1 (`zip:SF2023/SF2023.xml>epuap:Zalacznik[1]>base64`). The entity filed a PDF inside an ePUAP envelope instead of a structured statement. It was the entity with the thinnest structured coverage in the seed — 1 parsed file against 9 that plan 0005 has since mapped; with those 10 parsed, this PDF is the only statement it has filed that the pipeline cannot read (FY2023, the middle of its 2018–2025 run).

AGENT_SPEC §6C3 also routes IFRS filers and pre-2021 MSiG notices here. Neither exists in the seed today: the `czyMSR` flag is unreliable and every flagged statement is a UoR structure (plan 0004), and MSiG notices arrive in Phase 4.

## What the document actually is (probe, 2026-09-20)

Read directly out of MinIO and opened with PyMuPDF:

- **14 pages, 417,308 bytes, born digital.** Every page has a text layer, 509–4,074 characters each, 33,097 total. **Zero images on any page** — nothing is scanned.
- PyMuPDF's table finder locates 1–4 tables on 12 of the 14 pages.
- The header inside the PDF reads `SprFinJednostkaMalaWZlotych`, `SFJMAZ`, `1-2`, `Wariant sprawozdania 1`, period `01.01.2023`–`31.12.2023`. **It is a rendering of exactly the structure plan 0005 maps** — the same small form, the same schema version. The filer produced the statement in e-sprawozdanie software and filed the printout rather than the XML.
- **No natural persons found.** Scanned the extracted text for `Podpis`, `Imię`, `Nazwisko`, `PESEL`, `Kierownik`, `Zarząd`, `Prezes`: no hits. The only matches for those substrings are accounting vocabulary (`odpisy amortyzacyjne`, `rozliczenia międzyokresowe`, `data sporządzenia`). Invariant 6 is not at risk for this document — but that is one document, and step E makes the scan a precondition, not an observation.

Also stored, and **not** in scope: three type-1 PDFs for KRS `0000070294` (2017 filings — `Bilans`, `Rachunek zysków i strat`, `Informacja dodatkowa` as separate documents). Pre-2018 statements were put out of v1 scope in commit `b2bef41`. They are useful as extra C3 test material and nothing more.

## The scope question this plan asks the owner to settle

§6C3 specifies three tiers: PyMuPDF → Docling → vision LLM. **The seed justifies only the first.** There is no scanned page anywhere in scope, no IFRS filer, and no MSiG notice until Phase 4. Building a Docling integration and a vision-LLM tier now means building two routes with zero documents to validate them against, and the vision tier additionally needs an LLM provider and key that `.env.example` does not yet have (`docs/data_inventory.md` §8, gap 4).

**This plan therefore builds the router and tier 1, and leaves tiers 2 and 3 as unimplemented router branches behind a stable interface** — each raising a typed "tier not available" that quarantines the document with a reason code, so a document that genuinely needs them is recorded rather than silently mishandled. Tier 2 lands when the notes attachments arrive (Phase 7, stage G), tier 3 when MSiG's pre-2021 notices do (Phase 4) — each with a real corpus to measure against.

If you would rather have all three tiers now, say so before step B; the cost is roughly doubling this plan and adding the LLM-provider decision to its critical path.

## Decisions this plan makes (flag any you disagree with before step B)

1. **A PDF-derived fact is a fact like any other.** It lands in `financial_statements_canonical` with the same columns, the same chart codes, and the same grading. Nothing downstream should need to know the bytes were a PDF — otherwise every later stage grows a special case.

2. **The tier is recorded per file, not per fact**, as a new `tier` column on `parsed_documents` alongside the existing `member_kind`. §6C3 requires recording which tier handled each document; that is lineage about the file, and `financial_statements_canonical` already carries `source_member` and `source_document_hash` to join on. This keeps §5 unchanged.

3. **`source_element_path` carries the PDF locator**, not an XPath: `page=7;table=1;row=12;col=2`. The column's §5 description already allows a non-XPath form (plan 0004 widened it for `….USER` unions). A fact must be traceable to the place it was read from, and for a PDF that is a cell.

4. **The accounting identities are the extraction oracle.** A rendered balance sheet that balances, whose subtotals sum, and whose net result ties to the income statement is almost certainly extracted correctly; one that does not is either a bad extraction or a bad filing, and both must be looked at. So C3 output runs through the unchanged §4.3 checks, and **a C3 document that grades `quarantined` is treated as an extraction failure until proven to be a filing defect** — the opposite default from XML, where the filing is authoritative. This is the cheapest strong validation available and it costs no new machinery.

5. **Extraction is deterministic and idempotent.** Tier 1 is pure PyMuPDF over stored bytes, so re-running reproduces the output exactly (invariant 5). This is a further reason to keep the vision-LLM tier out for now: it is the one tier that cannot promise this without a cached-response mechanism, which is its own design problem.

6. **A PDF whose extraction cannot be trusted is quarantined, never partially written.** If the router cannot find the statements, or the identity checks fail in the current-year column, the document is quarantined with a reason code and no facts are written. Half a balance sheet is worse than none (invariant 4).

7. **Structure version for PDF-derived facts is the rendered structure, suffixed** — `small-2018-v1-2-pdf`. It is the same statutory vocabulary reached by a different route, and `dq_mart` (plan 0007) must be able to show extraction quality separately from XML parsing quality.

## Out of scope

- Docling (tier 2) and the vision LLM (tier 3) as implementations, and the LLM provider decision.
- The notes attachments (`Plik`) inside XML statements — stage G, Phase 7, even though they are PDFs.
- MSiG notices — Phase 4.
- The three pre-2018 PDFs, beyond optional use as test material.
- OCR of any kind. Nothing in scope is scanned.
- Any change to A3 or to stored bytes.

## Steps

### A. Router and manifest

- `parsing/pdf/router.py`: given the PDF bytes, decide the tier. Tier 1 when every page carries a text layer above a character-count threshold and no page is image-only; otherwise tier 2 or 3 per §6C3's rules, both of which raise `TierNotAvailable` for now.
- The threshold is a named constant with the probe's numbers in a comment (the in-scope document's thinnest page has 509 characters), not a magic number.
- Add `tier` to the `parsed_documents` DDL in `parsing/manifest.py`, nullable, set only for `member_kind = 'pdf'`. Follow ADR 0006 for the migration.
- `containers.py` already yields the PDF member with `kind = 'pdf'`; C1 keeps recording `needs_pdf_tier` for anything the router refuses.

### B. Tier 1 extraction — `parsing/pdf/pymupdf_extractor.py`

- Open from bytes with PyMuPDF, never from a path, and never over the network.
- Read the rendered header first (`Kod sprawozdania`, `Kod systemowy`, `Wersja schemy`, `Okres od`/`do`). The probe shows these are present as text. **Detect the structure from the header, exactly as C1 does for XML** (§6C1: never from the filename, and here never from the layout either). A PDF whose header cannot be read is quarantined `pdf_header_unreadable`.
- Locate each statement section by its heading, then extract its table with `find_tables()`.
- Map rows to chart codes through a **declarative label map**, `config/mappings/pdf/<body>.yaml`, keyed on the body's Polish labels — which already exist in `canonical_chart.yaml` and the body files. Matching is on a normalised label (case, whitespace, the `–`/`-` dash variants the probe shows in short-form labels), not on row position: row order in a rendering is not guaranteed and a positional map would break on the first filer whose software lays out differently.
- An unmatched row is a hard error for the document, not a skipped row: an unrecognised label means the map is incomplete, and silently dropping it loses data (invariant 4).
- Amounts are parsed from the Polish rendering (`1 058,62`, non-breaking and thin spaces as thousands separators, comma decimal) straight to `Decimal`. Never `float`.

### C. Wiring

- `financial_statements_canonical` gains the C3 branch: files the router accepts are extracted, contract-checked, graded and written exactly like XML ones. Files it refuses are quarantined with `pdf_tier_unavailable` and their tier recorded.
- Run metadata counts documents by tier and outcome.
- No new Dagster asset.

### D. Fixtures and the golden check

- The in-scope document, trimmed the way plan 0004 trimmed the XML fixtures, committed under `tests/fixtures/pdf/` with its provenance in a README.
- `small_2018_v1_2_pdf.expected.json`: the golden output. **Amended 2026-09-21: build it from the FY2024 filing's prior-year column, not by eye.** That column is the same company's same FY2023 figures, filed as XML and already parsed, so it is an authoritative oracle: every line the extractor produces must equal it, and any line it cannot match is a mapping gap to investigate rather than a judgement call. Hand-checking is the fallback for the lines the comparative column does not carry, and the README records which lines were verified which way.
- Synthetic negatives: a copy with the header removed, a copy with one table row relabelled to something unmapped, and a copy with an amount altered so the balance sheet no longer balances.

### E. Personal-data precondition (invariant 6)

- Before any extracted text is written anywhere, scan it for natural-person indicators and fail the document if any are found. The probe shows this document is clean, but a rendered statement can carry a signature block, and unlike XAdES there is no structural element to strip.
- The scan is a shared helper, reused from or alongside `acquisition/redaction.py`, and it is a test-covered gate — not an eyeball check performed once.
- The committed fixture is scanned in CI, like the XML fixtures are.

### F. Docs

- **ADR 0011**, recording the tier-scoping decision and its trigger conditions: exactly what has to appear in the corpus for tier 2 and tier 3 to be built.
- `DIRECTORY_STRUCTURE.md` §2: `config/mappings/pdf/`, and the `parsing/pdf/` modules that now exist versus the ones still planned.
- `AGENT_SPEC.md` §6C3: note that the tier is recorded on `parsed_documents`, and that C3 facts are graded as extraction evidence (decision 4).
- `docs/data_inventory.md`: gap 4 (no LLM provider) is deferred, not resolved, with this plan's reasoning.

## Tests

- **`test_router.py`:** a text-layer PDF routes to tier 1; an image-only page routes onward and raises `TierNotAvailable`; the threshold boundary is exact.
- **`test_pymupdf_extractor.py`:** the golden fixture reproduces its expected JSON exactly; values are `Decimal`; Polish number formats parse, including the space and non-breaking-space thousands separators; an unmapped label raises; a missing header quarantines.
- **`test_accounting_identities.py`:** the golden PDF fixture passes every applicable check — this is decision 4's regression test; the altered-amount fixture quarantines.
- **Personal data:** the scanner catches a synthetic signature block; the committed fixture is clean.
- **Idempotence:** extracting twice yields byte-identical Parquet.
- **No network:** as everywhere else, PyMuPDF opens from bytes only.

## Definition of done

- [ ] Router, tier 1 extractor, label map and manifest `tier` column implemented and tested.
- [ ] KRS `0000181328` FY2023 parses into canonical facts that **pass the accounting identity checks** and **equal the FY2024 filing's prior-year column line for line**, or is quarantined with an investigated reason.
- [ ] That entity's structured coverage goes from 10 files (after plan 0005) to 11, with no gap in 2018–2025.
- [ ] Golden fixture hand-verified against the rendering; personal-data scan green in CI.
- [ ] Tiers 2 and 3 quarantine explicitly with a recorded reason; no document is silently skipped.
- [ ] ADR 0011 accepted; docs from step F updated.
- [ ] `make check` and `make test-integration` green; re-materialization byte-identical.

## Risks

- **One document is a thin basis for a label map.** The map will fit this filer's software and may not generalise to the next PDF filer. That is acceptable because the failure mode is loud — an unmatched label raises rather than guessing (step B) — but the map should be expected to grow with each new PDF, and the plan does not pretend it is complete.
- **Table detection is heuristic.** PyMuPDF found tables on 12 of 14 pages; the two without are prose. If `find_tables()` misses a statement section, the document quarantines rather than yielding a partial statement (decision 6), so the risk is coverage, not correctness.
- **Decision 4 inverts the usual default** and could mask a real filing defect as an extraction bug. Mitigated by requiring every quarantined C3 document to be traced to the rendering and classified, exactly as plan 0004 did for its 21 quarantined XML files.

## Next plan

Plan 0007 (SQLMesh `quarantine` + `dq_mart`) completes Phase 3.
