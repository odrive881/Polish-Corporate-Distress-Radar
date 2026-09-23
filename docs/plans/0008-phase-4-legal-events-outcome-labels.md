# 0008 — Phase 4: legal events and outcome labels

**Stage:** Phase 4 (AGENT_SPEC.md §10: "Legal events, outcome labels, censoring, regime flags").
- **Spec stages:** A4 (§6A); the legal-notice side of C; stage F's first label model (§6F, PROJECT_OVERVIEW stage 8).
- **Domain rules:** the outcome taxonomy (§4.6) and bitemporality (§4.7).
- **Outputs:** the canonical datasets `legal_events` and `outcome_labels` (§5).

**Order:** after plan 0007, which is complete. Phase 5 (the feature store) needs labels to train against and
`legal_events` to date them, so nothing downstream starts before this lands. Plan 0006 (the C3 PDF tier) was
deferred to "no earlier than Phase 4, when MSiG forces a PDF text layer". Whether MSiG forces it is now a question
this plan answers in step A, not an assumption (decision 2).

## Status: complete (2026-09-23); review fixes applied the same day

**Review fixes, 2026-09-23.** A review of the whole phase found six problems; all are fixed and each has a test that
fails without its fix:
1. **A fresh clone could not plan.** DuckDB refuses a view over Parquet that does not exist yet, so the
   `ext.legal_events` view stopped every SQLMesh plan, the DQ group's included, until `legal_events` had run once.
   `transform/config.py` now builds an empty view with the declared columns for a dataset not yet written.
   Tested for all four Parquet views on an empty warehouse.
2. **`raw_redactions` pointed at the old MSiG records** after the vocabulary change. The table was keyed by the
   received hash alone, so re-reducing the same bytes lost its row. It is now keyed by (received hash, redaction
   version); MSiG records carry their extraction key as the version; the 49 missing rows were recovered from
   `msig_notices` (ADR 0009 addendum).
3. **One notice, one event, lost a dismissal.** 0000386777's 2020 sanacja notice also dismissed the pending
   bankruptcy petition, and the company stayed excluded after its sanacja ended. The notice-kind rules now have
   `additional` rules, and one notice can record several events. The first is
   `bankruptcy_petition_dismissed_by_restructuring`, which maps to a new closing event type,
   `bankruptcy_petition_dismissed`. 0000386777 is labelled again from 2025-09-30. The label set moved to
   `a1ac9fb07f79…` (4,694 rows); `a5da757f8341…` stays frozen.
4. **Invariant 2 did not name MSiG's reduced records.** It now does, in CLAUDE.md and AGENT_SPEC §2.
5. **The fixture gate was weaker than the redactor.** Its pattern let "NADZORCY SĄDOWEGO" and a name through. It now
   applies the production rule (`redaction.may_name_a_person`) to every long string a fixture keeps, and a test
   proves it catches that case.
6. **The probe notebook still carried the old redactor.** Its fixture cell now writes through
   `redact_registry_extract`.

Checked afterwards: 89 `legal_events` rows in 70 groups, all 15 checks pass, and nothing is quarantined.


**Step I close-out, 2026-09-23.** Docs brought in line with what was built:
- **AGENT_SPEC:**
  - §4.6: the sources and the break, the `silent_exit` rule, censoring, the window and exclusions;
  - §5: `legal_events` and `outcome_labels` as built;
  - §6A: the A4 row, and fingerprinting where no cache applies;
  - §6C3: MSiG no longer routes to the PDF tier;
  - §10: phase 3's note;
  - §11.3: terms, recorded in ADR 0011.
- **CLAUDE.md:**
  - invariants 2 and 6, which name the registry redaction and MSiG's person-free records;
  - MSiG's glossary line;
  - a "Known moving targets" entry for the Phase 4 sources and their traps;
  - the PDF-tier entry's premise, struck.
- **`docs/data_inventory.md`:**
  - §3 statuses (KRS and MSiG built, KRZ blocked);
  - §6, the legal fixtures;
  - §7, access and the two new pacing settings (also in `.env.example`);
  - §8 gap 5.
- **Plan 0006:** its Phase 4 trigger did not fire.
- **Elsewhere:**
  - the glossary, `TECHNICAL_ARCHITECTURE.md` (A4) and `PROJECT_OVERVIEW.md` (stage 7);
  - `DIRECTORY_STRUCTURE.md` (`tests/fixtures/legal/`, and the modules and models of steps B–H);
  - the README (status, and how to run `legal_to_labels`).

ADR 0009's addendum and ADR 0011 were written as the steps ran.

**Carried to Phase 5 and the owner:**
- ~~the registry-lag risk for `alive` labels near the cutoff (step G)~~ resolved by plan 0009 (lag allowance);
- ~~petition-stage exclusions that never expire (step G)~~ resolved by plan 0009 (24-month expiry);
- KRZ access, and board, capital, office and PKD changes as registry-dynamics events (feature work);
- the substitute of plan 0006 (a missing year from the next filing's prior-year column).

### Step H close-out

- **One job:** `legal_to_labels`, the `legal` and `labels` groups: A4 fetches, then `legal_events`, then the label
  models and their audits, then the frozen set.
- **Checks the assets report about their own run:** `krs_extracts_quarantine`, `msig_notices_quarantine` and
  `legal_events_quarantine` count this run's quarantined records by reason, at severity warn, because quarantine
  is by design. They are yielded by the assets rather than read from `quarantine_events`, whose append-only rows
  would count old detections.
- **`seed_acceptance` on `legal_events`** (`parsing/legal_acceptance.py`): each hinted entity must have an event of
  the class its hint names, and every other entity must have no qualifying event. It publishes a table of
  verdicts, so a mismatch is a finding to investigate, not a bare failure.
- **Live, run `cde2a23f`:**
  - all 17 checks pass (4 from the assets, 13 SQLMesh audits);
  - every KRS and MSiG fetch found unchanged content;
  - the label set came out as `a5da757f8341…` again, "already frozen", still one `label_sets` row.

  Registry bytes to a frozen label set, reproducibly, in one run.

### Step G close-out

**2026-09-23.** The SQLMesh models `staging.legal_events_canonical` (view),
`staging.outcome_label_grid`, `marts.outcome_labels`, `marts.outcome_label_exclusions` and
`marts.legal_event_coverage`; five audits in `transform/audits/outcome_labels.sql`; four SQLMesh unit tests;
`freeze_label_set` and `label_sets` in `labels.py`; the `labels` Dagster group (`label_models` with 13 asset
checks, then `outcome_labels`). The label parameters reach the SQL as SQLMesh variables, read from
`config/labels/outcome_labels_v1.yaml` and the taxonomy in `transform/config.py`, and are never restated there. What
it decides, beyond the text below (flag any you disagree with):
- **A deregistration's label depends on what came before it** (decision 5, worked through):
  - with nothing before it, it is a `silent_exit`;
  - after a bankruptcy, restructuring or liquidation, it takes that class and proceeding (the §9.2 case
    "deregistration after bankruptcy is `bankruptcy`");
  - after a merger it is `merged_away`, and the row is **censored**: leaving by merger is not an outcome.
- **"In a proceeding" means** a petition-stage or opening event of a class, with no closing event for that class
  since. A petition-stage order with no recorded outcome therefore keeps the entity excluded until a closing
  event. That is right for the seed, but at scale a dismissed petition the sources never record would exclude an
  entity for good.
- **Windows end on a month-end:** `window_end = LAST_DAY(as_of_date + horizon)`. 29 Feb + 1 month would otherwise
  be 29 Mar, dropping two days.
- **`source_era` has a third value, `mixed`,** for a window that spans KRZ's launch (decision 4 had
  `pre_krz` | `krz`). Such a window is neither, and 458 seed rows are in it.
- **Censored rows have `outcome_class` null,** never `alive` (§4.6). An audit holds "class if and only if not
  censored".
- **`proceeding_id_note` is the "documented reason"** for a labelled event without a proceeding:
  - `procedure_has_no_case` for liquidation, a silent exit or the simplified restructuring;
  - `source_omits_signature` for 0000507997's undated declaration.
- **The registry now yields `registered`** (entry 1), and an entity's grid starts there. `legal_events` gained
  `ends` and `precludes_silent_exit` from the taxonomy, so the SQL needs no other input.
- **Event coverage is its own mart,** `marts.legal_event_coverage` (source × year, with `both` for events both
  sources describe), not an extension of `dq_mart_coverage`, whose grain is a filing's fiscal year.
- **A frozen set is written once** to `outcome_labels/label_set_hash=<hash>/` and never overwritten. The hash is of
  the sorted rows (as CSV), not the Parquet bytes, so a library upgrade cannot change a set's identity.
- **Live, 2026-09-23.** Runs `cec77ccb` and `7062dab8`:
  - all 13 checks pass;
  - label set `a5da757f8341…` has 4,670 rows (2,335 per horizon);
  - at 12 months, 64 rows are labelled bankruptcy, 28 liquidation and 24 restructuring, and 108 are censored;
  - 954 grid rows are excluded as in-proceeding and 234 as deregistered;
  - the rebuild reproduced the same hash, wrote nothing, and added no `label_sets` row.

  Every hinted entity turns positive exactly one horizon before its first event.
- **Open risk for the owner: registry lag near the cutoff.** *(Resolved 2026-09-23 by plan 0009: a 12-month lag allowance in label version 2.)* An `alive` row is one whose window ends before the
  cutoff with no event seen. An event decided in that window but entered after the cutoff is missed, and the lag
  reached 21 months in the seed (0000225506). Before 2021 MSiG usually publishes first; after it, without KRZ, the
  registry is the only source. One option: for the KRS-only era, bring the cutoff for `alive` back by a lag
  allowance. That is a label-config change (`outcome_labels_v2`), not built here.

### Step F close-out

**2026-09-23.** `parsing/legal_events.py` (one pure function per source, then `finalise`),
`parsing/msig_notice_kinds.py` with `config/mappings/msig_notice_kinds.yaml`, the `LEGAL_EVENTS` contract, the
`legal_events` asset (group `legal`), and the `ext.legal_events` view. What it changes here:
- **MSiG notices are typed by rules, not parsed as text.** The text is never stored (step E), so the rules read
  each reduced record's chapter code and terms. Of the seed's 49 notices, 17 are events, 32 are procedural (claims
  lists, creditors' meetings, routine company-law notices, deliberately skipped), and none is unclassified. The
  taxonomy maps the kinds, with two new event types: `restructuring_proceeding_ended` (closing) and
  `restructuring_arrangement_confirmed` (signal).
- **One proceeding, one `proceeding_id`.** A proceeding runs under several case files (`GU` and `GUp`, `GR` and
  `GRs`). The files an MSiG notice lists together are linked, and every row carries its linked set's first-published
  signature, keeping its own as `case_signature`. Signatures are cut to their four parts, which also absorbs a
  registry typo (`RZ1Z/GU/5/2022/20`).
- **Deduplication refines decision 3:**
  - openings group by outcome class, because the registry's generic "restructuring opened" and MSiG's "sanacja
    opened" are one opening;
  - an unsigned row joins the one signed group of its kind with the same event date;
  - an undated row joins the one group of its kind decided in the year before the row became known. That puts
    0000440028's undated registry liquidation with MSiG's resolution of 2023-07-21.
- **Columns beyond AGENT_SPEC §5 (amend in step I):**
  - added: `outcome_class`, `stage`, `statute`, `case_signature`, `removed_on` (the date an entry removed the
    record) and `event_year` (the partition);
  - `published_date` is named `known_from`, and there is one per row.
- **The taxonomy's guessed shapes were fixed against live extracts.** Curators are dated at
  `dzial5.kurator[].dataPowolania`; the merger section at `opisPolaczeniaPodzialuPrzeksztalcenia` (0000225506's
  2008 demerger).
- **The registry redactor was hardened (`krs-json-2`).** 0000225506's allowlisted `organWydajacy` quotes an order
  naming a temporary court supervisor, a company in that case. An allowlisted value naming a role with no
  legal-form marker after it is now reduced. All 17 stored extracts re-redact byte-identically, so nothing stored
  changed (ADR 0009 addendum).
- **Rejects are quarantined as stage `C4`,** with the log as the current answer, like A1–A4.
- **Live runs, 2026-09-23.** Runs `ded34011` and `15098f96`: 71 rows in 52 dedup groups, nothing quarantined, and
  byte-identical Parquet across runs. `make transform-plan` applies with the new view.
- **Seed acceptance, by data:** each of the 9 hinted entities has a qualifying event with a date and a source
  document, and 7 of them are confirmed by both sources. The 8 others have none (0000225354 has only arrears and
  curator signals). The asset check that makes this permanent is step H's.
  | KRS | Hint | First qualifying event |
  |---|---|---|
  | 0000070294 | bankrupt | bankruptcy, petition-stage order 2017-02-09 (MSiG) |
  | 0000181328 | bankrupt 2025-05-20 | liquidation 2010-02-01 (reversed); bankruptcy order 2025-02-04, declared 2025-05-20 |
  | 0000188883 | liquidating bankruptcy | bankruptcy declared 2014-01-21 |
  | 0000225506 | bankrupt | bankruptcy, petition-stage order 2022-03-25 |
  | 0000277937 | restructuring | simplified restructuring announced 2020-10-29 (MSiG only) |
  | 0000386777 | restructuring | sanacja petition order 2018-08-24 (MSiG only) |
  | 0000397658 | liquidation | liquidation opened 2021-10-01 |
  | 0000440028 | liquidation | liquidation resolution 2023-07-21 (MSiG's date) |
  | 0000507997 | bankrupt 2025-08-05 | bankruptcy, undated, entered 2025-08-13 (on or before) |

### Step E close-out

**2026-09-23.** `acquisition/msig_client.py`, `config/mappings/msig_vocabulary.yaml`, the
`msig_notices` manifest table and the `msig_notices` asset (group `legal`, resource `msig_api`, 15 requests a
minute). ADR 0011's second addendum has the per-entity findings. ADR 0009's addendum, item 3, records how notices
are stored. What it changes here:
- **A notice's text is never stored, so step F cannot "parse notices as text".** Each notice is reduced at fetch
  time to a person-free record: structured fields, a chapter code (`III/1`, `IX`, `I/2`), case signatures, the
  vocabulary terms it contains, and every date with the terms just before it. Step F types notices from that
  record. The typing rules are therefore step F's, as are the taxonomy's MSiG mappings (keyed on the chapter code
  and the terms).
- **Notices are fetched once.** A run reads every search page and fetches only notices not stored under the
  current extraction key (extraction version plus the vocabulary's hash). Editing the vocabulary re-fetches
  everything, which is what re-extraction needs.
- **MSiG reaches back to 2003 and adds real events.** The seed has a petition-stage order a month before a
  declaration, a COVID simplified restructuring, 2018 sanacja petitions, and a liquidation resolution date the
  registry lacks (ADR 0011, second addendum).
- **Live runs, 2026-09-23.** Run `026569e5`: 49 notices for 8 entities, nothing quarantined, nothing name-like
  in any record. Run `5b97a848`: search pages only, no notice fetched again, every fingerprint unchanged. The
  vocabulary then gained "uchwał" and "wspólnik", so a liquidation resolution's date carries its context. Run
  `26029b09` re-extracted all 49 under the new key, and 13 of them, plus one search page, are the fixtures in
  `tests/fixtures/legal/msig/`.

### Step C close-out

**2026-09-23.** `acquisition/krs_extract.py`, the registry redactor in
`acquisition/redaction.py` (`REGISTRY_REDACTION_VERSION = "krs-json-1"`), the `legal_source_fetches` manifest table,
and the `krs_extracts` asset (group `legal`, resource `krs_api`, 15 requests a minute). ADR 0009's addendum records
decision 3; CLAUDE.md and AGENT_SPEC §2 now name the registry redaction. What it found:
- **No two responses are byte-identical.** Every extract carries its generation time (`naglowekP.dataCzasOdpisu`),
  and the API sends no cache headers, so the HTTP cache never answers. The plan's "refetches only through the
  cache" could not hold. Instead each fetch is fingerprinted without that timestamp. An unchanged extract adds a
  `legal_source_fetches` row that points at the object already stored, and no new raw object. It still counts as
  a complete fetch for the cutoff.
- **An unknown KRS returns 404 with a JSON body.** The shared base now raises `SourceNotFound`, a subclass of
  `PermanentSourceError`, and A4 quarantines the entity (`krs_extract_not_found`).
- **Entry descriptions are a closed set of phrases** (`REJESTRACJA W KRAJOWYM REJESTRZE SĄDOWYM`, `ZMIANA DANYCH W
  REJESTRZE`, `WYKREŚLENIE Z KRAJOWEGO REJESTRU SĄDOWEGO`, `SPROSTOWANIE WPISU`), so they are allowlisted. The
  four fixtures were regenerated with the production redactor. They differ from the probe's only in the
  timestamp and 0000440028's restored deregistration entry, which the taxonomy now resolves (2025-09-10).
- **Header entries carry their own date** (`dataWpisu`), while every other record is dated through its
  `nrWpisuWprow`. Step F dates the two differently.
- **Live run, 2026-09-23.** `make test-integration` passes (39 tests). Dagster run `bd6b9c31` fetched all 17 seed
  extracts: 17 stored, 0 quarantined, every one redacted (`krs-json-1`), and the three deregistrations present. A
  second run (`9323bbe0`) fetched all 17 again and stored nothing new: 17 fetch rows, each pointing at the first
  run's object.

### Step B close-out

**Owner decisions, 2026-09-23: all five accepted as recommended**, with ADR 0011 (now `accepted`) and the rule
for a declaration with no decision date (`event_date` null, `known_from` the entry date, labels treat the event as
on or before `known_from`). Decision 3 is signed off, and its ADR 0009 addendum was written in step C, before any
bytes.

**Step B close-out, 2026-09-23.** `config/statutory/procedure_taxonomy.yaml` and
`config/labels/outcome_labels_v1.yaml`, loaded by `parsing/legal_taxonomy.py` and `labels.py`, tested in
`tests/parsing/test_legal_taxonomy.py` and `tests/test_labels.py`. What it found:
- **The restructuring date field is the opening decision date.** One MSiG search per seed restructuring
  (memory only, dates and keywords printed): 0000277937's `dataNadaniaKlauzuliWykonalnosci` 02.07.2021 is the day
  MSiG 135/2021 says its sanacja opened, and its asset-security order's 01.04.2021 matches MSiG 88/2021.
- **KRS still says "POSTĘPOWANIE NAPRAWCZE" for sanacja after 2016.** 0000386777's remedial proceedings of
  17.04.2020 are the sanacja MSiG 87/2020 announces (VI GRs 3/20). The taxonomy maps the label under both the 2003
  act (to 2015) and Prawo restrukturyzacyjne (from 2016). "POSTĘPOWANIE RESTRUKTURYZACYJNE" does not say which of the
  four procedures opened, so it maps to a new generic `restructuring_proceedings_opened`.
- **MSiG carries restructuring events the registry does not.** 0000277937 announced a COVID-era simplified
  restructuring in MSiG 212/2020 (2020-10-29), nine months before the sanacja. 0000386777 had sanacja petitions with
  asset-security orders in 2018 (VI GR 27/18, VI GR 40/18). Neither is in its KRS extract. Both move a label
  earlier, so step E matters for pre-2021 restructurings, not only for publication dates.
- **Every declaration adds an empty `opisZakonczeniaProcesuUpadlosci`.** Dated by its entry, it would end the
  bankruptcy on the day it opened. The mapping applies only when `dataZakonczeniaPostepowania` is filled (`when:
  present`).
- **The production redactor must keep entry descriptions (`naglowekP.wpis[].opis`).** They are the only
  deregistration signal, and the probe's 40-character rule reduced 0000440028's to `[REDACTED]`. Step C: put `opis`
  on the allowlist, and regenerate that fixture.
- **0000507997's court date and signature are in free text:** `organWydajacy` ends "5 SIERPNIA 2025 R., SYGN. AKT
  KR1S/GU/977/202…", truncated by the registry. Step F may read the date from there; the signature is cut short, so
  it cannot be the `proceeding_id`.
- **Mergers preclude `silent_exit`** (`precludes_silent_exit` on `merger_division_transformation`): a merged-away
  company is deregistered too, and that is not an exit. That goes one step past decision 5's wording; flag it if
  you disagree.
- The simplified-restructuring dates are 2020-06-24 to 2021-11-30 (the `upr_covid` statute).

### Step A close-out

**2026-09-22.** Full findings, the per-entity table, and the terms are in
**ADR 0011** (`proposed` then, accepted 2026-09-23). `notebooks/exploration/legal_sources_probe.py` is the probe. It held every response in
memory and printed only structure, dates and case signatures. Four redacted KRS extracts are in
`tests/fixtures/legal/krs/`, gated by `tests/acquisition/test_legal_fixtures.py`. What it changes here:
- **The KRS extract holds up across the seed.** It returned all 17 extracts over plain `httpx`, with no key and no
  wall, and **every one of the 9 hinted entities has its event in it**. 8 of 9 have a usable date:
  - `0000507997`'s declaration has only an entry date;
  - the restructuring openings' date field is named `dataNadaniaKlauzuliWykonalnosci`, which needs confirming (confirmed in step B).

  Decision 1 as recommended.
- **Decision 2 resolved: MSiG is a JSON API that returns notice text. The PDF tier is not needed,** and plan 0006's
  Phase 4 trigger does not fire. Step E becomes an MSiG JSON adapter.
- **The registry carries petition-stage evidence after all.** `dzial4.zabezpieczenieMajatkuOddalenieWnioskuOUpadlosc`
  (asset-security orders while a petition is pending, and dismissals) means "petitions never produce a registry
  entry" was wrong. The taxonomy gains `bankruptcy_petition_asset_security` and
  `restructuring_petition_asset_security` as petition-stage event types.
- **KRZ sits behind Imperva, like RDF.** It is not built in Phase 4 (ADR 0011 decision 4). Seed acceptance does not
  need it; `source_era` keeps the gap visible.
- **Decision 3 changes shape.** Notaries are named in free text (articles of association, liquidation and
  dissolution resolutions), and MSiG notice bodies name trustees. Redaction must be a key list **plus an allowlist
  of generic free-text fields**, with everything else reduced to its leading date. For MSiG, store the structured
  fields plus the extracted date and signature, not the body (ADR 0011 decision 3). The fixtures follow exactly
  this rule.
- **Deregistration has no structured field.** It is read from an entry's `opis` (`WYKREŚL…`). 3 of the 17 are
  deregistered, and 2 of them are the "in liquidation" hints, which are stale.
- **Registry lag is 14 days to 21 months** from decision to entry. `known_from` by entry date is safe but can be
  very late, and MSiG's earlier publication date matters for features.
- **Corrections to the text below:**
  - the header key is `naglowekP.wpis`, not `wpisy`;
  - `0000225354` (no hint) shows arrears with enforcement and court curators: a Phase 5 feature finding, not a
    label.
- **New open question for step B:** a declaration with no decision date. Proposed rule: `event_date` null,
  `known_from` the entry date, and labels treat the event as happening on or before `known_from`.

## Why

Nothing downstream of the canonical table can be evaluated until the project knows which companies became
distressed, and when. §4.6 defines the classes, and §5 defines `legal_events` and `outcome_labels`, but no code,
config or fixture for either exists:
- `config/statutory/procedure_taxonomy.yaml` is "not written" (`docs/data_inventory.md` §5);
- the notice fixtures are "missing" (§6);
- KRZ and MSiG access are "terms to confirm" (§7, and AGENT_SPEC §11.3).

The seed already contains the answer to check against. 9 of the 17 entities carry a publicly reported distress
status in `config/segments/construction_sme_v1_seed.yaml`:
- five in bankruptcy (two with declaration dates, 2025-05-20 and 2025-08-05; one of them a liquidating
  bankruptcy);
- two in restructuring (`0000277937`, `0000386777`);
- two in liquidation (`0000397658`, `0000440028`).

Phase 4 is done when the pipeline finds each of them independently, from the registers, with a date and a source
document.

### What changed since the specs were written: the KRS extract carries proceedings

The specs assume two eras: KRZ from late 2021, and before that only MSiG, as PDF. One request on 2026-09-22 to the
Ministry of Justice's **open KRS API** (`api-krs.ms.gov.pl/api/krs/OdpisPelny/{krs}?rejestr=P&format=json`) for
seed entity `0000181328` suggests the split is less stark:
- **`dane.dzial6.postepowanieUpadlosciowe.informacjaOOgloszeniuUpadlosci`** carries the bankruptcy declaration with
  the court (`organWydajacy`), the case signature (`sygnatura`: `KI1L/GU/43/2025`) and the decision date (`data`:
  20.05.2025). The date matches the seed's status hint, and the declaration is tied to entry number 44
  (`nrWpisuWprow`).
- **`dzial6.likwidacja`** carries liquidation opened (2010-02-01) and closed (2010-05-18) for the same company, so
  the extract reaches back well before KRZ existed.
- **`naglowekP.wpisy`** lists every registry entry (45 here) with its entry date (`dataWpisu`). So every fact in the
  extract can be dated by the entry that introduced it (`nrWpisuWprow`), and by the one that removed it
  (`nrWpisuWykr`). That is `known_from` for free, and it is point-in-time-correct by construction.

The API is public and needs no key; terms and rate expectations are unconfirmed. The extract names natural persons
(board members, shareholders, the trustee), which is decision 3.

The case signature is the same identifier KRZ and MSiG notices quote, so it is the natural `proceeding_id` and the
backbone of cross-source deduplication (§4.6).

This is one entity and one request. It justifies a probe (step A), not a design. But if it holds, KRZ and MSiG stop
being the only sources of labels and become sources of what the registry does not carry:
- petitions, and petitions dismissed for insufficient assets, which never produce a registry entry;
- publication dates earlier than the registry entry;
- anything missing from older extracts.

## Owner decisions needed before step B (recommendations first)

1. **The KRS full extract is the primary structured source of proceedings; KRZ and MSiG fill its gaps.**
   *Recommended.* It covers both eras in one structured format, per entity, with entry-dated history. The
   alternative, KRZ after 2021 and MSiG PDFs before, is two unstructured-to-structured pipelines and a source break
   in the labels. Either way `legal_events` keeps `source` per event, and deduplication runs across all three.
2. **MSiG and the C3 PDF tier are built only if step A shows the registry leaves a gap that matters.**
   *Recommended.* Step A answers two questions:
   - Which pre-2021 event types does the KRS extract lack for the seed? Expected: dismissed petitions.
   - Does the MSiG notice search return notice text, or only issue PDFs?

   If the gap is real and only PDFs exist, plan 0006's trigger fires, and its PyMuPDF text tier (not Docling, not
   the vision tier) is built for notices only. If the search returns text, or the gap is empty for the seed, plan
   0006 stays deferred, and the reason is recorded there.
3. **Registry and register JSON are redacted of natural persons before hashing, extending ADR 0009.**
   *Recommended, and it needs your sign-off.* It widens the one exception to invariant 2 ("sole exception: signer
   data", CLAUDE.md, ADR 0009). The alternative, storing the extract unredacted, breaks invariant 6.
   - **Redact:** names and PESEL numbers of board members, proxies, shareholders who are natural persons, trustees,
     supervisors and administrators. Each is replaced with a fixed placeholder.
   - **Keep:** the structure and the entry numbers. "The board changed at entry 31" survives, while no one's name
     does.

   Recorded as an ADR 0009 addendum. The redaction version joins the raw-store metadata, as it does for filings.
4. **`outcome_labels` gains `trigger_event_type` and `source_era`** (an AGENT_SPEC §5 amendment). *Recommended.*
   - **The problem:** petitions and dismissals are only visible from KRZ's launch, so before it a bankruptcy label
     starts at the declaration, and after it at the petition, months earlier.
   - **Why columns:** that is a real source break. Models must be able to see it, the way they see `regime_flag`.
   - **The columns:** `trigger_event_type` records which event started the label, and `source_era` (`pre_krz` |
     `krz`) records which world the horizon window sits in.
5. **`silent_exit` is a KRS deregistration with no qualifying proceeding before it.** *Recommended.* Its event date
   is the deregistration's decision date, and its `known_from` the entry date. "Filings cease" (§4.6) is then a
   feature (Phase 5, from `filing_index`), not a second label rule. Two rules would disagree about when the exit
   happened.

## Decisions this plan makes (flag any you disagree with before step C)

1. **Events are normalised in Python; labels are built in SQLMesh.** Turning an extract or a notice into dated
   events means walking JSON (or notice text), which is Python: `src/distress_radar/parsing/legal_events.py`, pure
   functions per source, written as Parquet like the canonical table. Labels are a SQL transformation on tabular,
   already-canonical events: a SQLMesh model, as PROJECT_OVERVIEW's crosswalk puts stage 8 in `transform/`. This
   mirrors ADR 0010's boundary.
2. **`event_date` and `known_from` are different columns, and both are required.**
   - `event_date` is when the court decided, or the petition was filed.
   - `known_from` is the earliest date a source made it public: the KRS entry date, or the KRZ/MSiG publication
     date if earlier. When sources disagree, the earliest publication wins, and every source's date stays in
     `legal_events` rows.
   - Labels look forward, so they are dated by `event_date`. Features look back, so they must use `known_from`.
     Mixing the two is the classic leakage bug (§9.1).
3. **`proceeding_id` is the normalised court case signature,** e.g. `KI1L/GU/43/2025`: whitespace, case and
   separators made canonical. `dedup_group_id` is a hash of `(krs, proceeding_id, event_type)`, so the same
   declaration seen in KRS, KRZ and MSiG is one group with three source rows. An event with no signature (some
   liquidation openings are shareholder resolutions) groups on `(krs, event_type, event_date)`.
4. **The taxonomy is dated config.** `config/statutory/procedure_taxonomy.yaml` maps each source's event type to a
   project event type and an outcome class, with effective dates per statute (Prawo upadłościowe, Prawo
   restrukturyzacyjne, the COVID-era simplified restructuring). It also holds the regime window. Nothing about
   which event means what is a Python constant (invariant 7). Unknown source event types are quarantined with a
   reason code, never mapped by guess and never dropped.
5. **Label parameters are versioned config, not statutory:** `config/labels/outcome_labels_v1.yaml` holds the
   horizons (12, 24), the `as_of_date` grid, the event-precedence rule and the cutoff policy. Its version is
   `label_version`. It is a subdirectory of `config/`, so no ADR is needed. Proposed values:
   - **Grid:** month-ends from 2012-01-31 (electronic MSiG) to the data cutoff. Phase 5 intersects the grid with
     whatever features exist.
   - **Precedence:** the earliest qualifying event in the horizon sets the class. On the same date, bankruptcy
     outranks restructuring outranks liquidation.
   - **Cutoff:** the earliest last-complete-fetch date across the sources used. A label whose horizon passes the
     cutoff with no event seen is `censored`, never `alive` (§4.6).
6. **The regime flag marks rows whose horizon window overlaps the regime window.** The window is 2020-01-01 to
   2021-12-31 per §4.6, in the taxonomy config. The simplified-restructuring procedure's own dates are recorded
   beside it for reference: in force from mid-2020 to 30 November 2021, exact dates confirmed against the act in
   step B. Flagging by window overlap, rather than by event date, lets a model exclude every example the spike
   could have touched, including the `alive` ones.
7. **Label sets are frozen with a content hash in Python, not in SQL.** After the SQLMesh model builds, a
   `freeze_label_set` step hashes the sorted label rows. It writes the hash to `label_set_hash`, and a manifest row
   (`label_sets` in Postgres: hash, `label_version`, cutoff, row count, created). A model trains on a hash, never
   on "the latest labels" (§4.6, and the four MLflow IDs in CLAUDE.md).
8. **Seed scale only.** Phase 4 runs on the 17-entity seed, per entity by KRS, with no enumeration of any register
   (§6A). A universe-scale backfill is a later, explicit operation.

## Out of scope

- Features from legal events or registry dynamics (board turnover, office moves): Phase 5. Step F records those
  event types, because they come from the same extract at no extra cost, but nothing reads them yet.
- `entity_size_class_history` and the average-employment decision (`docs/data_inventory.md` gap 8). Still open;
  labels do not need it.
- A5 reference and macro data.
- Docling and the vision-LLM tiers, even if decision 2 builds the PyMuPDF tier.
- Growing the universe beyond the seed.
- Any retuning of parsing, grading or identity rules.

## Steps

### A. Access probe for the three sources (no production code)

`notebooks/exploration/legal_sources_probe.py` (marimo), run by hand, at human pace, for the seed only. Findings go
to **ADR 0011** (sources, access and terms). Redacted response samples go to `tests/fixtures/legal/`. Nothing
unredacted is committed, and `test_no_fixture_contains_personal_data` must cover the new fixtures.

- **KRS open API:**
  - Fetch `OdpisPelny` for all 17 seed entities (and `OdpisAktualny` for one, for comparison).
  - Record the response shape, and every `dzial6` subsection seen: bankruptcy, restructuring (not yet observed;
    the two seed restructurings will show its shape), liquidation, dissolution.
  - Record how deregistration appears.
  - Record whether entry dates cover every `nrWpisuWprow` referenced.
  - Record any stated rate limit or terms, and the HTTP behaviour (plain `httpx`, or bot protection).
- **KRZ:**
  - Is there a public per-entity lookup by KRS usable without a browser: a JSON API behind the portal, a documented
    API, or neither?
  - What does a company's proceeding carry: petition date, decision dates, dismissals, announcements with
    publication dates, the signature?
  - Terms, and bot protection. If the portal behaves like RDF (WAF, CAPTCHA), the route is the HAR import pattern
    of plan 0003, never a bypass (CLAUDE.md).
- **MSiG:** does `wyszukiwarka-msig.ms.gov.pl` search by KRS, and return notice text or only issue PDFs? How far
  back does it reach for the 9 distress entities, and on what terms?
- **Gap analysis:** for each of the 9 distress entities, which events the KRS extract alone yields, and which need
  KRZ or MSiG. This table decides decision 2, and whether plan 0006's trigger fires.

### B. Statutory and label config

- `config/statutory/procedure_taxonomy.yaml` covers:
  - source event types for KRS (`dzial6` subsections and their fields), KRZ and MSiG, mapped to project event types
    (`bankruptcy_petition`, `bankruptcy_declared`, `bankruptcy_petition_dismissed_no_assets`,
    `arrangement_approved`, `accelerated_arrangement_opened`, `arrangement_proceedings_opened`,
    `remedial_proceedings_opened`, `simplified_restructuring_announced`, `liquidation_opened`,
    `liquidation_closed`, `deregistered`, plus the registry-dynamics types);
  - the outcome class for each, from §4.6;
  - effective dates per statute;
  - the regime window.
- `config/labels/outcome_labels_v1.yaml` per decision 5.
- Pydantic models that load both and reject:
  - an event type mapped to two classes;
  - overlapping effective ranges;
  - a class outside §4.6.
- Tests: lookups resolve across effective-date boundaries (§9.2 "Statutory config").

### C. A4 — KRS extract adapter

- `src/distress_radar/acquisition/krs_extract.py` on the shared base: `httpx`, the `hishel` cache, the persistent
  `pyrate-limiter` bucket, and `tenacity` with transient and permanent errors kept apart. The rate is conservative
  until ADR 0011 says otherwise.
- Per-entity `OdpisPelny` JSON, redacted per decision 3 (`acquisition/redaction.py` gains a registry-JSON
  redactor), then content-addressed into MinIO with the redaction version (invariant 2, ADR 0009 addendum).
- **Manifest:** a `legal_source_fetches` table (`krs`, `source`, `sha256`, `fetched_at`, `ingestion_run_id`). The
  latest successful fetch per `(krs, source)` sets that source's cutoff.
- A KRS the API does not know is a quarantine event (`krs_extract_not_found`), with the entity kept.
- Dagster asset `krs_extracts` (group `legal`). Re-materializing refetches only through the cache, so no new raw
  objects appear for unchanged extracts.

### D. A4 — KRZ adapter (shape set by step A)

- Per-entity lookup by KRS: `src/distress_radar/acquisition/krz_client.py` on the same base, or, if step A finds a
  browser wall, a manual-capture importer on the plan 0003 pattern.
- Lookup by KRS returns legal entities only. Any natural-person record that still appears (e.g. an announcement
  naming a trustee) is redacted before hashing. A consumer-bankruptcy record, if one is ever returned, is dropped
  at acquisition with a quarantine event (`natural_person`), as A2 does (invariant 6). A fixture proves both.
- Dagster asset `krz_proceedings`.

### E. A4 — MSiG, only if decision 2 calls for it

- If the search returns text: `acquisition/msig_client.py`, and notices parsed as text in step F.
- If only issue PDFs exist and the gap matters: execute plan 0006 steps for the **PyMuPDF text tier only**, scoped
  to notices. Record the tier per document, and mark plan 0006 "partially executed" with the reason.
- If neither is needed: record why in plan 0006's status section and in ADR 0011, and skip this step.

### F. `legal_events`: normalisation, deduplication, contract

- `src/distress_radar/parsing/legal_events.py`: one pure function per source (`from_krs_extract`, `from_krz`,
  `from_msig`). Each emits `legal_events` rows:
  - `krs`, `event_type`, `event_date`, `published_date` / `known_from`, `source`, `proceeding_id`,
    `source_document_hash`, `dedup_group_id`;
  - plus, for lineage (invariant 3), `source_element_path` (the JSON path or notice locator) and
    `ingestion_run_id`.
- **KRS dating:** `known_from` is the entry date of the entry that introduced the fact (`nrWpisuWprow` →
  `naglowekP.wpisy[].dataWpisu`). A fact whose entry number has no date is quarantined
  (`krs_entry_date_missing`), not dated by guess.
- **Deduplication** per decision 3. Groups keep every source row: the canonical event is derived (earliest
  `known_from`, and the court decision date as `event_date`), never stored in place of the rows.
- **Unmapped source event types** → quarantine (`legal_event_type_unmapped`), with the file kept.
- **Contract and write:**
  - a Pandera contract (`LEGAL_EVENTS`) beside the other three;
  - Parquet under `WAREHOUSE_DIR/legal_events/`, partitioned by `event_year`;
  - the same atomic, deterministic write as the canonical table (ADR 0008).
- Dagster asset `legal_events`, and an `ext.legal_events` view for SQLMesh (ADR 0010).

### G. `outcome_labels`: the SQLMesh model, freezing, audits

- `transform/models/marts/outcome_labels.sql` (FULL): the `as_of_date` grid × horizons × entities in scope.
  - The class comes from the earliest qualifying deduplicated event in `(as_of_date, as_of_date + horizon]`, by the
    config's precedence; otherwise `alive` or `censored` per the cutoff.
  - It also sets `event_date`, `proceeding_id`, `regime_flag`, `label_version`, and `trigger_event_type` and
    `source_era` if decision 4 is accepted.
  - An entity already in a proceeding at `as_of_date` is not a prediction target for that date. It is excluded,
    with a count in the audit output, rather than labelled with an event that has already happened.
- **`freeze_label_set`** (`src/distress_radar/labels.py`, called by the Dagster asset after the build) computes and
  records `label_set_hash` (decision 7). A rebuild over unchanged inputs must reproduce the same hash.
- **Audits** (non-blocking, surfaced as asset checks, ADR 0010):
  - one row per `(krs, as_of_date, horizon_months)`;
  - no `alive` row whose horizon passes the cutoff;
  - no event dated on or before its `as_of_date`;
  - every non-`alive`, non-`censored` row has a `proceeding_id` or a documented reason;
  - a regime flag exactly on the rows whose window overlaps the regime window.
- `dq_mart_coverage` gains event coverage per source and fiscal year. That is the number that shows the source
  break.

### H. Dagster wiring

- **Assets:**
  - group `legal`: `krs_extracts`, `krz_proceedings` (and `msig_notices` if built), `legal_events`;
  - a `labels` asset that runs the SQLMesh label model and then freezes the set.
- One run from `krs_extracts*` goes from registry bytes to a frozen label set.
- Asset checks:
  - quarantine counts by reason;
  - the seed acceptance check below;
  - the SQLMesh audits.
- Docstrings state inputs, outputs and partition scheme (§8).

### I. Docs

- **ADR 0011:** sources, access, terms and rates, the gap table, and decision 2's outcome.
- **ADR 0009 addendum:** registry and register redaction (decision 3). CLAUDE.md's invariant 2 and invariant 6
  wording updated to name it.
- **AGENT_SPEC:**
  - §5: `legal_events` lineage columns, and `outcome_labels` columns if decision 4 is accepted;
  - §4.6: the `silent_exit` rule (decision 5), and the KRS extract as a source beside KRZ and MSiG;
  - §6A: the A4 adapters as built.
- **`docs/data_inventory.md`:** statuses for §3, §5 (taxonomy), §6 (fixtures) and §7 (access).
- **CLAUDE.md** "Known moving targets": the KRS API and KRZ access findings.
- **`DIRECTORY_STRUCTURE.md`:** `config/labels/`, `tests/fixtures/legal/`, and the new modules.
- **Plan 0006:** its status updated per decision 2's outcome, either way.
- **README:** status and how to run the `legal` and `labels` groups.

## Tests

- **Taxonomy and label config:** effective-date boundaries; every §4.6 class reachable; an unknown source event
  type quarantined, not mapped.
- **Redaction:** a KRS extract fixture with synthetic persons loses every name and PESEL and keeps every entry
  number. The redaction is deterministic, so re-redacting is a no-op. No committed fixture contains personal data.
- **`legal_events`:** each source's fixture yields the expected events and dates; `known_from` comes from the entry
  date, not the decision date; a missing entry date is quarantined; the contract holds.
- **Deduplication:** one declaration seen in KRS and KRZ with different publication dates forms one group; the
  earliest `known_from` wins; both rows survive.
- **Label construction (§9.2):**
  - each outcome class;
  - censoring at the cutoff (an entity alive past it is `censored`, never `alive`);
  - an event on the `as_of_date` itself (excluded, not labelled);
  - precedence on a same-day tie;
  - the regime flag at both window edges;
  - `silent_exit` versus deregistration after bankruptcy (the latter is `bankruptcy`);
  - the source-era break.
- **Consumer bankruptcy:** a fixture record for a natural person never reaches raw storage or `legal_events`.
- **Idempotence:** re-running every new asset on unchanged input gives byte-identical Parquet and the same
  `label_set_hash`.

## Definition of done

- [x] ADR 0011 accepted: access and terms confirmed for every source used, and the gap table recorded. *(Accepted 2026-09-23.)*
- [x] The procedure taxonomy and label config are written, validated and tested. *(Step B, 2026-09-23; MSiG mappings follow in step E.)*
- [x] KRS extracts (and KRZ, and MSiG if built) acquired for the 17-entity seed, redacted and content-addressed. *(KRS 2026-09-23, run `bd6b9c31`; MSiG 2026-09-23, 49 notices; KRZ not built, ADR 0011.)*
- [x] `legal_events` persisted and contracted, with full lineage and deduplication. *(Step F, 2026-09-23.)*
- [x] **Seed acceptance:** each of the 9 entities with a distress status hint has a matching event, found
      independently, with a source document and a date. Every mismatch with a hint is investigated and recorded;
      the hints are hints, not ground truth. The 8 others have no qualifying event, or any event found is
      explained. *(The `seed_acceptance` check, run `cde2a23f`: 9 matched, 8 clear.)*
- [x] `outcome_labels` built, audited and frozen, with a reproducible `label_set_hash` and censoring and regime
      flags per §4.6. *(Step G, 2026-09-23: set `a5da757f8341…`.)*
- [x] Dagster runs the `legal` and `labels` groups end to end; audits and acceptance surface as asset checks. *(Job `legal_to_labels`, 2026-09-23.)*
- [x] Docs from step I updated. *(2026-09-23.)*
- [x] `make check` and `make test-integration` green; re-running is byte-identical. *(2026-09-23: 576 + 10 SQLMesh tests, 40 integration; `legal_events` and the frozen label set, 21 Parquet files, unchanged across a re-run.)*

## Risks

- **The KRS finding may not generalise.** One extract showed a bankruptcy and a liquidation. Restructuring entries,
  deregistered entities and very old histories are unseen. Step A exists so the design follows the data, not the
  single example.
- **KRZ may sit behind the same wall as RDF.** Then the route is manual capture or a sanctioned channel, never a
  bypass, and the label set carries fewer petition-date events until then. Decision 4's `source_era` makes that
  visible rather than silent.
- **The source break is real whatever is built.** Before KRZ, petitions and dismissals are largely invisible, so a
  pre-2021 bankruptcy label starts later than a post-2021 one. The mitigation is to record it (decision 4) and
  let Phase 6's out-of-time evaluation measure it, not to paper over it.
- **Registry dates are not decision dates.** An entry can land weeks after the court decides. Using the decision
  date as `known_from` would leak; decision 2 keeps them apart. The audit "no event dated on or before its
  `as_of_date`" guards the label side.
- **Redaction touches an invariant.** Decision 3 needs sign-off before step C writes any bytes. Until then, step A's
  samples stay local and redacted by hand.
- **Terms may forbid what a step assumes.** Access is confirmed in step A before any adapter is written (§11.3).

## After Phase 4

Phase 5 (AGENT_SPEC §10): the feature store with ASOF assembly and the blocking leakage test. It consumes
`legal_events` through `known_from`, and trains against a frozen `label_set_hash`. The substitute from plan 0006
(sourcing a missing year from the next filing's prior-year column, dated by that filing) is Phase 5's to build.
