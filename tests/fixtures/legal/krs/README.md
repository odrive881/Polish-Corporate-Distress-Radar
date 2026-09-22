# KRS full extracts (`OdpisPelny`), redacted

Captured 2026-09-22 from the open KRS API
(`https://api-krs.ms.gov.pl/api/krs/OdpisPelny/{krs}?rejestr=P&format=json`) by
`notebooks/exploration/legal_sources_probe.py` (plan 0008 step A, ADR 0011), with
`PROBE_WRITE_FIXTURES=1`. Re-running that notebook regenerates them.

| File | Why it is here |
|---|---|
| `odpis_pelny_0000181328.json` | bankruptcy declared (decision date and signature), a prior asset-security order in `dzial4`, and a 2010 liquidation opened and reversed |
| `odpis_pelny_0000507997.json` | bankruptcy entered **without** a decision date or signature; arrears with enforcement in `dzial4` |
| `odpis_pelny_0000277937.json` | restructuring (sanacja) opened after an asset-security order |
| `odpis_pelny_0000440028.json` | voluntary liquidation opened, closed, and the entity deregistered |

**Redaction (invariant 6).** These are public extracts of legal entities, but they name natural persons, so:
- every `imie`, `imieDrugie`, `nazwiskoICzlon`, `nazwiskoIICzlon` and `pesel` value is replaced with `[REDACTED]`;
- every free-text field longer than 40 characters and not on a short allowlist of generic fields (court names,
  PKD descriptions, share counts, reporting periods, procedure types) is reduced to `[REDACTED]` plus its leading
  date. Notarial citations, representation clauses, security orders, and liquidation and dissolution resolutions
  can all name people who appear in no structured field. The seed showed notaries named in both of the latter.

Structure, entry numbers (`nrWpisuWprow` / `nrWpisuWykr`) and entry dates are kept.
`tests/acquisition/test_legal_fixtures.py` is the gate.

This is the probe's redaction, not the production one. That one is plan 0008 decision 3 (an ADR 0009 addendum),
and the allowlist approach here is its proposed starting point.
