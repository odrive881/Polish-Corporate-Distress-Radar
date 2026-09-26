# 0009 — Redact natural persons' data from raw downloads

- **Status:** accepted
- **Date:** 2026-09-17

## Context

Two invariants collided in the stored statements. Invariant 2 keeps downloaded bytes unmodified. Invariant 6 keeps natural persons out of the project. The plan 0004 work found that filed statements carry personal data inside their electronic signatures:

- **XAdES signatures (`ds:Signature`)** hold the signer's X.509 certificate, which names the person. Profil Zaufany signatures also hold `DaneZPOsobyFizycznej`: first name, surname and PESEL. 121 stored statement files had certificates, and 69 had PESEL numbers.
- **Signed PDFs embedded as notes** (`Plik/Zawartosc`), 14 files, hold the signer's name in the signature dictionary (`/Name`), certificates in `/Contents` (qualified Polish certificates include `PNOPL-<PESEL>`), and visible signature stamps.
- **Git history:** the Phase 0 fixture `tests/fixtures/neobis_001.xml` carried two PESEL numbers and surnames, and was committed to a public repository.

RDF API responses (JSON) and GUS BIR1 responses carry no personal fields.

The owner decided to redact.

## Decision

1. **Redact at the acquisition boundary, before hashing** (`distress_radar.acquisition.redaction`, `REDACTION_VERSION = "1"`). The raw store holds the redacted file. Invariant 2 now reads: bytes are stored as downloaded *except for this documented, deterministic redaction*.
   - **XML:** every `ds:Signature` is removed. A signature container (a `ds:Signature` or `Signatures` root) is replaced by the document it wraps. A detached signature file is dropped from the ZIP.
   - **Base64 payloads** (notes, ePUAP attachments) are redacted recursively and re-encoded.
   - **PDF:** signature fields, their appearance streams, the `/Perms` and `/DSS` entries and every signature dictionary are removed, and the file is rewritten with PyMuPDF.
   - **ZIP:** rebuilt with the same member names, order, timestamps and compression.
   - A file with nothing to remove is stored byte for byte as received.
2. **The redacted file is the source of truth.** The sidecar records `redaction_version` and `received_sha256` (the hash of the file as received). `raw_redactions` in Postgres logs every replacement. A hash is not personal data.
3. **A file that cannot be redacted safely is not stored**: an encrypted or unreadable PDF, or an ambiguous signature container. The download fails as a permanent source error.
4. **Already-stored files are replaced once** by `distress_radar.acquisition.redaction_migration`. It writes the redacted object, repoints the manifest in one transaction and then deletes the old object. This is the only code path that deletes raw objects (`delete_for_redaction`).
5. **The signature's validity is given up.** The project never verified signatures, and the RDF submission itself is the provenance the pipeline relies on (`filing_index`, `known_from`).

## Consequences

- **Committed fixtures are gated, not eyeballed (2026-09-20, plan 0005 step E).** `test_no_fixture_contains_personal_data` scans every file in `tests/fixtures/statements/` for `PESEL`, `X509Certificate`, `SignatureValue`, `ds:Signature` and 11-digit runs. Plan 0004 did that scan by hand; a fixture added later can no longer reintroduce signer data quietly.
- **Parsing is unaffected.** Statements parse as before, and `containers.unwrap` still handles wrapped files if any arrive. Derived datasets must be re-materialized after the migration, because `source_document_hash` changes.
- **Residual personal data, accepted for now:**
  - **Free text:** notes and accounting-policy text can name board members, and signature stamps drawn into page content are not removed. Board composition is public KRS data, and text extraction (G) must not emit person names (PROJECT_OVERVIEW: pseudonymise).
  - ~~**File names:** uploaders' names sometimes include signatories' first names (one seed file name ends "signed by" and two first names), in `filing_index.file_name`, the RDF detail JSON and ZIP member names. They are matching keys and are left as they are.~~ Reopened and decided in the second addendum below (2026-09-26, plan 0011): file names are no longer kept as text.
- **Local copies outside the raw store are gone (2026-09-17).** The manual HAR captures in `.cache/rdf_inbox/` and the step-A HAR `tests/fixtures/rdf/rdf-przegladarka_ms_gov_pl.cleaned.har` held the files as filed; both were deleted after import. `test_har_import.py`'s replay test is guarded by `skipif(not RECORDED_HAR.exists())` and now skips. Re-importing needs a fresh capture, and a capture must be deleted once imported. Importing a HAR now stores only redacted files.
- **Public git history was rewritten (2026-09-17).** The original `neobis_001.xml` (two PESEL numbers, in every commit since the initial one) was replaced by the redacted file across all 13 commits with `git filter-repo`, and the public GitHub repository was deleted and recreated rather than force-pushed, so the old objects are not retrievable by SHA. Old commits `77a012b`/`d3242f3` and blob `a6abde3` return 404/422 from the API; scanning every object in the remote, in local `.git`, and in the working tree with `personal_data_markers()` returns nothing.
- **A new redaction rule means a new `REDACTION_VERSION`** and another migration pass. `personal_data_markers()` is the check both the migration and the tests use.

## Addendum, 2026-09-23: registry and register JSON (plan 0008 decision 3)

The owner signed off plan 0008 decision 3 on 2026-09-23. The KRS full extract (and later MSiG notices) names
natural persons: board members, proxies, shareholders, liquidators, trustees, supervisors, curators, and notaries
in free text (ADR 0011). The one exception to invariant 2 widens to cover them. Invariant 2 now reads: bytes are
stored as downloaded *except for the documented, deterministic redactions of this ADR*.

1. **KRS extracts are redacted before hashing** by `redaction.redact_registry_extract`
   (`REGISTRY_REDACTION_VERSION = "krs-json-1"`, now `"krs-json-2"`). It is a key list plus an allowlist:
   - **Person keys** (`imie`, `imieDrugie`, `nazwiskoICzlon`, `nazwiskoIICzlon`, `pesel`) become `[REDACTED]`.
   - **Free text longer than 40 characters** is kept only on an allowlist of fields that are generic by
     construction: names of legal entities (`nazwa`), court and authority names, share counts, reporting
     periods, procedure types, PKD descriptions, and the registry entry descriptions (`naglowekP.wpis[].opis`).
     Every other long string becomes `[REDACTED]` followed by the first date it contained, if any. This removes
     the notaries, representation clauses and resolution texts the key list cannot see.
   - **Entry descriptions are allowlisted** because they are the only deregistration signal (ADR 0011
     decision 5), and they come from a closed set of registry phrases (for example "WYKREŚLENIE Z KRAJOWEGO
     REJESTRU SĄDOWEGO"). The probe's 40-character rule had reduced one to a placeholder.
   - **Backstop:** each word of three or more letters from a removed name is searched for in every remaining
     string, and a string that contains one is blanked too.
   - **Role words (`krs-json-2`, 2026-09-23, plan 0008 step F).** An allowlisted field can still quote an order that
     appoints someone: 0000225506's `organWydajacy` names its temporary court supervisor, a company in that case.
     An allowlisted value longer than 40 characters is now reduced too if a role word (supervisor, trustee,
     curator, notary, administrator, adviser, liquidator, attorney) is not followed within 80 characters by a
     legal-form marker (SPÓŁKA, S.A., KRS and the like). All 17 stored extracts re-redact byte-identically under
     `krs-json-2`, so nothing stored needed replacing.
   - **Fail closed:** a result that still holds an 11-digit run (PESEL-shaped) or a removed value raises
     `RedactionError`, and nothing is stored. The fetch is quarantined (`krs_extract_unredactable`).
   - **Kept:** the structure, entry numbers (`nrWpisuWprow`, `nrWpisuWykr`), entry dates, and the identifiers of
     legal entities (KRS, REGON, NIP).
   - **Canonical output:** sorted keys and a fixed indent, so the same response always stores the same bytes, and
     re-redacting a redacted extract changes nothing.
2. **The sidecar and `raw_redactions`** record the redaction version and the hash of the response as received, as
   for filings.
3. **MSiG notices (plan 0008 step E) are never stored as text.** `msig_client.reduce_notice`
   (`MSIG_EXTRACTION_VERSION = "msig-notice-1"`) reduces each notice at fetch time to a person-free record:
   - its structured fields, minus the text and the neighbouring notices' ids;
   - its chapter code (e.g. `III/1`);
   - the case signatures, matched by a pattern that only admits court signatures;
   - which terms of `config/mappings/msig_vocabulary.yaml` occur in it;
   - every date, as ISO, with the vocabulary terms in the 80 characters before it.

   Only vocabulary terms, dates and signatures come out of the text, so a name cannot. The record stores the
   vocabulary's hash, and a PESEL-shaped run fails closed (`msig_notice_unredactable`). The search pages hold no
   personal fields and are stored as received. Re-typing notices under a new vocabulary means fetching them again.
   - **Versioned by extraction key (2026-09-23, Phase 4 review).** A record's redaction version is
     `msig_client.extraction_key`: the extraction version plus the vocabulary's hash, e.g.
     `msig-notice-1+0fa06810ada8`. The bare `msig-notice-1` on older rows means the first vocabulary
     (`69f999d9…`).
   - **`raw_redactions` is keyed by (received hash, redaction version),** so the same received bytes
     reduced under a new vocabulary are a new row. The old single-column key had dropped the 49
     re-reductions' rows. `ensure_schema` re-keys an older table in place and recovers them from
     `msig_notices`.
4. **The fixtures follow the production redactor.** `tests/fixtures/legal/krs/` is regenerated by it, and
   `tests/acquisition/test_legal_fixtures.py` remains the gate.

Residual, accepted: a legal entity's name can contain a founder's surname (e.g. "KOWALSKI SP. Z O.O."). That is the
public name of a legal entity, not a natural person's record.

## Second addendum, 2026-09-26: file names and document metadata (plan 0011)

The owner reopened the file-name residual above and decided plan 0011's decisions 0–4 on 2026-09-26, then
widened it to two things plan 0011's census found inside the statement files. Invariant 2's exception covers
these too: they are removed before hashing, deterministically, and a stored object records the version that
removed them.

**What was found (plan 0011 step A, counts only).** A filer's file name is stored in the RDF detail
(`nazwaPliku`, 134 details), in `filing_index.file_name`, in the ZIP member names of all 126 downloads, in the
sidecar's `original_filename` (118), in every `source_member` path derived from a member, and potentially in
`quarantine_events.detail` and a kept `content-disposition` header. 68 of the 129 distinct names keep words
that are neither statement vocabulary, numbers nor the company's own name, so which of them name a person
cannot be settled without reading them. Inside the statements, 295 attachment names (`Plik/Nazwa`, in 108
downloads) are the same kind of free text, and of 284 embedded PDFs, 108 carry an `/Author`, 165 a title
and 163 XMP metadata: an author is usually the person who wrote the notes.

**Decision.**
1. **A filer's file name is never kept as text** (plan 0011 decision 1). It becomes a **token**: the
   filing's `document_ref` in URL-safe base64 (`+` → `-`, `/` → `_`, padding dropped, so it is safe as a path
   component and maps one-to-one onto the `document_ref`) followed by the original's extension, lower-cased,
   when that is one of `.xml`, `.xades`, `.pdf`, `.doc`, `.docx`, `.xls`, `.xlsx`, `.odt`, `.ods`, `.rtf`,
   `.txt`, `.zip`, and by nothing otherwise. The token is derived from the filing alone, needs no secret, and
   reveals nothing a hash of a short name would (a salted hash was the alternative; an unsalted one is
   reversible by trying common names).
2. **Everywhere the name is stored, the token replaces it**, before hashing where the name is inside stored
   bytes (plan 0011 decision 2):
   - the RDF detail's `nazwaPliku` (the detail is otherwise stored as received);
   - `filing_index.file_name`;
   - the ZIP members: a member named by a filing's `nazwaPliku` takes that filing's token; when a download
     has one content member and one filing, the member takes the token whatever its name (as matching already
     pairs them); any other member becomes `unmatched-<n>` plus its extension, and parsing quarantines it as
     `member_not_in_filing_index`, as it would have. Directory components inside the ZIP are dropped;
   - the sidecar's `original_filename`; `content-disposition` is no longer a kept header (RDF sends
     none, and it would name the file);
   - `source_member` paths, which are built from the renamed members (plan 0011 decision 4), and with them
     every derived dataset and the quarantine details that quote a member.
   Member matching (`parsing/containers.py`) keeps comparing names for equality, now tokens (decision 3).
3. **Attachment names inside a statement (`Plik/Nazwa`) become `plik-<n>`** plus the extension under the same
   list, `n` counting the statement's `Plik` elements in document order from 1; an ePUAP envelope's
   `Zalacznik@nazwaPliku` becomes `zalacznik-<n>` the same way (one seed download has one). The XSD's
   `TNazwaPliku` pattern (`[a-zA-Z0-9_.-]{5,55}`) admits both. The attachment's content is kept, redacted as
   before.
4. **Embedded and top-level PDFs lose their document metadata:** the whole document information dictionary
   (author, title, subject, keywords, creator, producer and dates) and the XMP metadata stream. Nothing in the
   project reads them. Page content is unchanged, and the free-text residual above still applies to it.
5. **Versions.** Downloads are redacted under `REDACTION_VERSION = "2"` (version 1's signature removal plus
   rules 1–4); details under their own version, `rdf-detail-1`. The sidecar and `raw_redactions` record them
   as before. Already-stored objects are re-derived once by a migration of the same shape as the signature
   one: the new object written, the manifest repointed in one transaction, the old object deleted only after
   the new one verifies (plan 0011 step E).
6. **Fail closed, and a standing check.** A download whose members cannot all be named, or a detail without
   the `document_ref` its token needs, is not stored. The names as received live only in memory, taken from
   the details fetched in the same run: a bundle of several filings whose row was expanded in an earlier run
   is expanded again first (A3), or read again from the capture (HAR import), and is refused if neither is
   possible. `personal_data_markers()` gains checks for a member,
   `nazwaPliku`, `Plik/Nazwa` or sidecar filename that is not a token, and for PDF metadata; the fixtures test
   and an asset check on new downloads run it (plan 0011 step F).

**Consequences.** Every stored download's hash changes, and with it every derived row's
`source_document_hash`, `source_member` and `ingestion_run_id`; the figures must not move, which
`notebooks/exploration/canonical_value_hash.py` checks. A file's own name is no longer evidence of anything:
a `.pdf` test (`latest_filed_as_pdf`) reads the token's extension, which is kept. Old public commits still hold
this ADR's earlier quote of one file name; removing it from the history is a separate, owner-only decision
(plan 0011, Risks).

