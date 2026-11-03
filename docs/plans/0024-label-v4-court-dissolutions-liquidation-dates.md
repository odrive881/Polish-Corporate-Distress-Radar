# 0024 — Outcome labels, version 4: court dissolutions, liquidation dates, late simplified restructurings

**Stage:** C (legal events) and F (labels), after plan 0022. **Status: complete (2026-11-03). Active from 2026-10-26: written on the
owner's request after the review of the legal quarantine (§ "Why"); owner decisions 1 and 2 taken
(2026-10-26), decisions 3–8 this plan's own (flag any to change). 2026-11-02: steps A.3–A.4 done, decision 4
changed by its own rule (§ "Progress"), steps B–C built on branch `plan-0024-label-v4`. 2026-11-03: A.1–A.2
done, the liquidation rule excludes merger plans (owner, after A.2), D's replay done. 2026-11-03: merged to master, D's rebuild done (v4 set `a10a093b9007`), step E's docs sweep done.**

- **Invariants:** 1 (point in time: an event is known from its publication), 4 (no silent loss: what no rule
  maps stays quarantined, never guessed), 5 (idempotence: the label set is rebuilt from stored bytes, no
  fetch), 6 (legal entities only: notices stay reduced records, their text is never stored or printed), 7
  (statutes and mappings are dated config).
- **Spec:** AGENT_SPEC §4.6 (the outcome taxonomy; this plan changes the `silent_exit` row), §5
  (`legal_events`, `outcome_labels`).
- **Inputs:** the stored KRS extracts and MSiG notice records of the 1,497 entities fetched on 2026-10-26 (run
  `b95d78a9`, the first fetch over list v3's draw), label set `ac0633459a02…` (`outcome_labels_v3`).

## Why

The first legal fetch over list v3's draw (2026-10-26) froze label set `ac0633459a02…` and grew
`legal_events_quarantine` from 314 records to 593. Recomputed read-only from the stored documents, they fall into
the groups below. Four touch a label; the rest do not (§ "Left quarantined").

**1. MSiG chapter IV is the court dissolving dormant companies, not republished registry entries.** 131 notices
of 113 entities, rising every year (10 in 2020, 26 in 2024, 32 in 2025, 26 so far in 2026). The chapter is "IV.
Ogłoszenia wymagane przez ustawę o Krajowym Rejestrze Sądowym", and every notice's terms are "ogłasza /
zawiadamia, wpis do rejestru, likwidac, rozwiąz". Plan 0014 (item 7) read them as MSiG republishing registry
entries, and plan 0022 left one hand reading to decide. Decided without storing or printing any text: 12
notices, sampled across the four term patterns, were fetched into memory and tested for marker phrases, only
the booleans printed. All 12 announce the **initiation** ("wszczęcie") of proceedings and call on whoever holds
the company's property to come forward, 10 within three months; 8 say "bez przeprowadzenia postępowania
likwidacyjnego", 5 cite art. 25a; none is a dissolution decision or a liquidation opening. That is the registry
court's proceeding to dissolve a company without liquidation (ustawa o KRS, art. 25a–25d), which ends in
deregistration unless property turns up.

Of the 113, 33 are already deregistered and 30 labelled `silent_exit`, but from the later deregistration; **62
are labelled `alive` throughout**. 76 of the 113 are in list v3's random group.

**2. A date bug: MSiG liquidation openings dated by the notice's first date.** The `liquidation_opened` rule
prefers a date after "uchwał" and falls back to the first body date. 91 of the 166 MSiG liquidation openings are
dated by that fallback, which is often the company's registration date quoted in the heading, or a statute's
date. 88 of the 91 are also recorded by the registry; 13 disagree with its date, 5 of them earlier. Three
companies' labels are wrong:

| Entity | List | MSiG's date | The registry's | Effect |
|---|---|---|---|---|
| 0000435747 | v3, distress | 2012-10-08 (its registration) | 2024-04-16 | no label rows: "in liquidation" since it was founded |
| 0000523549 | v3, random | 2014-09-17 (its registration) | 2020-09-21 | no label rows |
| 0000302136 | v3, random | 2000-09-15 (the KSH's own date) | none | refused as before the statute, so `alive` through 2026-09 |

0000302136's notice also says "połącz" and "uproszczon": it may be a merger notice that mentions a liquidation
(the merger rule excludes "likwidac"), so its class is checked before anything maps it (step A).

**3. Simplified restructurings published after 2021-11-30.** 0000370981 (plan 0014's known case; arrangement day
2021-11-27, published 2021-12-07) and 0000552750 (list v3's random group; arrangement day 2021-12-01, published
2021-12-03). The mapping ends with its statute on 2021-11-30, and the rule dates the event by publication, so
both are refused and both companies are labelled `alive`.

**4. A KRS dissolution field reduced by the redaction** (31 entities): `okreslenieOkolicznosci` over 40 characters
became `[REDACTED]` (`krs-json-2`), so the `ROZWIĄZANIE` test can never match. It maps to a signal, not a class;
28 of the 31 are already labelled distressed and 3 `alive`. Not fixed here (decision 7).

## Owner decisions (2026-10-26)

1. **Chapter IV's initiation is a dated opening of the `silent_exit` class.** The label fires when the court
   acts, not at a deregistration years later or not yet entered. The deregistration that follows stays a
   `silent_exit` exit. A company that already had a bankruptcy, restructuring or liquidation keeps that class,
   as now: the initiation comes after it and the entity is already out of the grid.
2. **The simplified restructurings published after 2021-11-30 are accepted** (§ "Why", item 3).

## Decisions this plan makes (flag any you disagree with)

3. **The new event type is `court_dissolution_initiated`:** `stage: opening`, `outcome_class: silent_exit`,
   `precludes_silent_exit: false` (so the deregistration after it stays a `silent_exit`, not `merged_away`).
   Under a new statute entry `krs_art25a` (ustawa o KRS, art. 25a–25d, from 2015-01-01; the date confirmed
   against the act in step A). Precedence is unchanged: `silent_exit` is last, so a same-day bankruptcy,
   restructuring or liquidation wins.
4. **It lasts until the deregistration**, like every opening (an opening stays "in a proceeding" until closed;
   `deregistered` excludes the entity from then on). The court can also discontinue the proceeding when
   property or filings turn up, and no source this project reads records that. Step A measures how often an
   initiated company shows life afterwards; if it is more than a handful, this changes before step C to the
   petition stage's 24-month expiry (`petition_expiry_months`), and the plan says so.
5. **Chapter IV's rule:** `chapters: [IV]`, `all: [likwidac, rozwiąz]`, dated by the court's decision
   (`prefer: [[postanowieni]]`, `otherwise: none`), else undated and so on or before its publication. Every one
   of the 131 notices has both terms. A chapter IV notice without them stays quarantined. The vocabulary does not
   change, so the extraction key does not, and nothing is fetched again.
6. **Liquidation openings never take the fallback date.** The `liquidation_opened` rule takes `otherwise: none`
   (plan 0022 decision 3's tool). An undated MSiG opening is merged with the registry's record of the same
   opening where one exists (88 of 91), whose date wins; the 3 with none are dated on or before their
   publication. Also a guard in `legal_events_canonical`: an MSiG `event_date` earlier than the entity's own
   `registered` event is set null, as the source of a mistake, and counted in the asset's metadata, never
   silently.
7. **The late simplified restructurings:** the MSiG mapping gets `publication_grace_days: 31` (new, MSiG mappings
   only): a publication-dated event resolves under a statute that ended at most that many days before it. The
   statute's own dates stay as enacted. *Alternative, not taken:* dating these events by the arrangement day
   ("dniem układowym"), which is the more exact opening date but moves every simplified restructuring's date,
   and 0000552750's (2021-12-01) is still a day past the statute's end.
8. **The redacted dissolution field (item 4) waits.** Fixing it means allowlisting `okreslenieOkolicznosci` in
   a new redaction version (`krs-json-3`) and fetching every extract again, for a signal. It is worth doing only
   if a later plan reads that field as part of the art. 25a proceeding; recorded here, not built.

## Steps

### A. Checks before any rule is written (read-only)

1. **All 131 chapter IV notices,** by the same in-memory probe (booleans only, 4 seconds a request, about 9
   minutes): each says "wszczęcie", and none says the court dissolved the company or discontinued the
   proceeding. Any that does becomes its own kind (a dissolution decision is the deregistration's, a
   discontinuance a closing) before step B, or stays quarantined.
2. **0000302136's notice:** a merger plan or a liquidation, by the same probe ("plan połączenia", "otwarcie
   likwidacji", "w likwidacji").
3. **Decision 4's measure:** of the initiated companies, how many filed a statement (`filing_index`) or changed
   their board after the notice, and how many are still registered 24 months after it.
4. **The art. 25a statute date,** from the act's text.

### B. Mappings

- `config/mappings/msig_notice_kinds.yaml` **v3:** `court_dissolution_initiated` (chapter IV, decision 5);
  `liquidation_opened` with `otherwise: none` (decision 6); the merger rule's exclusion revisited if step A.2
  finds a merger notice that names a liquidation.
- `config/statutory/procedure_taxonomy.yaml` **v3:** the statute `krs_art25a`, the event type and its MSiG
  mapping; `publication_grace_days: 31` on the simplified-restructuring mapping (decision 7), and the loader's
  check that only an MSiG, publication-dated mapping may carry it.
- `transform/models/staging/legal_events_canonical.sql`: decision 6's guard.

### C. Label version 4 and tests

- `config/labels/outcome_labels_v4.yaml`: v3's parameters plus `taxonomy_version: 3`, `notice_kinds_version: 3`
  (plan 0022 decision 1), the default `LABEL_VERSION`. Scoring's risk set and the realised calibration read the
  latest v4 set from then on (plan 0022 decision 6).
- **Tests:** a chapter IV initiation is a `silent_exit` opening, dated by its decision or undated; a chapter IV
  notice without both terms stays quarantined; a deregistration after an initiation stays `silent_exit`; a
  liquidation notice with no "uchwał" date is undated and takes the registry's date; an MSiG date before
  registration is nulled and counted; a simplified restructuring published 2021-12-07 resolves, one published
  2022-01-15 is still refused; a grace on a KRS mapping is a load error; v4 differs from v3 only in the mapping
  versions; `seed_acceptance` unchanged (whether a seed entity has a chapter IV notice is checked in the replay).

### D. Result

A read-only replay first (quarantine and events before and after, every changed entity listed), then
`legal_events` and the `labels` group rebuilt from stored bytes, with no fetch, and the v4 set frozen. Recorded
here with its hash, its row count, the entities whose labels change, and each of the three companies in item 2
checked by hand against the registry.

### E. Docs sweep and close-out

AGENT_SPEC §4.6's `silent_exit` row (a deregistration, or the registry court's initiation of a dissolution
without liquidation; "filings ceasing" stays a feature); the dated corrections in plan 0014 (item 7) and plan
0022 (§ "Left quarantined") that chapter IV is the art. 25a initiation; README's pick-up paragraph; the
`LABEL_VERSION` default wherever it is quoted; `make docs-check`, then the hand read against this plan's
results.

## Progress

**2026-11-01, steps A.3 and A.4 (read-only).** Chapter IV now holds 132 notices of 113 entities (list v3's
first legal fetch). Every one carries both "likwidac" and "rozwiąz"; none has a "postanowieni" date, so every
initiation is undated, on or before its publication. 35 of the 113 are deregistered, a median 228 days after
the first notice (97 to 1,848); 12 had a bankruptcy, restructuring or liquidation before it. **22 show life
afterwards and are not deregistered** (11 filed a statement after the notice, 13 changed their board), and of
the 60 first notified at least 24 months ago, **33 are still registered**. First notices by year: 3, 1, 3, 9,
5, 8, 12, 23, 25, 24 (2017–2026). The act of 28 November 2014 (Dz.U. 2014 poz. 1924), art. 6: in force on
1 January 2015, so `krs_art25a` starts then.

**Decision 4 changed, as it said it would.** 22 is more than a handful: the initiation is a **petition-stage**
`silent_exit` event, not an opening. It labels the window it falls in, excludes the entity for
`petition_expiry_months` (24) at most, and nothing else closes it; the deregistration that follows is still
a `silent_exit`. The label grid's "in a proceeding" test now counts `silent_exit` petitions too (it read only
the other three classes, so a `silent_exit` opening would never have excluded anything).

**Decision 6's guard sits in `legal_events`, not `legal_events_canonical`.** Nulling the date before
deduplication lets the undated MSiG row join the registry's record of the same opening (the SQL view groups
after the date is set, so it could not), and the asset counts it in its metadata
(`msig_dates_before_registration_nulled`).

**2026-11-02, steps B and C built** (branch `plan-0024-label-v4`, worktree `../LARGE_pipeline-0024`):
`msig_notice_kinds.yaml` v3 (`court_dissolution_initiated`, `liquidation_opened` with `otherwise: none`),
`procedure_taxonomy.yaml` v3 (`krs_art25a`, the event type, its mapping, `publication_grace_days: 31` on the
simplified restructuring, refused at load on a non-MSiG mapping and applied only to an event its notice
dates by publication), the grid change, `outcome_labels_v4.yaml` as the default `LABEL_VERSION`, and the tests
step C lists. One seed fixture changes: 0000397658's liquidation notice of 2021-11-25 was dated 01.10.2021 by
the fallback (the date follows "zawiadamia", not "uchwał"), and is now undated; the replay checks it against
the registry.

**2026-11-02, steps A.1 and A.2 (read-only, the same in-memory probe).** A.1: of the 132 chapter IV notices,
all 132 name the company's assets and 124 say "wszczęcie"; none says the court dissolved the company or
discontinued the proceeding. The other 8 are the same call worded otherwise, so no further kind is needed.
A.2: **0000302136's notice is a merger plan** ("plan połączenia", the acquired company dissolved "bez
przeprowadzenia likwidacji"), with neither "otwarcie likwidacji" nor "w likwidacji". The stored records
show 7 notices of 5 entities the liquidation rule matched with a merger term (0000172778, 0000251888 ×3,
0000302136, 0000811275, 0001006920), none with "otwarci"; the probe found each a merger plan. No other
notice the rule matches has a merger term. **Owner decision (2026-11-03):** `liquidation_opened` gains
`none: [połącz, przekształc, plan podziału]`; such a notice matches no rule and stays quarantined
(`msig_notice_unclassified`), and the registry's record dates the merger itself. Three of them (0000172778,
0000811275, 0001006920) were dated liquidation openings under v2 too, so the current v3 labels carry a false
liquidation for those three.

**2026-11-03, step D's replay** (stored bytes, master config v2 against the branch's v3, nothing written):
2,877 documents (1,497 KRS extracts, 1,380 MSiG fetches). Rows 7,778 → 7,911, rejects 595 → 462. Added:
132 `court_dissolution_initiated` (113 entities, all undated, so effective from publication), 2
`simplified_restructuring_announced` (0000370981, 0000552750), 2 `liquidation_opened` once refused for their
fallback date; removed: the 3 merger plans above; re-dated: 85 MSiG liquidation openings now undated
(0000397658, 0000435747 and 0000523549 take the registry's date; 18 entities have no registry row and take
publication). New rejects: only the 7 merger plans. 191 entities change, all in `entity_master`. No MSiG date
falls before registration once the fallback is gone (the guard nulls 0). `seed_acceptance` unchanged (9
matched, 8 clear).

**2026-11-03, step D's rebuild** (run 07601e37: `legal_events` and the `labels` group from stored bytes, no
fetch; the nightly's catch-up fetch was cancelled for it). Label set **`a10a093b90073419b7855247e2d8ec47e5ebd78dc4851cfda9880dc06d831116`**
(`outcome_labels_v4`, cutoff 2026-11-03), **279,868 rows over 1,489 entities**, against v3's `b1f256f8efea`
(2026-11-02, 282,626 rows, 1,487 entities). All label checks pass. 120 entities' labels change:

- **`silent_exit` 1,188 → 3,828 rows:** 1,702 `alive` rows (76 entities) and 1,343 censored rows (57) now end
  in an initiation; 3,570 rows of 106 entities leave the grid, 1,834 of them censored in v3, as excluded while
  the petition is open (24 months at most).
- **The three companies of item 2:** 0000435747 and 0000523549 now have label rows (276 and 144, 36 each
  `liquidation`, dated by the registry: 2024-04-16 and 2020-09-21); 0000302136 is unchanged (`alive`, its
  merger plan quarantined). The other merger plans lose their false liquidation: 0000172778, 0000811275 and
  0001006920 have no `liquidation` row left (151 rows `liquidation` → `alive`, 54 → censored).
- **Simplified restructurings:** 0000370981 and 0000552750 have 36 `restructuring` rows each.
- **An initiation before a bankruptcy** relabels the window it opens first: 0000676128 (initiation published
  2025-04-25, bankruptcy 2026-08-31) and 0000069241 (2017-12-01, 2019-10-17), 10 rows `bankruptcy` →
  `silent_exit`. That is decision 4 as written (the first event in the window sets the class).

**2026-11-03, step E.** AGENT_SPEC §4.6's `silent_exit` row and the label paragraph (a `silent_exit`
petition excludes like the others), its status paragraph, plan 0014 item 7 (0000370981 resolved) and
plan 0022's chapter IV correction, README's status line and pick-up paragraph (the v4 set, scoring refused
until `backtest_v10`), `backtest_v10.yaml`'s note that it pins a v4 set, and the `LABEL_VERSION` default
(`settings.py`, `.env.example`, README). `make docs-check` passes.

## What this plan does not do

- **No retraining.** `backtest_v9` stays pinned to its v3 set. `backtest_v10` pins its label set when list v3's
  downloads are in (plan 0019 step G), and will name the v4 set then.
- **No fetch.** Everything is rebuilt from stored bytes; the nightly check picks up the new rules on its next run.
- **No KRZ** (ADR 0011).

## Left quarantined

- **MSiG III/9** (205 notices, 51 entities): trustee notices after a bankruptcy; 50 of the 51 are already
  labelled bankrupt, the other has no label rows.
- **MSiG IX** (56 notices, 11 entities): approval and supervisor notices; all 11 are already labelled
  restructuring.
- **KRS suspensions** (`zawieszenieWznowienie`, 131 entities): deliberately unmapped (plan 0022), not distress. 87
  are labelled `alive`; a suspension is a feature candidate, not a label.
- **KRS bankruptcy-petition dismissals** (6, all already labelled distressed), **the redacted dissolution field**
  (31, decision 8), **notices from before a mapping's range** and **minor chapters** (about 20, mostly 2002–2008),
  and **one MSiG notice under another company's KRS number** (`msig_krs_mismatch`).
