# 0008 — Phase 4: legal events and outcome labels

**Stage:** Phase 4 (AGENT_SPEC.md §10: "Legal events, outcome labels, censoring, regime flags").
- **Spec stages:** A4 (§6A); the legal-notice side of C; stage F's first label model (§6F, PROJECT_OVERVIEW stage 8).
- **Domain rules:** the outcome taxonomy (§4.6) and bitemporality (§4.7).
- **Outputs:** the canonical datasets `legal_events` and `outcome_labels` (§5).

**Order:** after plan 0007, which is complete. Phase 5 (the feature store) needs labels to train against and
`legal_events` to date them, so nothing downstream starts before this lands. Plan 0006 (the C3 PDF tier) was
deferred to "no earlier than Phase 4, when MSiG forces a PDF text layer". Whether MSiG forces it is now a question
this plan answers in step A, not an assumption (decision 2).

## Status: draft — owner decisions below are pending (2026-09-22)

## Why

Nothing downstream of the canonical table can be evaluated until the project knows which companies became
distressed, and when. §4.6 defines the classes, and §5 defines `legal_events` and `outcome_labels`, but no code,
config or fixture for either exists:
- `config/statutory/procedure_taxonomy.yaml` is "not written" (`docs/data_inventory.md` §5);
- the notice fixtures are "missing" (§6);
- KRZ and MSiG access are "terms to confirm" (§7, and AGENT_SPEC §11.3).

The seed already contains the answer to check against. 9 of the 17 entities carry a publicly reported distress
status in `config/segments/construction_sme_v1_seed.yaml`:
- five in bankruptcy (two with declaration dates, 2025-05-20 and 2025-08-05; one of them a liquidating
  bankruptcy);
- two in restructuring (`0000277937`, `0000386777`);
- two in liquidation (`0000397658`, `0000440028`).

Phase 4 is done when the pipeline finds each of them independently, from the registers, with a date and a source
document.

### What changed since the specs were written: the KRS extract carries proceedings

The specs assume two eras: KRZ from late 2021, and before that only MSiG, as PDF. One request on 2026-09-22 to the
Ministry of Justice's **open KRS API** (`api-krs.ms.gov.pl/api/krs/OdpisPelny/{krs}?rejestr=P&format=json`) for
seed entity `0000181328` suggests the split is less stark:
- **`dane.dzial6.postepowanieUpadlosciowe.informacjaOOgloszeniuUpadlosci`** carries the bankruptcy declaration with
  the court (`organWydajacy`), the case signature (`sygnatura`: `KI1L/GU/43/2025`) and the decision date (`data`:
  20.05.2025). The date matches the seed's status hint, and the declaration is tied to entry number 44
  (`nrWpisuWprow`).
- **`dzial6.likwidacja`** carries liquidation opened (2010-02-01) and closed (2010-05-18) for the same company, so
  the extract reaches back well before KRZ existed.
- **`naglowekP.wpisy`** lists every registry entry (45 here) with its entry date (`dataWpisu`). So every fact in the
  extract can be dated by the entry that introduced it (`nrWpisuWprow`), and by the one that removed it
  (`nrWpisuWykr`). That is `known_from` for free, and it is point-in-time-correct by construction.

The API is public and needs no key; terms and rate expectations are unconfirmed. The extract names natural persons
(board members, shareholders, the trustee), which is decision 3.

The case signature is the same identifier KRZ and MSiG notices quote, so it is the natural `proceeding_id` and the
backbone of cross-source deduplication (§4.6).

This is one entity and one request. It justifies a probe (step A), not a design. But if it holds, KRZ and MSiG stop
being the only sources of labels and become sources of what the registry does not carry:
- petitions, and petitions dismissed for insufficient assets, which never produce a registry entry;
- publication dates earlier than the registry entry;
- anything missing from older extracts.

## Owner decisions needed before step B (recommendations first)

1. **The KRS full extract is the primary structured source of proceedings; KRZ and MSiG fill its gaps.**
   *Recommended.* It covers both eras in one structured format, per entity, with entry-dated history. The
   alternative, KRZ after 2021 and MSiG PDFs before, is two unstructured-to-structured pipelines and a source break
   in the labels. Either way `legal_events` keeps `source` per event, and deduplication runs across all three.
2. **MSiG and the C3 PDF tier are built only if step A shows the registry leaves a gap that matters.**
   *Recommended.* Step A answers two questions:
   - Which pre-2021 event types does the KRS extract lack for the seed? Expected: dismissed petitions.
   - Does the MSiG notice search return notice text, or only issue PDFs?

   If the gap is real and only PDFs exist, plan 0006's trigger fires, and its PyMuPDF text tier (not Docling, not
   the vision tier) is built for notices only. If the search returns text, or the gap is empty for the seed, plan
   0006 stays deferred, and the reason is recorded there.
3. **Registry and register JSON are redacted of natural persons before hashing, extending ADR 0009.**
   *Recommended, and it needs your sign-off.* It widens the one exception to invariant 2 ("sole exception: signer
   data", CLAUDE.md, ADR 0009). The alternative, storing the extract unredacted, breaks invariant 6.
   - **Redact:** names and PESEL numbers of board members, proxies, shareholders who are natural persons, trustees,
     supervisors and administrators. Each is replaced with a fixed placeholder.
   - **Keep:** the structure and the entry numbers. "The board changed at entry 31" survives, while no one's name
     does.

   Recorded as an ADR 0009 addendum. The redaction version joins the raw-store metadata, as it does for filings.
4. **`outcome_labels` gains `trigger_event_type` and `source_era`** (an AGENT_SPEC §5 amendment). *Recommended.*
   - **The problem:** petitions and dismissals are only visible from KRZ's launch, so before it a bankruptcy label
     starts at the declaration, and after it at the petition, months earlier.
   - **Why columns:** that is a real source break. Models must be able to see it, the way they see `regime_flag`.
   - **The columns:** `trigger_event_type` records which event started the label, and `source_era` (`pre_krz` |
     `krz`) records which world the horizon window sits in.
5. **`silent_exit` is a KRS deregistration with no qualifying proceeding before it.** *Recommended.* Its event date
   is the deregistration's decision date, and its `known_from` the entry date. "Filings cease" (§4.6) is then a
   feature (Phase 5, from `filing_index`), not a second label rule. Two rules would disagree about when the exit
   happened.

## Decisions this plan makes (flag any you disagree with before step C)

1. **Events are normalised in Python; labels are built in SQLMesh.** Turning an extract or a notice into dated
   events means walking JSON (or notice text), which is Python: `src/distress_radar/parsing/legal_events.py`, pure
   functions per source, written as Parquet like the canonical table. Labels are a SQL transformation on tabular,
   already-canonical events: a SQLMesh model, as PROJECT_OVERVIEW's crosswalk puts stage 8 in `transform/`. This
   mirrors ADR 0010's boundary.
2. **`event_date` and `known_from` are different columns, and both are required.**
   - `event_date` is when the court decided, or the petition was filed.
   - `known_from` is the earliest date a source made it public: the KRS entry date, or the KRZ/MSiG publication
     date if earlier. When sources disagree, the earliest publication wins, and every source's date stays in
     `legal_events` rows.
   - Labels look forward, so they are dated by `event_date`. Features look back, so they must use `known_from`.
     Mixing the two is the classic leakage bug (§9.1).
3. **`proceeding_id` is the normalised court case signature,** e.g. `KI1L/GU/43/2025`: whitespace, case and
   separators made canonical. `dedup_group_id` is a hash of `(krs, proceeding_id, event_type)`, so the same
   declaration seen in KRS, KRZ and MSiG is one group with three source rows. An event with no signature (some
   liquidation openings are shareholder resolutions) groups on `(krs, event_type, event_date)`.
4. **The taxonomy is dated config.** `config/statutory/procedure_taxonomy.yaml` maps each source's event type to a
   project event type and an outcome class, with effective dates per statute (Prawo upadłościowe, Prawo
   restrukturyzacyjne, the COVID-era simplified restructuring). It also holds the regime window. Nothing about
   which event means what is a Python constant (invariant 7). Unknown source event types are quarantined with a
   reason code, never mapped by guess and never dropped.
5. **Label parameters are versioned config, not statutory:** `config/labels/outcome_labels_v1.yaml` holds the
   horizons (12, 24), the `as_of_date` grid, the event-precedence rule and the cutoff policy. Its version is
   `label_version`. It is a subdirectory of `config/`, so no ADR is needed. Proposed values:
   - **Grid:** month-ends from 2012-01-31 (electronic MSiG) to the data cutoff. Phase 5 intersects the grid with
     whatever features exist.
   - **Precedence:** the earliest qualifying event in the horizon sets the class. On the same date, bankruptcy
     outranks restructuring outranks liquidation.
   - **Cutoff:** the earliest last-complete-fetch date across the sources used. A label whose horizon passes the
     cutoff with no event seen is `censored`, never `alive` (§4.6).
6. **The regime flag marks rows whose horizon window overlaps the regime window.** The window is 2020-01-01 to
   2021-12-31 per §4.6, in the taxonomy config. The simplified-restructuring procedure's own dates are recorded
   beside it for reference: in force from mid-2020 to 30 November 2021, exact dates confirmed against the act in
   step B. Flagging by window overlap, rather than by event date, lets a model exclude every example the spike
   could have touched, including the `alive` ones.
7. **Label sets are frozen with a content hash in Python, not in SQL.** After the SQLMesh model builds, a
   `freeze_label_set` step hashes the sorted label rows. It writes the hash to `label_set_hash`, and a manifest row
   (`label_sets` in Postgres: hash, `label_version`, cutoff, row count, created). A model trains on a hash, never
   on "the latest labels" (§4.6, and the four MLflow IDs in CLAUDE.md).
8. **Seed scale only.** Phase 4 runs on the 17-entity seed, per entity by KRS, with no enumeration of any register
   (§6A). A universe-scale backfill is a later, explicit operation.

## Out of scope

- Features from legal events or registry dynamics (board turnover, office moves): Phase 5. Step F records those
  event types, because they come from the same extract at no extra cost, but nothing reads them yet.
- `entity_size_class_history` and the average-employment decision (`docs/data_inventory.md` gap 8). Still open;
  labels do not need it.
- A5 reference and macro data.
- Docling and the vision-LLM tiers, even if decision 2 builds the PyMuPDF tier.
- Growing the universe beyond the seed.
- Any retuning of parsing, grading or identity rules.

## Steps

### A. Access probe for the three sources (no production code)

`notebooks/exploration/legal_sources_probe.py` (marimo), run by hand, at human pace, for the seed only. Findings go
to **ADR 0011** (sources, access and terms). Redacted response samples go to `tests/fixtures/legal/`. Nothing
unredacted is committed, and `test_no_fixture_contains_personal_data` must cover the new fixtures.

- **KRS open API:**
  - Fetch `OdpisPelny` for all 17 seed entities (and `OdpisAktualny` for one, for comparison).
  - Record the response shape, and every `dzial6` subsection seen: bankruptcy, restructuring (not yet observed;
    the two seed restructurings will show its shape), liquidation, dissolution.
  - Record how deregistration appears.
  - Record whether entry dates cover every `nrWpisuWprow` referenced.
  - Record any stated rate limit or terms, and the HTTP behaviour (plain `httpx`, or bot protection).
- **KRZ:**
  - Is there a public per-entity lookup by KRS usable without a browser: a JSON API behind the portal, a documented
    API, or neither?
  - What does a company's proceeding carry: petition date, decision dates, dismissals, announcements with
    publication dates, the signature?
  - Terms, and bot protection. If the portal behaves like RDF (WAF, CAPTCHA), the route is the HAR import pattern
    of plan 0003, never a bypass (CLAUDE.md).
- **MSiG:** does `wyszukiwarka-msig.ms.gov.pl` search by KRS, and return notice text or only issue PDFs? How far
  back does it reach for the 9 distress entities, and on what terms?
- **Gap analysis:** for each of the 9 distress entities, which events the KRS extract alone yields, and which need
  KRZ or MSiG. This table decides decision 2, and whether plan 0006's trigger fires.

### B. Statutory and label config

- `config/statutory/procedure_taxonomy.yaml` covers:
  - source event types for KRS (`dzial6` subsections and their fields), KRZ and MSiG, mapped to project event types
    (`bankruptcy_petition`, `bankruptcy_declared`, `bankruptcy_petition_dismissed_no_assets`,
    `arrangement_approved`, `accelerated_arrangement_opened`, `arrangement_proceedings_opened`,
    `remedial_proceedings_opened`, `simplified_restructuring_announced`, `liquidation_opened`,
    `liquidation_closed`, `deregistered`, plus the registry-dynamics types);
  - the outcome class for each, from §4.6;
  - effective dates per statute;
  - the regime window.
- `config/labels/outcome_labels_v1.yaml` per decision 5.
- Pydantic models that load both and reject:
  - an event type mapped to two classes;
  - overlapping effective ranges;
  - a class outside §4.6.
- Tests: lookups resolve across effective-date boundaries (§9.2 "Statutory config").

### C. A4 — KRS extract adapter

- `src/distress_radar/acquisition/krs_extract.py` on the shared base: `httpx`, the `hishel` cache, the persistent
  `pyrate-limiter` bucket, and `tenacity` with transient and permanent errors kept apart. The rate is conservative
  until ADR 0011 says otherwise.
- Per-entity `OdpisPelny` JSON, redacted per decision 3 (`acquisition/redaction.py` gains a registry-JSON
  redactor), then content-addressed into MinIO with the redaction version (invariant 2, ADR 0009 addendum).
- **Manifest:** a `legal_source_fetches` table (`krs`, `source`, `sha256`, `fetched_at`, `ingestion_run_id`). The
  latest successful fetch per `(krs, source)` sets that source's cutoff.
- A KRS the API does not know is a quarantine event (`krs_extract_not_found`), with the entity kept.
- Dagster asset `krs_extracts` (group `legal`). Re-materializing refetches only through the cache, so no new raw
  objects appear for unchanged extracts.

### D. A4 — KRZ adapter (shape set by step A)

- Per-entity lookup by KRS: `src/distress_radar/acquisition/krz_client.py` on the same base, or, if step A finds a
  browser wall, a manual-capture importer on the plan 0003 pattern.
- Lookup by KRS returns legal entities only. Any natural-person record that still appears (e.g. an announcement
  naming a trustee) is redacted before hashing. A consumer-bankruptcy record, if one is ever returned, is dropped
  at acquisition with a quarantine event (`natural_person`), as A2 does (invariant 6). A fixture proves both.
- Dagster asset `krz_proceedings`.

### E. A4 — MSiG, only if decision 2 calls for it

- If the search returns text: `acquisition/msig_client.py`, and notices parsed as text in step F.
- If only issue PDFs exist and the gap matters: execute plan 0006 steps for the **PyMuPDF text tier only**, scoped
  to notices. Record the tier per document, and mark plan 0006 "partially executed" with the reason.
- If neither is needed: record why in plan 0006's status section and in ADR 0011, and skip this step.

### F. `legal_events`: normalisation, deduplication, contract

- `src/distress_radar/parsing/legal_events.py`: one pure function per source (`from_krs_extract`, `from_krz`,
  `from_msig`). Each emits `legal_events` rows:
  - `krs`, `event_type`, `event_date`, `published_date` / `known_from`, `source`, `proceeding_id`,
    `source_document_hash`, `dedup_group_id`;
  - plus, for lineage (invariant 3), `source_element_path` (the JSON path or notice locator) and
    `ingestion_run_id`.
- **KRS dating:** `known_from` is the entry date of the entry that introduced the fact (`nrWpisuWprow` →
  `naglowekP.wpisy[].dataWpisu`). A fact whose entry number has no date is quarantined
  (`krs_entry_date_missing`), not dated by guess.
- **Deduplication** per decision 3. Groups keep every source row: the canonical event is derived (earliest
  `known_from`, and the court decision date as `event_date`), never stored in place of the rows.
- **Unmapped source event types** → quarantine (`legal_event_type_unmapped`), with the file kept.
- **Contract and write:**
  - a Pandera contract (`LEGAL_EVENTS`) beside the other three;
  - Parquet under `WAREHOUSE_DIR/legal_events/`, partitioned by `event_year`;
  - the same atomic, deterministic write as the canonical table (ADR 0008).
- Dagster asset `legal_events`, and an `ext.legal_events` view for SQLMesh (ADR 0010).

### G. `outcome_labels`: the SQLMesh model, freezing, audits

- `transform/models/marts/outcome_labels.sql` (FULL): the `as_of_date` grid × horizons × entities in scope.
  - The class comes from the earliest qualifying deduplicated event in `(as_of_date, as_of_date + horizon]`, by the
    config's precedence; otherwise `alive` or `censored` per the cutoff.
  - It also sets `event_date`, `proceeding_id`, `regime_flag`, `label_version`, and `trigger_event_type` and
    `source_era` if decision 4 is accepted.
  - An entity already in a proceeding at `as_of_date` is not a prediction target for that date. It is excluded,
    with a count in the audit output, rather than labelled with an event that has already happened.
- **`freeze_label_set`** (`src/distress_radar/labels.py`, called by the Dagster asset after the build) computes and
  records `label_set_hash` (decision 7). A rebuild over unchanged inputs must reproduce the same hash.
- **Audits** (non-blocking, surfaced as asset checks, ADR 0010):
  - one row per `(krs, as_of_date, horizon_months)`;
  - no `alive` row whose horizon passes the cutoff;
  - no event dated on or before its `as_of_date`;
  - every non-`alive`, non-`censored` row has a `proceeding_id` or a documented reason;
  - a regime flag exactly on the rows whose window overlaps the regime window.
- `dq_mart_coverage` gains event coverage per source and fiscal year. That is the number that shows the source
  break.

### H. Dagster wiring

- **Assets:**
  - group `legal`: `krs_extracts`, `krz_proceedings` (and `msig_notices` if built), `legal_events`;
  - a `labels` asset that runs the SQLMesh label model and then freezes the set.
- One run from `krs_extracts*` goes from registry bytes to a frozen label set.
- Asset checks:
  - quarantine counts by reason;
  - the seed acceptance check below;
  - the SQLMesh audits.
- Docstrings state inputs, outputs and partition scheme (§8).

### I. Docs

- **ADR 0011:** sources, access, terms and rates, the gap table, and decision 2's outcome.
- **ADR 0009 addendum:** registry and register redaction (decision 3). CLAUDE.md's invariant 2 and invariant 6
  wording updated to name it.
- **AGENT_SPEC:**
  - §5: `legal_events` lineage columns, and `outcome_labels` columns if decision 4 is accepted;
  - §4.6: the `silent_exit` rule (decision 5), and the KRS extract as a source beside KRZ and MSiG;
  - §6A: the A4 adapters as built.
- **`docs/data_inventory.md`:** statuses for §3, §5 (taxonomy), §6 (fixtures) and §7 (access).
- **CLAUDE.md** "Known moving targets": the KRS API and KRZ access findings.
- **`DIRECTORY_STRUCTURE.md`:** `config/labels/`, `tests/fixtures/legal/`, and the new modules.
- **Plan 0006:** its status updated per decision 2's outcome, either way.
- **README:** status and how to run the `legal` and `labels` groups.

## Tests

- **Taxonomy and label config:** effective-date boundaries; every §4.6 class reachable; an unknown source event
  type quarantined, not mapped.
- **Redaction:** a KRS extract fixture with synthetic persons loses every name and PESEL and keeps every entry
  number. The redaction is deterministic, so re-redacting is a no-op. No committed fixture contains personal data.
- **`legal_events`:** each source's fixture yields the expected events and dates; `known_from` comes from the entry
  date, not the decision date; a missing entry date is quarantined; the contract holds.
- **Deduplication:** one declaration seen in KRS and KRZ with different publication dates forms one group; the
  earliest `known_from` wins; both rows survive.
- **Label construction (§9.2):**
  - each outcome class;
  - censoring at the cutoff (an entity alive past it is `censored`, never `alive`);
  - an event on the `as_of_date` itself (excluded, not labelled);
  - precedence on a same-day tie;
  - the regime flag at both window edges;
  - `silent_exit` versus deregistration after bankruptcy (the latter is `bankruptcy`);
  - the source-era break.
- **Consumer bankruptcy:** a fixture record for a natural person never reaches raw storage or `legal_events`.
- **Idempotence:** re-running every new asset on unchanged input gives byte-identical Parquet and the same
  `label_set_hash`.

## Definition of done

- [ ] ADR 0011 accepted: access and terms confirmed for every source used, and the gap table recorded.
- [ ] The procedure taxonomy and label config are written, validated and tested.
- [ ] KRS extracts (and KRZ, and MSiG if built) acquired for the 17-entity seed, redacted and content-addressed.
- [ ] `legal_events` persisted and contracted, with full lineage and deduplication.
- [ ] **Seed acceptance:** each of the 9 entities with a distress status hint has a matching event, found
      independently, with a source document and a date. Every mismatch with a hint is investigated and recorded;
      the hints are hints, not ground truth. The 8 others have no qualifying event, or any event found is
      explained.
- [ ] `outcome_labels` built, audited and frozen, with a reproducible `label_set_hash` and censoring and regime
      flags per §4.6.
- [ ] Dagster runs the `legal` and `labels` groups end to end; audits and acceptance surface as asset checks.
- [ ] Docs from step I updated.
- [ ] `make check` and `make test-integration` green; re-running is byte-identical.

## Risks

- **The KRS finding may not generalise.** One extract showed a bankruptcy and a liquidation. Restructuring entries,
  deregistered entities and very old histories are unseen. Step A exists so the design follows the data, not the
  single example.
- **KRZ may sit behind the same wall as RDF.** Then the route is manual capture or a sanctioned channel, never a
  bypass, and the label set carries fewer petition-date events until then. Decision 4's `source_era` makes that
  visible rather than silent.
- **The source break is real whatever is built.** Before KRZ, petitions and dismissals are largely invisible, so a
  pre-2021 bankruptcy label starts later than a post-2021 one. The mitigation is to record it (decision 4) and
  let Phase 6's out-of-time evaluation measure it, not to paper over it.
- **Registry dates are not decision dates.** An entry can land weeks after the court decides. Using the decision
  date as `known_from` would leak; decision 2 keeps them apart. The audit "no event dated on or before its
  `as_of_date`" guards the label side.
- **Redaction touches an invariant.** Decision 3 needs sign-off before step C writes any bytes. Until then, step A's
  samples stay local and redacted by hand.
- **Terms may forbid what a step assumes.** Access is confirmed in step A before any adapter is written (§11.3).

## After Phase 4

Phase 5 (AGENT_SPEC §10): the feature store with ASOF assembly and the blocking leakage test. It consumes
`legal_events` through `known_from`, and trains against a frozen `label_set_hash`. The substitute from plan 0006
(sourcing a missing year from the next filing's prior-year column, dated by that filing) is Phase 5's to build.
