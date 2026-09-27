# 0013 — Scaled access to financial statements

- **Status:** proposed. The owner decides; this ADR lays out the options and a recommendation.
- **Date:** 2026-09-27
- **Follows up:** 0007 (options a and b, left open when option C failed on 2026-09-16)
- **Opened by:** plan 0012, owner decision 0

## Context

Phase 6 built the modelling harness on the 17-entity seed, and the harness works. The seed cannot
say whether a model works: it has 11 events in 9 entities, and in the backtest a single cell of 84
reaches three events (plan 0012). It was also hand-picked with distress hints, so its base rate is
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
- *For:* no external dependency; fixes the calibration problem the hand-picked seed has.
- *Against:* still manual, and still far short of v1; a stepping stone, not a route.

## Recommendation

Pursue (a) and (b) in parallel, and use (c) and (d) meanwhile.

1. **Send the request for (a) now.** It is free, it preserves the pipeline and the invariants, and
   its lead time is the longest, so it should start first. Ask for bulk re-use of the statements of
   the v1 universe (or allow-listed access at an agreed rate), and state the use: research
   modelling, legal entities only, natural persons removed at acquisition.
2. **Ask two or three vendors for quotes for (b)**, with the original filed documents as a hard
   requirement and A1 discovery (the candidate list) in the same quote. A feed of parsed figures
   only is out, whatever its price.
3. **Meanwhile, (d):** capture a larger sample by hand, chosen by a written sampling rule (for
   example, random KRS numbers from the construction segment, not distress hints), so the next
   backtest measures calibration on something closer to the population.

## Decision

Open. To be recorded here by the owner, with the date and the chosen route or routes.

## Consequences

- Whichever route delivers filed XML, A3 needs no new parser: `rdf_manual_import`, the RDF adapter
  and the parsing stages already take it. A new *delivery* adapter (bulk files, an allow-listed
  client, or a vendor drop) goes in `acquisition/`, with its own ADR entry for terms and rate.
- A1 discovery needs a source whichever route is chosen for A3; option (b) may settle both.
- Until one of these lands, Phase 7 (text signals) and Phase 8 (LightGBM, survival) can be built on
  the seed, but, like Phase 6, they prove machinery, not performance.
- `CLAUDE.md`'s "Known moving targets" should point here once the owner decides.
