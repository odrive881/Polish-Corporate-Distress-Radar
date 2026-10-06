# 0014 — A1 discovery: a hand-filtered Rejestr.io list

- **Status:** accepted (2026-10-06, owner). The list's composition is not yet measured, and it is not yet a
  representative sample (§ Consequences).
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
- **To make it representative** (the owner's stated next step): record the filters used, describe the frame
  they draw from (how many companies match the segment's definition in all), and draw the next list from that
  frame by a written rule, stratified if wanted, with the strata and their weights recorded. A list built that
  way gets a new `discovery_source` version, and the backtest reports which list each result rests on.
- **Rejestr.io's terms of use are not yet confirmed** for this use (AGENT_SPEC §11.3). The owner checks them;
  until then nothing from Rejestr.io beyond the KRS numbers is stored, and nothing from it is published.
- **Follow-up work** goes in plan 0014:
  - a loader for the list into `universe_candidates`, beside the seed's YAML loader;
  - A2 over its KRS numbers;
  - the composition census above, after A2 and the KRS extracts.
