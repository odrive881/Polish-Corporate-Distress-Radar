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

## Status: active (2026-10-08): owner decisions 1–6 accepted as recommended; steps B–D built and tested, step F's docs written; step A's two seed checks run (decision 5's rule agrees on all placeable rows, decision 3's pairing exact on all 8 groups, so pairing is on); step A's field-by-field comparison waits on a listing from the script; a sample of the script's output received; step 0 waits on the list the script runs on

### Where this stands (2026-10-09)

- **Built (2026-10-08), with nothing more to build before the owner's next items:**
  - the manifest columns (step B);
  - the importer and its asset (steps C and D), tested on synthetic listings and ZIPs;
  - step A's two seed checks: type codes agree on every row they can place, and all 8 correction groups pair
    exactly, so pairing is on;
  - the docs (step F).

  `make check` and `make test-integration` are green.
- **The importer has not met real data yet.** Its input is the format of decision 2, which the script does not
  write yet. The next step is the owner's: the script updated to write `documents.csv` and `entities.csv`, run
  on two seed entities (step A), then on all 17 (step E).

As of 2026-10-07:

- **The script is already running** on the owner's laptop, on the ADR 0014 list (about 600 companies), since about
  2026-10-03, with no CAPTCHA so far. The owner solves one by hand if it comes (ADR 0013, rule 2 as amended).
  Its output predates decision 2's contract, so step A starts from what it actually writes.
- **Since 2026-10-07:** the owner has the specifications of `documents.csv` and `entities.csv` to update the
  script with (§ "Progress"); list v2, which the script runs on next, is to be drawn by ADR 0014's sampling rule
  (addendum of 2026-10-07, proposed).

**Open for the owner, in order:**

1. ~~A sample of the script's output~~ received 2026-10-07 (§ "Progress", below). ~~Whether each entity was
   finished (`entities.csv`)~~: the owner adds it to the script (2026-10-07). The listing's missing columns
   (decision 2) are listed per field in § "Progress" for the owner to add. (The challenge log is dropped: ADR
   0013 rule 7, withdrawn 2026-10-07.)
2. ~~The pace the running script keeps~~ (owner, 2026-10-07): the owner's to set, below ADR 0013's ceiling, and
   it changes with the situation: a randomised delay, roughly 45 seconds to 2 minutes a download at present.
   Nothing in the importer depends on it.
3. **The exact list the script runs on** (it differs slightly from the file of ADR 0014), and the Rejestr.io
   filter settings used to build it. Step 0 loads that list, not the earlier file. *2026-10-07:* list v2 is to
   be drawn by ADR 0014's sampling rule (addendum of 2026-10-07, proposed): the frame's counts and its exported
   KRS numbers, from Rejestr.io before the trial ends, then the group sizes and the seed; step 0 then also loads
   each company's group and probability of being drawn.
4. ~~Rejestr.io's terms of use~~ confirmed by the owner (2026-10-07): a commercial aggregator of public data,
   used by hand through its search, as it is meant to be used, on a 14-day trial during which the owner builds
   the full list. Only KRS numbers still enter the pipeline (ADR 0014's design, not a condition of the terms).
5. **(Reminder for the owner, deferred 2026-10-07.) On RDF:** whether each correction's tab in an expanded row has its own "Pobierz dokumenty" (step A,
   decision 3).
6. **List v1's downloads in the old format.** The script has run on the 611 companies since about 2026-10-03,
   writing `filing_dates.csv` (`doc_name;krs;filing_date;period;document_id`), the format `report_import.py`
   already reads, without the type, correction flag or status. Either they are imported as they are (corrections
   inferred from the `_2` suffixes and shared ids, the missing fields completed later from details or a re-run),
   or those companies are downloaded again once the script writes `documents.csv`. Either way list v1 stays
   outside any weighted result (ADR 0014 addendum).

**What can be built meanwhile (2026-10-07), in this order** (1–3 done 2026-10-08, § "Progress"):

1. **Step A's two seed checks, from the stores alone:** decision 3's pairing rule (a correction group's members
   paired with their tabs by the statement's header dates against "Data sporządzenia dokumentu") on the seed's 8
   groups, and decision 5's type-code rule (name and period) on the 538 rows. Only the field-by-field comparison
   with a new listing waits on the script.
2. **Step B:** `filing_index.rdf_document_id` with decision 1's backfill, `listing_sha256`, and
   `rdf_listed_entities`.
3. **Steps C and D:** `script_import.py` and the `rdf_script_import` asset, built to the `documents.csv` and
   `entities.csv` specifications given to the owner (§ "Progress"), tested on synthetic listings and ZIPs; item 1's
   result decides the pairing, and the unpaired path stands otherwise. A difference in the script's real output
   is adjusted when step A sees it.

Once the owner accepts ADR 0014's sampling addendum:

4. **Weighted models:** sample weights in training (`models/classical.py`, today unweighted) and in the
   evaluation (Brier score, reliability, base rates), with group A alone reported beside them.
5. **Step 0's draw:** the seeded permutation of a frame file, groups A and B, each company's probability of
   being drawn, loaded into `universe_candidates`; tested on a synthetic frame, the export's format read from
   Rejestr.io's.

Waiting on the owner: plan 0013's new prefilter version (its item 3), item 6 above, then step A's comparison,
step E, the composition census and the draw.

**After the import, each back to the owner with its counts** (§ "Deferrals this plan reopens"): KRZ, the PDF
statement tier, and the share of scans; and, from ADR 0014, the list's composition and a written rule to make
the next list representative.

### Progress

- **Step 0 and list v1's first import (2026-10-09).** The owner's first listing in decision 2's format:
  `documents.csv` (1,242 rows, 231 entities) and `entities.csv` (671 searches), 535 of list v1's 609 companies
  searched. Owner's notes: entities with fewer than three filed years are left aside; a run of not-found searches
  (2026-10-08, 13:57 to 23:52, 405 searches) was the connection dropping, not RDF; early searches marked
  incomplete whenever more files came than the page listed.
  - **Built from it** (`make check` and `make test-integration` green): `load_krs_list` (step 0, KRS column
    only) and `universe_candidates`' `krs_list` config; in the importer, a missing correction flag read from
    the ids (older filings show none), a found search with no count kept as incomplete, `outages.csv` (the
    span above, written to the inbox), completeness judged by the rows listed against the page's count, the
    segment's `min_history_years`, and a group's ZIP held back while the listing lacks some of its tabs.
  - **Step 0:** list v1 loaded as `rejestr_io_v1`, 609 KRS numbers (ADR 0014 records its SHA-256). A2: 592
    resolved, 16 quarantined `pkd_section_mismatch` (7 of them with downloads), 1 `ambiguous_match`.
  - **Import:** 190 entities, 1,150 rows indexed, 1,118 with their document stored; 34 entities with fewer
    than three filed years not imported; 405 outage searches not recorded; 258 searches recorded, 254
    complete. Not stored: 27 groups whose correction tabs the listing lacks (their ZIPs held), 4 ZIPs the
    redactor left personal data in (a qualified certificate's PESEL, a certificate, a trusted-profile
    attachment inside `DaneZalacznika`), refused by the post-redaction scan, and 1 deleted row. The store-wide
    `personal_data` scan finds nothing. One entity (0000681661) has listed documents but only an outage
    search.
  - **Open from it:** the script to write every tab of an expanded row (then the 27 groups import); the
    redactor to cover the 4 ZIPs' signature forms; the 305 companies searched only during the outage and the 74
    not yet searched; the composition census (ADR 0014) over the 592.
- **Step F, docs (2026-10-08).** README § "Manual RDF capture" gains "Scripted downloads" (the inbox, both
  listings' columns and formats, the import and its refusal counts). The data inventory updates §2's status, adds
  `RDF_SCRIPT_INBOX` to §7's RDF row, and states in §2.4 what is no longer indexed (types 3, 4 and 20 beyond the
  seed, decision 4). DIRECTORY_STRUCTURE names the inbox on `script_import.py`'s line. ADR 0013's rule 1
  records that the pace is the owner's, below the ceiling (decision 6), rule 5 points to decision 2's contract,
  and § Consequences records the importer as built. AGENT_SPEC §6A gets a table of A3's four fetch tiers. Once
  step E has run, its results are added to the inventory's status.
- **Step A's seed checks (2026-10-08, `notebooks/exploration/script_import_seed_checks.py`), counts only.**
  - **Decision 5, type codes:** of the 538 rows, the rule gives each its own code wherever it can place one: 134
    by the detail's type name (131 of code 18, 3 of code 1) and 396 by their code's configured name (codes 1, 3,
    4, 5, 19, 20), which tests only the period bounds. The 8 rows of code 2 have no name in config, as expected
    (plan 0013, item 2). None differs. Only codes 18 and 1 have detail names to test the names themselves; the
    other names are checked when the script's first listing is compared (step A's remaining part).
  - **Decision 3, pairing:** all 8 correction groups pair exactly as the HAR import did, so
    `PAIR_BY_PREPARED_DATE` is on. A group the rule cannot pair is still stored unpaired.
- **Built, 2026-10-08: steps B, C and D.** `make check` and `make test-integration` (60 tests, 6 of them the
  importer's) green. Fixing the integration tests showed that the HAR tests' fixture details all carried one
  `idDokumentu`; each invented document now has its own, and a detail completes only a scripted row
  (`id-…`), never another RDF-keyed one.
  - **B:** `filing_index.rdf_document_id` (unique per KRS where set) and `listing_sha256`; `rdf_type_code` may be
    null (decision 5); `rdf_listed_entities`. A row from the listing counts as detailed (`needs_detail`). The
    backfill (`script_import.backfill_document_ids`) reads `idDokumentu` from each stored detail, then the ids in
    the stored `filing_dates.csv` for the auditor-report rows it dated, and runs at the start of every import.
  - **C:** `acquisition/script_import.py`, fetch tier `pad_script`, to the specifications of § "Progress" below
    (16 columns, `Tak` / `Nie`, "NIEUSUNIĘTY" as the page writes it). Both listings are stored raw before they are
    read. A row is refused, with its line, when a value cannot be read or its dates are out of order; readings of
    one id that disagree are refused together, a correction whose original tab is missing too. Rows match by
    id, then by the single candidate, else become `id-<idDokumentu>`. Refusals are counted in the asset's
    metadata, as `har_import.py` and `report_import.py` report theirs; none is written to `quarantine_events`,
    whose A-stage entries the `quarantine` model keeps for good.
  - **Decision 1's HAR side:** a detail carrying an `idDokumentu` a scripted row holds completes that row and
    removes the list row the capture added (`record_a3_detail` returns the map); for an entity with scripted
    rows, `har_import.py` adds only the listed documents the capture expanded and reports the rest.
  - **Decision 3:** `pair_by_prepared_date`, on since the seed check; a group it cannot pair is stored as
    `unmatched-<n>`. When it pairs a group, each row's `file_name` is set to its member's token, which parsing
    matches on.
  - **Decision 5:** `rdf_document_types.yaml` version 4: `period_end_before` / `period_end_from` per code;
    `RdfDocumentTypes.code_for(name, period_end)`.
  - **Tokens:** `id-<digits>` is a file-name token beside the base64 form (`redaction._TOKEN_NAME`), so a
    scripted row's stored members pass the `personal_data` check.
  - **D:** `RDF_SCRIPT_INBOX` (default `.cache/rdf_script_inbox`); the `rdf_script_import` asset after
    `rdf_manual_import`, with the blocking `personal_data` check; parsing depends on it. `report_import.py` notes
    that the new importer supersedes it for new captures.
  - **Leakage:** a third synthetic entity, keyed `id-…`, with no file name and dated by a listing, a correction
    included (`test_a_statement_dated_by_the_listing_is_known_from_its_listed_date`).
  - **Not handled, for step A to see:** the sample's `.xml` and `.xades` of one period in one ZIP. If the `.xades`
    is an enveloping signature, both are statements and the pair stays unpaired for a single row (two content
    members, no names). The listing's `language` is read but not stored (it stays in the raw listing).

- **The script's output, as it writes it (sample, 2026-10-07).** One entity's folder (`0000563676`) and the
  listing so far (`filing_dates.csv`, 76 entities of ADR 0014's list v1, the 611 companies the script is still running on), both
  in `.cache/rdf_script_inbox/` (ignored; root copies are caught by `.gitignore` too).
  - **Layout:** `<krs>/<krs>_<period_end>[_<n>].<ext>`, extracted, plus `<krs>/_originals/<krs>_<period_end>.zip`
    as delivered. In the sample each ZIP holds one member, byte-equal in size to its extracted copy; members keep
    the filer's file name, which the importer turns into a token (ADR 0009 second addendum).
  - **Listing:** `;`-separated, UTF-8 with a BOM, columns `doc_name`, `krs`, `filing_date` ("Data dodania"),
    `period` (period end), `document_id`, one row per extracted file; files from one ZIP share a `document_id`.
    Against decision 2 it lacks `row_document_id`, `type_name`, `period_start`, `prepared_date`, `is_ifrs`,
    `is_correction`, `status`, `deleted_on` and `captured_at`, and there is no `entities.csv`.
  - **Counts:** 485 files, 464 documents, 458 entity-periods. 431 files `.xml`, 44 `.xades`, 10 `.pdf`; by period,
    410 have an XML, 42 exist only as `.xades`, 6 only as PDF (one 2018 filing of 5 PDFs, and one entity's
    2019–2023). Periods per entity: 1 (2), 2 (8), 3 (3), 4–7 (27), 8–9 (36), so 10 entities fall short of v1's
    three filed years.
  - **For the importer:** extensions vary in case and form (`.XML`, `.XAdES`, `.xml (1).xades`, `.xhtml.xades`); a
    period can appear as both `.xml` and `.xades` under one `document_id`; short periods (a changed fiscal year)
    count as periods of their own.
  - **What each row should carry, for the owner to add to the script (2026-10-07).** One row per tab of an
    expanded row (the document and each correction), of every in-scope row, downloaded or not:

    | Column | Page field | Today | Why |
    |---|---|---|---|
    | `krs` | KRS | `krs` | the entity |
    | `document_id` | "Identyfikator dokumentu" of the tab | `document_id`, per file, not per tab | the key (decision 1) |
    | `row_document_id` | the id of the row that was expanded | missing | ties a correction to its original |
    | `type_name` | the row's type ("Roczne sprawozdanie finansowe", "Sprawozdanie z badania", …) | missing | statement or auditor report; the type code (decision 5) |
    | `period_start` | "Okres sprawozdawczy", from | missing | short and changed fiscal years |
    | `period_end` | "Okres sprawozdawczy", to | `period` | the fiscal year |
    | `prepared_date` | "Data sporządzenia dokumentu" | missing | pairing a correction group's members (decision 3) |
    | `is_ifrs` | MSR | missing | IFRS statements are out of the mapped structures |
    | `is_correction` | "Dokument jest korektą" | missing | original or correction; today only guessable from `_2` |
    | `submission_date` | "Data dodania", of the tab | `filing_date`, per file | `known_from` (invariant 1) |
    | `status` | status | missing | a deleted or withdrawn document is not a filing |
    | `deleted_on` | "Data usunięcia dokumentu przez sąd" | missing | when it stopped being one |
    | `file` | the ZIP saved for the row | `doc_name`, the extracted file | links the row to its bytes; empty when not downloaded |
    | `captured_at` | the time the script read the tab | missing | the listing's own date: a later deletion or correction is seen against it |
    | `tab` | "Szczegóły dokumentu *n* / *m*": *n* and *m* | missing | every tab of a row read (*m* tabs, one row each) |
    | `language` | "Język dokumentu" | missing | text signals read Polish only |

    Two layout changes: name each ZIP after its row's `document_id` (`<krs>/<row_document_id>.zip`), since
    period names collide (two originals for one period, a changed fiscal year); and keep the extracted files or
    not, as convenient, since the importer reads the ZIPs. Not collected: "Identyfikator zgłoszenia", "Nazwa
    dokumentu" (the filer's free text), "Wydział sądu", "Sygnatura sprawy", anything from "Pokaż zgłoszenie" or
    "Pokaż treść dokumentu" (both list signatories by name). `entities.csv` as in decision 2: `krs`, `searched_at`, `found`, `list_rows`,
    `complete`.
  - **Flags as the page writes them (2026-10-07).** `is_ifrs`, `is_correction`, `found` and `complete` hold
    `Tak` / `Nie`, as RDF shows them, not yes/no; the importer maps them. The owner has both specifications, as
    prompts for the script: `documents.csv` one row per tab, appended, never rewritten; `entities.csv` one row
    per search, appended, `complete` = `Nie` on any early stop or doubt, and a number RDF does not find written
    `found` = `Nie`, `complete` = `Tak`.
  - **Owner decision (2026-10-07): keep both the `.xades` files and the ZIPs.** A `.xades` here is an enveloping
    signature with the statement inside `ds:Object` (on 42 periods the only copy), which `redaction.py` unwraps
    before hashing; the ZIPs stay the raw input of decision 2. Neither is deleted or committed.

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
     the list counts them) and `complete` (every in-scope row expanded and downloaded); flags as `Tak` / `Nie`
     (amended 2026-10-07, § "Progress");
   - ~~`challenges.csv`, the log of ADR 0013 rule 7~~ (added 2026-10-06; dropped 2026-10-07 by the owner, with
     rule 7: the import carries no challenge counts);
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
   minute", allows it. The choice goes into ADR 0013 either way. *In practice (owner, 2026-10-07): the owner
   sets the pace, slower than this and varying, within ADR 0013's ceiling.*

Settled since, outside this plan: a CAPTCHA is solved by the owner by hand (ADR 0013, rule 2, amended
2026-10-06), and A1 is the Rejestr.io list (ADR 0014).

## Deferrals this plan reopens (found in the doc sweep, 2026-10-06)

Three things were deferred "until the universe grows beyond the seed", and this plan is where it grows. Each
is measured once the list is imported, and goes back to the owner with its counts; none is built here.

- **KRZ** (plan 0009, ADR 0011: deferred until the universe grows beyond the seed). Without it, insolvency
  events after 2021 reach the labels only through the KRS registry, with lag of up to 21 months, so the `alive`
  labels of the newer years are the least reliable. Measure: entities of the list with a post-2021 proceeding in
  the KRS extract, and the lag between decision and entry.
- **The PDF statement tier** (plan 0006, trigger 2): PDF-only years not recoverable from a later filing, and
  whether they concentrate in distressed entities. Measure: `needs_pdf_tier` in `parsed_documents`, joined to
  a later filing of the same entity, split by label.
- **Scans** (plan 0013, risks): on the seed, 459 notes pages and 4 auditor reports have no text layer, nearly
  all from entities with no event. Measure the share on the list, by label, before any text feature is read
  as evidence; OCR stays its own decision.

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
The owner runs the script on all 17 seed entities (about 180 documents: a few hours, depending on the pace),
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
- ADR 0013: the pace is the owner's, within its ceiling (decision 6).
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
- [ ] `make check` and `make test-integration` green; docs from step F updated. *(Green and written,
  2026-10-08; to be swept again after step E.)*

## Risks

- **The page changes.** A PAD script reads the page's layout; a redesign breaks it silently or, worse, shifts a
  column. The importer's checks (date order, ids per KRS, type names from config) catch a shifted field; the
  seed comparison of step E is the regression test to rerun after any script change.
- **The WAF.** ADR 0013's rule stands: a challenge stops the run, and more challenges are a reason to revisit
  the route, not to tune the script. With no challenge log (rule 7 withdrawn, 2026-10-07), nothing in the
  import shows that rate; only the owner does.
- **A listed date typed or read wrong** becomes a wrong `known_from`, and so a leak or a lost signal. The
  listing is stored raw, every date points back to it, and the date-order check bounds it; step E measures the
  agreement with the details on the seed.
- **Personal data in the inbox.** The ZIPs as downloaded carry signatures and filers' file names. The inbox is
  gitignored and the pre-commit hook refuses them; they are deleted once imported (ADR 0009).
