# KRS full extracts (`OdpisPelny`), redacted

Captured from the open KRS API
(`https://api-krs.ms.gov.pl/api/krs/OdpisPelny/{krs}?rejestr=P&format=json`), first by
`notebooks/exploration/legal_sources_probe.py` (plan 0008 step A, ADR 0011) and regenerated on 2026-09-23 with
the production redactor, `distress_radar.acquisition.redaction.redact_registry_extract` (plan 0008 step C). Each
file is exactly what `krs_extract.fetch_extract` would store for that entity on that day.
Re-running the notebook with `PROBE_WRITE_FIXTURES=1` regenerates them through the same redactor.

| File | Why it is here |
|---|---|
| `odpis_pelny_0000181328.json` | bankruptcy declared (decision date and signature), a prior asset-security order in `dzial4`, and a 2010 liquidation opened and reversed |
| `odpis_pelny_0000507997.json` | bankruptcy entered **without** a decision date or signature; arrears with enforcement in `dzial4` |
| `odpis_pelny_0000277937.json` | restructuring (sanacja) opened after an asset-security order |
| `odpis_pelny_0000440028.json` | voluntary liquidation opened, closed, and the entity deregistered |

**Redaction (invariant 6, ADR 0009 addendum).** These are public extracts of legal entities, but they name
natural persons, so:
- every `imie`, `imieDrugie`, `nazwiskoICzlon`, `nazwiskoIICzlon` and `pesel` value is replaced with `[REDACTED]`;
- every free-text field longer than 40 characters and not on the allowlist (legal entities' names, courts and
  authorities, share counts, reporting periods, procedure types, PKD descriptions, registry entry descriptions) is
  reduced to `[REDACTED]` plus the first date it contains. Notarial citations, representation clauses, security
  orders, and liquidation and dissolution resolutions can all name people who appear in no structured field.

Structure, entry numbers (`nrWpisuWprow` / `nrWpisuWykr`), entry dates and entry descriptions are kept; the
latter carry the only deregistration signal (0000440028's `WYKREŚLENIE Z KRAJOWEGO REJESTRU SĄDOWEGO`).
`tests/acquisition/test_legal_fixtures.py` is the gate.
