# 0011 — Redact natural persons from filers' file names

**Stage:** A (acquisition), B (raw persistence) and C (parsing), across the stored seed.
- **Invariants:** 6 (legal entities only), 2 (raw immutability and its one exception), 3 (lineage), 5 (idempotence).
- **ADRs:** 0009 and its addendum (natural persons are removed before hashing).

## Status: draft (2026-09-24); owner decisions pending before step B

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

## Where the file name is stored (to be confirmed in step A)

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

## Owner decisions needed before step B (recommendations first)

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
3. **Matching a member to its filing.** *Recommended:* compare hashes. The member's name and `nazwaPliku` are
   both hashed with one salt kept out of the repository, and `containers.py` compares the hashes. The
   alternative is to match on the document reference and position alone, where that is unambiguous; it needs
   no salt but may not cover every download shape.
4. **`source_member` paths.** *Recommended:* member names in paths become their hashes, so lineage still points
   at one member (invariant 3). Re-running parsing then changes those columns, a new `spec_hash` and run id
   for every file, with the figures unchanged (`notebooks/exploration/canonical_value_hash.py` checks that).

## Steps (after the owner's decisions)

- **A. Census and confirmation.** Every place in the list above, confirmed against the code and the stored seed:
  which tables, sidecars, objects and Parquet columns hold a file name, with counts. Nothing is changed.
- **B. ADR 0009 second addendum:** the rule for file names, and the extended raw-bytes exception.
- **C. Acquisition.** Redact at fetch time: the detail JSON before hashing, `filing_index.file_name`, the sidecar,
  and ZIP member names in the redacted copy. Tests on fixtures with invented names, never real ones.
- **D. Parsing.** Member matching by hash; `source_member` paths with hashed member names.
- **E. Migration.** Re-derive the stored seed: raw objects, Postgres rows, and a full re-parse and rebuild of
  every derived dataset, checked value for value against the old figures. Old objects are deleted only after the
  new ones verify, as in the signature migration.
- **F. A standing check.** `personal_data_markers` (or a sibling) extended to file names, run by
  `tests/acquisition/test_legal_fixtures.py` and as a Dagster asset check on new downloads.

## Risks

- **The ZIP rewrite changes every statement's raw hash**, so every derived row's `source_document_hash` moves.
  The signature migration has done this once, and its checks are the model.
- **A salt kept outside the repository** becomes something a fresh clone needs before it can parse. Losing it
  means re-downloading. Decision 3's alternative avoids that.
- **The public repository's history.** ADR 0009's quote of the file name was in public commits from
  2026-09-17 to 2026-09-26, and step A checks fixtures and notebook outputs for others. Removing it from the history means a
  rewrite and recreating the public repository, as ADR 0009 did for the PESEL numbers: a separate, owner-only
  decision.
