# 0011 — Redact natural persons from filers' file names

**Stage:** A (acquisition), B (raw persistence) and C (parsing), across the stored seed.
- **Invariants:** 6 (legal entities only), 2 (raw immutability and its one exception), 3 (lineage), 5 (idempotence).
- **ADRs:** 0009 and its addendum (natural persons are removed before hashing).

## Status: steps A–C done (2026-09-26); step D next

## Why

RDF gives every filed document the name the filer gave its file (`nazwaPliku`), and the project stores it as
it came. File names are free text, and filers put people in them: one seed statement's file name ends "signed
by" and two first names. That conflicts with invariant 6.

**This reopens an accepted decision.** ADR 0009 (Consequences, "Residual personal data, accepted for now")
already lists file names, in `filing_index.file_name`, the RDF detail JSON and ZIP member names, and leaves them
because they are matching keys. Plan 0010 step D met the same file name again and the owner asked for this plan.
ADR 0009 quoted that file name, names included, from 2026-09-17 until the quote was removed on 2026-09-26
(the names remain in the public history; see Risks).

A census on 2026-09-24, matching 40 common Polish first names against the 134 file names `filing_index` holds,
found that one. The census proves nothing about the rest: surnames, initials (several seed file names carry
two-letter groups that may be initials) and uncommon names do not match a list.

## Where the file name is stored (confirmed in step A, below)

A name that reaches any of these has been stored:
1. **Postgres `filing_index.file_name`**, written from the detail's `nazwaPliku` (`manifest.py`).
2. **The raw RDF detail JSON**, stored in the raw store and referenced by `filing_index.detail_sha256`. The
   name is inside the hashed bytes.
3. **The raw-store sidecar's `original_filename`** (`raw_store.py`), for single-document downloads.
4. **ZIP member names inside stored downloads.** A filer's file usually becomes a member of the ZIP RDF serves,
   and member names are inside the hashed bytes.
5. **`source_member` paths** built from member names: `parsed_documents.source_member`, the canonical Parquet's
   `source_member`, `restatement_events`, `identity_check_results`, and `quarantine_events`.
6. **Matching.** `parsing/containers.py` ties a ZIP member to its filing by comparing the member's base name with
   `filing_index.file_name`. A redacted file name must still match, or parsing breaks.
7. **Quarantine details** (found in step A). `member_not_in_filing_index` writes the member's name and the
   filing rows' file names into `quarantine_events.detail`, an append-only log.
8. **Sidecar HTTP headers** (found in step A). `content-disposition` is one of the headers A3 keeps, and it
   names the file.

### Step A census (2026-09-26)

Read-only, over Postgres, MinIO, the warehouse, the repository and its history. Counts only: no file
name is quoted here or anywhere else.

| Place | Holds a filer's file name | Count |
|---|---|---|
| 1. `filing_index.file_name` | yes | 134 rows (131 RDF type 18, 3 type 1), 129 distinct: 121 `.xml`, 5 `.xades`, 3 `.pdf` |
| 2. Raw RDF detail JSON | yes, in `nazwaPliku` only | all 134 detail objects |
| 3. Sidecar `original_filename` | yes | 118 of 126 downloads |
| 4. ZIP member names | yes | all 126 downloads are ZIPs; 134 members, each named exactly as its row's `file_name` |
| 5. `source_member` paths | yes, the member name | `parsed_documents` 126 distinct paths (451 rows); canonical 124; `identity_check_results` 124; `restatement_events` 30 |
| 7. `quarantine_events.detail` | could: `member_not_in_filing_index` quotes member and file names (`parsing/statements.py`) | 0 of 68 rows today |
| 8. Sidecar `http_headers` | could: `content-disposition` is a kept header | 0 sidecars carry it |
| `raw_document_fetches.source_url`, `quarantine_events.entity_key` | no | 0 of 672 and 0 of 68 |
| Other raw objects (KRS, MSiG, BIR1, RDF lists and lookups, probe pages; 337) | no | 4 RDF lookups matched only because a filer named its file after the company (`nazwaPodmiotu`) |
| Dagster run storage, `.cache/rdf_inbox`, the hishel caches | no | no `DAGSTER_HOME` (runs are ephemeral); the inbox is empty; RDF was never reached over plain HTTP |
| Tracked files and every commit | no personal data | 10 files match a stored name, all generic (`SF2023.xml`, "sprawozdanie finansowe za rok 2025 korekta.xml", …) or a company name; the one known personal file name appears nowhere since ADR 0009's quote was removed, apart from that quote in history |

**How many names are personal cannot be settled by reading them.** After removing generic statement
vocabulary, numbers and the filer's own company name, 68 of the 129 distinct file names keep words that
are not explained; 22 of those carry two-letter groups that may be initials. Telling which are people
would mean reading possible personal data into a review, which is the thing this plan avoids; it is the
case for decision 1 (never keep the text), not for a name detector.

**Matching (decision 3).** Name matching is needed only where a ZIP holds several statements: 8
downloads cover two `filing_index` rows each. In all 8, each row's `nazwaPliku` is exactly one member's
name, the two names differ, and both rows have their detail stored. So a download's members can be
renamed to a token built from the `document_ref` they belong to, at redaction time, with no secret.

## Owner decisions (made 2026-09-26)

Decisions 0–4 below were **accepted as recommended**, with decision 3 as revised after step A (the
`document_ref` token). The owner then **widened the scope** to two findings of a deeper look inside the
stored statements, redacted in the same version and the same migration:
- **attachment names** (`Plik/Nazwa`): 295 in 108 downloads, the same free text as `nazwaPliku`, become
  `plik-<n>` plus the extension;
- **embedded PDF metadata**: of 284 PDFs, 108 carry an `/Author` (usually the person who wrote the notes),
  165 a title and 163 XMP; the information dictionary and the XMP stream are removed.

The rules are in ADR 0009's second addendum (step B). The decisions as they were put:

0. **Reopen ADR 0009's accepted residual.** *Recommended:* yes. Matching needs a stable key, not the text
   (decision 3), and a file name has no other use here. The quoted example in ADR 0009 lost the names on
   2026-09-26, ahead of this decision (a one-line edit, which does not reach the public history; see Risks).
1. **What counts as personal in a file name.** *Recommended:* a file name is never kept as text. Store a
   person-free form: the extension, plus a salted hash of the full name for matching (decision 3). Detecting
   names in free text is unreliable (initials, surnames, diminutives), and the project needs
   the name for nothing but matching and a `.pdf` test. The alternative is a name detector that keeps the rest
   of the text, as `may_name_a_person` does for KRS extracts; it keeps more, and can miss.
2. **Raw bytes.** Items 2 and 4 are inside content-addressed raw objects, which are never modified (invariant 2)
   except to remove people before hashing (ADR 0009). *Recommended:* extend that exception. The detail JSON is
   redacted like a KRS extract (its `nazwaPliku` replaced before hashing), and ZIP member names are rewritten in
   the redacted copy, as signatures already are. Stored objects are re-derived by a migration, as
   `redaction_migration.py` did for signatures (ADR 0009 addendum). The alternative is to leave raw bytes as
   they are and redact only the derived tables, which leaves the names stored.
3. **Matching a member to its filing.** *Recommended (revised after step A):* a token from the
   `document_ref`. At redaction time, the member named by a filing's `nazwaPliku` is renamed
   `<document_ref><extension>` in the stored ZIP, and `filing_index.file_name` and the detail's
   `nazwaPliku` get the same token, so `containers.py` keeps matching by equality and needs no secret.
   Step A shows every multi-statement download resolves this way. A hash of the name would not do
   without a salt: a short file name with two first names is reversed by trying common names. The
   earlier recommendation, a hash salted with a secret kept out of the repository, stays the fallback
   for a download whose details are not at hand when it is stored; it makes a fresh clone depend on
   the salt.
4. **`source_member` paths.** *Recommended:* member names in paths become their tokens (decision 3), so lineage still points
   at one member (invariant 3). Re-running parsing then changes those columns, a new `spec_hash` and run id
   for every file, with the figures unchanged (`notebooks/exploration/canonical_value_hash.py` checks that).

## Steps (after the owner's decisions)

- **A. Census and confirmation.** Every place in the list above, confirmed against the code and the stored seed:
  which tables, sidecars, objects and Parquet columns hold a file name, with counts. Nothing is changed.
- **B. ADR 0009 second addendum:** the rule for file names, and the extended raw-bytes exception. **Done
  (2026-09-26):** tokens, where they replace names, attachment names, PDF metadata, versions
  (`REDACTION_VERSION = "2"`, `rdf-detail-1`), fail-closed rules.
- **C. Acquisition.** Redact at fetch time: the detail JSON before hashing, `filing_index.file_name`, the sidecar
  (with any `content-disposition`), ZIP member names, attachment names and PDF metadata in the redacted copy.
  The download redactor needs the download's filing rows (`document_ref`, `nazwaPliku`) to name members, which
  both A3 paths (Playwright and HAR import) have when they store. Tests on fixtures with invented names, never
  real ones.

  **As built (2026-09-26).** In `acquisition/redaction.py`: `file_token`, `redact_download(raw, names)`
  (members renamed, directory entries dropped, attachment names and PDF metadata in every nested file),
  `redact_rdf_detail`, `original_file_name`; `personal_data_markers` flags a member name that is not a token
  (or not one of given filings'), a `nazwaPliku` that is not its token, an attachment name that is not a
  placeholder, and PDF metadata. No action or marker quotes a name.
  - **A3** (`document_retrieval.py`): details are stored redacted (`rdf-detail-1`) and parsed from what is
    stored, so `filing_index.file_name` gets the token; `A3Detail.original_names` carries the names as
    received, in memory. `download_filing(..., names=...)` refuses a bundle without every name before
    downloading; `retrieve_document` re-expands the listed row it downloads through when the names are not in
    hand; the sidecar's `original_filename` is the token; `content-disposition` is no longer kept.
  - **HAR import** re-reads the row from the capture for the same reason, and attempts each download once per
    import (a bundle's rows share one).
  - **The migration** takes a download's names from `filing_index` and redacts details with the detail
    redactor; the rest of its rework is step E.
  - **Fixtures** regenerated by the production redactor: eleven statement fixtures had a real attachment
    name (now `plik-1.pdf`, one `plik-1.doc`), and the RDF detail fixture its `nazwaPliku`; each file is
    otherwise byte-identical.
  - Found while building: an ePUAP envelope can name its attachment in `Zalacznik@nazwaPliku` (one in the
    seed); it becomes `zalacznik-<n>` (ADR addendum rule 3).
- **D. Parsing.** Member matching on tokens, by equality as today; `source_member` paths carry tokens; the
  quarantine detail quotes tokens only.
- **E. Migration.** Re-derive the stored seed: raw objects, Postgres rows, and a full re-parse and rebuild of
  every derived dataset, checked value for value against the old figures. Old objects are deleted only after the
  new ones verify, as in the signature migration.
- **F. A standing check.** `personal_data_markers` extended to member names, `nazwaPliku`, `Plik/Nazwa`,
  sidecar file names and PDF metadata, run by the fixture tests and as a Dagster asset check on new downloads.
  CLAUDE.md and AGENT_SPEC invariants 2 and 6 name the widened redaction once it is built.

## Risks

- **The ZIP rewrite changes every statement's raw hash**, so every derived row's `source_document_hash` moves.
  The signature migration has done this once, and its checks are the model.
- **A salt kept outside the repository** becomes something a fresh clone needs before it can parse. Losing it
  means re-downloading. Decision 3's alternative avoids that.
- **The public repository's history.** ADR 0009's quote of the file name was in public commits from
  2026-09-17 to 2026-09-26, and step A checks fixtures and notebook outputs for others. Removing it from the history means a
  rewrite and recreating the public repository, as ADR 0009 did for the PESEL numbers: a separate, owner-only
  decision.
