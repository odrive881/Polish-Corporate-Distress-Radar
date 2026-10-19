# 0014 — A1 discovery: a hand-filtered Rejestr.io list

- **Status:** accepted (2026-10-06, owner). The list's composition is not yet measured, and it is not yet a
  representative sample (§ Consequences).
  *2026-10-07:* list v2's sampling rule, a random group and a distress group from one recorded frame, is proposed
  (§ "Addendum, 2026-10-07"). *2026-10-13:* the owner's two list v2 files are received and recorded there
  (§ "Lists received, 2026-10-13"); they were not drawn by its rule, so the addendum stays proposed. *2026-10-18:* what the weights need, and what would break them, is recorded
  (§ "What the weights need").
  *2026-10-19:* list v3's random group is drawn by the owner's script, systematically from a recorded frame of
  59,547, and checked (§ "List v3's random group"); its distress group is to be redrawn the same way, so the
  addendum stays proposed. *2026-10-20:* the distress group is redrawn by the owner's distress script (17:21 the
  same day), checked, and its draw spec written (§ "List v3's distress group"); the addendum goes to the owner.
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
  `universe_candidates`. *Loaded 2026-10-09* as `discovery_source` `rejestr_io_v1`: 609 rows, 609 distinct well-formed KRS
  numbers, SHA-256 `d69e8866cf9db774a8c0abfa2fe0edde5e567bb164cce8611e4f375d29ca492e` (plan 0014, step 0).
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

**Composition census, list v1 (2026-10-09, `notebooks/exploration/list_v1_composition_census.py`).** Of 609
KRS numbers, A2 resolved 592; 16 are outside section F (`pkd_section_mismatch`), 1 an ambiguous match. Of the 592:

- **PKD:** division 41: 261, 42: 95, 43: 236 (PKD 2007 for 540, PKD 2025 for 52). **Legal form:** all
  `sp. z o.o.`. **GUS status:** 578 active, 14 suspended. **None deregistered:** the list holds no company
  already deleted from KRS, so v1 cannot show an exit that ended in deregistration (survivorship).
- **Region:** every voivodeship; Mazowieckie 144, then Śląskie 61, Dolnośląskie 58, Małopolskie 52,
  Wielkopolskie 49, the rest 11–41 each.
- **Registered in KRS:** before 2010: 82; 2010–2018: 160; 2019–2021: 136; 2022 or later: 214, so over a third
  cannot have three filed years yet.
- **Filed years** (the 223 searched and found so far): 1–2: 34, 3–5: 79, 6 or more: 110. 369 not searched yet,
  or only during the outage.
- **Size as the filings show it** (186 with a parsed statement; not the §4.4 class, which waits on
  employment): form filed full 96, small 56, micro 34; total assets < 2m PLN 55, 2–10m 53, 10–50m 69, 50m or
  more 9; revenue < 2m 12, 2–10m 100, 10–50m 69, 50m or more 5.
- **Distress, as the pipeline's legal events find it:** 20 companies with a bankruptcy (7), liquidation (9)
  or restructuring (4) event, by the first; first events before 2019: 3, 2019–2020: 4, 2024–2026: 12, 1 undated.
  20 of 592 is the list's rate by construction, not the segment's.

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

### Lists received, 2026-10-13

The owner's two files for list v2, kept outside the repository like v1's (in `.cache/universe_lists/`, ignored by
git; some company names name people). Columns `krs, company_name` and a status column; only the KRS numbers
enter the pipeline. Both have well-formed, distinct KRS numbers and no company in common.

| Group | File | Rows | Status column | SHA-256 |
|---|---|---|---|---|
| A (random) | `Merged sample (600).csv` | 600 | `in_distress`: 494 `False`, 106 `True` | `6d58ee4379aa58bae97d8a973a03a856216f912f6a15d84f0f6114f8cfc9fa48` |
| B (distress) | `Merged distress sample (65-35 split).csv` | 200 | `distress_type`: 130 `w upadlosci`, 70 `w likwidacji` | `a8fc9cfb99a11406e963449ee9efc6a6530075d569472fd10e1c5acc16c33a4b` |

They replace two earlier drafts (2026-10-09: 660 rows; 199 rows that included `w zawieszeniu`).

- **The owner's rule for A:** at least three years of continuous operation.
- **B's split is the owner's choice,** 65% bankruptcy, 35% liquidation. Rejestr.io's status filter offers only
  W zawieszeniu, W likwidacji and W upadłości, so restructuring cannot be selected and D is in practice
  "bankrupt or in liquidation, as Rejestr.io shows them now". Restructuring events still come from the
  pipeline's labels (KRS extract, MSiG), for companies in either group. Suspension (`w zawieszeniu`) is left
  out: it is dormancy, not an outcome class of AGENT_SPEC §4.6. Two of B's companies are seed entities.
- **Not drawn by the rule above.** No frame file, N_F, N_D, seed or permutation is recorded, so π and the weights
  cannot be computed from these files. What the files show:
  - A is four contiguous runs of KRS numbers, not a spread over a frame: 0000645842–0000656620 (165),
    0000756893–0000764849 (149), 0000868333–0000876804 (180), 0001001976–0001011163 (106), roughly four
    registration cohorts from late 2016 to early 2023. The last run was registered after the frame's
    2022-01-01 cut, so at most two or three statements fall inside v1's years.
  - B's KRS numbers spread from 0000004617 to 0001006534. Whether its companies' entries were first made on or
    after 2019-01-01 is not checked yet; the KRS extract (A4) tells, once they are downloaded.
- **Until the frame is recorded,** these lists are used like v1: purposive, their results stated as the lists',
  not the segment's. To weight them, the owner records the filter settings and the count each search returned
  (per status for B, per search for A), and how the rows were chosen from each search's results.

### What the weights need (2026-10-18)

The owner's question: Rejestr.io's count depends on the filters (PKD alone gives about 109,000 companies, PKD and
at least three years of operation about 60,000, a distress status a few hundred to a few thousand per
category). Which count do the weights need? Neither number matters in itself. What matters is the **pool each
group was actually drawn from**, counted with the **same base filters for both groups**, so that the distress
pool is a part of the random group's pool (D ⊂ F, above).

**The numbers to record:**

| # | Number | Example |
|---|---|---|
| 1 | N_F: the companies in the pool group A was drawn from, with exactly the filters used for A | PKD 41–43, `sp. z o.o.`, at least 3 years: about 60,000 |
| 2 | N_D per category: the same filters as #1 plus the status filter, one count per status | the same plus `w upadłości`, plus `w likwidacji` |
| 3 | How many were taken from each | 600 from #1; 130 and 70 from #2 (known) |
| 4 | How the rows were chosen from each search's results | at random, the first pages, by some sort order |
| 5 | The date and every filter setting, as Rejestr.io shows them | a screenshot is enough |

The overlap d_A, the members of D drawn into A, is counted by the pipeline: A's file flags 106 `in_distress`
(per status once their labels are in). The formula is the § "Weights" above, applied per status, since B was
split 130/70 by status: for a status c, π = f_A + (1 − f_A) · n_Bc / (N_Dc − d_Ac). An illustration with an
assumed N_D: a company outside D stands for N_F / n_A = 60,000 / 600 = 100 companies, and a bankrupt company of B
for about 1,800 / (130 + its share of A's 106) ≈ 13. So a distressed company counts about eight times less than a
healthy one, which restores the segment's mix of events and non-events.

**What would break them:**

1. **Different base filters for the two groups.** If A came from PKD and three years (60,000) but the distress
   counts from PKD and status alone, without the three-year filter, D is not a part of F and the weights mix two
   populations. The distress searches are re-run with A's filters on, and those counts are the ones recorded.
   The same holds for the legal form: if 109,000 includes every legal form and 60,000 is `sp. z o.o.` only, the
   60,000 is the frame.
2. **A filter that drops dead companies.** Rejestr.io's "at least three years of operation" must keep companies
   now bankrupt, in liquidation or deleted from KRS (the frame takes every status, § "The frame"). If it counts
   only companies still active, F leaves out exactly the events. Check: the 60,000 search narrowed further to `w
   upadłości` should return about what the distress search did.
3. **How the 600 were chosen** (the serious one). N_F / n_A is a company's weight only if every company in F had
   the same chance of being drawn. A's file is four unbroken runs of KRS numbers (§ "Lists received"), which
   looks like whole pages of a result list sorted by KRS. A company registered outside those four periods had no
   chance of being drawn, so its weight is undefined, not 100. Two ways out:
   - **Recommended:** export F's KRS numbers once (§ "The frame", "Kept"), and draw A again by pipeline code
     with a recorded seed (§ "The draw"). Companies already downloaded are reused where the new draw picks them.
     The export must happen before the Rejestr.io trial ends (`docs/data_inventory.md`).
   - **Fallback**, if F cannot be exported: record exactly how the four runs came about (sort order, pages), and
     define the population as the companies registered in those four periods, weighted within each period. The
     weighted results then speak only for those registration years, not for the segment.

   The same applies to B: if its 130 and 70 are the first rows of a sorted result list, they over-represent
   whatever that order favours, and #4 says so.

Until these are recorded, plan 0014's decision 7 holds: the champion rule reads the lists unweighted and a
promotion on them is provisional.

### List v3's random group, 2026-10-19

The owner drew a new group A with their own script against Rejestr.io's search, and kept a run log beside it.
Both files stay outside the repository (`E:\Rejestr.io company pull\most_recent_run\`); the log names every drawn
company, and some names name people, so only its counts, settings and hash are copied here. An earlier file of the
same day (`rejestr_sample.csv`, 13:04, "1 in 40 of about 58,000", whose step and total did not agree) is
superseded and not used.

| File | Rows | SHA-256 |
|---|---|---|
| `rejestr_sample_2026-10-19_1403.csv` (`krs, company_name, status`, in a seeded random order) | 600 | `6af8e190654be47aeb257b8f57313343e07155d717841c2e61a4e9a7ba3da7b4` |
| `rejestr_log_2026-10-19_1403.txt` (filters, totals, sampling, every pick with its position) | — | `25da25574f0c9ca0105be7fe17e6634f41bc6b56861e03a06e070c964707f2b3` |

**The frame, as the log records it** (2026-10-19, 14:03–14:37 Warsaw time):

| Filter | Setting |
|---|---|
| Legal form (`lf`) | `szoo` (`sp. z o.o.`) |
| Industry (`ind`) | `F` (Budownictwo, sekcja PKD F); whether Rejestr.io reads the primary PKD, and which PKD version, is not yet known |
| Registration date (`rd`) | 2001-01-01 to 2022-12-31 |
| Status, location, finances, reporting type, employment, beneficial owner, CEO gender | not set |

N_F = 59,547, both as the full search's total and as the sum of its 22 registration-year slices (2001: 977 …
2021: 8,644, 2022: 7,316; every year's count is in the log).

**The draw: systematic, with a random start.** The year slices were taken 2001 to 2022, each sorted by
registration date, newest first, and concatenated. The step is N_F / 600 = 99.245, the start 6.229329 (random in
[0, step)), the script's seed 3160363883, and pick i is position ⌊start + i · step⌋. This is not § "The draw"'s
seeded permutation of an exported frame, but it is an equal-probability design: every company in F had the
same chance, **π = 600 / 59,547**, so a weight of 99.245 each. Sorting by registration year before stepping
stratifies it implicitly by year.

**Checked by the pipeline's side (2026-10-19), reading only KRS, position, date and status:** every one of the 600
positions equals ⌊start + i · step⌋, and its page, offset and year agree with it; every registration date lies in
its slice's year; the CSV and the log hold the same 600 KRS numbers, well-formed and distinct; every year's
picks are within one of N_year / step (2001: 10 of 9.8 … 2021: 87 of 87.1).

**Composition (Rejestr.io's flags, not the pipeline's labels):** 4 `W upadłości`, 19 `W likwidacji` (3.8%), 16
`W zawieszeniu` (not distress, § "Lists received"; the CSV leaves their status blank), and 86 deregistered (14%),
so the frame keeps dead companies (§ "What the weights need", item 2). 73 were registered in 2022, after §
"The frame"'s 2022-01-01 cut: they stay in, and the pipeline's filed-years rule decides whether they count. The
group shares 6 companies with list v2's A, 3 with list v2's B, none with the superseded 13:04 file.

**What it settles, of § "What the weights need":** #1 (N_F), #3 (n_A = 600), #4 (how the rows were chosen) and
#5 (date and filters) for group A. Item 3 there, the serious one, no longer applies to A.

**Still open:**

1. ~~**Group B is redrawn**~~ *Done, 2026-10-19 (§ "List v3's distress group").* By the same script with the same filters plus one status filter, once for `W upadłości`
   and once for `W likwidacji`, each with its exact N_Dc, step, random start and log. List v2's B cannot be
   weighted (its filters and how its rows were chosen are unrecorded). From A's flags, N_D is of the order of
   2,300, of which about 400 bankrupt, so 130 bankrupt companies would be about a third of all of them.
2. **Whether `ind=F` is the primary PKD,** and which PKD version. If it matches any listed PKD, F is wider than §
   "The frame"'s segment; still a valid frame, recorded as such.
3. **Scripted access to Rejestr.io** (618 requests, pauses of 0.9–1.8 s). The terms confirmed on 2026-10-07 were
   for use by hand. Whether they allow the script is the owner's to confirm, as RDF's rate was (ADR 0007).
4. **A full frame export** (KRS, registration date, status, deregistered flag; no names), about 600 requests at 100
   a page, would let pipeline code check the log against F and extend the draw later. Recommended while the
   trial lasts; without it, the log is the record.

The new groups get `discovery_source` versions `rejestr_io_v3_random` and `rejestr_io_v3_distress` (plan 0019
decision 3). With the two-group formula of § "Weights" applied per status, π for a company of D_c is
600 / 59,547 + (1 − 600 / 59,547) · n_Bc / (N_Dc − d_Ac). Plan 0019 builds it (its decision 10 for the systematic
design, accepted by the owner 2026-10-19).

### List v3's distress group, 2026-10-19

The owner redrew group B with a second script (the same step principle, one run per status) and kept one run log
for both runs beside its CSV. Both files stay outside the repository (`E:\Rejestr.io company
pull\most_recent_distressed_run\`); the log names every drawn company, so only counts, settings and hashes are
copied here.

| File | Rows | SHA-256 |
|---|---|---|
| `rejestr_distressed_2026-10-19_1721.csv` (`krs, company_name, status`, in a seeded random order) | 200 | `2c81fcaa47dab56500073fa1a18980e22d06b232d32ae9f59d6124ad5523b126` |
| `rejestr_distressed_log_2026-10-19_1721.txt` (filters, the filter funnel, both runs, every pick) | — | `fd65884e6443ecf1b498ccee42bf54d4f300823ad384020d6d6a5e9b0590727e` |

**The runs** (2026-10-19, 17:21–17:24 Warsaw time, seed 1381911424): group A's filters plus one status filter
each, the same year slices and sort.

| Status (filter) | N_Dc | n_Bc | Step | Start | π for a company of D_c alone |
|---|---|---|---|---|---|
| `W upadłości` (`bpcy`) | 209 | 130 | 1.607692 | 0.558806 | 1 − (1 − 600/59,547)(1 − 130/209) ≈ 0.626 |
| `W likwidacji` (`liq`) | 1,998 | 70 | 28.542857 | 2.395162 | 1 − (1 − 600/59,547)(1 − 70/1,998) ≈ 0.045 |

- **Independent runs, so the formula is not the addendum's.** B was not drawn from D minus A: each run stepped
  over the whole of D_c. A company carrying the statuses C has π = 1 − (1 − 600/59,547) · Π (1 − n_Bc/N_Dc) over
  c in C, as plan 0019 step B computes it. This replaces the dependent formula of § "List v3's random group"'s
  last paragraph. 4 companies of F carry both statuses; 3 of them were drawn (in the `bpcy` run), π ≈ 0.639.
- **The frame drifted by 2.** The distress script's funnel counts 59,549 with A's filters, three hours after A's
  59,547. Each B run's N_Dc is counted against the frame as it then stood; the spec records 59,549 on both runs
  (`frame_size`), and N_F stays 59,547.
- **D is Rejestr.io's flag on 2026-10-19, still registered.** None of the 200 is deregistered, so a bankruptcy
  closed and struck off is not in D; and the flag cannot be filtered to entries on or after 2019-01-01 (§ "The
  distress stratum"). Of the 86 bankrupt picks already in `legal_events` (from list v2's B), about 80% opened in
  2019 or later, the rest in 2010–2018, before v1's years: those stay in their group and drop out by the
  pipeline's own filters, counted.

**Checked by the pipeline's side (2026-10-20), against the draw spec
`config/segments/construction_sme_v1_draw_v1.yaml`:** both runs pass `draw_systematic` (every pick at ⌊start +
i · step⌋ in its slice's year, no warning, no shifted pick, the CSV and the log hold the same 200 KRS numbers).
With A: 800 rows, 793 companies, 7 in both groups (A's 4 bankrupt companies and 3 of its 19 in liquidation), none
quarantined. 86 of B are list v2's B, so their downloads are reused.

**Still open:** items 2–4 of § "List v3's random group" (`ind=F`'s meaning, scripted access, a frame export),
running `universe_draw`, and the owner's acceptance of the addendum.
