# 0022 — Outcome labels, version 3: restructuring openings and endings

**Stage:** C (legal events) and F (labels), after plans 0009 and 0014. **Status: complete (2026-10-19):
mappings, tests and label version built; label set `afeba985…` frozen (§ "Result"). Retraining on v3 is the
owner's call (decision 6).**

## Why

The night of 2026-10-19 was the first `legal_to_labels` run over list v2's distress group (200 entities), and
`legal_events_quarantine` grew from plan 0014's 88 records to 342. Most of them do not touch a label (§ "Left
quarantined"). One group does: **7 entities of the distress group (`w upadłości`) opened a restructuring, mostly an
accelerated arrangement (przyspieszone postępowanie układowe), 7 months to 2.5 years before their bankruptcy**, and
nothing recorded it:

| Entity | Opening (decision) | First event recorded before |
|---|---|---|
| 0000090096 | 2017-12-13 | 2019-09-30, bankruptcy |
| 0000152748 | 2017-05-24 | 2018-12-03, bankruptcy petition |
| 0000236155 | 2018-04-27 | the same day, but as a bankruptcy |
| 0000373685 | 2016-07-15 | 2019-03-07, bankruptcy petition |
| 0000384681 | 2020-06-02 | the same day, but as a bankruptcy |
| 0000488626 | 2021-11-10 | 2023-02-01, bankruptcy |
| 0000533967 | 2019 (notice of 2019-10-23) | 2021-05-12, bankruptcy |

So their labels carried the wrong class (bankruptcy, not restructuring) and the wrong timing for every `as_of_date`
before the bankruptcy, and the months spent in restructuring were labelled rather than excluded. The owner asked
for the chapter IX rules as label version 3 (2026-10-19).

Four causes, all of them the seed's 49 notices and 17 extracts never showing a form:

1. **MSiG chapter IX.** `msig_notice_kinds.yaml` v1 recognised only a sanacja opening. An accelerated
   arrangement opening, one that names no procedure, a discontinuance whose notice cites the pending bankruptcy
   petition (art. 9b, so "układ" occurs), and an ending said as "prawomocn" all went to quarantine.
2. **The registry's arrangement section** (`dział 6, postepowanieUkladowe`) was an unknown section. Its opening
   date matches MSiG's in every case (0000066923, 0000074968, 0000205656, 0000236155, 0000373685, 0000488626).
3. **The restructuring section's other records:** an opening with no `rodzajPostepowania` (4 records), the
   section's ending (`zakonczeniePostepowania…`, 6 records: "UMORZENIE POSTĘPOWANIA", "ZAKOŃCZENIE PRZEZ
   ZATWIERDZENIE UKŁADU") and a revoked arrangement (`uchylenieUkladu`) were skipped without a trace, the ending
   because its section was known. So a restructuring the registry closed never closed here.
4. **GR cases in the bankruptcy section.** The registry entered 0000236155's "V GR 4/18" and 0000384681's "XVIII
   GR 20/20 (XVIII GRP 8/20)" as bankruptcy declarations, with a court supervisor, not a trustee, and for the
   second a court named "PRZYSPIESZONE POSTĘPOWANIE UKŁADOWE". GR is the restructuring register; bankruptcies
   are GU. 2 of the 141 records in that section. Under the label precedence a same-day bankruptcy wins, so the
   MSiG fix alone would have changed nothing for these two.

## Decisions this plan makes (flag any you disagree with)

1. **A label version names its mapping versions.** `legal_events` is built from whatever the taxonomy and notice
   kinds say now, so a label version that left them out could change meaning under the same name.
   `outcome_labels_v3.yaml` is v2's parameters plus `taxonomy_version: 2` and `notice_kinds_version: 2`; v1 and
   v2 default both to 1. `transform/config.py` refuses to build labels from other versions
   (`check_mapping_versions`). Consequence: `LABEL_VERSION=outcome_labels_v2` no longer builds on this commit;
   v2 is rebuilt from a commit whose mappings it names (plan 0021's `make reproduce` checks one out).
2. **An opening that does not name its procedure is the generic `restructuring_proceedings_opened`.** The
   registry's arrangement section holds accelerated arrangements too (0000236155's "V G 4/18" is one in MSiG), so
   it never asserts `arrangement_proceedings_opened`. MSiG's "przyspieszon" openings are
   `accelerated_arrangement_opened`. Both are restructuring openings; the label does not differ.
3. **An undated notice stays undated.** Several opening and discontinuance notices date nothing with
   "postanowieni" before it, and some quote only the Prawo restrukturyzacyjne's own date (15.05.2015). Their
   rules take `otherwise: none` (new): the event is dated null, so "on or before its `known_from`", and the
   registry's record of the same case gives the decision date where it exists.
4. **A revoked arrangement is a signal (`arrangement_revoked`), not an opening.** It opens no proceeding; the
   bankruptcy that may follow is its own event.
5. **The GR register decides, read from the signature.** A bankruptcy-section record whose `sygnatura`
   contains "GR" is a restructuring opening, from 2016 (the register's start; an earlier one is quarantined);
   anything else, an unsigned record included, stays a bankruptcy. Two new `when` tests do it: `excludes` (an
   empty value counts as not containing) and `absent`. Limit: a plain substring. No court code in the stored
   signatures contains "GR" (KRZ-era ones are like "KI1L/GU/43/2025"), but a new one could; the plan's check is
   the count in § "Result", to be re-run when list v3 arrives.
6. **Scoring's risk set and the realised calibration read the latest set of `LABEL_VERSION`,** not of the served
   model's training label version. No new v2 set will be frozen, so the old rule would have frozen the risk set
   at the 2026-10-19 v2 set without anyone noticing. Who is in a proceeding now, and what happened, are the
   current labels' answer; the model's own label version stays on every score row. The served models are still
   trained on v2: retraining or a backtest on v3 is the owner's call (`backtest_v8` pins a v2 set).

## What changed

- **`config/mappings/msig_notice_kinds.yaml` v2:** `accelerated_arrangement_opened`, `restructuring_opened`, two
  more `restructuring_ended` rules (a discontinuance citing the petition; an ending said as "prawomocn", dated
  by "zakończ"). Each rule excludes the approval, creditors'-list and administrator notices that quote the same
  words. `PreferDates.otherwise` (`first_body_date` | `none`).
- **`config/statutory/procedure_taxonomy.yaml` v2:** the GR split of the bankruptcy section; the restructuring
  section's kindless opening, its ending and its revocation; the arrangement section's opening and revocation;
  MSiG's two new kinds; the event type `arrangement_revoked`. `When.excludes`, `When.absent`.
- **`config/labels/outcome_labels_v3.yaml`**, the default `LABEL_VERSION`; `LabelConfig.taxonomy_version`,
  `notice_kinds_version`; `labels.check_mapping_versions`, called by `transform/config.py`.
- **`scores` and `realised_calibration`:** decision 6.
- **Tests:** the GR split, an unsigned and a GU record staying bankruptcies, the registry's restructuring records
  opening, closing and revoking, each new MSiG kind with its date, an approval notice that is not an opening, the
  procedural administrator notice, `otherwise: none`, the `when` tests, v3 differing from v2 only in the mapping
  versions, and a refused build from other mappings. `test_unknown_sections_and_values_are_rejected` now uses
  `zawieszenieWznowienie`, since `postepowanieUkladowe` is mapped.

## Effect on `legal_events` (whole store, read-only replay, 2026-10-19)

Quarantined records: 342 → 314 (`legal_event_type_unmapped` 81 → 71, `msig_notice_unclassified` 261 → 243).
Events: `accelerated_arrangement_opened` 0 → 8, `restructuring_proceedings_opened` 14 → 29,
`restructuring_proceeding_ended` 20 → 34, `arrangement_revoked` 0 → 4, `bankruptcy_declared` 204 → 202. Every
change is on the 7 entities above or the same proceedings' records at 0000066923, 0000074968, 0000205656,
0000367088 and 0000404461, except three registry endings no rule read before: 0000386777 (a seed entity, its
sanacja discontinued 2025-09-04) and 0000427434 (an arrangement approved 2021-02-25, revoked 2025-01-21,
discontinued 2025-11-21).

## Left quarantined

- **Chapter IV (36 notices, 30 entities, 21 with no event at all):** "ogłasza / wpis do rejestru / likwidac /
  rozwiąz", mostly 2024–2026. Plan 0014 read them as MSiG republishing registry entries; they may instead be the
  court's proceedings to dissolve a dormant company without liquidation, which would be a silent exit. The
  notice text is not stored, so one notice read by hand decides it. Not mapped by guess.
- **Chapter III/9 (139 notices, 40 entities):** every one of these entities already has a bankruptcy event, and
  in all but one the notices come after it (trustee notices). No label changes.
- **The rest of chapter IX** (approval and supervisor notices), **KRS suspensions** (56, deliberately unmapped),
  **petition dismissals** (`oddalenieWnioskuOUpadlosc`, 3, the class already right), **notices from before a
  mapping's range** (7) and **0000370981's simplified restructuring announced 2021-12-07**, after the procedure's
  end date (plan 0014's open item).

## Result (2026-10-19, run `4360de66`)

`legal_events` and the `labels` group rebuilt from stored bytes (no fetch): **label set `afeba985d8d9…`
(`outcome_labels_v3`, 146,608 rows)** against last night's `228e8dee2468…` (`outcome_labels_v2`, 146,674). Every
audit and `seed_acceptance` pass; `legal_events_quarantine` warns at 314. **462 rows change, on 11 entities**, the
7 above and the same proceedings at 0000066923, 0000074968, 0000205656 and 0000404461; no other entity's labels
move:

| v2 | v3 | Rows | What it is |
|---|---|---|---|
| `alive` | restructuring | 170 | a window that held an opening nothing recorded |
| bankruptcy | restructuring | 94 | the restructuring came first |
| bankruptcy | excluded | 75 | the entity was already in restructuring at `as_of_date` |
| excluded | bankruptcy | 61 | a GR case no longer a bankruptcy, or a discontinuance reopening the months before the bankruptcy |
| `alive` | excluded | 43 | already in restructuring |
| restructuring | excluded | 12 | |
| excluded or absent | `alive` / restructuring | 5 | |
| censored | excluded | 2 | |

66 rows fewer in all: months now excluded as in a proceeding. The served models (trained on v2) are unchanged;
from tonight scoring's risk set comes from v3 (decision 6). The GR count of decision 5 is 2 of 141.

## Out of scope

Retraining or re-pinning a backtest on v3 (the owner's decision); chapter IV; KRZ.
