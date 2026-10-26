# 0024 — Outcome labels, version 4: court dissolutions, liquidation dates, late simplified restructurings

**Stage:** C (legal events) and F (labels), after plan 0022. **Status: active (2026-10-26): written on the
owner's request after the review of the legal quarantine (§ "Why"); owner decisions 1 and 2 taken
(2026-10-26), decisions 3–8 this plan's own (flag any to change); nothing built.**

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
