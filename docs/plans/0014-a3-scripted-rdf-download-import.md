# 0014 — A3: importing the scripted RDF downloads

**Stage:** A3 (AGENT_SPEC §6A), B (raw persistence); PROJECT_OVERVIEW stage 3.
- **Invariants:** 1 (point in time: the listed "Data dodania" is `known_from`), 2 (raw immutability, with its
  one exception: redaction before hashing), 3 (lineage), 4 (no silent loss), 5 (idempotence), 6 (legal entities
  only).
- **ADRs:** 0013 (accepted 2026-10-06: the route), 0007 (its limits), 0009 and its addenda (redaction, file
  names as tokens).
- **Output:** `filing_index` rows and stored documents for any entity the owner's Power Automate Desktop (PAD)
  script has downloaded, through the same parsing stages as today.

**Order:** after ADR 0013's decision. It does not wait for plan 0013 (Phase 7). A1 is the owner's
Rejestr.io list (ADR 0014, accepted 2026-10-06), loaded in step 0.

## Status: active (2026-10-06): owner decisions 1–6 accepted as recommended; nothing built; step A waits on a sample of the running script's output

### Where this stands (2026-10-06)

- **The script is already running.** It has run on the owner's laptop for three days on the ADR 0014 list (about
  600 companies), with no CAPTCHA so far. The owner solves one by hand if it comes (ADR 0013, rule 2 as
  amended). Its output predates decision 2's contract, so step A starts from what it actually writes. The
  owner shares one entity's output (listing and file layout, never the ZIPs), and the importer either reads it
  as it is or the script adapts. Whatever the script did not record cannot be recovered later without a
  re-download, above all "Data dodania" per document and whether each entity was finished.
- **To confirm:** the pace the running script keeps (decision 6: one RDF action every 20 seconds).
- **A1 is settled for now** by ADR 0014 (accepted): the Rejestr.io list. It adds step 0 below.

## Why

ADR 0013 chose a PAD script as the route to RDF at scale. The pipeline cannot take its output yet:

- `har_import.py` needs a HAR, which PAD does not record.
- `report_import.py` (the `manual_files` tier) takes only auditor reports, and only onto `filing_index` rows
  that a HAR capture already created. For an entity the script reaches first, there is no row to match.

So every entity beyond the seed needs an importer that builds `filing_index` from what the script hands over.

## What the script can see (from the captured DOM, `tests/fixtures/rdf/dom_expanded_row.html`)

- **The collapsed list row:** the type's name (not its code), the document's name, and the reporting period.
- **The expanded row**, one tab per document (the document and each of its corrections): type name,
  "Identyfikator dokumentu" (the detail's numeric `idDokumentu`), "Identyfikator zgłoszenia", "Data sporządzenia
  dokumentu", MSR yes/no, "Dokument jest korektą", the period, the KRS number, "Data dodania", the status and
  "Data usunięcia dokumentu przez sąd".
- **Not visible anywhere on the page:** RDF's API id (`identyfikator`, e.g. `kQL-7bDLHvl-dIGIeLuLlQ==`), which
  is `filing_index.document_ref` for every row today; the type code (`rodzaj`); and the file name (`nazwaPliku`),
  which is what parsing uses to pair the members of a ZIP with their rows.

## On the seed (census, 2026-10-06, from `filing_index`)

- 17 entities, 538 rows. Type 18: 131 rows, all expanded and downloaded, 8 of them corrections. Type 19: 52
  rows, 51 stored by `report_import.py`, **none expanded**, so none has a detail.
- 8 downloads are shared by two rows (a statement and its correction, in one ZIP). The other 169 cover one row
  each.
- One (KRS, period) has two original statements, so a key on (KRS, period, type) is not unique for statements.
- Type codes that share a name (1 and 18 "Roczne sprawozdanie finansowe", 5 and 20 "Sprawozdanie z
  działalności") split by **period**: codes 1, 2 and 5 only on periods ending in 2017, codes 18, 19 and 20 from
  periods ending 2018-09-30. They do not split by filing date: three type-1 rows were filed in 2020.
- 8 rows are deleted (`deleted_on` or status).
- Corrections were filed 83 to 509 days after their originals.

## The constraints that decide the design

1. **The key.** The script sees `idDokumentu`; `filing_index` keys on `identyfikator`. No public page
   links the two except the detail JSON, which a HAR holds and the script does not.
2. **Pairing ZIP members.** A correction group downloads as one ZIP with one member per document. Today the
   members are paired by `nazwaPliku`, which the script cannot see. A single-member ZIP pairs with its single
   row whatever its name (`parsing/containers.py`, `match_members`).
3. **Absence must mean something.** A document missing from the listing may mean "not filed" or "the script
   stopped first". Filing-behaviour features read the absence of a filing as a signal, so the importer must
   know when an entity's listing is complete (invariant 4).
4. **Pace.** ADR 0013 sets at most 3 documents a minute. On the seed, the two categories average about 11
   documents per entity. An entity also costs a search, list pages and one expansion per row. Paced per
   download, that is about 4 minutes per entity, about 8 days of continuous running for 3,000 entities. Paced
   per action, like the Playwright tier's `RDF_REQUESTS_PER_MINUTE`, it is about 8 minutes per entity, about
   17 days. These are estimates from seed averages, not measurements.

## Owner decisions (accepted by the owner, 2026-10-06, as recommended)

1. **The document key.** Recommended:
   - add `filing_index.rdf_document_id` (the numeric `idDokumentu`), unique per KRS;
   - backfill it on the seed from the stored detail JSON (131 type-18 rows) and from the owner's
     `filing_dates.csv`, already stored raw (type 19);
   - a script row matches an existing row by `rdf_document_id`. Failing that, it matches by (KRS, type, period,
     original or correction), only when that names exactly one row with no `rdf_document_id`, as
     `report_import.py` matches today. Otherwise it becomes a new row keyed `id-<idDokumentu>` (it cannot
     collide with RDF's base64 ids);
   - `har_import.py` learns the same lookup, so a HAR captured later completes the script's row rather than
     adding a second one.

   Rejected: re-keying every row on the numeric id. It would rewrite `document_ref` in every derived dataset,
   the file tokens and the quarantine keys, for no gain over a column.
2. **The listing the script writes** (the contract; the owner adapts the script, or shares its current output
   so the importer can follow it). Recommended:
   - `documents.csv`, `;`-separated UTF-8, one row per tab of an expanded row. Columns: `krs`, `document_id`,
     `row_document_id` (the id on the list row that was expanded; equal to `document_id` except for a
     correction), `type_name`, `period_start`, `period_end`, `prepared_date`, `is_ifrs`, `is_correction`,
     `submission_date` ("Data dodania", never "Data sporządzenia dokumentu"), `status`, `deleted_on`, `file`
     (the ZIP's path, empty when not downloaded) and `captured_at`;
   - `entities.csv`, one row per KRS number searched: `krs`, `searched_at`, `found`, `list_rows` (all types, as
     the list counts them) and `complete` (every in-scope row expanded and downloaded);
   - one ZIP per expanded row, exactly as "Pobierz dokumenty" delivered it, at `<krs>/<row_document_id>.zip`;
   - "Identyfikator zgłoszenia" is not collected (nothing needs it), and neither is anything from "Pokaż
     zgłoszenie".
3. **Correction groups (constraint 2).** Recommended:
   - step A checks the seed's 8 groups, asking two things: whether each tab has its own download, and whether
     a member can be paired with its tab by the statement's own content (its header dates against the tab's
     "Data sporządzenia dokumentu") without any name;
   - a rule that pairs all 8 exactly as the HAR import did becomes the pairing for script groups;
   - otherwise a group's members stay unpaired (`member_not_in_filing_index`, as now), its rows are indexed and
     dated, and a HAR capture completes it. On the seed that is 8 of 131 statements.
4. **Scope of the listing.** Recommended: only the categories downloaded (types 18 and 19), with `list_rows` per
   entity as the only trace of the rest. Nothing downstream reads types 3, 4 or 20 (the feature families read
   statements and auditor reports). Indexing every row would need an expansion per row for its id, which
   multiplies the pace cost. The data inventory says what is no longer indexed.
5. **Type codes from names.** Recommended:
   - `rdf_document_types.yaml` (a new version) gains, per code, the name as the page shows it and the
     periods it applies to: codes 1, 2 and 5 for periods ending before 2018-01-01, codes 18, 19 and 20 from
     then on;
   - the rule is tested against all 538 seed rows, whose codes came from RDF's own list;
   - a name or period the rule cannot place is indexed with no code and reported, never guessed.
6. **The pace (constraint 4).** Recommended: per action, at most one RDF action (search, list page, expansion,
   download) every 20 seconds. It is how ADR 0007's tier counted, and it is the reading of "3 a minute" least
   likely to be the reason the WAF reacts. Per download is twice as fast. ADR 0013's wording, "3 documents a
   minute", allows it. The choice goes into ADR 0013 either way.

Settled since, outside this plan: a CAPTCHA is solved by the owner by hand (ADR 0013, rule 2, amended
2026-10-06), and A1 is the Rejestr.io list (ADR 0014).

## Out of scope

- Any list of KRS numbers not supplied by the owner (AGENT_SPEC §6A: no enumeration), and redrawing the
  list to be representative (ADR 0014, the owner's next step).
- The PAD script itself: it is the owner's, and runs on Windows outside the repository. This plan defines
  its output and imports it.
- Types 3, 4 and 20 (decision 4); pre-2018 statements (still out of v1 scope, `rdf_document_types.yaml`).
- KRZ, OCR, the PDF statement tier (plan 0006 stays deferred).

## Steps

### 0. The list (A1, ADR 0014) and A2 over it
- A loader for the owner's list (the version the script runs on, read from outside the repository) into
  `universe_candidates`, KRS numbers only, `discovery_source` naming the list and its SHA-256, beside the
  seed's YAML loader (`acquisition/universe_discovery.py`). Malformed or duplicate numbers are quarantined
  (A1), as the seed's are.
- A2 (GUS BIR1) over it, so every entity the importer meets is in `entity_master`, with the segment checks A2
  already applies (legal form, PKD). Entities A2 rejects are reported in the composition census.
- **The composition census** (ADR 0014, § Consequences), from A2 and, once run over the list, the KRS
  extracts (A4): PKD section F or not, legal form, size, region, registration date, and the pipeline's own
  distress labels. Recorded in the progress section by counts.

### A. Census and the owner's check (no code, except reads of the stores)
- **The owner:** run the script on two seed entities, one of them with a correction group (an entity from
  `filing_index` with `correction_of` set), into a fresh inbox, writing the listing of decision 2.
  - Note whether a correction's tab has its own "Pobierz dokumenty".
  - The ZIPs stay local and are never committed.
- **Then, by counts only:**
  - compare the listing field by field with the same rows' stored details;
  - test the pairing rule of decision 3 on the 8 seed groups;
  - test the type-name rule of decision 5 on the 538 rows.

  Recorded in the progress section.

### B. Manifest (`acquisition/manifest.py`)
- `filing_index.rdf_document_id` (unique per KRS where set), with the backfill of decision 1.
- `filing_index.listing_sha256`: the stored listing a row's detail fields came from. A row whose detail came
  from a HAR keeps `detail_sha256`; a row from the listing has `listing_sha256` and counts as detailed.
  `FilingDocumentState.needs_detail` and its readers learn this.
- A table `rdf_listed_entities` (`krs`, `searched_at`, `found`, `list_rows`, `complete`, `listing_sha256`,
  `ingestion_run_id`), so completeness is a stored fact, not a guess. Migration tests as for earlier columns.

### C. The importer (`acquisition/script_import.py`, fetch tier `pad_script`)
- Store both listings raw before reading them (invariant 2), then:
  - parse them, refusing the whole file on a bad header;
  - check every row: dates in order (period end < submission ≤ `captured_at`), `row_document_id` naming a row
    of the same KRS, and no two rows disagreeing on one id;
  - create or complete `filing_index` rows (decision 1) with the listed date as `known_from` and
    `listing_sha256` as its source. A detail captured later in a HAR replaces the date and clears the source,
    as for the auditor reports.
- Per ZIP:
  - check its members against its group (decision 3);
  - redact (ADR 0009: signatures, PDF metadata, members renamed to their row's token);
  - refuse the ZIP if `personal_data_markers` finds anything after redaction;
  - store it and record the download on every row it covers.
- **Refusals are reported, never dropped** (invariant 4):
  - entities not in `entity_master` (A2 runs first, `not_in_entity_master`);
  - unknown type names;
  - unmatched rows;
  - ZIPs with no row, rows with no ZIP;
  - entities whose listing is incomplete.
- Re-importing the same inbox adds nothing (invariant 5). A later listing that shows a deletion or a new
  correction updates the row as `har_import.py` does.

### D. Wiring
- Settings: `RDF_SCRIPT_INBOX` (default `.cache/rdf_script_inbox`, gitignored), in `.env.example`.
- A Dagster asset `rdf_script_import` beside `rdf_manual_import`, with the blocking `personal_data` check after
  it (plan 0011). Run metadata gives the refusal counts.
- `report_import.py` stays for the seed's existing list. A note says the script's importer supersedes it for
  new captures.

### E. The seed as the acceptance test
The owner runs the script on all 17 seed entities (about 180 documents: an hour or two at the chosen pace),
and the importer loads them into an empty database. Every column of the statements' and reports' rows in
`filing_index` must equal today's HAR-built rows, after the key and lineage columns are removed:
- the period, status and deletion;
- the submission date;
- the correction link;
- the stored bytes' hash after redaction.

The canonical financial facts rebuilt from them must also be identical. Every difference is explained in the
progress section before this plan closes.

### F. Docs
- README: § "Manual RDF capture" gains "Scripted downloads", covering the listing contract, the inbox and the
  import.
- The data inventory: §2's status, the credentials table, and the types no longer indexed.
- DIRECTORY_STRUCTURE: the new module and inbox.
- ADR 0013: the pace chosen (decision 6).
- AGENT_SPEC §6A: the A3 tiers.

## Tests (`tests/acquisition/`, no network; synthetic listings and ZIPs, no real names)

- Listing parsing:
  - a bad header or a missing column refuses the file;
  - "Data sporządzenia" in the date column is caught by the date-order check where it can be.
- Keys:
  - a row matches by `rdf_document_id`, then by the single-candidate rule, else becomes `id-<n>`;
  - a later HAR completes it and adds no second row.
- Completeness: an incomplete entity's missing documents are reported, and its `rdf_listed_entities` row says
  so.
- Redaction:
  - a ZIP carrying a signature and PDF metadata is stored redacted, with `received_sha256` naming the original;
  - a member name that is not a token fails the `personal_data` check.
- Corrections: the pairing rule of decision 3, or the unpaired path, on a synthetic group.
- Type codes: the name-and-period rule on every combination in config; an unknown name is indexed with no
  code.
- Idempotence: importing twice gives the same rows and objects.
- Point in time: the leakage test runs unchanged, and one synthetic entity whose `known_from` comes from a
  listing is added to its warehouse.

## Definition of done

- [x] Owner decisions 1–6 made (2026-10-06, as recommended).
- [ ] Step 0: the list loaded, A2 run over it, its composition census recorded.
- [ ] Step A's census in the progress section; the decisions revisited with it.
- [ ] `rdf_script_import` built, with the manifest changes, the `personal_data` check passing after it.
- [ ] The seed reproduced through the script (step E), every difference explained.
- [ ] `make check` and `make test-integration` green; docs from step F updated.

## Risks

- **The page changes.** A PAD script reads the page's layout; a redesign breaks it silently or, worse, shifts a
  column. The importer's checks (date order, ids per KRS, type names from config) catch a shifted field; the
  seed comparison of step E is the regression test to rerun after any script change.
- **The WAF.** ADR 0013's rule stands: a challenge stops the run, and more challenges are a reason to revisit
  the route, not to tune the script.
- **A listed date typed or read wrong** becomes a wrong `known_from`, and so a leak or a lost signal. The
  listing is stored raw, every date points back to it, and the date-order check bounds it; step E measures the
  agreement with the details on the seed.
- **Personal data in the inbox.** The ZIPs as downloaded carry signatures and filers' file names. The inbox is
  gitignored and the pre-commit hook refuses them; they are deleted once imported (ADR 0009).
