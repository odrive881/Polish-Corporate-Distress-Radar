# 0013 — Scaled access to financial statements

- **Status:** accepted (2026-10-06): RDF documents are downloaded through the public UI by a Power
  Automate Desktop script at a human pace; the Ministry of Justice has no API endpoint for this yet
  (§ Decision).
- **Date:** 2026-09-27
- **Follows up:** 0007 (options a and b, left open when option C failed on 2026-09-16)
- **Opened by:** plan 0012, owner decision 0

## Context

Phase 6 built the modelling harness on the 17-entity seed, and the harness works. The seed cannot
say whether a model works: it has 11 events in 9 entities, and in the backtest no cell of 84
reaches three events once each fold trains only on labels public by its test year (plan 0012,
corrected 2026-09-29). It was also hand-picked with distress hints, so its base rate is
wrong for the population by construction. **The critical path to a meaningful model is the size of
the universe, not modelling.**

The v1 universe is construction, `sp. z o.o.`, small and medium, at least three filed years: a few
thousand entities (AGENT_SPEC §1). Each has several filings, and a filing is one or more documents.
That is tens of thousands of documents.

Two things block getting them:

1. **Financial statements (A3).** RDF sits behind an Imperva WAF that served an hCaptcha to an
   honest, human-paced browser (ADR 0007, 2026-09-16), so option C failed by its own rule. The
   interim route is manual capture: a person uses the public UI and saves HAR files, which
   `rdf_manual_import` imports (`README.md` § "Manual RDF capture"). It preserves the raw bytes and
   the lineage, and it scales with a person's time. Even at the 3 documents a minute KRS support
   confirmed for automation, tens of thousands of documents are days of continuous downloading;
   by hand it is hundreds of hours.
2. **Discovery (A1).** No registry aggregator is chosen, so there is no candidate list of KRS numbers
   for the universe beyond the seed (`docs/data_inventory.md`, "Registry aggregator account").

Whatever route is chosen must keep the invariants: the filed document's bytes stored unmodified and
content-addressed before parsing (invariant 2), lineage from every fact to its document (3), natural
persons removed before storing (2, 6; ADR 0009), and the source's terms respected (AGENT_SPEC §11.3).
That rules out any route that gets past the WAF without sanction, and it makes a feed of
already-parsed figures, without the filed documents, a poor fit.

## Options

**(a) Sanctioned bulk or allow-listed access from the Ministry of Justice.** A formal request to the
operator of RDF, either for an allow-listed client (an IP or key the WAF lets through at an agreed
rate), or for the documents in bulk as a re-use request under the open-data act (ustawa z dnia
11 sierpnia 2021 r. o otwartych danych i ponownym wykorzystywaniu informacji sektora publicznego),
which the KRS open API already rests on (ADR 0011).
- *For:* the filed XML itself, so the pipeline runs unchanged (the RDF adapter and its tests are
  kept for exactly this, ADR 0007); official; free or near it.
- *Against:* an answer takes weeks to months, and may be no. The informal support confirmation of
  2026-09-15 shows a channel exists, but not that it grants this.

**(b) A licensed commercial feed.** Quotes from registry-data vendors for the target universe's
statements.
- *For:* fast once contracted; may also settle A1 discovery in the same contract.
- *Against:* cost; terms that must allow this use and storage; and most feeds deliver figures, not
  the filed documents. Only a feed of the original filed XML keeps invariants 2 and 3; parsed figures
  would be a second, unverifiable parser in front of ours.

**(c) Manual capture, scaled.** More people, or more time, on the existing HAR route.
- *For:* works today, fully within the terms, keeps the raw bytes.
- *Against:* hundreds of hours for the v1 universe, and it must be repeated each filing season.

**(d) A smaller universe first.** Grow the seed by hand to the scale where some folds clear the
minimum-events rule, drawn by a documented sampling rule instead of distress hints.
- *For:* fixes the calibration problem the hand-picked seed has.
- *Against:* still manual, and still far short of v1; a stepping stone, not a route. And it is
  not free of A1: a sample needs a frame to be drawn from, a list of construction `sp. z o.o.`
  KRS numbers, which only discovery provides. Probing KRS numbers at random until enough
  construction companies turn up is the bulk enumeration AGENT_SPEC §6A forbids.

## Recommendation

Pursue (a) and (b) in parallel, and use (c) and (d) meanwhile.

1. **Send the request for (a) now.** It is free, it preserves the pipeline and the invariants, and
   its lead time is the longest, so it should start first. Ask for bulk re-use of the statements of
   the v1 universe (or allow-listed access at an agreed rate), and state the use: research
   modelling, legal entities only, natural persons removed at acquisition.
2. **Ask two or three vendors for quotes for (b)**, with the original filed documents as a hard
   requirement and A1 discovery (the candidate list) in the same quote. A feed of parsed figures
   only is out, whatever its price.
3. **Meanwhile, (d), once there is a frame to sample:** capture a larger sample by hand, drawn
   by a written sampling rule (for example, at random from a candidate list of the construction
   segment, never by distress hints and never by probing KRS numbers), so the next backtest
   measures calibration on something closer to the population. The frame is A1's, so (d) waits
   for a discovery source, from (b) or elsewhere.

## Decision

**Accepted by the owner, 2026-10-06: scripted downloads through RDF's public UI, at a human pace, with
Power Automate Desktop (PAD).**

- **(a) answered: not available yet.** The Ministry of Justice told the owner that RDF has no API
  endpoint for this access, and that one is planned, with no date given. (a) is revisited when it
  ships: an official channel that delivers the filed documents replaces the route below through a
  new delivery adapter (§ Consequences), and the RDF adapter kept by ADR 0007 is checked against it.
- **The route.** A PAD script, run by the owner on their own machine, drives an ordinary browser
  through RDF's public UI and downloads each category of document the pipeline needs, one category
  at a time, at a human pace. It extends to every category what plan 0013 (decision 0c) did for the
  seed's auditor reports on 2026-10-01, and it is the kind of "non-invasive download automation
  script" KRS support confirmed on 2026-09-15 (ADR 0007). It is not ADR 0007's option C: that was a
  Playwright tier inside the pipeline, and it stays failed.
- **The other options.** (b) is not taken up for A3. (c) gives way to the script; HAR capture
  (`rdf_manual_import`) stays for single entities and for documents the script cannot fetch. (d)
  still applies: the next universe beyond the seed is drawn by a written sampling rule from a frame,
  never by distress hints.

### The rules the script keeps

1. **Pace:** at most 3 documents a minute, the rate KRS support confirmed; one browser, one document
   at a time, never two scripts at once. A higher rate needs a new ADR (ADR 0007).
2. **A challenge stops the script.** On a CAPTCHA, a WAF block page or any page other than the one it
   expects, the script stops and does not retry. The script never solves a challenge, and no service does;
   no stealth settings, no replayed cookies (ADR 0007's limits). *Amended 2026-10-06 (owner):* the owner,
   at the machine, solves a CAPTCHA by hand, as in ordinary use of the page, and the run then continues at
   the same pace. ~~Each challenge is noted with its time, so their rate is known.~~ (withdrawn with rule 7,
   2026-10-07)
3. **Scope:** the categories the pipeline reads, by `config/mappings/rdf_document_types.yaml`: annual
   financial statements from 2018 (type 18) with their corrections, and auditor reports (type 19).
   Another category joins by a new version of that file, never by the script alone. The script never
   opens "Pokaż zgłoszenie" (it lists the signatories by name).
4. **Per entity, from a list:** the script searches only KRS numbers it is given. It never enumerates
   KRS numbers (AGENT_SPEC §6A); the list comes from A1 discovery or a written sample.
5. **What it hands over,** in an inbox outside the repository:
   - each ZIP exactly as "Pobierz dokumenty" delivered it (raw immutability, invariant 2);
   - a listing, one row per document: KRS number, RDF's document id (`idDokumentu`), type, period
     end, "Data dodania" (the detail's `dataDodania`, never "Data sporządzenia dokumentu") and, for a
     correction, the id of the document it corrects. The listing is stored raw and every date points
     back to it (plan 0013 decision 6, as amended 2026-10-02): it is the documents' `known_from`.
6. **Personal data:** the ZIPs as downloaded name people. They stay out of the repository and are
   deleted once imported; only redacted copies are stored (ADR 0009).
7. ~~**The challenge log is the WAF check**~~ *Withdrawn 2026-10-07 (owner): the script keeps no challenge
   log. Rule 2 stands: a challenge still stops the script, and any block page, or CAPTCHAs coming more often,
   still means stopping and revisiting (a). Nothing records their rate, so that judgement rests on the owner,
   at the machine.* As added 2026-10-06, replacing ADR 0007's probe rerun for this route: The probe notebook tests plain HTTP, which tells nothing about a person's browser, so the script
   keeps a log of every CAPTCHA, block page and unexpected page: time, KRS number, the action it interrupted,
   and whether it was solved by hand. The log is handed over with the listing and stored with the import. A
   rising rate of challenges, or any block page, means stopping and revisiting (a), not tuning the script.

## Consequences

**Of the decision (2026-10-06):**

- **An importer for the script's output** is the next A3 build, its own plan. `acquisition/report_import.py`
  (the `manual_files` tier) takes only auditor reports, and only onto `filing_index` rows that a HAR
  capture already created. For an entity first reached by the script there is no such row, so the
  listing has to create the `filing_index` rows itself, statements and corrections included, with the
  same matching rules and refusals as `har_import.py`.
- ~~**A1 discovery is still open.**~~ Settled by ADR 0014 (accepted 2026-10-06): the owner's Rejestr.io list,
  and from list v2 a sampling rule (ADR 0014 addendum, 2026-10-07, proposed). As written: the script needs a
  list of KRS numbers, and no source for one is chosen.
- **Volume:** tens of thousands of documents at 3 a minute are days of running for the v1 universe,
  and each filing season adds a wave (PROJECT_OVERVIEW stage 3). The script resumes from where it
  stopped; a re-import adds nothing (idempotence, invariant 5).
- **In operation (owner, 2026-10-06):** the script has been running on the owner's laptop for three days
  on the list of ADR 0014, with no CAPTCHA so far.
- **The permission is informal.** KRS support's confirmation was not a written policy, and the WAF
  can change without notice. A rise in challenges is a reason to stop and revisit (a), not to tune
  the script; since rule 7's withdrawal (2026-10-07) only the owner sees that rise.

**Of the options, as written on 2026-09-27:**

- Whichever route delivers filed XML, A3 needs no new parser: `rdf_manual_import`, the RDF adapter
  and the parsing stages already take it. A new *delivery* adapter (bulk files, an allow-listed
  client, or a vendor drop) goes in `acquisition/`, with its own ADR entry for terms and rate.
- A1 discovery needs a source whichever route is chosen for A3; option (b) may settle both.
- Until one of these lands, Phase 7 (text signals) and Phase 8 (LightGBM, survival) can be built on
  the seed, but, like Phase 6, they prove machinery, not performance.
- `CLAUDE.md`'s "Known moving targets" points here and records the decision (2026-10-06).
