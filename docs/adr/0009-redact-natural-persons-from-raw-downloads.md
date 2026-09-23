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
  - **File names:** uploaders' names sometimes include signatories' first names (e.g. "…Podpisane Marcin i Zbyszek.xml"), in `filing_index.file_name`, the RDF detail JSON and ZIP member names. They are matching keys and are left as they are.
- **Local copies outside the raw store are gone (2026-09-17).** The manual HAR captures in `.cache/rdf_inbox/` and the step-A HAR `tests/fixtures/rdf/rdf-przegladarka_ms_gov_pl.cleaned.har` held the files as filed; both were deleted after import. `test_har_import.py`'s replay test is guarded by `skipif(not RECORDED_HAR.exists())` and now skips. Re-importing needs a fresh capture, and a capture must be deleted once imported. Importing a HAR now stores only redacted files.
- **Public git history was rewritten (2026-09-17).** The original `neobis_001.xml` (two PESEL numbers, in every commit since the initial one) was replaced by the redacted file across all 13 commits with `git filter-repo`, and the public GitHub repository was deleted and recreated rather than force-pushed, so the old objects are not retrievable by SHA. Old commits `77a012b`/`d3242f3` and blob `a6abde3` return 404/422 from the API; scanning every object in the remote, in local `.git`, and in the working tree with `personal_data_markers()` returns nothing.
- **A new redaction rule means a new `REDACTION_VERSION`** and another migration pass. `personal_data_markers()` is the check both the migration and the tests use.

## Addendum, 2026-09-23: registry and register JSON (plan 0008 decision 3)

The owner signed off plan 0008 decision 3 on 2026-09-23. The KRS full extract (and later MSiG notices) names
natural persons: board members, proxies, shareholders, liquidators, trustees, supervisors, curators, and notaries
in free text (ADR 0011). The one exception to invariant 2 widens to cover them. Invariant 2 now reads: bytes are
stored as downloaded *except for the documented, deterministic redactions of this ADR*.

1. **KRS extracts are redacted before hashing** by `redaction.redact_registry_extract`
   (`REGISTRY_REDACTION_VERSION = "krs-json-1"`). It is a key list plus an allowlist:
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
   - **Fail closed:** a result that still holds an 11-digit run (PESEL-shaped) or a removed value raises
     `RedactionError`, and nothing is stored. The fetch is quarantined (`krs_extract_unredactable`).
   - **Kept:** the structure, entry numbers (`nrWpisuWprow`, `nrWpisuWykr`), entry dates, and the identifiers of
     legal entities (KRS, REGON, NIP).
   - **Canonical output:** sorted keys and a fixed indent, so the same response always stores the same bytes, and
     re-redacting a redacted extract changes nothing.
2. **The sidecar and `raw_redactions`** record the redaction version and the hash of the response as received, as
   for filings.
3. **MSiG notices (plan 0008 step E)** are stored as their structured fields plus the dates and case signatures
   extracted from the text, never the notice body. That rule gets its own redaction version when step E builds it.
4. **The fixtures follow the production redactor.** `tests/fixtures/legal/krs/` is regenerated by it, and
   `tests/acquisition/test_legal_fixtures.py` remains the gate.

Residual, accepted: a legal entity's name can contain a founder's surname (e.g. "KOWALSKI SP. Z O.O."). That is the
public name of a legal entity, not a natural person's record.
