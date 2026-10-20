# 0019 — A seeded draw from a recorded frame, weighted results, and the provisional champion

**Stage:** A1 (AGENT_SPEC §6A: universe discovery, the draw and each company's probability of being drawn) and
I (§6I: the backtest, the champion rule and the registry), with what the report, the API and the explorer say
about a promotion. It builds the parts of ADR 0014's addendum (2026-10-07, proposed) that need code, and makes
plan 0014's decision 7 (2026-10-18) visible wherever a champion is shown.
- **Domain rules:** only KRS numbers and a status enter the pipeline from a Rejestr.io file. The file stays
  outside the repository and its other columns are never read (ADR 0014, invariant 6). A draw is reproducible
  from the frame file, the seed and the spec alone (invariant 5). A weight is a fact about how a company was
  drawn, never about its outcome, so no weight reads a label. The backtest stays out-of-time only, with the Brier
  score beside AUC (CLAUDE.md). A result over an outcome-selected group is never presented as the segment's
  unless it is weighted (ADR 0014 addendum, "Use").
- **Inputs:** a frame file exported from Rejestr.io (none exists yet; the code is built and tested on synthetic
  frames); `universe_candidates` and `entity_master`; the backtest's population (`models/population.py`), config
  (`models/splits.py`), metrics (`models/evaluation.py`, which already take a weight), models (`classical.py`,
  `gbm.py`, `survival.py`, which already take a weight, plan 0015 decision 9), the champion rule
  (`models/champion.py`), the registry (`models/registry.py`) and serving (`models/serving.py`).
- **ADRs:** 0014 (the frame, the draw, the weights, and § "What the weights need", 2026-10-18), 0006 (plain
  idempotent DDL), 0009 (what may be stored from a list).

**Order:** after plan 0018. It does not wait for the frame: steps B–F are built and tested on synthetic frames,
and step G runs the draw once the owner has exported one. Step B goes first because the Rejestr.io trial has a
deadline, and the frame must be exported before it ends (`docs/data_inventory.md`).

## Status: active (2026-10-18): written on the owner's request (items 1–3 of "what can be built in the meantime"); owner decisions 0–9 accepted as recommended (2026-10-18); 2026-10-19: list v3's random group drawn by the owner's script and checked (ADR 0014, § "List v3's random group"), decision 10 accepted by the owner (2026-10-19); steps B–D built (2026-10-19); 2026-10-20: the distress group redrawn, the draw spec written (step G); 2026-10-20: `universe_draw` run (800 rows, 793 companies) and ADR 0014's addendum accepted; open: `entity_master` on the drawn lists, the weighted backtest (step G), the docs sweep (step H), steps E–F; 2026-10-20: the trial ends about 2026-11-02, `ind=F` is the primary PKD and scripted access is allowed (owner)

### Outstanding (2026-10-19): the owner's

Step A's three questions, as they stand after list v3's random group (ADR 0014, § "List v3's random group"):

1. ~~**The export's header line.**~~ *Overtaken, 2026-10-19:* the owner's script drew group A itself, from a
   frame of N_F = 59,547 it counted but did not export, and logged every pick. A full frame export (KRS,
   registration date, status, deregistered flag) is still recommended while the trial lasts: it lets step B's
   code check the log against F. Without it, the log is the record (decision 10).
2. ~~**How group B's rows were chosen.**~~ *Overtaken:* list v2's B is not kept (decision 1's alternative). B is
   redrawn by the same script, same filters plus `W upadłości`, then `W likwidacji`, each logged with N_Dc, step
   and start.
3. ~~**When the Rejestr.io trial ends.**~~ *Owner, 2026-10-20:* 13 days left, so about 2026-11-02. B is
   redrawn; the frame export, if wanted, must happen before then.

And two new ones, from the log:

4. ~~**Whether Rejestr.io's `ind=F` is the primary PKD**~~ *Owner, 2026-10-20:* yes, and pre-2004 codes are
   mapped to it too. The PKD version is not stated (ADR 0014, § "List v3's random group").
5. ~~**Whether Rejestr.io's terms allow the script**~~ *Owner, 2026-10-20:* yes, that is what Rejestr.io is for.

Decision 10 is accepted (2026-10-19), so step B can be built for both designs now.

Steps B–D are built (2026-10-19, "Built" under each); steps E–F are not started.

## Why

List v2's two files (ADR 0014, § "Lists received, 2026-10-13") were not drawn by the addendum's rule: no frame,
no seed, and group A is four unbroken runs of KRS numbers. So no company has a known probability of being
drawn, no result can be weighted back to the segment, and plan 0014's decision 7 makes every promotion
provisional until one can. Three things are missing, and all three can be built before the frame exists:

1. **The draw itself, as code.** The addendum says the draw is done "by pipeline code from the stored frame file,
   so anyone can repeat it". That code does not exist. Once the owner exports a frame, the draw should take
   minutes, not a plan.
2. **Weights through the backtest.** Every metric and model function accepts a weight, but nothing passes one:
   no weight column, no config section, no weighted bootstrap, no weighted champion rule, no weighted report.
   Without these, decision 7's weighted re-run would be a build, not a config change.
3. **"Provisional" where a champion is shown.** Decision 7 says a promotion on an unweighted run is provisional
   and is called so. Today nothing in the registry, the report, the API or the explorer can say it, so it
   depends on someone remembering to write it.

## What there is to build on (2026-10-18)

- `load_krs_list` (`acquisition/universe_discovery.py`) reads a CSV's KRS column into `UniverseCandidate` rows,
  and the `universe_candidates` asset records the list's SHA-256 in its metadata. `universe_candidates` holds
  `krs`, `discovery_source`, `discovered_at`, `ingestion_run_id` and two hints, keyed on `(krs, discovery_source)`.
- `evaluation.py`: `brier`, `log_loss`, `base_rate`, `auc` and `reliability` take an optional weight.
  `top_decile_precision` and `bootstrap_intervals` do not.
- `classical.py`, `gbm.py`, `survival.py`: the fits take an optional per-row weight.
- `champion.py`: `ChampionVerdict` (incumbent, promoted, verdicts, comparisons), the paired entity bootstrap,
  and the floor of `min_promotion_events` distinct events. `ChampionConfig` has no weight.
- `registry.py` sets the alias `incumbent`, and `champion` on a promoting verdict. `serving.py` serves
  `champion`, else `incumbent`. `methodology.py` prints "promoted" or "nothing promoted".
- The backtest report's banner already says "Purposive lists, not evidence" whenever the population holds a
  list (`report.py`).

## Owner decisions (accepted by the owner, 2026-10-18, as recommended)

0. **Where the draw spec lives.** A draw is statutory-like config: data, versioned, never constants (invariant
   7's spirit). *Recommended:* `config/segments/construction_sme_v1_draw_v<n>.yaml`, beside the segment spec it
   draws from, so no new directory is needed. It holds the frame file's SHA-256, its date, every filter setting
   as Rejestr.io showed it, N_F, the statuses that define D and N_D per status, the seed, n_A, n_B per status,
   and the list version it produces. *Alternative:* a new `config/universe/` directory (needs a
   DIRECTORY_STRUCTURE entry).
1. **What gets redrawn.** *Recommended:* group A is always redrawn from the frame, since its four contiguous runs
   cannot be weighted (ADR 0014, § "What the weights need", item 3). Group B is kept only if the owner confirms
   that its 130 and 70 were chosen at random from each status search's results, *and* that those searches used
   A's base filters. Then B's π is n_Bc / N_Dc per status, and the formula is adjusted for two independent draws.
   Otherwise B is redrawn too. Companies already downloaded are reused wherever the new draw picks them.
   *Alternative:* redraw both always (the addendum's rule exactly, more downloads).
2. **The frame file.** *Recommended:* one CSV export of F with a KRS column and a status column, kept in
   `.cache/universe_lists/` like the other lists. D is read from its status column, so D ⊂ F holds by
   construction. If Rejestr.io cannot export a status, then one more export per status, each refused unless
   every KRS in it is also in F. Only the KRS and status columns are read.
3. **List versions.** *Recommended:* the new draw is `rejestr_io_v3_random` and `rejestr_io_v3_distress`. The
   existing `rejestr_io_v2_distress` rows stay, purposive like v1 (the addendum's "List v1 and the seed" rule
   extended to them), unless decision 1 keeps B, in which case B's rows gain a draw record and no new version.
4. **Where π is stored.** *Recommended:* a new Postgres table `universe_draws` (`krs`, `list_version`, `group`
   (`A` or `B`), `stratum` (the status, or `none`), `inclusion_probability` (`numeric`, exact), `draw_spec_sha256`,
   `frame_sha256`, `ingestion_run_id`), keyed on `(krs, list_version)`. `universe_candidates` is left unchanged.
   *Alternative:* columns on `universe_candidates`, which would be null for every non-drawn source.
5. **Who is in a weighted result.** *Recommended:* only drawn entities: the weighted backtest's population is
   the members of one draw whose acquisition is complete, trained and evaluated with weights 1/π. The seed,
   list v1 and v2's purposive rows never enter a weighted result (the addendum). The unweighted lists backtest
   keeps running beside it, unchanged. *Alternative:* everything in, with weight 1 for the non-drawn, which
   mixes a weighted sample with purposive ones and speaks for neither.
6. **The bootstrap under a draw.** *Recommended:* resample entities **within each draw stratum** (A, then B per
   status), each stratum keeping its size, and compute weighted metrics on each replicate. That respects how the
   sample was drawn. *Alternative:* a plain entity bootstrap with weights, which understates the variance from
   B's small strata.
7. **What the event floor counts in a weighted run.** *Recommended:* the actual distinct events, unweighted. The
   floor measures how much evidence the comparison rests on, and a weight does not add evidence. *Alternative:*
   a weighted count, which would make a weighted floor nearly impossible to reach.
8. **What "provisional" means, in code.** *Recommended:* a verdict is **provisional** whenever the champion rule
   ran unweighted, and **final** only from a weighted run. This needs no list of outcome-selected sources: every
   list so far is purposive, so every unweighted verdict is the lists'. A later weighted verdict supersedes: if
   it promotes the same model, the `champion` alias stays and becomes final; if it promotes another, the alias
   moves; if it promotes nothing, the alias is removed and `incumbent` serves (decision 7's "the weighted one
   stands").
9. **Weighted top-decile precision.** *Recommended:* built, as the weighted share of events among the rows whose
   cumulative weight makes up the top 10%. Without it the weighted report has a blank column.
10. **A systematic draw done outside the pipeline** (2026-10-19; list v3's group A, ADR 0014). *Accepted by the
    owner, 2026-10-19, as recommended.* *Recommended:* the draw spec (decision 0) takes a `design`: `permutation` (step B's draw from an exported
    frame, as written) or `systematic`. A `systematic` spec records N_F per registration-year slice, the sort,
    n, the step, the start, the script's seed and the run log's SHA-256. Step B then reads the log rather than
    drawing: it refuses the log if its hash differs, if any pick is not at ⌊start + i · step⌋, or if a slice's
    count or the picks' years disagree with the spec, and π = n / N_F for every pick (per status in B, with the
    two-group formula), in `Fraction`. Only the log's KRS, position, year and status columns are read; its names
    never leave the reader. If a frame export arrives, the same checks also run against it. Decision 6's strata
    for A become registration-year bands, which the systematic step stratified implicitly. *Alternative:* redraw
    A by step B's permutation from an exported frame, which costs the owner a full export and the downloads
    of a new 600.

## Steps

### A. Verify before building (no code)

*2026-10-19:* overtaken in part by list v3's random group; § "Outstanding" says what remains.

- From the owner: Rejestr.io's export format (column names, delimiter, whether a status is included), using the
  header line only, without company names. That decides decision 2's shape.
- From the owner, for decision 1: how B's rows were chosen from each status search.
- Confirm against `docs/data_inventory.md` when the Rejestr.io trial ends, and write the date into this plan.
  Step B must be finished before then.

### B. The draw (decisions 0–3)

- `acquisition/sampling.py` (A1), pure functions with no I/O beyond reading the frame file:
  - `load_draw_spec(path)`: a Pydantic model of the spec (decision 0), with `extra="forbid"`.
  - `read_frame(path, spec)`: reads the KRS and status columns only. It refuses the file if its SHA-256 differs
    from the spec's, if any KRS is malformed (quarantined by line like `load_krs_list`, never repaired), if the
    row count is not N_F, or if a status count is not N_Dc. If D comes as separate exports (decision 2), it
    refuses any export with a KRS outside F.
  - `draw(frame, spec)`: sorts F's KRS numbers, permutes them with `numpy.random.default_rng(seed)`, takes A as
    the first n_A, and B per status as the first n_Bc members of D_c not in A, in permutation order. It returns
    one row per drawn company with its group, stratum and π from the addendum's formula, applied per status
    (ADR 0014, § "What the weights need"). The formula is done in `fractions.Fraction`, so it is exact before it
    is stored as `numeric`.
  - Extending a draw (the addendum's "Extending later") is the same call with a larger n: the first n_A of a
    larger n_A are the same companies. Tested.
- No company name, or any column besides KRS and status, is held in memory past the reader.

**Built (2026-10-19).** `acquisition/sampling.py`, tested in `tests/acquisition/test_sampling.py` on synthetic
frames and logs only. As written above, with these differences:

- **The order is not numpy's.** A permutation draw sorts F by the SHA-256 of `"<seed>:<krs>"`. NumPy does not
  promise that `default_rng`'s stream stays the same across versions; a hash does. It is just as random, and a
  larger n still extends a draw.
- **Decision 10's design.** `load_draw_spec` reads either design (`design: permutation | systematic`).
  `read_run_log` keeps only a log's settings and each pick's KRS, position, year, registration year and status.
  `draw_systematic` refuses a log unless:
  - its hash is the spec's;
  - its result is OK, with no warnings;
  - its filters equal the frame's, with the status filter unset for A and the stratum's for B;
  - its sort, year slices, total, n, seed, start and step agree with the spec;
  - every pick lies at ⌊start + i · step⌋, in its slice's year;
  - every B pick carries its stratum's status.

  π is n_A / N_F, and 1 − (1 − π_A)(1 − π_Bc) for a member of D_c, since A and B are independent runs. A
  company in both runs has a row in each, with the same π. The spec allows A alone (π_Bc is then 0), so list
  v3's A can be stored before B is redrawn.
- **Checked against the real log, locally (not committed):** list v3's A log, with a spec written from it,
  passes every check. That gives 600 picks, each with π = 600 / 59,547: 577 `none`, 19 `w_likwidacji`, 4
  `w_upadlosci`. The picks equal the CSV's KRS numbers.
- **The distress log (2026-10-20).** The owner's distress script writes one log for both statuses: a `SAMPLING -
  <status> (op=<filter>)` section and a raw filter line per run, a filter funnel, and one PICKS table whose
  `sampled_as` column names the run. `read_run_log(path, status_filter)` reads such a log for one run; both
  distress keys of `logs` then name the same file. A pick may carry two statuses (`W likwidacji; W upadłości`):
  a B pick needs its run's among them, and π counts every distress run whose status the company carries in any
  log, 1 − (1 − π_A) · Π (1 − π_Bc). A log whose funnel prints a frame count other than N_F is refused unless
  the run records that count as `frame_size` (the frame drifts between runs taken hours apart).
- **Not built:** D as separate exports (decision 2's fallback). It waits on the export's header, if a frame is
  ever exported. The real spec file is step G's.

### C. Storing the draw (decisions 3–4)

- `universe_draws` DDL in `acquisition/manifest.py`, plain and idempotent (ADR 0006).
- The asset `universe_draw` (`dagster_defs/assets/acquisition.py`, group `acquisition`), configured with the spec
  path and the frame path. It writes `universe_draws` and the drawn companies' `universe_candidates` rows under
  the spec's list version, `ON CONFLICT DO NOTHING`. Its metadata holds the spec and frame hashes, N_F, n per
  group and stratum, and how many drawn companies are already in `entity_master` (their downloads are reused).
  `entity_master` then runs as for any list.
- `rdf_queue` (plan 0018) puts a draw's never-searched companies first in addendum order: B, then A in
  permutation order, so whatever part of A is downloaded at any moment is itself a random sample.

**Built (2026-10-19).** As written above, with these differences:

- **π is stored as an exact fraction.** `pi_numerator` and `pi_denominator` hold it, with `inclusion_probability`
  a generated `numeric` beside them. A `numeric` alone cannot hold 600 / 59,547 exactly: it has no finite decimal.
- **The key adds the spec's hash:** `(krs, list_version, draw_spec_sha256)`. A spec that adds a group changes π
  for companies already drawn (a D member of A, once B exists). So each spec keeps its own rows, and a weighted
  backtest pins one (step D).
- **The columns:**
  - `draw_group`, since `group` is a reserved word.
  - `source_sha256`, the frame file's or the run log's hash, in place of `frame_sha256`, since a systematic draw
    has no frame file.
  - `registration_year`, the implicit strata of a systematic draw (decision 6).
  - `draw_order`, the download order.
- **The download order of a systematic run** is the same seeded hash order as a permutation's, over the run's
  picks. It is not the CSV's shuffle, which nothing checks. The queue (`rdf_queue.py`) puts the never-searched
  drawn companies first: B, then A, each in draw order, then everyone else. Other tiers are unchanged.
- `universe_draw` writes nothing when a file disagrees with its spec. Its metadata holds the spec's and sources'
  hashes, N_F, the rows per list version and stratum, and how many drawn companies `entity_master` already
  holds. `entity_master` lists it beside `universe_candidates` as an upstream.
- **Tested:** the manifest (π exact and reduced, reinserts no-ops, one row per spec), the queue's order, and each
  group's download order. Not materialized on real data: that waits on step G's spec.

### D. Weights through the backtest (decisions 5, 6, 9)

- `BacktestConfig` gains an optional `weights` section: `list_version`, `draw_spec_sha256` (pinned like the label
  set) and `bootstrap: stratified`. Without it, every config computes exactly as before (`backtest_v1`–`v8`
  reproduce byte for byte, tested).
- `population.py`: with `weights`, the population is the draw's members whose acquisition is complete. The
  population hash covers them, and every other entity is reported as `not_in_draw`.
- `dataset.py` attaches each row's weight (1/π of its entity), read from `universe_draws` by `list_version`,
  never from a label.
- The fits pass the weight (already supported). The Platt calibration on the inner validation year is fitted with
  it. Optuna's objective (plan 0015) uses the weighted Brier score.
- `evaluation.py`: `bootstrap_intervals` takes the weight column and a stratum column (decision 6), and
  `top_decile_precision` gets its weighted form (decision 9).
- The report: a weighted run prints three blocks per horizon. They are **weighted, A and B** (the headline);
  **A alone** (self-weighting, the addendum's check); and **unweighted**, marked as the sample's, not the
  segment's. The banner says which draw and spec hash it rests on.
- MLflow: `draw_spec_sha256` is logged as a parameter beside the four required IDs. The population hash already
  covers the entities.

**Built (2026-10-19).** As written above, with these differences:

- **The config section** is `weights: {draw_spec_sha256, random, distress, bootstrap: stratified}`. It names
  both groups' list versions, since a draw has two, and it requires a `population` rule.
- **`models/weights.py`** reads `universe_draws` under the pinned spec. Each drawn entity gets the weight
  1/π and a stratum:
  - `B:<status>` for a distress-group company;
  - `A:<registration year>` for one from a systematic random group (decision 10's bands are single years);
  - `A` for one from a permutation.

  A company in both groups is resampled with B. Two different π for one company refuse the draw.
- **The population.** `population.classify` takes the draw and leaves every other entity out first, as
  `not_in_draw`. That reason is listed only in a weighted run, so unweighted reports keep their reason rows.
- **How the weight travels.** It is a `draw_weight` column on the label rows, so the modelling dataset and the
  survival records carry it. It is never an input: it has no `*__known_from` companion, and LightGBM's
  `NOT_INPUTS` names it.
  - **Weighted fits:** the regression, the classical models' mappings, LightGBM (Optuna's fits and its
    inner-year Brier score, the Platt map, the fold's booster) and both survival models.
  - **Unweighted:** the survival models' time-dependent metrics (concordance, integrated Brier).
  - **The champion rule** still reads unweighted rows; step E changes that.
- **Top-decile precision, weighted,** counts the share of the boundary row's weight that fits in the top tenth.
  It equals the unweighted form only where a tenth of the total is a whole number of rows. The unweighted form
  rounds up to a whole row, so equal weights can differ from it by that rounding. The test of equal weights
  leaves it out for that reason.
- **The three blocks.** A weighted run's `cells` are the headline. `basis_cells` holds every pooled cell
  twice more: `a_alone` (the random group's rows, unweighted) and `unweighted` (all rows). The report prints them
  per horizon as "Pooled, by basis", under a banner naming the draw spec. Its reliability table is weighted.
- **Checked:** `make check`; `tests/models/test_weighted_backtest.py`. A synthetic unweighted backtest's report, cells and
  reliability table are byte-identical to the code before this step (compared against the previous commit's code
  in a worktree). Not run on real data: that waits on step G's draw.

### E. The weighted champion rule (decision 7)

- `ChampionConfig` gains `weighted: bool` (default false), allowed only when the backtest has `weights`.
- With it, the paired Brier difference and both AUCs are weighted. The paired bootstrap resamples within strata
  (decision 6). The floor counts distinct events, unweighted (decision 7).

### F. Provisional and final, everywhere a champion is shown (decision 8)

- `ChampionVerdict` gains `basis` (`unweighted` or `weighted`) and the property `provisional`
  (`basis == "unweighted"`).
- `registry.py`: every version it registers carries the tags `champion_basis` and `provisional`. A weighted
  verdict applies decision 8's supersession: keep, move, or remove the `champion` alias. It never touches
  `incumbent`.
- `serving.py`: the served version reports its basis, so every consumer reads it from one place.
- The backtest report's "Champion rule" section prints, on an unweighted promotion: "Provisional: promoted on an
  unweighted run over purposive lists (plan 0014 decision 7); a weighted run decides."
- The API's data-version metadata and the explorer's model panel say "provisional champion" or "champion".
  `methodology.py` (and so the report and the public site) states the basis beside "promoted".
- The champion rule itself, its floor and its margin are unchanged (plan 0015 decision 7).

### G. The real draw (waits on the owner's export)

*2026-10-19:* group A of list v3 is drawn (ADR 0014, § "List v3's random group"); under decision 10 (accepted), step G
writes its `systematic` spec from the log and waits only on B's redraw.

*2026-10-20:* B is redrawn (ADR 0014, § "List v3's distress group") and the spec is written,
`config/segments/construction_sme_v1_draw_v1.yaml`: A and both B runs, checked against the real logs locally
(800 rows, 793 companies, none quarantined). *2026-10-20:* `universe_draw` run (run `384de5ba`): the same 800 rows
and 793 companies, none quarantined (counts in ADR 0014, § "List v3's distress group"); the addendum accepted by
the owner. `entity_master` is not run yet.

- Write the spec from the owner's numbers (ADR 0014, § "What the weights need": the five numbers), commit it,
  run `universe_draw` and then `entity_master`, and record the counts here and in ADR 0014's addendum. The
  addendum's status then goes to the owner to accept.
- Hand the new queue to the owner's script (step C). The first weighted backtest is a new `backtest_v<n>`
  once the draw's downloads are in. It stays a decision.

### H. Docs sweep and close-out

- `DIRECTORY_STRUCTURE.md`: `acquisition/sampling.py`, the draw spec, the new tests. AGENT_SPEC §5:
  `universe_draws`. `docs/data_inventory.md`: the frame file's row. README: the draw's commands, and the
  provisional or final champion in the status line. Plan 0014's decision 7 and ADR 0014's addendum: point here.
  Then `make docs-check`, and the by-hand read against this plan's results.

## Tests

- **Draw:** byte-identical output for the same frame, spec and seed. A changed seed gives a different draw. A
  larger n extends a draw without changing its first members. Members of D drawn into A stay in A. B never
  overlaps A. π matches a hand computation on a small frame, exactly (`Fraction`). A frame whose hash, row count
  or status count disagrees with the spec is refused. A D export holding a KRS outside F is refused. A malformed
  KRS is quarantined, never padded.
- **No personal data:** reading a frame with a company-name column keeps nothing but KRS and status (asserted on
  the rows and on the quarantine records).
- **Weights are no leak:** a weight is constant per entity and read from `universe_draws` alone. A test changes
  every label and checks that the weights are unchanged.
- **Weighted metrics:** an integer weight equals repeating the row, for every metric including the new
  top-decile precision. The stratified bootstrap keeps each stratum's size.
- **Old configs:** `backtest_v8` (no `weights`) produces the same report and verdict as before, byte for byte.
- **Supersession:** on synthetic registries, a weighted verdict keeps, moves or removes `champion` as decision
  8 says, and never removes `incumbent`.
- **Provisional everywhere:** an unweighted promotion shows "provisional" in the report, the registry tags, the
  API metadata, the explorer panel and the methodology figure. A weighted one shows "final".

## Definition of done

- [x] Owner decisions 0–9 accepted (2026-10-18, as recommended).
- [x] Decision 10 (a systematic draw done outside the pipeline) accepted (2026-10-19).
- [ ] Step A's answers recorded (export format, how B was chosen, the trial's end date).
- [ ] Steps B–F built, with their tests in `make check`.
- [x] Step G: the real draw run on the owner's logs, its counts recorded here and in ADR 0014 (2026-10-20);
  its weighted backtest waits on the downloads.
- [ ] Docs swept (step H).

## Risks

- **The trial ends before the frame is exported.** Then no draw is possible from Rejestr.io, and only the
  addendum's fallback is left (weighting within registration periods, ADR 0014, § "What the weights need").
  Step A asks for the date first.
- **Rejestr.io's counts move.** The frame is a snapshot. The spec records its date, and a re-export with other
  counts is a new spec version, never an edit.
- **The redraw costs downloads.** A new A of 600 overlaps the old one only by chance, so most of it is new work
  for the script at 3 documents a minute. B first keeps the events coming early (the addendum's order).
- **Weighted results on few events are noisy.** A's events are few (a random 600 at a 1–2% yearly rate), and
  B's weights are small, so intervals will be wide. The floor (decision 7) keeps the rule from promoting on them.
- **A weighted verdict can demote a provisional champion.** That is decision 8 working as intended: scoring
  then serves `incumbent`, and alerts on a model-version change are already not jumps (plan 0018).

## Out of scope

- Exporting the frame, and any Rejestr.io use beyond the owner's searches (ADR 0014).
- Accepting ADR 0014's addendum: the owner's, after step G.
- Sector, regional and realised-vs-predicted pages (plan 0016 decision 0). They become possible after a weighted
  backtest, but they are a plan of their own.
- Running `backtest_v9` (plan 0014, § "Events"): it is unweighted and does not wait for this plan.
- Any change to the champion rule's floor, margin or runs.
