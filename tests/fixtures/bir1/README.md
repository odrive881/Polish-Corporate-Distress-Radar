# BIR1 fixtures

Responses recorded from the GUS BIR1 **test** environment
(`wyszukiwarkaregontest.stat.gov.pl`, public test key) on 2026-09-14, used by
`tests/acquisition/test_regon_client.py`. No network in tests.

Sanitisation: contact fields (`praw_numerTelefonu`, `praw_numerFaksu`,
`praw_adresEmail`, `praw_adresStronyinternetowej`) are blanked. The test
environment already anonymises street names (`ul. Test-…`). `\r\n` normalised
to `\n`.

| Prefix | KRS | Case |
|---|---|---|
| `legal_entity_*` | 0000163893 | sp. z o.o., predominant PKD 4120Z (section F) |
| `wrong_pkd_*` | 0000028301 | sp. z o.o., predominant PKD 2511Z (section C) |
| `legal_form_mismatch_*` | 0000040289 | S.A. (form symbol 116), PKD 4399Z |
| `not_found_search.xml` | 9999999999 | `ErrorCode` 4 |
| `natural_person_search.xml` | — | **Synthetic.** Real response shape with `Typ` = `F` and placeholder values; no natural person's data was ever fetched (invariant 6) |

This directory is deliberately a subdirectory: `tests/conftest.py` globs only
top-level `fixtures/*.xml` for the `parsed_filing` fixture.
