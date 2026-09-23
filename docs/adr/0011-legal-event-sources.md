# 0011 — Legal-event sources: the KRS extract, MSiG and KRZ

- **Status:** accepted (2026-09-23, by the owner, with plan 0008's decisions). KRZ stays open.
- **Date:** 2026-09-22

## Context

Plan 0008 (Phase 4) needs dated insolvency, restructuring and liquidation events for the seed, to build
`legal_events` and `outcome_labels`. The specs assumed two eras: KRZ from December 2021, and before that only the
MSiG archive, as PDF, which would force the deferred C3 PDF tier (plan 0006) into existence. One request to the
open KRS API while writing the plan suggested the registry extract itself records proceedings. Step A probed all
three sources for the 17-entity seed (`notebooks/exploration/legal_sources_probe.py`):
- plain `httpx` with the project's User-Agent, at about one request every 4 seconds;
- responses held in memory only;
- nothing unredacted written anywhere.

## Findings

### 1. The open KRS API: works, structured, covers both eras

`GET https://api-krs.ms.gov.pl/api/krs/OdpisPelny/{krs}?rejestr=P&format=json` returned `200` JSON for **all 17**
seed entities: no key, no bot protection (server `Kestrel`), 33–167 KB each. The extract is the full one, with
history, and is served for deregistered entities too.
- **Dating.** Every fact carries `nrWpisuWprow` (the entry that introduced it) and, if removed, `nrWpisuWykr`. The
  header's `naglowekP.wpis[]` gives each entry's `numerWpisu`, `dataWpisu`, `opis` and the court. Every entry number
  referenced in sections 4–6 had a date. (The plan said `wpisy`; the key is **`wpis`**.)
- **Proceedings (`dzial6`):**
  - `postepowanieUpadlosciowe[]`: `informacjaOOgloszeniuUpadlosci` (the court, `sygnatura`, and the decision date
    `data`), `sposobProwadzeniaPostepowania` (e.g. `UPADŁOŚĆ`, `UPADŁOŚĆ OBEJMUJĄCA LIKWIDACJĘ MAJĄTKU DŁUŻNIKA`),
    `opisZakonczeniaProcesuUpadlosci` (with `dataZakonczeniaPostepowania`), and the trustee.
  - `postepowanieRestrukturyzacyjneNaprawczePrzymusowaRestrukturyzacjaUporzadkowanaLikwidacja[]`:
    `otwarciePostepowania…` with `sygnaturaSprawy`, `rodzajPostepowania` (`POSTĘPOWANIE RESTRUKTURYZACYJNE`,
    `POSTĘPOWANIE NAPRAWCZE`) and a date field named **`dataNadaniaKlauzuliWykonalnosci`**.
  - `likwidacja[]`: `otwarcieLikwidacji` and `zakonczenieLikwidacji`, both free text with a leading date, plus the
    liquidators.
  - Also `rozwiazanieUniewaznienie` (dissolution) and `polaczeniePodzialPrzeksztalcenie` (merger/transformation).
- **Petition stage (`dzial4`).** `zabezpieczenieMajatkuOddalenieWnioskuOUpadlosc[]` holds asset-security orders
  made while a petition is pending: `…WPostepowaniuUpadlosciowym` and `…WPostepowaniuRestrukturyzacyjnym`, with the
  case signature and a date. The section's name also covers dismissals of a petition; none was seen in the seed.
  **The registry does carry petition-stage evidence**, which the plan assumed it never does.
- **Arrears (`dzial4.zaleglosci`)** with enforcement (`dataWszczeciaEgzekucji`), and **curators (`dzial5.kurator`)**
  with appointment dates. These are not taxonomy events, but they are strong distress signals (below).
- **Deregistration has no structured field.** It appears only in an entry's `opis` (the keyword `WYKREŚL…`). All
  three deregistered seed entities were found that way.
- **Natural persons** sit under `imiona` / `nazwisko` / `identyfikator.pesel` in shareholders, the board, proxies,
  the supervisory board, curators, liquidators, trustees, restructuring supervisors, and the bankrupt's
  representative. Across the seed that is about 750 person-bearing values. They also appear in **free text** that
  no key identifies:
  - notaries in the articles-of-association entries;
  - notaries in the liquidation and dissolution resolutions.

  A key-based redactor is therefore not enough (see Decision 3).

### 2. MSiG: a JSON search API that returns notice text, with no PDF involved

`wyszukiwarka-msig.ms.gov.pl` is an Angular app on IIS with no bot protection. Its bundle calls an API the page
discovers at `/home/getapiurl` (currently `https://wyszukiwarka-msig.ms.gov.pl/api`):
- **Search:** `GET /Monitor/Search?krs=&signatureType=A&from=&to=&page=` returns `{countPages, page, list[]}`, each
  item with `id`, `monitorNumber`, `dateOfPublication`, `entityName` and `signatureOfCase`. `signatureType` is
  required: `A` is the notice base, `B` the KRS-entries base. `countPages` is a constant; `/Monitor/SearchCount`
  gives the real page count. The UI's earliest searchable date is 2001.
- **A notice:** `GET /Monitor/Detalis?Id=` returns JSON with `krs`, `signatureOfCase`, `chapterName`,
  `monitorNumber`, `dateOfPublication`, and the notice text in `textInPosition` and `textInBody`.
- For `0000188883`, the search returned 20 notices from 2014 to 2024. The first, in MSiG 28/2014 on 2014-02-11
  under `IX GU 103/13`, contains the decision date "21 stycznia 2014", which matches the registry.
- **Notice text names people** (the trustee at least). It is free text, so redacting it needs more than a key list
  (Decision 3).

### 3. KRZ: behind the same WAF as RDF

`https://krz.ms.gov.pl/` answered a plain client with a 212–838-byte challenge page and an `x-iinfo` header. That is
Imperva Incapsula, the wall that stopped automated RDF access (ADR 0007). The probe stopped there, as required, and
found no public API. Commercial resellers advertise a KRZ API, which suggests a sanctioned channel may exist; that
is for the owner to pursue, not the probe.

### 4. The seed, source by source

| KRS | Hint | What the KRS extract yields | Gap |
|---|---|---|---|
| 0000507997 | bankrupt, declared 2025-08-05 | bankruptcy entered 2025-08-13 (entry 44), **no decision date or signature**; arrears with enforcement from 2023 | decision date; post-2021, so KRZ only |
| 0000181328 | bankrupt, 2025-05-20 | asset security 2025-02-04; **declared 2025-05-20** (`KI1L/GU/43/2025`), entered 2025-08-08; liquidation 2010, reversed | none |
| 0000188883 | liquidating bankruptcy | **declared 2014-01-21** (`IX GU 103/13`), entered 2014-02-04; MSiG notice 2014-02-11 | none |
| 0000070294 | bankrupt | **declared 2017-03-08**, entered 2017-04-07; proceeding ended 2021-07-27; **deregistered 2021-11-19** | none |
| 0000225506 | bankrupt | asset security 2022-03-25; **declared 2022-06-13**, entered **2024-03-15** | none (but see lag) |
| 0000277937 | in restructuring | security (sanacja) 2021-04-01; restructuring opened (`VIII GR 7/21`, date 2021-07-02), entered 2021-10-22 | meaning of the date field |
| 0000386777 | in restructuring | bankruptcy-petition security 2019-10-10 (`VI GU 751/19`); **remedial proceedings** opened on the same signature (date 2020-04-17), entered 2020-06-24 | meaning of the date field |
| 0000397658 | in liquidation | liquidation opened 2021-10-01, closed 2022-06-30; **deregistered 2022-10-31** | hint stale |
| 0000440028 | in liquidation | liquidation opened (entry 2023-08-18), closed and **deregistered 2025-09-10** | opening date not in text; hint stale |
| 0000225354 | — | **arrears with enforcement 2023–24; curators appointed 2025** | not a taxonomy event: a feature, and a finding |
| 7 others | — | no proceeding sections | — |

**All 9 hinted entities have their event in the KRS extract.** 8 of 9 have a usable date:
- 0000507997's declaration has an entry date but no decision date.
- The restructuring dates' meaning needs confirming.

Two of the "in liquidation" hints are stale: both liquidations finished, and the companies are deregistered.

**Registry lag is large and variable.** From decision to entry: 14 days (0000188883), 30 days (0000070294), 80 days
(0000181328), and **21 months** (0000225506). Dating by entry is always safe, since it is never earlier than the
truth, but it can be very late. An earlier publication (MSiG before 2021, KRZ after) is the fix.

## Decision

1. **The KRS full extract is the primary source of proceedings, in both eras** (plan 0008 decision 1, as
   recommended). It is structured, per entity, entry-dated, and yields every hinted event in the seed.
2. **MSiG is the secondary source, read as text through its JSON API. The C3 PDF tier is not needed.** Plan 0006's
   Phase 4 trigger ("MSiG forces a PDF text layer") **does not fire**, and plan 0006 stays deferred. MSiG supplies
   pre-2021 publication dates, which are often earlier than the registry entry, and the notice text as a second
   reading of the decision date.
3. **Redaction must be an allowlist, not a key list** (input to plan 0008 decision 3, still the owner's to sign).
   - **Key-based part:** person-keyed leaves (`imie`, `imieDrugie`, `nazwiskoICzlon`, `nazwiskoIICzlon`, `pesel`)
     become a placeholder.
   - **Allowlist part:** free text is kept only for fields that are generic by construction:
     - court names;
     - PKD descriptions;
     - share counts;
     - reporting periods;
     - procedure types.

     Everything else is reduced to its leading date.
   - **Coverage:** that covers the notarial citations the key list misses.
   - **MSiG notice bodies** cannot be allowlisted field by field. The options for the ADR 0009 addendum are:
     - store only the notice's structured fields, plus the dates and signature extracted from the text, as the
       redacted "raw" document;
     - or a named-entity pass (spaCy `pl_core_news_lg` is in the locked stack).

     Recommended: the first, since it is deterministic.

   The probe's fixtures use exactly the key and allowlist rules, and `tests/acquisition/test_legal_fixtures.py`
   gates them.
4. **KRZ is not built in Phase 4.** It would add post-2021 petition dates, dismissals, and publication dates earlier
   than the registry's. That's worth having, but not needed for seed acceptance: the KRS extract covers every hinted
   event, and `dzial4` gives petition-stage evidence. Automated access is blocked by the WAF, and the route stays
   what ADR 0007 set for RDF: manual capture or a sanctioned channel, never a bypass. `outcome_labels.source_era`
   (plan 0008 decision 4) keeps the gap visible.
5. **Deregistration is read from entry descriptions.** An entry whose `opis` contains `WYKREŚL` is a `deregistered`
   event dated by that entry. It is the only signal the API gives, and the liquidation-closure entry usually
   carries it.

## Open questions for plan 0008

- **The date field on restructuring openings is named `dataNadaniaKlauzuliWykonalnosci`** ("date the enforceability
  clause was granted"). For 0000277937 it is 2021-07-02 against an entry of 2021-10-22, which is plausible as the
  opening decision. The name may be a misnomer in the API's schema, or the field may really mean the clause date.
  Confirm against the MSiG notice (pre-2021, 0000386777) before the taxonomy treats it as `event_date`.
- **A declaration with no decision date** (0000507997). Rule needed: `event_date` null and `known_from` the entry
  date; labels treat the event as happening **on or before** `known_from`. Or require a second source (KRZ) for it.
- **0000225354** has arrears, enforcement and court curators, but no hint and no proceeding. It is not a label;
  record it as a finding for Phase 5 features. It is exactly the kind of company the model should learn to flag.
- **MSiG coverage before 2012,** and for the other pre-2021 entities, was not checked (one search was run). Step E
  of plan 0008 does it per entity.

## Terms and rates

- **KRS API:** launched by the Ministry of Justice on 2022-03-08 under the Act of 11 August 2021 on open data and
  the re-use of public-sector information (*ustawa o otwartych danych i ponownym wykorzystywaniu informacji
  sektora publicznego*). Its scope is the full and current extract, "with regard to GDPR". That is the legal basis
  for re-use. **No rate limit is published.** The adapter stays conservative (the BIR1 default of 30 a minute, or
  lower) until one is.
- **MSiG search:** a public service of the same ministry, used here exactly as its own UI uses it, one entity at a
  time. No terms page was found. It should be treated like the KRS API: low rate, per entity, no enumeration
  (AGENT_SPEC §6A).
- **KRZ:** a WAF. No automated access.
- Re-run this probe before any backfill beyond the seed: both APIs are undocumented in their details, and can
  change without notice.

## Consequences

- Plan 0008 steps C (KRS adapter) and F (normalisation) can proceed on known shapes, and step E becomes an MSiG
  JSON adapter, not a PDF pipeline. Plan 0006 records that its Phase 4 trigger did not fire.
- The redaction design changes from "a list of keys" to "a list of keys plus an allowlist of free text". It needs
  the owner's sign-off before any extract is stored (plan 0008 decision 3).
- Labels for post-2021 bankruptcies start at the declaration or the registry's petition-stage order, not at the
  petition itself. `source_era` and `trigger_event_type` make that explicit.

## Addendum, 2026-09-23: the open questions after plan 0008 step B

- **The restructuring date field is resolved.** `dataNadaniaKlauzuliWykonalnosci` is the decision date, despite its
  name. On 0000277937 the opening (02.07.2021) and the asset-security order (01.04.2021) match the dates MSiG
  135/2021 and 88/2021 give.
- **0000386777's "POSTĘPOWANIE NAPRAWCZE" of 17.04.2020 is a sanacja** (MSiG 87/2020, VI GRs 3/20). The registry kept
  the pre-2016 label, and the taxonomy maps it for both eras.
- **A declaration with no decision date:** the owner accepted the proposed rule (`event_date` null, `known_from` the
  entry date, on or before it for labels).
- **MSiG coverage grows the gap table.** The KRS extracts miss 0000277937's simplified restructuring (MSiG 212/2020)
  and 0000386777's 2018 sanacja petitions (VI GR 27/18, VI GR 40/18). Both are earlier than anything in the
  registry, so MSiG is a source of pre-2021 events, not only of publication dates. MSiG also still publishes
  Prawo restrukturyzacyjne notices after KRZ's launch (for example 0000386777 in 2025).
- **The MSiG search requires `from` and `to`** (HTTP 444, "Daty publikacji od i Daty publikacji do - Wymagane",
  without them).

## Addendum, 2026-09-23: MSiG across the seed (plan 0008 step E)

- **API shape.** `Monitor/SearchCount` returns the number of result pages (1 when there are none), and
  `Monitor/Search` returns up to 20 notices a page. Both need `from` and `to`. A notice's detail has `id`, `krs`,
  `entityName`, `numberOfNotice`, `page`, `monitorNumber`, `dateOfPublication`, `chapterName`, `signatureOfCase`,
  `textInPosition` (a header naming the company, the court and its registration date), `textInBody`, and the
  neighbouring notices' ids.
- **Chapters are coded.** Bankruptcy notices carry a subchapter: `III/1` for a declaration, `III/3` and `III/4` for
  claims lists and distribution plans, `III/6` for an ending, and `III/9` ("other") for petition-stage orders.
  Restructuring notices (`IX`) and company-law notices (`I/2`) have no subchapter, so they are typed from their
  text. Chapter names vary across years (the "I NAPRAWCZE" suffix before 2016, spacing), and the Roman numeral plus
  the subchapter number is the stable key.
- **Coverage: 49 notices for 8 of the 17 entities, back to 2003,** earlier than the 2012 this ADR assumed. The two
  2025 bankruptcies (0000181328, 0000507997) have none, which is right: their notices are in KRZ.
- **What MSiG adds to the registry, per entity:**
  - **0000070294:** a petition-stage order (temporary court supervisor) of 2017-02-09, a month before the
    declaration. The declaration itself was published 2017-03-17, three weeks before its registry entry
    (2017-04-07).
  - **0000277937:** the COVID-era simplified restructuring (arrangement day 2020-10-26, published 2020-10-29), nine
    months before anything in the registry.
  - **0000386777:** a 2018 sanacja petition with asset security (VI GR 27/18, 2018-08-24) and another in 2019
    (VI GR 40/18). The notice of 2020-04-17 both opens the sanacja and dismisses the bankruptcy petition.
  - **0000440028:** the date of the liquidation resolution, 2023-07-21, which the registry entry lacks.
  - **0000188883 and 0000397658:** the same events as the registry. The registry was earlier for 0000188883.
  - **0000225506:** a 2008 company-law notice only. Its 2022 bankruptcy is in KRZ, the gap ADR 0011 decision 4
    accepts.
- **Storage.** A notice's text is never stored (ADR 0009 addendum, item 3). The reduced record keeps what step F
  needs to type it. Re-typing under a new vocabulary means fetching again, which the extraction key
  (`msig_client.extraction_key`) triggers automatically.
