# 0014 — A1 discovery: a hand-filtered Rejestr.io list

- **Status:** accepted (2026-10-06, owner). The list's composition is not yet measured, and it is not yet a
  representative sample (§ Consequences).
  *2026-10-07:* list v2's sampling rule, a random group and a distress group from one recorded frame, is proposed
  (§ "Addendum, 2026-10-07").
- **Date:** 2026-10-06
- **Follows up:** 0013 (A1 was left open; ADR 0013's scripted downloads need a list of KRS numbers)

## Context

A3 now has a route at scale: the owner's Power Automate Desktop script (ADR 0013). It searches RDF only for KRS
numbers it is given and never enumerates them (AGENT_SPEC §6A), so the universe is whatever list it is given.
Until now the only list was the hand-picked 17-entity seed (`config/segments/construction_sme_v1_seed.yaml`),
chosen with distress hints, so its base rate is wrong for the population by construction (ADR 0013, option d).

## Decision

**Accepted by the owner, 2026-10-06: the first universe beyond the seed is a list of companies the owner built
with Rejestr.io's (`rejestr.io`) search filters.**

- **How it was built:** the owner selected about 600 construction-related companies by PKD, at various income
  levels, locations and distress statuses. The exact filter settings are not recorded yet.
- **The file:** a CSV with two columns, `krs_num` and `company_name`, 611 rows, 611 distinct well-formed KRS
  numbers, none of them a seed entity. Version received 2026-10-06: SHA-256 `636f4bab25f0…` (in full,
  `636f4bab25f04cbd16be951958abb8422c7714773b49a2b4c73c34578aa2362a`).
- **The list the script actually runs on differs slightly.** A few companies were skipped or removed; the shape
  is the same. The list of record is the one the script runs on, and it is the one loaded into
  `universe_candidates`.
- **Only KRS numbers enter the pipeline.** `discovery_source` names the list and its version. Company names are
  not taken from it: `entity_master` takes the name from GUS (A2). A few names in the file contain a person's
  name, so the file is not committed. It stays outside the repository, like the inboxes.
- **No enumeration:** the pipeline reads this list and nothing else for A1. It never searches Rejestr.io or any
  registry for more numbers on its own.

## Consequences

- **The list is purposive, not a probability sample.** It was chosen by hand along axes that include distress
  status, the same kind of hint ADR 0013 warned makes a base rate wrong by construction. Until its composition is
  measured and reweighted, or the list is redrawn by a written rule, results on it are machinery tested on a
  larger set, not population estimates. Calibration (Brier score) is the measure most affected. Every report
  built on it states this, as the seed caveat does.
- **Its composition is to be measured and stated** once A2 and the KRS extracts have run over it:
  - PKD section F against "construction-related" codes outside F;
  - legal form (v1 is `sp. z o.o.` only);
  - size class;
  - region;
  - registration date and filed years (v1 needs at least three);
  - the distress status as the pipeline's own labels find it, not as the filter showed it.

  One sign already: 174 of the 611 have KRS numbers of 0001000000 or higher, among the most recently issued. Some
  of them will not have three filed years yet. Registration dates come from the KRS extracts.
- **Comparing it with the segment:** PROJECT_OVERVIEW stage 1 already names the check, GUS aggregate counts
  by PKD section and employment band against the discovered universe. It needs A5's GUS BDL adapter, which is
  not built.
- **To make it representative** (the owner's stated next step): record the filters used, describe the frame
  they draw from (how many companies match the segment's definition in all), and draw the next list from that
  frame by a written rule, stratified if wanted, with the strata and their weights recorded. A list built that
  way gets a new `discovery_source` version, and the backtest reports which list each result rests on.
- ~~**Rejestr.io's terms of use are not yet confirmed**~~ *Confirmed by the owner, 2026-10-07* (AGENT_SPEC
  §11.3): Rejestr.io is a commercial aggregator of public registry data, and the owner uses it by hand through
  its search, as it is meant to be used, on a 14-day trial during which the full list is built. Only KRS numbers
  still enter the pipeline, and nothing from Rejestr.io is published.
- **Follow-up work** goes in plan 0014:
  - a loader for the list into `universe_candidates`, beside the seed's YAML loader;
  - A2 over its KRS numbers;
  - the composition census above, after A2 and the KRS extracts.

## Addendum, 2026-10-07: the sampling rule for list v2 (proposed)

- **Status:** proposed (2026-10-07). The design is the owner's: a larger group drawn at random and a smaller
  group of companies in distress, for a v1 that will run on under about 1,000 companies at first. The frame's
  counts, the group sizes and the seed are blanks (`…`) until the owner has run the filters; the draw happens only
  once they are filled in and this addendum is accepted.
- **Why two groups:** at under 1,000 companies, a random sample alone yields too few events to fit or test a
  model on (a few dozen, at an assumed yearly event rate of 1–2%). A distress group adds events. Because its
  selection is written down with the frame's counts, every company's probability of being drawn is known, and
  the model can be weighted back to the population. A list picked by eye, like v1's, cannot be.

### The frame

One frame, F, from Rejestr.io's search, the same for both groups. Only filters that define the segment:

| Filter | Setting |
|---|---|
| Primary PKD (przeważający) | divisions 41, 42, 43; the PKD version Rejestr.io filters on recorded (PKD 2025 moves part of 41.10 out of section F) |
| Legal form | `sp. z o.o.` |
| Status | every status: active, in liquidation, bankrupt, in restructuring, deleted from KRS |
| Registration date | before 2022-01-01, so that three filed years are possible |
| Financial values | none; if a size filter is unavoidable, a generous band, with the field, its year and the bounds recorded |

- **Not filtered:** profit, loss, equity or any other value a feature reads (filtering on them would select on
  the predictors); and the latest year's revenue or size (a company that failed in 2021 has none, so the filter
  would drop exactly the events). Size class is computed by the pipeline from the filings (AGENT_SPEC §4.4),
  never by the filter.
- **Recorded:** every filter setting as Rejestr.io shows it, the date the frame was taken, and N_F = … companies.
- **Kept:** the frame's KRS numbers, exported from Rejestr.io before the trial ends, as a file outside the
  repository, with its SHA-256 here. Without it the draw cannot be checked, repeated or extended.

### The distress stratum

D ⊂ F: the companies Rejestr.io shows with an entry of bankruptcy, restructuring or liquidation (AGENT_SPEC
§4.6's classes) first entered on or after 2019-01-01, so that at least one statement before the event falls
inside v1's years (from 2018). Recorded: the Rejestr.io setting used and N_D = …. A deregistration with no such
entry (`silent_exit`) is not in D: Rejestr.io cannot tell it apart from a merger.

D is the design variable, not the outcome. The pipeline labels every company from the KRS extract and MSiG, as
now. A company in D the pipeline finds no event for, or one outside D it does, stays in its group with its
weight; the agreement between Rejestr.io's flag and the pipeline's labels is counted and reported.

### The draw

- **Seed:** a fixed integer, s = …, recorded here before the draw.
- **Order:** the frame's KRS numbers sorted, then put in one random order by the seed (a permutation of F). The
  draw is done by pipeline code from the stored frame file, so anyone can repeat it.
- **Group A (random):** the first n_A = … companies of the permutation, whatever their status. Members of D drawn
  here belong to A.
- **Group B (distress):** the members of D not in A, in the permutation's order, the first n_B = … of them (all of
  them if fewer).
- **Extending later** never means picking by hand: more of A is the next companies of the same permutation, more
  of B the next members of D, and the list gets a new version.

### Weights

With f_A = n_A / N_F, and d_A the number of D's members in A:

- a company outside D: probability of being drawn π = f_A;
- a company in D: π = f_A + (1 − f_A) · n_B / (N_D − d_A).

Its weight is 1/π. Each company carries its group, π and the list version (`discovery_source` versions
`rejestr_io_v2_random` and `rejestr_io_v2_distress`, plan 0014 step 0).

### Use

- **Training** on A and B together, with the weights.
- **Base rates, calibration (Brier score, reliability) and the backtest's headline results** are weighted, over A
  and B together; the same on A alone is reported beside them as the check. B alone never gives a rate or a
  calibration, and no unweighted result over A and B is presented as the population's.
- **Download order:** B first (it is small, and it brings the events early), then A in the permutation's order, so
  whatever part of A has been downloaded at any moment is itself a random sample.
- **Labelling text signals (plan 0013):** B's companies are where the scarce positives are. A golden sample from
  the two groups, with its drawing rule recorded, replaces finishing the seed's notes queue.

### Things to expect, and how they are handled

- **Many of B's companies stop filing before their event:** fewer than three filed years, or a last statement
  years before the petition. They are not dropped at the draw. The pipeline's own filters (filed years, size
  class per year) apply to both groups alike, and the drop-outs are counted per group. A company that stops
  filing before failing is part of the filing-behaviour signal.
- **Distressed companies shrink:** a company small or medium in its good years may be micro in its last. Size is
  judged per year by the pipeline, not at the draw.
- **The 2020–21 regime:** the simplified COVID restructuring will be over-represented in B. The regime flag
  (AGENT_SPEC §4.6) and the `no_regime` backtest run already handle it; B's event years are counted, so the
  out-of-time folds can be checked for events in every test year.
- **List v1 (the 611) and the seed** stay separate, purposive lists. A company of theirs that the draw picks
  counts in its group like any other, and its documents already downloaded are reused; no other company of
  theirs enters a weighted result.
