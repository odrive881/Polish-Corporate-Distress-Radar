# 0009 — Outcome labels, version 2: a lag allowance and petition expiry

**Stage:** F (labels), after plan 0008. **Status: complete (2026-09-23).**

## Why

The Phase 4 review (plan 0008, status section) left two label questions to the owner, who decided both on
2026-09-23:

1. **Registry lag.** The registry enters a decision up to 21 months after it is made (0000225506, ADR 0011).
   After KRZ's launch (2021-12-01), nothing this project reads publishes insolvency events sooner: MSiG no longer
   does, and KRZ is not built. So an `alive` row whose window ends shortly before the cutoff can miss an event
   decided in that window and not yet entered. **Decision: a lag allowance.**
2. **Proceedings that never end.** An entity counts as "in a proceeding" from a petition-stage or opening event
   until a closing event. A petition that no source ever records as opened, dismissed or withdrawn would exclude
   the entity for good. **Decision: petition-stage events expire.**

The owner also accepted as built `source_era = mixed`, a merger blocking a silent exit, merged-away rows being
censored, and a deregistration taking any earlier distress class. KRZ stays deferred until the universe grows
beyond the seed.

## What changed

- **`config/labels/outcome_labels_v2.yaml`:** version 1 plus two fields. `LabelConfig` defaults both to "off", so
  version 1 builds exactly as before.
  - `alive_lag_months: 12`. A window ending on or after KRZ's launch is `alive` only if it ends at least 12
    months before the cutoff; otherwise it is censored. Earlier windows keep the cutoff itself, because MSiG
    publishes within weeks.
  - `petition_expiry_months: 24`. A petition-stage event keeps an entity "in a proceeding" for at most 24
    months. Openings still last until a closing event.
- **The label version is a setting,** `LABEL_VERSION` (default `outcome_labels_v2`). `transform/config.py` passed
  version 1 by name before. Both new values reach the SQL as SQLMesh variables, and 0 or null switch them off.
- **SQL:** `staging.outcome_label_grid` computes `alive_limit` per row and applies the expiry in
  `in_proceeding`; the `labels_no_alive_past_cutoff` audit applies the same limit.
- **Tests:** two SQLMesh unit tests for version 2. The lag test uses a one-month allowance around a test launch
  date; the expiry test covers a petition that expires and an opening that does not. The four earlier tests pin
  version 1's behaviour explicitly. Python tests check that version 2 differs from version 1 in exactly these two
  fields.

## Result (seed, 2026-09-23)

| Set | Version | Rows | Note |
|---|---|---|---|
| `a5da757f8341…` | v1 | 4,670 | before the Phase 4 review fixes |
| `a1ac9fb07f79…` | v1 | 4,694 | after them |
| `066d18bbd4cd…` | v2 | 4,694 | current |

Compared with v1 (`a1ac9fb07f79`), 196 rows moved from `alive` to censored: 100 at 12 months and 96 at 24.
No row was added or removed, and no positive label changed. The latest `alive` window now ends 2025-08-31,
inside the limit of 2025-09-23. All 15 checks pass.

**Petition expiry changes nothing in the seed, correctly.** Every seed petition is followed by an opening or a
closing within 24 months; the longest wait is 19.8 months, 0000386777's 2018 sanacja petition. The rule will
matter at scale, and the unit test covers it until then.
