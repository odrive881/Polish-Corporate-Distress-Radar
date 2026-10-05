# 0013 — Phase 7: text signals, measured, folded into features

**Stage:** Phase 7 (AGENT_SPEC §10: "Text extraction with measured eval, folded into features").
- **Spec stages:** G1 (Polish preprocessing, lemma prefilter), G2 (constrained extraction with evidence), G3
  (golden set and CI gate), §6G; the text family of H (§6H); PROJECT_OVERVIEW stage 6.
- **Output:** the canonical dataset `text_signals` (§5), and a feature set that reads it.
- **Domain rules:** point-in-time (§4.7, invariant 1): a signal is known when the document carrying it was
  filed; no silent loss (invariant 4): an absent document is not an absent signal; legal entities only
  (invariant 6): filed text names people, and no name may reach a stored signal, a prompt or a committed file.

**Order:** after plan 0012 (complete). It does not wait for ADR 0013: like Phase 6, it builds and measures
machinery on the seed. Phase 8 (LightGBM, survival, SHAP) reads the feature set this plan adds.

## Status: active (2026-10-05): owner decisions 0–8 accepted; steps A to D and F to I built, step E's tooling built, with the notes' queue (50 of 240 pages labelled and committed) and the auditor reports' (`golden_sample_v2`, 102 pages); the rest of the labelling and the first model call wait on the owner; the auditor reports of decision 0(c) are stored, dated from the owner's list (decision 6 amended 2026-10-02) and read by the text job, the opinion by the report's headings (`rules_v2`, 2026-10-05); their features (decision 7) are next

### Where this stands (2026-10-05): what waits on the owner, in order

1. **Label the rest of the golden set:** the notes' queue (`golden_sample_v1`, 240 pages, 77 labelled on
   2026-10-05) and the auditor reports' (`golden_sample_v2`, 102 pages of 20 reports, 15 with a span to mask by
   hand), picked at the top of the notebook (`uv run marimo run
   notebooks/labelling/golden_set.py` from a WSL terminal, not through Claude Code, whose background tasks stop
   after 30 minutes). Save each page before moving on; `make label-export` writes a checkpoint to
   `evals/text_signals/` for review and commit.
2. **Confirm the provider's data-retention terms** (decision 2), then set `ANTHROPIC_API_KEY` and
   `EXTRACTION_API_CONFIRMED=true` in `.env`. Until then the eight model signals are `not_run`, and the notes'
   features are null wherever the prefilter selected a page.
3. **Accept each signal's first result** after `make eval`: `make eval-accept SIGNAL=... BY=...`.
4. **Then rerun** the `text` job and the `features` job: the notes' features fill in, with no new version.
5. **Decision 0(c), auditor reports: stored and dated (2026-10-02, below).** Left for the owner: the three
   reports with no date in the list and 0000507997's "2019" file (below). The reports' text step is built
   and run on the seed (2026-10-05, progress), and the opinion rule reads the report's headings (`rules_v2`,
   2026-10-05); the report pages are queued for labelling (`golden_sample_v2`, 102 pages); left: labelling
   them and the feature set (decision 7).
6. **Approve or change** step I's four departures from decision 7 (progress, step I), and the choices recorded
   "for the owner to see" in the 2026-10-05 progress entries (reports in the same datasets with `document_kind`,
   every signal read from a report, the notes' features reading the notes only).

### Decision 0(c): the auditor reports (2026-10-01)

- **Captured by the owner** with Power Automate Desktop, at a human pace, without HAR files (PAD does not
  record DevTools). Kept unredacted and local in `.cache/rdf_auditor_reports/<krs>/` (gitignored; a copy dropped
  at the root, `/auditor_reports/`, is ignored too). Nothing from them is committed; on 2026-10-01 nothing read them
  (since: imported and dated on 2026-10-02, read by the text step on 2026-10-05, below and in the progress section).
- **What is there:** 57 files in 13 entities: all 52 type-19 rows of `filing_index` (12 entities) and 5 pre-2018
  reports (0000209396, 0000225506, 0000386777, 0000440028, 0000498679), which have no type-19 row. Four
  entities have no auditor report on RDF: 0000041651, 0000070294, 0000188883, 0000397658. Of 56 PDFs, 48 have a
  text layer and 8 are scans (`needs_ocr`; OCR is its own decision). One file is not a report: 0000507997's
  "2019-12-31" is a statement XML (`JednostkaMala`), to be checked on RDF.
- **What they hold** (keyword counts over the 48 text PDFs, no labels): wording of 6 qualified opinions and 1
  disclaimer; "draws attention" wording in 38. Modern Polish opinions rarely say "bez zastrzeżeń", so
  `rules_v1`'s opinion rule will need the opinion section's heading. 13 reports, in 5 of the distressed
  entities, are on periods ending at least six months before the entity's first petition or opening; 44 belong
  to entities with no event.
- **The blocker: no submission date.** A row's submission date comes from its expanded detail, which the HAR
  carries; none of the 52 type-19 rows has one. By decision 6 a document without its own detail is not used:
  the report's signing date is only a lower bound on its filing, and the statement's filing date can precede
  the report's, so any proxy could leak (invariant 1), most of all for late-filing distressed entities. As they
  stand the reports can feed the golden set (`opinion_type`, `emphasis_of_matter`), not a feature.
- **Before anything reads them:** they go through `acquisition/redaction.py` into the raw store, as statements
  do (ADR 0009: 49 PDFs carry document metadata, the ZIPs filers' file names, and the reports name the key
  auditor). Each of the 52 matches its `filing_index` row by (krs, period end, type 19): one per period.
- **Recommended:** one short HAR session per entity (12), no downloads: search the entity, expand each
  *Sprawozdanie z badania* row so its detail loads, save the HAR to `RDF_MANUAL_INBOX`; `rdf_manual_import`
  already reads details. Then a redacting importer for the captured reports (dated by their own detail), and
  `modified_opinion`, `emphasis_of_matter` and an audit-firm change feature as a new feature set (decision 7).
- **Update, 2026-10-02: dated from a list, decision 6 amended (owner).** The owner collected each report's
  "Data dodania" (the detail's `dataDodania`, what `known_from` is everywhere else) with a script into
  `filing_dates.csv` (`krs`, period, date, RDF's numeric `idDokumentu`), checked against the detail on screen
  (0000123720, 2025: 2026-07-06). **Decision 6 now reads:** a separately filed document's `known_from` is its own
  detail's submission date, or, without a detail, that date as listed by hand, provided the list is stored raw and
  every date points back to it. `rdf_auditor_report_import` (`acquisition/report_import.py`) builds this: each
  ZIP as RDF delivered it is redacted (ADR 0009) and stored on its type-19 row; the list is stored raw, and each
  date fills `submission_date` with `filing_index.submission_date_sha256` naming the list. A detail captured later
  replaces the date and clears that column. Imported twice to the same rows; `personal_data` check passed.
  - **Result:** 51 of 52 type-19 rows stored, 49 dated. Against the statements of the same period: 31 the same
    day, 11 later (up to 511 days, mostly the late filers 0000153402 and 0000181328), 7 one to 14 days earlier.
  - **Not used, and why:** 0000507997's "2019" file is a statement XML, not stored (its row is dated: the list's
    2020-07-14 is not the statement's 2020-07-18); 0000181328 2019 is `unknown` in the list; 0000291203 2024
    and 2025 are not in it. The five pre-2018 reports have no type-19 row: their period has one type-2 row each,
    a code `rdf_document_types.yaml` does not name yet. Confirming type 2's name on RDF and adding it as
    `canonical: auditor_report` (a new config version) lets the same import take them.
  - Found on the way: `redaction.py` raised on a PDF whose `/Metadata` entry is not a stream; it now reads the
    catalog entry and removes it (regression test added). The stored objects re-scan clean.


### Progress

- **Step A (2026-09-29), the census** (`notebooks/exploration/text_census.py`, counts only, no text read out):
  - **Separately filed documents: 316 listed since 2017, none detailed or downloaded.** Approval resolutions
    (type 3) 115 in 15 entities; loss-coverage resolutions (4) 91 in 15; management reports (20) 58 in 12;
    auditor reports (19) 52 in 12. Plus the pre-2018 types: 68 statements as PDF parts (1), 15 management reports
    (5), 8 of type 2. Auditor reports exist for 12 of 17 entities, more than "small companies are rarely audited"
    suggested.
  - **The going-concern flags are in all 130 XML statements** (the 131st is the `needs_pdf_tier` PDF). Micro
    statements keep them in `InformacjeOgolneJednostkaMikro`, the others in `WprowadzenieDoSprawozdania...`.
    36 statements report a threat, 18 of them that the statements are *not* prepared on a going-concern basis;
    34 carry a `P_5C` description.
  - **What the flag separates, on a hand-picked seed:**

    | statements | count | report a threat | entities flagging / filing |
    |---|---|---|---|
    | distress entities, filed before their first event | 27 | 6 | 3 / 6 |
    | distress entities, filed on or after it | 36 | 29 | 9 / 9 |
    | entities with no event | 67 | 1 | 1 / 8 |

    Most flags follow the event (a company in bankruptcy says so), and point-in-time dating keeps those out.
    Before it, 6 of 27 statements in 3 entities flag, against 1 of 67 elsewhere: suggestive, and far too few
    to be evidence. 3 of the 9 distressed entities filed no XML statement before their first event (their
    events precede 2018).
  - **Wariant 2 (FY2025+) adds two structured fields the plan did not expect:** average annual employment in
    full-time equivalents (UoR art. 64 ust. 1 pkt 4) and whether the statement must be audited. They are
    numbered by the introduction's variant, not by one name: `P_8`/`P_9` in the full introduction (after its
    merger block `P_6`), `P_7`/`P_8` in the small and micro ones, from the 2025 XSDs' documentation. In
    earlier structures `P_7`/`P_8` are accounting-policy text, never employment. On the seed: 9 statements
    (8 full, 1 micro), 5 audit required, 4 not. This is the first structured source of employment
    (`docs/data_inventory.md` gap 8), from FY2025 on only.
  - **Embedded notes: 295 attachments in 100 statements of 16 entities**: 280 PDFs, 13 ZIP or office files, 2
    other. The PDFs have 1,182 pages, 707 with a text layer (200+ characters); 126 PDFs are text throughout,
    15 mixed, **139 have no text layer at all**. The scans are not where the events are: 131 of the 139 belong to
    entities with no event, and 267 of the 323 pages from distressed entities have text.
  - **What changes in the decisions:** 0(a) stands and is the first build; 0(b) is viable without OCR for the
    distressed entities, but see the new risk on missingness; 0(c) is 316 documents of manual capture for the
    seed; decision 7 gains employment and the audit flag (below).
- **Owner decisions (2026-09-29):** all nine accepted as recommended. Build order agreed: the introduction's
  flags and employment first (step B), then masking and the notes' text layer (C, D), the golden set (E), the
  extractors with the response store and gate (F, G), `text_signals` (H), features and the backtest (I); the
  hand capture of decision 0(c) is decided after I.
- **Step B (2026-09-29):** `parsing/statement_introduction.py`, read with
  `config/mappings/statement_introduction.yaml`, written by the `financial_statements_canonical` asset to a new
  dataset, `statement_disclosures` (AGENT_SPEC §5), from the same validated files and with their lineage.
  - **Changed from the step as drafted, for the owner to see:** the flags go to their own dataset, not to
    `text_signals` rows with `extraction_method` `xml_field`. Employment and the audit flag are not text
    signals and have no `signal_type`, and `text_signals` carries an evidence span and a page that a
    structured field does not have. `text_signals` will take its `going_concern_uncertainty` rows from the
    notes (step F) and, if decided, the auditor reports. `P_5C`'s text is not kept at all yet: only whether it
    is filed. It waits for step C's masking.
  - **The config is pinned to the XSDs:** for every mapped version and every introduction block its XSD
    declares, each item's element is documented as that item, and each flag's encoding is the XSD's own
    (`xs:boolean`, or the enumeration 1, 2). Swapping employment to the wrong number fails the test. A new
    structure version not listed in the config fails at load time.
  - **Live run on the seed**, twice through the `features` job: 309 rows from 129 statements, the same bytes both
    times. `going_concern_threat` true in 35, `going_concern_basis` false in 18, a description filed in 33;
    employment and the audit flag in the 9 wariant 2 statements (5 audited). The census's 130 and 36 include
    one statement the canonical stage does not map. `financial_statements_canonical`, `identity_check_results`,
    `restatement_events`, `legal_events` and `feature_store` are byte for byte unchanged: the new config is not
    part of the spec hash, so no file is re-parsed under a new run id; each row carries `config_version`.
- **Step C (2026-09-30):** `extraction/page_text.py` (the attachments embedded in a statement, each PDF's text
  layer per page: `text`, `needs_ocr` for a scan, `sparse` for a blank or signature page; office files
  `unsupported`) and `extraction/masking.py` (`[osoba]` for `persName`, `[pesel]` for 11-digit runs, `[email]`,
  `[telefon]` after a phone marker; company names kept). ADR 0009's third addendum records the rule.
  - **The model is not in `uv.lock`.** `pl_core_news_lg` 3.8.0 is a 550 MB wheel, and `uv` restarts a failed
    download from zero: on this connection it failed after hours. `make models` fetches it resumably, checks the
    SHA-256 pinned in the Makefile and unpacks it under `.cache/models/` (`SPACY_MODEL_DIR`); CI runs it with
    its own cache. spaCy itself is locked. The masker refuses any other model version (`MASKING_VERSION` 1).
    The pinned file was downloaded by the owner through a browser and matched the first 382 MB of an
    interrupted download byte for byte, and the archive tests clean.
  - **Tests:** planted, invented names in inflected forms (*Annie Wiśniewskiej*, *Janem Kowalskim*), a double
    surname and signature lines are all masked; company names and figures stay; an amount written with spaces
    is not a phone number; masking is stable and idempotent. Synthetic PDFs only: real notes stay out of git.
  - **On the seed** (census section 6, counts only): 702 text pages (the census's 707 counted characters before
    whitespace is normalised), 459 scanned (`needs_ocr`), 21 sparse, 15
    unsupported attachments, no unreadable PDF. The masker replaced 697 names on 217 pages, 43 e-mail
    addresses and 43 phone numbers, and no PESEL. These are counts, not quality: the masker's recall and
    over-masking are measured on the golden set (step E). Masked page text is not in the warehouse yet; step H decides
    where it lives. (Since step E, the local labelling queue and the golden set hold masked pages.)
- **Step D (2026-09-30):** `extraction/preprocessing.py` (sentences and lemmas from the pinned model, NER and
  parser off, its sentence recogniser on) and `config/extraction/prefilter_v1.yaml`: per `signal_type`, terms
  whose words must all appear in one sentence, in any order; a word is alternative lemmas or a `stem*`.
  - **The lemmatiser is uneven, and the config says where.** It leaves a sentence's capitalised first word as
    it is (`Występuje`), so text is lowercased first, one character for one so offsets hold. Some verbal nouns
    come out as the verb (`naruszenia` → `naruszyć`), some as no word (`zwrócenie` → `zwrócć`), and a plural can
    be its own lemma (`warunki`): those words are stems. Every term carries an invented example, and a test runs
    each through the model: three terms failed it while being written, and were fixed before any page was read.
    Bare `układ` is not a term: it is also the income statement's layout (*układ kalkulacyjny*).
  - **The enum is pinned to AGENT_SPEC §5** by a test that reads the spec's line.
  - **On the seed** (census section 7, counts only; no page was read): 190 of 702 masked text pages are selected
    for some signal, 88 of 262 from entities with a distress event and 102 of 440 from the rest.
    `going_concern_uncertainty` 55 and 35 (it also selects the going-concern accounting policy, on purpose),
    `post_balance_sheet_event` 44 and 20, `loss_coverage_resolution` 18 and 56, `litigation` 3 and 5,
    `key_customer_loss` 0 and 7. Nothing for `covenant_breach` or `continued_existence_vote`, nor for the
    auditor-report signals, whose documents are not in hand. Whether a zero is an absence or a miss is what the
    rejected-page sample of step E measures; until then these are selections, not signals.
- **Step E, tooling (2026-09-30); the labels themselves are the owner's.**
  - **The queue** (`extraction/golden.py`, `extraction/label_queue.py`, `make label-queue`): every masked text
    page `prefilter_v1` selects, and a fixed random sample of those it rejects, from
    `config/extraction/golden_sample_v1.yaml` (50, drawn by hashing each page id with the file name). On the seed:
    **240 pages to label**, 190 selected and 50 of the 512 rejected. Local (`LABELLING_DIR`, gitignored), rebuilt
    byte for byte, and a rebuild keeps every row already there; a labelled page missing from a rebuild is an error.
  - **Labelling** (`notebooks/labelling/golden_set.py`): each page for all nine signals, so a page selected for
    one signal is a labelled rejection for the others; evidence must be a verbatim span of the masked page;
    `opinion_type` takes one of four values, the other signals only present or absent. A name the masker missed is
    masked by hand, counted, and those counts against the masker's are its recall. Over-masking cannot be judged
    from masked text and is not measured. Definitions: `evals/text_signals/labelling_guide.md`, accepted by the owner
    as drafted (2026-09-30).
  - **Changed from the step as drafted, for the owner to see:** the committed set is `pages.jsonl` (each page's
    masked text, once) plus one `<signal_type>.jsonl` per signal with a label for every page, not one file per
    signal each carrying the text: every page is labelled for every signal, and the text nine times over would be
    the same bytes nine times.
  - **The masking check** (`golden.masking_findings`): the masker re-run over every text field of an eval file;
    anything it would still replace is a finding, reported by line and kind, never quoted. The export refuses a
    page that fails it; `make check` runs it over `evals/`; the pre-commit scan runs it over staged eval files
    instead of the raw-download markers (a notes page may well contain the word "PESEL"), and without the model it
    refuses rather than passes. The masker is not idempotent everywhere: on 6 of the seed's 702 text pages a
    second pass masks one more person, and one of them is in the queue (a rejected-sample page); the notebook
    shows the span in red, for the owner to mask.
- **Step F (2026-09-30), built and tested without a single call.**
  - **What:** `extraction/schemas.py` (a strict JSON schema per signal, sent as structured output, and the
    Pydantic model that validates every answer, first call and replay alike; a test keeps them in step),
    `extraction/rules.py` with `config/extraction/rules_v1.yaml` (`opinion_type` by lemma rules, most severe
    opinion on the page wins, each term's example checked through the model), `extraction/response_store.py`
    (decision 3: responses in MinIO under `extraction/responses/`, keyed by the SHA-256 of the full request body,
    write-once; Postgres manifest `extraction_responses`), `extraction/extractor.py` (the request, the evidence
    check, a sync transport for development and a Message Batches transport for backfills), and
    `config/extraction/extractor_v1.yaml` naming each signal's method and prompt; eight prompts in
    `prompts/extraction/`, each carrying its definition from the accepted labelling guide, and the
    `prompts/CHANGELOG.md` entry. `anthropic` 1.9.0 is locked.
  - **Kept or discarded:** an answer is kept only if it validates and, when present, its evidence is a verbatim
    substring of the masked page; otherwise it is discarded with a reason code (`no_evidence`,
    `evidence_not_on_page`, `invalid_output`, `refusal`, `max_tokens`, and `api_error` for no response, which is
    not stored and is retried on the next run). A second run over the same pages makes no call and gives the
    same answers; pages with identical masked text share one request.
  - **Changed from decision 2, for the owner to see:**
    - the model is `claude-opus-5-5`, not `claude-opus-5`: the decision named "the SDK's current default", and
      the current default Opus is now 5.5, at a lower price ($4 / $20 per million tokens against $5 / $25). It
      is one line of `extractor_v1.yaml`;
    - `effort: medium` is set explicitly (this model's default; thinking cannot be switched off on it);
    - **no refusal fallback**, though the SDK guidance recommends one by default: it re-runs a refused request
      on another model, whose answer would be stored under a key naming this one, against decision 2's own rule
      that no model replaces another without a golden-set comparison; the Batches API rejects it anyway. A
      refusal is discarded and counted;
    - not `messages.parse`: the schema is sent as `output_config.format` and the answer validated by the same
      Pydantic model, so one fixed request body serves a single call and a batch and is what the key hashes.
      Nothing is parsed from free text either way;
    - the auditor's going-concern rule waits for auditor reports (decision 0c): on the notes that signal is
      free text, and goes to the model.
  - **The gate on live calls:** `anthropic_client` refuses unless `EXTRACTION_API_CONFIRMED=true` and
    `ANTHROPIC_API_KEY` are set (both in `.env.example`); nothing sets them.
  - **On the seed, counted without a call:** the prefilter selects 233 model requests over the 240 queued pages
    (`going_concern_uncertainty` 90, `loss_coverage_resolution` 74, `post_balance_sheet_event` 64, `litigation`
    8, `key_customer_loss` 7), about 930,000 characters of input with the prompts. At Opus 5.5's list price
    that is a few dollars of input, plus the thinking and answer tokens, which are not known before a run:
    an estimate of well under $10 in all, half that through batches. Not measured.
- **Step G (2026-09-30), built; nothing to score until the first labels.**
  - **What:** `extraction/eval_harness.py` (scores and the gate, no I/O), `extraction/eval_run.py` (`make eval`,
    `make eval-accept`), `config/extraction/eval_gate_v1.yaml` (tolerances, 0: hold or improve), and
    `prefilter_version` and the extractor in use (`EXTRACTOR_VERSION`) named in config and settings.
  - **A result** per signal and method (`results/<signal_type>/<prompt>__<model>.json`, or
    `<rules_version>__rule.json` for a rule): every golden page's outcome, the SHA-256 of what it depends on
    (golden files, prompt or rules, schema, prefilter, model, effort), and three scores with their counts: the
    prefilter's recall on labelled positives (and the positives it misses in the rejected sample, which stand
    for several in the whole pool), the extractor alone on the pages the prefilter selected, and end to end,
    a page not selected counting as absent. A discarded extraction counts as absent, with its reason counted;
    for `opinion_type` the wrong opinion is both a false positive and a false negative. The masker's recall
    goes to `results/masking/`.
  - **The gate** (a test in `make check`, offline): for a signal with any result, the method in use needs a
    result on the current files, its stored scores must be what its examples score, and its end-to-end
    precision and recall may not fall below the owner's last accepted result on the same golden file, beyond
    the tolerance. The committed results are checked on every run.
  - **Settled while building, for the owner to see:**
    - the gate holds end-to-end scores, not the extractor's alone: that is what reaches the features, and a
      prefilter change is gated with the rest;
    - **the gate starts per signal with its first result**, not with its golden file: labels can be committed
      before the provider's terms are confirmed without failing `make check` (decision 4 as written would fail
      it until the first model run);
    - acceptance is the owner's act (`make eval-accept SIGNAL=... BY=...`), and a new golden file needs a newly
      accepted result: scores on different labels are not comparable. A rerun that reproduces a result keeps
      its acceptance; a changed one loses it;
    - `make eval` uses the Batches API by default (`--sync` for single calls), and skips model signals, saying
      so, until `EXTRACTION_API_CONFIRMED`. `opinion_type` is scored offline.
- **Step H (2026-09-30), built and run on the seed.**
  - **What:** `extraction/text_signals.py` (reads the notes of every stored XML statement, masks, prefilters,
    extracts, and builds the datasets), `extraction/contracts.py` (Pandera), `extraction/manifest.py`
    (`text_extractions`: the first run per statement file and pipeline hash, so reruns reproduce the bytes),
    the asset `text_signals` in `dagster_defs/assets/extraction.py` with its blocking check `evidence_masked`,
    and the `text` job. Quarantine stages `G1` (an unreadable attachment) and `G2` (a discarded extraction, per
    signal) go to `quarantine_events`, and the `quarantine` model takes their current set from `text_coverage`.
  - **Changed from the step as drafted, for the owner to see:**
    - **a second dataset, `text_coverage`** (AGENT_SPEC §5, and CLAUDE.md's list of names): one row per
      (statement file, signal_type) saying what was read, with a status `read`, `partial`, `not_run` or
      `no_text`. `text_signals` alone cannot tell "read, and nothing found" from "never read", and until the
      owner confirms the provider's terms the eight model signals are never read: without it, step I would
      turn "not run" into "no warning" (invariant 4; constraint 2). A page the prefilter does not select is read
      for that signal, with the answer "absent";
    - **masked page text is not stored** (step C left this to step H): it is re-derived from the raw bytes on
      every run, deterministically, and only the evidence spans a signal quotes are kept. A run takes about
      three and a half minutes on the seed, most of it the masker;
    - the quarantine's current set for G1/G2 comes from `text_coverage`, not from the log, so a discard a new
      prompt no longer makes leaves the set, as a regraded statement does; `api_error` (no response yet) is
      not quarantined but counted as `unanswered`, and the statement is `partial` until a later run answers it;
    - the Dagster module is `assets/extraction.py`, as the tree reserved, with the group and job `text`.
  - **On the seed, twice, rules only (no model call):** 130 statements; 702 text pages, 459 scanned, 21 sparse,
    15 unsupported attachments, no unreadable PDF, as in step C. `text_signals` has **no row yet**: the one
    signal allowed to run, `opinion_type`, has no page the prefilter selects on the seed (step D). `text_coverage`
    has 1,170 rows: 59 statements with no readable notes (`no_text` for every signal), and 163 (statement,
    signal) pairs `not_run`, waiting on the model: `going_concern_uncertainty` 57, `post_balance_sheet_event` 51,
    `loss_coverage_resolution` 40, `litigation` 8, `key_customer_loss` 7. Both datasets byte for byte the same on
    the second run, every file keeping its first run id; no G1 or G2 detection; `evidence_masked` passed; the
    `quarantine` model rebuilt with all six audits passing.
- **Step E, labels (2026-10-01):** the owner labelled the first 50 queued pages (32 selected, 18 rejected), exported
  and committed: 3 `going_concern_uncertainty` and 1 `post_balance_sheet_event` present, nothing else; the
  masker hid 21 persons on them and the owner 1 more. 190 pages remain.
- **Step I (2026-10-01), built and run on the seed.**
  - **What:** `feature_set_v4` (`config/features/`), v3 unchanged plus two families in
    `features/feature_definitions.py`: `disclosure` (`going_concern_threat`, `going_concern_basis_abandoned`,
    `average_employment`, from `statement_disclosures`) and `text` (`going_concern_in_notes`,
    `loss_coverage_in_notes`, and `..._years` for each, from `text_coverage`). Each value speaks for the latest
    statement known at `as_of_date`, dated by its filing, and stops when the filing is deleted. The leakage
    test runs on v4: the correction and the deletion of its synthetic warehouse carry disclosures and notes,
    and two leaky variants (dated by the balance-sheet date) fail the truncation check. `backtest_v2`
    (`config/models/`), recorded before its first run. Both are the new defaults.
  - **Changed from decision 7, for the owner to see:**
    - **a text feature is null unless the notes were read for its signal** (`text_coverage` `read`, or a kept
      `present` on a `partial` read), so notes that were scanned, never sent to the model or not wholly
      answered never read as "no warning";
    - **the counts are of periods, not filings** (`..._years`): a correction refiles the same year, and
      counting it again would double a warning. Each period takes its latest filing whose notes were read, so
      a correction whose notes were not read does not erase its original's warning;
    - **`modified_opinion` and `emphasis_of_matter` are not built.** Decision 7 listed them "as decision 0
      allows", and only decision 0c (auditor reports) allows them: the notes are not where an opinion is
      given, and notes without one are not an unmodified opinion (§ Tests: "null is not false"). They come
      with 0c, as a new feature set;
    - **`loss_coverage_in_notes`, not `loss_coverage_by_capital`:** the signal says that a loss is to be
      covered, not from what, so the name the decision gave would claim more than the data holds;
    - the regression may now read the statement's flags as well as ratios (`models/splits.py`), a flag
      entering as 0 or 1.
  - **On the seed:** `features` job twice, 2,929 rows and 56 features, `leakage` passed both times, the
    `feature_store` files the same bytes. `going_concern_threat` is known on 1,429 rows of all 17 entities
    (true on 461); employment on 28 rows of 9 (FY2025 only). The notes' features are known only where the
    notes were wholly read, which before the first model call means where the prefilter selected nothing, so
    every value is false or 0 (`going_concern_in_notes` on 117 rows, `loss_coverage_in_notes` on 233). They
    fill in when the model signals run; the feature set does not change. `backtest_v2` twice, the same report
    bytes: 84 cells, none scored, as plan 0012 found. The flag costs the regression no row: its complete cases
    are v1's (625 rows and 6 events at 12 months, 550 and 6 at 24); among them the flag is true on 55, of
    which 21 at 12 months are distress rows. Too few to say anything.
- **Decision 0(c), the reports' text step (2026-10-05), built and run on the seed.**
  - **What:** the `text_signals` asset reads every stored, not deleted auditor report (`filing_index` rows of the
    types marked `canonical: auditor_report`) through the same page text, masking, prefilter and extractors as
    the notes (`text_signals.read_report`): the report's one PDF is read as attachment 1 with no element path.
    Dated by its own `submission_date` (decision 6, amended); a stored report with none is not read and is
    counted under `skipped: report_undated`, one whose object does not hold exactly one PDF under
    `report_not_one_pdf`. An unreadable report is G1, as an unreadable attachment is.
  - **Changed from the step as drafted, for the owner to see:**
    - **one dataset, a new column:** reports go into `text_signals` and `text_coverage` (AGENT_SPEC §5 names one
      `text_signals`), told apart by `document_kind` (`statement_notes` | `auditor_report`), not into a dataset of
      their own;
    - **every signal is read from a report**, not only `opinion_type` and `emphasis_of_matter`: a report's
      going-concern paragraph is the auditor's `going_concern_uncertainty`, and its emphasis paragraphs can
      name a lawsuit. Until the owner confirms the provider's terms only the rule runs, so the rest are
      `not_run` where the prefilter selects a page;
    - **the notes' features read the notes only:** a text feature now names its `document_kind`, by default
      `statement_notes`, so `feature_set_v4` is unchanged and needs no new version. Without it a report,
      often filed after its statement, would have become "the latest statement's notes". The leakage test's
      synthetic warehouse has a report finding a going-concern uncertainty that no notes feature may show,
      and the test fails when the filter is removed;
    - the pipeline hash is unchanged: the configs and prompts that shape a row are the same, so the notes keep
      their first run ids; the new column changes both datasets' bytes once.
  - **On the seed, twice, rules only:** 48 reports of 12 entities read (51 stored; the 3 undated are the gaps
    above: 0000181328 2019, 0000291203 2024 and 2025); 259 pages, 229 with text, 30 scanned (4 reports
    scans throughout, `no_text`), none unreadable. `opinion_type`: the prefilter selects 23 pages in 17
    reports, and `rules_v1` reads 11 as unqualified, 10 qualified, 2 disclaimer. These are not measured
    and not yet trusted: the census's keyword count found 6 qualified opinions and 1 disclaimer, and a page
    that mentions a qualified opinion is not one that gives it, which is the opinion rule's next step. Waiting
    on the model (`not_run`): `going_concern_uncertainty` in 44 reports (89 pages), `emphasis_of_matter` 35
    (38), `litigation` 4, `post_balance_sheet_event` 3. Both runs byte for byte the same; `evidence_masked`
    passed. The notes' 1,170 coverage rows are as before; `feature_store` is the same bytes, `leakage`
    passed; the `quarantine` model rebuilt, no G1 or G2.
  - **Not done here:** the report pages are not in the labelling queue (`golden_sample_v1` samples rejected
    pages from the notes; adding reports is a new sample version, with labelling); `rules_v1`'s opinion rule
    still lacks the opinion section's heading (the next step).
- **Decision 0(c), the opinion rule (2026-10-05): `rules_v2`, by headings, in `extractor_v2` (the new default).**
  - **Found first, by counts only** (no report text was read into a session: decision 2's confirmation is
    pending): KSB 700/705 fix one pair of headings per opinion, the opinion's own ("Opinia", "Opinia z
    zastrzeżeniem", "Opinia negatywna", "Odmowa wyrażenia opinii") and its basis's ("Podstawa opinii", "...z
    zastrzeżeniem", "...negatywnej", "Podstawa odmowy wyrażenia opinii"). Of the 44 text reports, 31 carry both
    and they agree in all 31; the other 13 carry only a plain "Podstawa opinii" (their opinion heading is not a
    line of its own). The short lines that begin "Opinia..." but are not a heading are the opinion on the
    management report ("Opinia o sprawozdaniu z działalności", 35 reports) and one firm's running title.
    Against that, `rules_v1` had read two reports headed "Opinia" as qualified (a sentence mentioning a
    qualification) and nothing in four.
  - **What:** `rules.py` takes a second form of rule, `opinion_headings`: a line that is, whole, an opinion
    heading gives the opinion (`high`), failing one a basis heading (`medium`), the first on the page, the
    line as evidence; a sentence never does. `rules_v2.yaml` lists the KSB headings; `prefilter_v2` selects
    every page with the lemma `opinia` for `opinion_type` (v1's terms selected only a modified opinion's
    wording, so "Opinia" never reached the rule); `extractor_v2` pins both and keeps v1's model, effort and
    prompts, so a model signal's request and stored response are unchanged (tested).
  - **On the seed, twice, the same bytes:** an opinion in all 44 text reports, by heading in 31, by basis alone
    in 13: 40 unqualified, 3 qualified, 1 disclaimer, exactly the heading counts; no report's pages disagree.
    The 4 scanned reports stay `no_text`. Of the notes, 6 pages are now selected for `opinion_type`, and none
    is read as an opinion. `evidence_masked` passed. Every file has a new run id (a new pipeline hash).
  - **For the owner:** the census's keyword count (6 qualified, 1 disclaimer) counted wording, not opinions;
    by headings there are 3 qualified opinions. An `absent` opinion on a page means no heading there, never an
    unqualified opinion. The evidence is the heading, so when labelling a report page the guide's
    `opinion_type` evidence may be the heading or the "Naszym zdaniem" sentence: the eval scores the value, and
    counts evidence overlap only as a statistic.
- **Decision 0(c), the report pages' labelling sample (2026-10-05): `golden_sample_v2`.**
  - **The design, chosen by the owner** from four with their page counts: every text page of 20 whole
    reports, those `rules_v2` reads as a modified opinion on some page (4) and 16 drawn by hashing each
    report's hash with the version. v1's design (every selected page and a rejected sample) would have queued
    about 205 of the 229 report pages, since `prefilter_v2` selects nearly every page that mentions an opinion.
    The modified stratum is chosen by the rule the eval scores: the rule's precision on modified opinions is
    measured on all of them, its recall only through the random draw. A report page is still marked
    `selected` or `rejected` by `prefilter_v2`; for a report, `rejected` is every page of a sampled report the
    prefilter passed over, not a random sample of a pool (the harness counts them, it does not weight them).
  - **What:** `golden.build_report_queue` and `GoldenSample.report_sample` (a sample is the notes' design or
    the reports', never both); `QueuePage.document_kind`, exported in `pages.jsonl`; `label_queue build` builds
    every sample's queue (or the one named), and `export` writes every local queue's labelled pages and refuses
    to drop a page already in `evals/` (a queue missing on this machine). The notebook picks the queue.
  - **On the seed:** 102 pages of 20 reports (85 selected); 15 of them hold a span the masker would still
    replace on a second pass, shown in red for the owner to mask (more than the notes' 6 of 702: reports name
    the auditor and signatories). The v1 queue was rebuilt with it: its 240 rows and the owner's 77 labels are
    unchanged, each row gaining `document_kind`.

## Why

Numbers arrive late and say little about intent; the text around them says what management and auditors
already know. A going-concern warning, a modified audit opinion or a vote on whether to dissolve the company
comes before the petition, and none of it is in the balance sheet. Phase 7 turns that text into dated, typed,
evidenced signals, with their extraction quality measured, so later models can use them without trusting an
unmeasured extractor.

## What there is to build on (seed, 2026-09-29)

What was read for this draft; counts marked *census* are step A's to establish, since Postgres and MinIO were
not running when it was written.

- **A structured going-concern disclosure in every XML statement, not parsed yet.** The introduction to the
  statement (`WprowadzenieDoSprawozdaniaFinansowego`, every form and structure version in `config/xsd/`) holds
  `P_5A` (prepared on the going-concern basis?), `P_5B` (no circumstances threatening it?) and `P_5C` (free-text
  description of the threats). Of the 17 statement fixtures, which are seed statements, three report a threat:
  `full_2018_v1_0_kalk_b1_2018` (`P_5B` false), `full_2018_v1_2_kalk_2021` (`P_5A` and `P_5B` false: *not*
  prepared on the going-concern basis) and `small_2025_v1_3_mala_kalk_2025` (`P_5B` false). **Wariant 2 encodes
  both flags as codes** (`full_2025_w2_kalk_2025`: `P_5A` 2, `P_5B` 2, `P_5C` "SPÓLKA ZNAJDUJE SIĘ W
  UPADŁOŚCI"), so their meaning must be read from the XSD documentation, as with the `.R2025` lines (plan 0005).
  No LLM, no PDF, already dated by the filing's `known_from`. This is the cheapest signal the phase has.
- **Notes embedded in the statements.** ADR 0009's second addendum counted 284 embedded PDFs (`Plik/Zawartosc`)
  in 108 downloads: mostly the notes (*informacja dodatkowa*), stored redacted (signatures, file names, PDF
  metadata removed). Their free text is kept and can name people (ADR 0009, "residual personal data, accepted
  for now": "text extraction (G) must not emit person names"). Whether each has a text layer or is a scan:
  *census*.
- **Separately filed documents are indexed, not downloaded.** Auditor reports (RDF type 19), management
  reports (20, and 5 before 2018) and resolutions on approval (3) and on profit allocation or loss coverage (4)
  are `download: false` in `config/mappings/rdf_document_types.yaml`, and have no detail, so no submission date
  (plan 0003: 404 rows without a detail across these and the pre-2018 types). Counts per type and entity:
  *census*.
- **Where the text signals of §5 can come from:**

  | `signal_type` | Source | In hand? |
  |---|---|---|
  | `going_concern_uncertainty` | statement `P_5A`/`P_5B`/`P_5C`; notes; auditor report | yes (XML, notes) |
  | `opinion_type`, `emphasis_of_matter` | auditor report | no: type 19 not downloaded, and many small `sp. z o.o.` are not audited (UoR art. 64) |
  | `covenant_breach`, `key_customer_loss`, `litigation`, `post_balance_sheet_event` | notes; management report | notes only |
  | `loss_coverage_resolution` | resolution (type 4) | no |
  | `continued_existence_vote` | a shareholders' resolution under KSH art. 233; not an RDF type seen so far | unknown |

- **Nothing for G is installed** (at drafting; step C has since added spaCy, the model and the masker). `src/distress_radar/extraction/` holds only `__init__.py`; `evals/text_signals/`
  and `prompts/extraction/` are empty; no spaCy, no `pl_core_news_lg`, no LLM SDK or key (`docs/data_inventory.md`:
  "LLM API key: not configured"). PyMuPDF is installed (redaction uses it).

## The constraints that decide what this phase can show

1. **Seventeen entities, eleven events** (plan 0012). No text feature can be shown to predict anything here. What
   Phase 7 can measure honestly is **extraction quality** against a hand-labelled set, and even that set is small:
   the gate it feeds is a regression guard, not a quality claim.
2. **The distressed file least.** All nine seed entities with a distress hint file management reports late or
   never (`docs/data_inventory.md` gap 8). Text signals will be missing exactly where they matter, and the gap is
   already a filing-behaviour feature. A missing document must stay null, never read as "no warning".
3. **Every text source but the XML flag carries personal data.** Notes and reports name board members;
   resolutions name shareholders and how each voted. The person has to be removed before text is stored as a
   signal, sent to a model or committed as an eval example, and the check has to be a test, not a promise.
4. **LLM output is not deterministic**, and invariant 5 requires byte-identical reruns. Responses have to be
   stored and replayed, not regenerated.

## Owner decisions (accepted by the owner, 2026-09-29, as recommended)

Recommendations first; each is the owner's to accept, change or reject before the step that needs it.

0. **Scope: which sources this phase reads.**
   - **(a) The XML going-concern flags.** Recommended, first: structured, free, already dated.
   - **(b) The embedded notes**, through a PyMuPDF text layer (plan 0006's tier 1, text only: no statement label
     map, so plan 0006 stays deferred). Recommended.
   - **(c) Auditor reports and loss-coverage resolutions (types 19 and 4)**, captured by hand for the seed, as
     statements were (plan 0003), with their details for `known_from`. Recommended *after* the census shows how
     many exist; flipping `download` is a new version of `rdf_document_types.yaml`. Management reports (20) only
     if the census finds them in the pre-event years that matter.
   - Resolutions are the densest personal data of any source (see decision 1): if (c) is chosen, they need their
     own rule before the first one is stored.
1. **Personal data in text: mask before anything leaves the page.** Recommended, as ADR 0009's third addendum:
   - page text is masked at the page-text step: person names found by spaCy's NER (`persName`) and the patterns
     `personal_data_markers` already knows (PESEL) become `[osoba]`; the masked page is the only text any later
     step sees: prefilter, LLM, evidence span, eval set;
   - `evidence_span` is stored masked; a blocking check re-runs the masker over `text_signals` and
     `evals/text_signals/`, and the pre-commit scan covers the eval files;
   - the masker's own recall is measured on the golden set (decision 5): NER misses names, and the rate is a
     number, not an assumption;
   - resolutions (if decision 0c): reduced at capture to a person-free record of the resolution's outcome (loss
     amount and how it is covered; the continuation vote's result), as MSiG notices are (ADR 0009 addendum), and
     never stored as documents. This gives up re-extraction for them, as MSiG did; the alternative, storing them
     masked, rests on NER catching every shareholder.
2. **Model and API: Claude through the Anthropic SDK, structured outputs, batches for backfills.** Recommended:
   - `claude-opus-5` (the SDK's current default). A cheaper model (`claude-sonnet-5`) is the owner's choice, and
     is compared on the golden set before it replaces anything;
   - responses validated against the Pydantic schema by the SDK (`messages.parse`), never parsed from free text;
   - the API's citations feature cannot be combined with structured outputs, so the evidence span is a schema
     field, and the code checks it is a verbatim substring of the masked page; an extraction whose span is not
     found is discarded and counted (§6G2: "an extraction without evidence is discarded");
   - backfills through the Message Batches API (asynchronous, half price); single calls only for development;
   - only masked text is sent. The owner confirms the organisation's data-retention terms with the provider
     before the first call, and `ANTHROPIC_API_KEY` joins `.env.example` and the credentials table.
   - Rules before models: auditor opinions use the standard wording of the Polish auditing standards (*opinia
     bez zastrzeżeń*, *z zastrzeżeniem*, *negatywna*, *odmowa wyrażenia opinii*; *istotna niepewność dotycząca
     kontynuacji działalności*), so `opinion_type` and the auditor's going-concern paragraph are lemma rules,
     measured like any extractor; the LLM takes the free-text signals.
   - *As built (step F):* `claude-opus-5-5`, the current default Opus (the current Sonnet is
     `claude-sonnet-5-5`); the schema sent as `output_config.format` and validated by the same Pydantic model
     rather than through `messages.parse`; no refusal fallback; the auditor's going-concern rule waits for
     auditor reports. Reasons in the progress section.
3. **Invariant 5: a response store, not re-generation.** Recommended: every response is stored content-addressed,
   keyed by the SHA-256 of (masked page text, prompt file, model id, schema), in MinIO beside the raw store, with
   a Postgres manifest row. A run calls the API only for keys it lacks; `text_signals` is rebuilt from the store
   byte for byte. A new prompt or model is a new key, never an overwrite.
4. **The CI gate without API calls in CI.** Recommended: `make eval` runs the extractors live (it costs money and
   needs the key) and writes `evals/text_signals/results/<signal_type>/<prompt version>__<model>.json`: every
   example's output, the scores and the hashes of the prompt, model, schema and golden file. `make check` then
   re-scores the committed results offline and fails if the prompt or model in use has no result for the current
   golden file, or if precision or recall fall below the last accepted result. The gate runs in the existing CI
   job, so the separate `extraction-eval.yml` the tree names is not written (the tree is corrected).
5. **The golden set: who labels, and what.** Recommended: the owner labels; a model may propose, and every
   proposal is confirmed or corrected by the owner, with that provenance on the row. Examples are masked page
   excerpts, one row per (page, `signal_type`), positives and negatives. They are drawn from every page the
   prefilter selects on the seed **and** a fixed random sample of pages it rejects, so the prefilter's recall is
   measured too. Expected size: tens of positives per signal at most; the scores print their counts, as the
   backtest does.
6. **Point in time.** A signal's `known_from` is the document's: the statement filing's for flags and embedded
   notes, the own detail's submission date for a separately filed document (one without a detail is not used,
   and counted). *Amended 2026-10-02 (owner):* or, without a detail, that date as listed by hand, stored raw
   with every date pointing back to the list (§ "Decision 0(c)", update). `fiscal_year` is the period the document reports on. The leakage test covers the new family the
   day it is written.
7. **Features: `feature_set_v4`, v3 plus a text family.** *From the census:* step B also reads wariant 2's
   employment and audit flag, which cost nothing extra; employment enters as a raw feature (like balance-sheet
   total and revenue, plan 0010), and the size-class decision (plan 0010 decision 4) can be revisited with it for
   FY2025+. Recommended, first cut, each null when no document:
   `going_concern_threat` and `going_concern_basis_abandoned` from the latest filed statement's flags; and, as
   decision 0 allows, `going_concern_in_notes`, `modified_opinion`, `emphasis_of_matter`, `loss_coverage_by_capital`
   from the latest document of their kind, with the count of prior filings flagging each. An auditor-change
   feature, if the auditor report is captured, keys on the audit firm (a legal entity), never the key auditor.
8. **The backtest.** Recommended: `backtest_v2` pins `feature_set_v4` and adds only `going_concern_threat` to the
   regression's inputs. It is recorded before any run, as decision 5 of plan 0012 requires; nothing on the seed
   is scored either way (plan 0012 correction).

## Out of scope

- The statement PDF tier (plan 0006 stays deferred: this plan reads text, not figures).
- Average employment from management reports (`docs/data_inventory.md` gap 8), though the same text layer would
  reach it; it waits for the size-class decision.
- HerBERT distillation (TECHNICAL_ARCHITECTURE G2): it needs LLM-labelled data at scale.
- KRZ, scale (ADR 0013), Phase 8 models.

## Steps

### A. Census (needs `make dev-up`)

Counts only, no text printed: documents per RDF type and entity, with and without details, by year relative to
each seed event; embedded PDFs with a text layer against scans (characters per page), pages per document; the
`P_5` flags over every stored statement, by entity and year, beside the label events; which of the eleven events
have any text document filed before them. The results go into this plan's progress, and decisions 0 and 5 are
revisited with them.

### B. Structured going concern (tier 0)

*As built (see Progress):* `parsing/statement_introduction.py` into `statement_disclosures`, not `text_signals`.
As drafted: `parsing/going_concern.py`: `P_5A`, `P_5B`, `P_5C` per structure version, the wariant 2 codes read
from the XSD documentation and pinned by a test against it (as `test_every_code_label_matches_its_xsd_label` pins
labels). Rows go to `text_signals` with `extraction_method` `xml_field`, `page` null and the element path as the
locator; `P_5C` is masked (step C) before it is stored. The three fixtures that report a threat are the first
tests.

### C. Page text and masking

*As built (see Progress):* the model is fetched by `make models`, not locked in `uv.lock`; spaCy itself is
locked. As drafted: `extraction/page_text.py`: the PyMuPDF text layer of each embedded (and, per decision 0, captured) PDF, page by
page, deterministic; a page with no text layer is recorded `needs_ocr`, counted, not guessed. `extraction/masking.py`:
spaCy `pl_core_news_lg` NER plus the PESEL patterns, `[osoba]` in place of each name. spaCy and its Polish model
are locked with `make lock` (the model is a package pinned by URL and hash). ADR 0009's third addendum is written
here, before the first masked text is stored.

### D. Prefilter (G1)

`extraction/preprocessing.py`: sentences and lemmas by spaCy; per-signal lemma lists in
`config/extraction/prefilter_v1.yaml` (engineering config, versioned by file name like feature sets). Its recall
is measured on step E's rejected-page sample.

### E. Golden set (G3)

*As built (see Progress):* the pages' masked text once in `pages.jsonl`, a label per page in each
`<signal_type>.jsonl`; a local queue and a marimo notebook for labelling. As drafted:
`evals/text_signals/<signal_type>.jsonl`, one file per `signal_type` (DIRECTORY_STRUCTURE naming): masked excerpts,
the label, its evidence, the document hash and page, and who labelled it. Committed only after the masking check
passes on it.

### F. Extractors (G2)

*As built (see Progress):* plus `extraction/response_store.py` and `config/extraction/extractor_v1.yaml`,
`rules_v1.yaml`; no prompt for `opinion_type`, which a rule reads. As drafted:

`extraction/schemas.py` (Pydantic: value, evidence span, page, confidence, per signal), `extraction/rules.py` (the
lemma rules of decision 2), `extraction/extractor.py` (the constrained call, the evidence check, the response store
of decision 3), `prompts/extraction/<signal_type>_v1.md` with `prompts/CHANGELOG.md`.

### G. Eval harness and gate

*As built (see Progress):* plus `extraction/eval_run.py` and `config/extraction/eval_gate_v1.yaml`; the gate
holds end-to-end scores and starts per signal with its first result. As drafted:

`extraction/eval_harness.py`: precision, recall and F1 per `signal_type`, with counts, and the masker's recall;
`make eval` (live) and the offline gate in `make check` (decision 4).

### H. `text_signals` and wiring

*As built (see Progress):* plus the `text_coverage` dataset and the `text_extractions` manifest; masked page
text not stored; the module is `dagster_defs/assets/extraction.py`. As drafted:

The dataset with its Pandera contract (AGENT_SPEC §5 columns, plus `document_ref`, `source_element_path` and
`ingestion_run_id` for lineage, invariant 3); unreadable documents and discarded extractions to `quarantine_events`
with reason codes; a `text` group and job in `dagster_defs/`, the masking check as a blocking asset check.

### I. Features and backtest

`feature_set_v4` (decision 7), the leakage test run on it, `feature_store` rebuilt twice to the same bytes;
`backtest_v2` (decision 8) run twice.

### J. Docs

AGENT_SPEC §5 and §6G as built; DIRECTORY_STRUCTURE (the new modules, `config/extraction/`, the gate replacing
`extraction-eval.yml`); `docs/data_inventory.md` §2.4 and the credentials table; README status and how to run the
text job and `make eval`; this plan's status.

## Tests

- **No person in any output:** the masker finds planted names in synthetic Polish text (inflected forms included);
  the blocking check fails on a `text_signals` row or eval file with an unmasked name.
- **Evidence or nothing:** an extraction whose span is not in the masked page is discarded and counted.
- **Replay:** a second run with the same inputs makes no API call and writes the same bytes; a changed prompt is a
  new key.
- **The gate:** a committed result below the accepted scores, or missing for the current prompt, fails `make check`.
- **Going-concern flags:** every structure version's fixture read by hand, the wariant 2 codes pinned to the XSD.
- **Point in time:** the leakage test on `feature_set_v4`, with a trap document filed after `as_of_date`.
- **Null is not false:** an entity with no auditor report has a null `modified_opinion`, never false.

## Definition of done

- [x] Owner decisions 0–8 made (2026-09-29, all as recommended).
- [x] Census in the progress section; decisions revisited with it (2026-09-29).
- [x] ADR 0009's third addendum (masking) accepted before the first masked text is stored (owner decision 1,
      2026-09-29; written in step C, no masked text stored yet).
- [ ] `text_signals` built from the chosen sources, every row with evidence and lineage, no unmasked name.
- [ ] Golden set labelled and committed; precision, recall and F1 per `signal_type` with counts; the gate in
      `make check`.
- [x] `feature_set_v4` leak-free and byte-reproducible; `backtest_v2` run twice, identical (2026-10-01; the
      notes' features fill in when the model signals run, by a rebuild, not a new version).
- [ ] `make check` and `make test-integration` green; docs from step J updated.

## Risks

- **Missingness that tracks the outcome.** On the seed, notes without a text layer come almost only from
  entities with no event (131 of 139 scanned PDFs), because the seed was hand-picked. A "no text signal found"
  feature would then partly encode "this company files scans", a seed artefact, not a signal. Unreadable is
  recorded as its own state, never as "no warning", and the backtest reports coverage by group.

- **The masker misses a name**, and a public eval file or a stored span carries it. Measured, gated, and the
  pre-commit scan is the last line; a found leak means a history rewrite (ADR 0009).
- **Scans.** If most embedded notes have no text layer, step C records them `needs_ocr` and the phase shrinks to
  the XML flags and what text there is; OCR or a vision model is its own decision, not a silent fallback.
- **A small golden set makes the gate noisy:** one example can move recall by several points. The gate compares
  against the accepted result with the counts shown; tolerances, if any, are an owner decision recorded in config.
- **Cost and terms of the API.** Decision 2 sends masked text only, batches backfills and stores every response
  once; the owner confirms the terms before the first call.
- **Post-event documents.** Distressed entities file late, often after the event (plan 0006: a statement filed
  eight days after the bankruptcy). Point-in-time dating keeps them out of the features that would predict it;
  the census shows how much text is left before each event.

## After Phase 7

Phase 8 adds LightGBM (native nulls suit the text family's missingness), survival models, calibration and SHAP on
`feature_set_v4`. What any of it says about the population still waits on ADR 0013.
