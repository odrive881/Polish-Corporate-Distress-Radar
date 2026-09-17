# RDF fixtures (A3)

Used by `tests/acquisition/test_document_retrieval.py` and
`tests/acquisition/test_document_retrieval_playwright.py`. No network in tests.

## Capture (plan 0003 step A)

One manual session in an ordinary browser, at human pace, on
`https://rdf-przegladarka.ms.gov.pl/wyszukaj-podmiot`:

- **KRS looked up:** `0000209396` (seed entity, `config/segments/construction_sme_v1_seed.yaml`)
- **HAR captured:** 2026-09-15: search → filing list → expand the 2025 annual statement → "Pobierz dokumenty"
- **DOM captured:** 2026-09-16: `<app-root>` outerHTML of the result list and of an expanded row

The HAR itself (`rdf-przegladarka_ms_gov_pl.cleaned.har`) was never committed (`*.har`
is gitignored): it was 9 MB of scripts, fonts, and a full filing. Its Imperva bot-check
entry (which carried a `reese84` token) was deleted and its cookies stripped.
`notebooks/exploration/rdf_har_extract.py` turns such a capture into the JSON files below.

**The capture was deleted on 2026-09-17** (ADR 0009): a filing as filed carries its
signatories' PESEL numbers, and this project stores no natural persons. The JSON and HTML
fixtures here are extracts that carry none. The one test that replayed the HAR
(`test_har_import.py::test_recorded_har_has_detail_and_download_but_only_one_list_page`)
is guarded by
`skipif(not RECORDED_HAR.exists())` and skips; reviving it needs a fresh capture.

## RDF API, as the SPA calls it

All under `https://rdf-przegladarka.ms.gov.pl/services/rdf/przegladarka-dokumentow-finansowych/`.

| Call | Fired by | Response |
|---|---|---|
| `POST podmioty/wyszukiwanie/dane-podstawowe` `{"numerKRS": …}` | "Wyszukaj" | entity found flag, name, legal form |
| `POST dokumenty/wyszukiwanie` (KRS encrypted by the SPA) | "Wyszukaj", paginator | filing list page: `id`, `rodzaj`, `status`, reporting period, deletion date |
| `GET dokumenty/{id}/id-dokumentu-i-korekt` | "Rozwiń" on a row | ids of the document and its corrections |
| `GET dokumenty/{id}` | "Rozwiń" on a row | detail: `dataDodania` (known_from), type, correction flag, file name |
| `POST dokumenty/tresc` `["{id}"]` | "Pobierz dokumenty" | the document bytes (a ZIP for XML statements) |
| `GET zgloszenie/{id}` | "Pokaż zgłoszenie" | **never used**: signatories by name (invariant 6); the adapter blocks it |

## Files

| File | Origin |
|---|---|
| `entity_found.json` | **Recorded**, byte for byte (HAR). |
| `filing_list_page0.json` | **Recorded**: page 0 of 5 at 10 rows (49 documents in total). |
| `document_corrections.json` | **Recorded**, for the 2025 annual statement `kQL-7bDLHvl-dIGIeLuLlQ==`. |
| `document_detail.json` | **Recorded**, same document (`dataDodania` 2026-06-29). |
| `document_download.meta.json` | **Recorded metadata only** of the download (request body, headers, size, sha256, ZIP member list). The 3.8 MB ZIP is not committed. |
| `dom_results.html` | **Recorded** DOM of the result list (page 1, 10 rows), as pasted from DevTools. No personal data or session tokens (checked). |
| `dom_expanded_row.html` | **Recorded** DOM with the 2025 annual statement's row expanded. No personal data or session tokens (checked). |
| `entity_not_found_synthetic.json` | **Synthetic.** Same shape as `entity_found.json` with `czyPodmiotZnaleziony: false`; an unknown-KRS response has not been recorded. |
| `filing_list_empty_synthetic.json` | **Synthetic.** Same shape as the recorded list, with no documents. |
| `waf_block_page_synthetic.html` | **Synthetic.** Written to match the Imperva Incapsula block page ADR 0007's probe observed (`_Incapsula_Resource` iframe, "Request unsuccessful. Incapsula incident ID"); every identifier zeroed. Not a recorded response. |

**Personal data (ADR 0009):** the gitignored HAR here holds one downloaded statement as filed, signatures included. Keep it local and never commit it. Delete it when it is no longer needed. The committed JSON/HTML fixtures carry no personal data.
