# Data Inventory

Everything the pipeline has to get hold of, gathered from the spec documents in one place: entity identifiers, financial filings, registry and legal documents, reference data, hand-built materials, and the access credentials for each source.

Sources: `AGENT_SPEC.md`, `DIRECTORY_STRUCTURE.md`, `README.md`, `CLAUDE.md`, `docs/PROJECT_OVERVIEW.md`, `docs/TECHNICAL_ARCHITECTURE.md`, with `.env.example` and `docs/adr/` checked for status. If this file and the spec disagree, the spec wins. Fix this file.

Stage codes use the letter scheme from `AGENT_SPEC.md` §6. The numbered stage (1–12) from `docs/PROJECT_OVERVIEW.md` is in brackets. Status was first compiled on 2026-09-14; rows are updated as plans land (latest: plan 0010 step H, 2026-09-26).

---

## 1. Entity identifiers and registry attributes

These are the "credentials" of a company: they decide whether it belongs in the universe and join every other dataset together.

| Item | Polish | Source | Stage | Feeds | Req. | Spec ref | Status |
|---|---|---|---|---|---|---|---|
| KRS number (10 chars, zero-padded) | numer KRS | A1 discovery, confirmed by BIR1 / KRS extract | A1, A2 (1, 2) | primary key of every dataset | required | SPEC §5, §6A | 17-entity seed in `config/segments/construction_sme_v1_seed.yaml` |
| NIP | Numer Identyfikacji Podatkowej | GUS BIR1, KRS extract | A2 (2) | `entity_master`, `financial_statements_canonical` | required | SPEC §5; OVERVIEW stage 2 | BIR1 adapter built |
| REGON | Rejestr Gospodarki Narodowej | GUS BIR1 | A2 (2) | `entity_master`, `financial_statements_canonical` | required | SPEC §5; OVERVIEW stage 2 | BIR1 adapter built |
| Legal form (must be `sp. z o.o.`; S.A. only for future Art. 397 scope) | forma prawna | GUS BIR1 (form symbol), KRS extract | A2 (2) | segment filter | required | SPEC §1, §4.5 | BIR1 adapter built |
| Entity type: legal entity vs natural person | typ podmiotu | GUS BIR1 (`Typ`) | A2 (2) | acquisition filter. Natural persons are never stored | required | SPEC §2.6, §12 | filter covered by synthetic fixture |
| Registry status: active / deregistered, and deregistration date | status, data wykreślenia | GUS BIR1, KRS extract | A2, A4 (2, 7) | `entity_master`, `silent_exit` label | required | SPEC §4.6, §6A; OVERVIEW stages 2, 7 | partial (BIR1) |
| PKD codes: predominant and all registered | kody PKD | GUS BIR1, KRS extract | A2 (2) | segment membership | required | SPEC §6A; OVERVIEW stage 2 | BIR1 adapter built |
| PKD 2007 ↔ PKD 2025 crosswalk | — | official classification tables → `config/mappings/pkd_crosswalk.yaml` | A2 (2) | consistent sector membership over time | required | SPEC §6A; DIR §1 | not yet written |
| Registered office / voivodeship | siedziba, województwo | GUS BIR1, KRS extract | A2, A4 (2, 7) | regional dashboards, peer comparison, office-move feature | required | SPEC §6H; OVERVIEW stages 7, 9, 12 | partial: BIR1 for the current office; moves from the KRS extract as `office_moved` events (plan 0010 step B) |
| Share capital (current and history) | kapitał zakładowy | KRS extract; also the equity section of the statement | A4, C (4, 7) | Art. 233 tripwire, capital-change events | required | SPEC §4.5, §6H | changes built as `capital_changed` events from the KRS extract (plan 0010 step B); Art. 233 reads share capital from the statement |
| Management board composition (counts only) | skład zarządu | KRS extract | A4 (7) | board turnover feature. Only pseudonymised counts are stored, never names | required | SPEC §6H, §12 | built as `board_changed` events (plan 0010 step B): a member joining or leaving, from the redacted extract. No names, and no composition, are stored |
| Source-conflict records (e.g. REGON vs KRS PKD) | — | derived | A2 (2) | `entity_reconciliation_log` | required | SPEC §6A | not started |

**Deliberately not taken from registries:** size class. It is computed per fiscal year from the filed statements (SPEC §4.4). Names of natural persons are also excluded (SPEC §2.6, §12).

---

## 2. Financial filings (RDF, per entity)

All documents in this section come from the Repozytorium Dokumentów Finansowych, looked up one KRS number at a time (A3 / stage 3). Raw bytes go to MinIO before any parsing (SPEC §2.2, §6B).

> **Status:** RDF is behind an Imperva Incapsula WAF that blocks plain HTTP clients. ADR 0007 option C (accepted) reaches it through a human-paced Playwright browser at 3 requests/minute, which KRS support informally confirmed is permitted. Plan 0003 builds it: every listed document is indexed with its detail (submission date included), and annual financial statements and their corrections are downloaded (`config/mappings/rdf_document_types.yaml`). The flow as observed: ADR 0007 addendum, 2026-09-16. **The live run was then shown an hCaptcha**, so until another access route exists, documents are captured by hand as browser recordings and imported (`README.md` § "Manual RDF capture").

### 2.1 Filing metadata

| Item | Feeds | Req. | Spec ref |
|---|---|---|---|
| Filing index per KRS number: document list, document types, fiscal period | `filing_index` | required | SPEC §6A; OVERVIEW stage 3 |
| **RDF submission date** for each document | `known_from` on every financial fact, which drives all point-in-time logic | required | SPEC §4.7; OVERVIEW stage 3 |
| Correction / re-filing markers | `restatement_events`, "corrections" filing-behaviour feature | required | SPEC §4.3, §6H |
| Missing years / gaps in the filing sequence | filing-behaviour features, `filing_overdue` alerts, survivorship handling | required | SPEC §4.7, §6H |

### 2.2 Annual financial statement — XML (Ministry of Finance structures)

| Structure | Mapping spec | Req. | Spec ref | Status |
|---|---|---|---|---|
| Full form (UoR Annex 1, `JednostkaInna`), schemas 1-0 and 1-2 | `full-2018-v1-0.yaml`, `full-2018-v1-2.yaml` | required | DIR §1; SPEC §6C | **mapped** (plan 0004); golden fixtures in `tests/fixtures/statements/` |
| Full form in thousands of złoty, schema 1-2 | `full-2018-v1-2-tys.yaml` | required | SPEC §4.2 | **mapped**; no seed filing uses it, tested with a synthetic document |
| Full form, schema 1-3 (FY2024, namespace dated 2025-01-01) | `full-2025-v1-3.yaml` | required | ADR 0005 addendum | **mapped** (plan 0005 step A); shares the 1-2 body, element trees are identical; fixture `statements/full_2025_v1_3_por_2024.xml` |
| New generation, fiscal years starting ≥ 2025-01-01 (CRWDE template 13817, "wariant 2 / wersja 1-0E") | `full-2025-w2-v1-0.yaml` | required | ADR 0005; SPEC §11.2 | **mapped**; fixtures `neobis_001.xml`, `statements/full_2025_w2_kalk_2025.xml` |
| Small / simplified form (`JednostkaMala`), schemas 1-0, 1-2, 1-3 | `small-2018-v1-0.yaml`, `small-2018-v1-2.yaml`, `small-2025-v1-3.yaml` | required | DIR §1; SPEC §4.1; ADR 0005 second addendum | **mapped** (plan 0005); 29 seed statement files parsed. One body (`jednostka_mala`) serves all versions, but a small envelope may carry the **full-form** statements instead, chosen per statement, so each spec accepts both (plan 0005 step D) |
| Micro form (`JednostkaMikro`), schemas 1-0, 1-2, 1-3, CRWDE template 13821 | `micro-2018-v1-0.yaml`, `micro-2018-v1-2.yaml`, `micro-2025-v1-3.yaml`, `micro-2025-w2-v1-0.yaml` | required (entities can switch form between years) | DIR §1; SPEC §4.1; ADR 0005 second addendum | **mapped** (plan 0005); 12 seed statements. Two bodies: 1-3 and wariant 2 drop the `G` block, and the micro income statement is a third layout, neither comparative nor calculation |
| Thousands-of-złoty twins of every form | one YAML per version | required | SPEC §4.2 | XSDs vendored and catalogued; none seen in the seed. Only `full-2018-v1-2-tys` is mapped, to keep unit normalisation under test |

Every XML statement must yield these components:

| Component | Polish | Variants to handle | Feeds |
|---|---|---|---|
| Balance sheet | bilans | full / small / micro | `statement_type = balance_sheet`; size class (balance sheet total); tripwires |
| Income statement | rachunek zysków i strat | **comparative** (by nature) or **calculation** (by function). Never derive one from the other | `income_statement`; size class (net revenue); ratios |
| Cash flow statement | rachunek przepływów pieniężnych | **direct** or **indirect** | `cash_flow`; `cashflow_ties` check |
| Statement of changes in equity | zestawienie zmian w kapitale własnym | — | `equity_changes`; supplementary/reserve capital and accumulated losses for Art. 233 |
| Current-year **and** prior-year columns | dane za rok bieżący / poprzedni | both captured | `prior_year_consistency` check → `restatement_events` |
| Declared unit | PLN / tys. PLN | normalise to złoty; quarantine if missing | all monetary values (SPEC §4.2) |
| Average employment | przeciętne zatrudnienie | — | size classification (SPEC §4.4) |
| Official XSD for the structure version | — | — | C1 validation (SPEC §6C) — **collected**: 53 files in `config/xsd/` with `catalog.yaml` (URL, SHA-256); 51 on 2026-09-17, plus CRWDE templates 13818 and 13819 on 2026-09-20 |

### 2.3 Annual financial statement — PDF / scans

| Item | Handled by | Req. | Spec ref |
|---|---|---|---|
| Statements filed as PDF (e.g. IFRS filers, non-XML attachments) | C3 tier: PyMuPDF → Docling → vision LLM | required (never silently dropped) | SPEC §6C3; OVERVIEW stage 4 — **tier deferred 2026-09-21** (plan 0006): the seed's one PDF statement is recorded `needs_pdf_tier` and its figures are recoverable from the next filing's comparative column |
| Scanned / degraded statements | C3 vision-LLM tier | required | SPEC §6C3 |

### 2.4 Accompanying documents (text signals)

| Document | Polish | Signals extracted (`signal_type`) | Req. | Spec ref |
|---|---|---|---|---|
| Auditor's report | sprawozdanie z badania | `opinion_type`, `going_concern_uncertainty`, `emphasis_of_matter` | required where audited | SPEC §5, §6G; OVERVIEW stage 6 |
| Auditor identity per year | biegły rewident / firma audytorska | "auditor change" feature | required | SPEC §6H |
| Management report | sprawozdanie z działalności | `going_concern_uncertainty`, `covenant_breach`, `key_customer_loss`, `litigation`, `post_balance_sheet_event` | required | SPEC §5; OVERVIEW stage 6 |
| Notes / additional information | informacja dodatkowa | same as management report | required | OVERVIEW stage 6 |
| Resolution approving the financial statement | uchwała o zatwierdzeniu sprawozdania finansowego | approval date (filing-lag feature) | required | OVERVIEW stage 3 |
| Resolution on profit allocation / loss coverage | uchwała o podziale zysku / pokryciu straty | `loss_coverage_resolution`; loss coverage history | required | SPEC §5; OVERVIEW stages 6, 9 |
| Resolution on the company's continued existence (Art. 233 KSH) | uchwała o dalszym istnieniu spółki | `continued_existence_vote` | required where it exists | SPEC §4.5, §5 |
| Corrections of any of the above | korekty | restatement / corrections features | required | OVERVIEW stage 3 |

---

## 3. Registry and legal-event documents

| Item | Polish | Source | Stage | Event types needed | Feeds | Req. | Spec ref | Status |
|---|---|---|---|---|---|---|---|---|
| Full KRS extract with history | odpis pełny z KRS | KRS (Ministry of Justice) | A2, A4 (2, 7) | board changes, share capital changes, registered office moves, PKD changes, liquidation opened, deregistration | `legal_events` (source `KRS`), registry-dynamics features, `liquidation` / `silent_exit` labels | required | OVERVIEW stages 2, 7; SPEC §5 | built (plan 0008): open KRS API, redacted (`krs-json-2`), all 17 seed extracts; proceedings, liquidation, deregistration, registration, arrears and curators mapped (plan 0008); board, capital and office changes mapped (plan 0010 step B); PKD changes not yet |
| KRZ insolvency and restructuring notices (late 2021 onward) | Krajowy Rejestr Zadłużonych | KRZ | A4 (7) | bankruptcy petition filed, bankruptcy declared, petition dismissed for insufficient assets, arrangement approval, accelerated arrangement, arrangement proceedings, remedial proceedings; plus `proceeding_id` and publication date | `legal_events` (source `KRZ`), `outcome_labels` | required | SPEC §4.6, §6A | not built: behind an Imperva WAF (ADR 0011 decision 4); manual capture or a sanctioned channel only |
| MSiG notices (from 2001; JSON with text) | Monitor Sądowy i Gospodarczy | MSiG search API | A4 (7) | same event types as KRZ, plus COVID-era simplified restructuring (2020–21, drives `regime_flag`), liquidation notices | `legal_events` (source `MSiG`), `outcome_labels` | required | SPEC §4.6; ADR 0011 | built (plan 0008): 49 seed notices, stored as person-free records and typed by `config/mappings/msig_notice_kinds.yaml` |
| GUS BIR1 reports: search result + full legal-entity report | — | GUS REGON BIR1 (SOAP) | A2 (2) | — | `entity_master` | required | SPEC §6A | built; fixtures in `tests/fixtures/bir1/` |

The KRZ and MSiG sources both contain consumer bankruptcies. Natural persons must be filtered out at acquisition (SPEC §2.6). Events that describe the same proceeding in both sources are deduplicated through `dedup_group_id` (SPEC §4.6, §5).

---

## 4. Universe-discovery inputs

| Item | Source | Stage | Feeds | Req. | Spec ref | Status |
|---|---|---|---|---|---|---|
| Candidate KRS lists | commercial registry aggregators, used within their terms of service | A1 (1) | `universe_candidates` (with `discovery_source`, `discovered_at`) | required | SPEC §6A; OVERVIEW stage 1 | aggregator not chosen; hand-picked seed in use |
| New-registration feeds | registry feeds | A1 (1) | `universe_candidates` | optional | OVERVIEW stage 1 | not started |
| Official entity counts by PKD section and employment band | GUS aggregate statistics | A1 (1) | coverage sanity check / report | required | OVERVIEW stage 1 | not started |
| Segment spec | `config/segments/construction_sme_v1.yaml` | A1 (1) | universe filter | required | SPEC §6A | present |

The spec forbids bulk enumeration of any source. Acquisition is per entity, seeded from `universe_candidates` (SPEC §6A).

---

## 5. Reference, macro and statutory data

| Item | Source | Stage | Stored as | Feeds | Req. | Spec ref | Status |
|---|---|---|---|---|---|---|---|
| NBP reference rate history | NBP web API (JSON) | A5 | raw → Parquet | macro features | required | SPEC §6A, §6H | not started; macro and sector features deferred to their own plan (plan 0010 owner decision 5) |
| Sector financial aggregates (construction) | GUS BDL (JSON) | A5 | raw → Parquet | macro / sector features, dashboards | required | SPEC §6H | not started |
| Regional indicators by voivodeship | GUS BDL | A5 | raw → Parquet | regional features, heatmaps | required | SPEC §6H; OVERVIEW stage 12 | not started |
| Eurostat series | Eurostat | A5 | raw → Parquet | macro context | optional | TECH_ARCH §3 A5 | not started |
| Official MF XSD schemas, one per structure version | Ministry of Finance | C1 | committed reference files | XSD validation | required | SPEC §6C1 | collected (`config/xsd/`, plan 0004; small, micro and the two remaining CRWDE templates added in plan 0005) |
| UoR size-class thresholds (balance sheet total, revenue, average employment; multi-year rule), dated | Ustawa o rachunkowości | E / H | `config/statutory/size_thresholds.yaml` | `entity_size_class_history` | required | SPEC §4.4 | not written: deferred with `entity_size_class_history` (plan 0010 owner decision 4), because average employment has no source (gap 8). Balance-sheet total and revenue enter `feature_store` as raw inputs meanwhile |
| KSH tripwire ratios: Art. 233 (sp. z o.o., ½ share capital), Art. 397 (S.A., ⅓ share capital), dated | Kodeks spółek handlowych | H | `config/statutory/ksh_tripwires.yaml` | tripwire features, `tripwire_triggered` alerts | required | SPEC §4.5 | written (plan 0010 step B): Art. 233 excludes the revaluation reserve, a choice recorded in the file |
| Annual statement filing deadlines: approval within 6 months, filing within 15 days (UoR art. 53, 69), COVID-era extensions (Dz.U. 2020 poz. 570 as amended), dated | Ustawa o rachunkowości; MF regulation | H | `config/statutory/filing_deadlines.yaml` | missing-year and late-filing features | required | plan 0010 decision 4 | written (plan 0010 step B), checked against the consolidated regulation |
| Insolvency / restructuring procedure taxonomy, dated | Prawo upadłościowe, Prawo restrukturyzacyjne, COVID-era acts | F | `config/statutory/procedure_taxonomy.yaml` | `outcome_labels` | required | SPEC §4.6 | written: KRS (plan 0008 step B), MSiG (plan 0008 step F), registry changes (plan 0010 step B) |
| Canonical chart of line items | built from the UoR annexes | C2 | `config/mappings/canonical_chart.yaml` | every mapping spec | required | SPEC §5, §6C2 | written (plan 0004), extended for the small and micro forms (plan 0005 step C) |
| Altman Z-score variants and Polish discriminant model coefficients | academic literature | I | model code / config | baseline models | required | SPEC §6I | not collected |

---

## 6. Hand-built and golden materials

| Item | Location | Req. | Spec ref | Status |
|---|---|---|---|---|
| Golden XML statements, at least one per structure version, each resolving every `required: true` mapping | `tests/fixtures/` | required | SPEC §6C2, §9.2 | 11 of the 12 mapped versions have one (`tests/fixtures/statements/`), small and micro included; `full-2018-v1-2-tys` has none by design (no seed filing uses it) and is tested with a synthetic document |
| Known-bad statements that must be quarantined (unbalanced, missing unit) | `tests/fixtures/` | required | SPEC §9.2 | covered by altered golden fixtures in `tests/parsing/test_accounting_identities.py` (`test_known_bad_statement_is_quarantined`, `test_altered_micro_total_assets_quarantines`); no committed known-bad file |
| Statement declared in thousands of złoty | `tests/fixtures/` | required | SPEC §9.2 | synthetic document in the tests (`full-2018-v1-2-tys`); no seed filing uses the unit |
| One comparative and one calculation income-statement filing | `tests/fixtures/` | required | SPEC §9.2 | present: `*_por_*` and `*_kalk_*` in `tests/fixtures/statements/`, every form |
| Statement pair with a restated prior-year column | `tests/fixtures/` | required | SPEC §4.3 | present: `full_2018_v1_2_por_2022` / `_2023` (`test_real_consecutive_years_restatements`) |
| Golden PDFs: text layer, table-heavy, scanned | `tests/fixtures/` | required | SPEC §6C3 | missing |
| Hand-labelled text-signal eval sets, one per `signal_type` (9 files) | `evals/text_signals/<signal_type>.jsonl` | required | SPEC §6G3; DIR §5 | missing |
| KRS / MSiG fixtures for each outcome class, including a cross-source duplicate and a consumer bankruptcy to filter out | `tests/fixtures/legal/` | required | SPEC §9.2 | present (plan 0008): 4 redacted KRS extracts, 13 MSiG notice records and a search page; cross-source duplicates in `test_legal_events.py`. Search is by KRS, so no consumer record can arrive; a notice for another KRS is quarantined unstored (`test_msig_client.py`). KRZ none (not built) |
| BIR1 responses | `tests/fixtures/bir1/` | required | — | present |

---

## 7. Access credentials and service accounts

| Credential / access | Env var | Stage | Req. | Status |
|---|---|---|---|---|
| GUS BIR1 **production** API key (issued by GUS on request) | `GUS_BIR1_API_KEY`, `GUS_BIR1_ENDPOINT=prod`, `BIR1_REQUESTS_PER_MINUTE` | A2 | required for real data | obtained 2026-09-16; set in the local `.env` (gitignored), verified with one production lookup |
| RDF portal access | `RDF_REQUESTS_PER_MINUTE`, `RDF_MANUAL_INBOX` | A3 | required | no login; WAF blocks plain HTTP, and automated browsers get a CAPTCHA (ADR 0007), so captured by hand for now |
| KRS extract access | `KRS_API_REQUESTS_PER_MINUTE` | A2, A4 | required | open KRS API, no key; open-data act basis, no published limit, 15/min here (ADR 0011) |
| KRZ access | — | A4 | required | Imperva WAF; no automated access (ADR 0011); a sanctioned channel is the owner's to pursue |
| MSiG search access | `MSIG_REQUESTS_PER_MINUTE` | A4 | required | public JSON API, no key, no terms page; per entity, 15/min (ADR 0011) |
| NBP API | — | A5 | required | public, no key |
| GUS BDL API (optional client key raises rate limits) | none yet; add one if a key is used | A5 | optional | not requested |
| Registry aggregator account / ToS acceptance | none yet | A1 | required for scaled discovery | aggregator not chosen |
| LLM API key (vision tier and text extraction) | none yet; must be added to `.env.example` | C3, G2 | required from Phase 3 / 7 | not configured |
| Postgres | `POSTGRES_HOST/PORT/DB/USER/PASSWORD` | B, J | required | local Docker Compose |
| MinIO | `MINIO_ENDPOINT/ACCESS_KEY/SECRET_KEY/BUCKET` | B | required | local Docker Compose |
| MLflow tracking server | `MLFLOW_TRACKING_URI` | I, J | required from Phase 6 | not deployed |
| Sentry DSN | `SENTRY_DSN` | L | optional | not configured |
| SOPS `age` key for encrypted committed config | — | platform | required for committed secrets | not set up |
| GitHub Actions secrets | — | CI | required | — |
| Static-site deploy token (GitHub Pages or Cloudflare Pages) | — | K2 | required from Phase 9 | later |
| Cloudflare R2 credentials (hosted object store option) | S3-compatible `MINIO_*` equivalents | B | optional | later |

---

## 8. Open gaps found while compiling this list

1. **RDF access rests on an informal confirmation** (ADR 0007). KRS support allowed 3 requests/minute verbally, and could not promise how the WAF reacts. Re-run the probe notebook before any backfill.
2. ~~**No source named for average employment.**~~ Answered by C2 in plan 0004: no MF structure carries it as a field. Superseded by item 8, which records the candidate sources and what is still undecided.
3. ~~**No pre-2025-generation XML fixture.**~~ Resolved in plan 0004: golden fixtures for schemas 1-0 and 1-2. ~~Small and micro forms still have none.~~ Ten short-form fixtures added in plan 0005 step E, covering every body-choice case; a committed fixture carrying signer data is now a test failure, not a manual check (`test_no_fixture_contains_personal_data`).
4. **No LLM provider or key** in `.env.example`, yet C3 and G2 both need one.
5. ~~**Terms of use unconfirmed** for KRS, KRZ, MSiG~~ Recorded in ADR 0011 (accepted 2026-09-23); KRZ is WAF-blocked. Any aggregator's terms remain unconfirmed (SPEC §11.3).
6. ~~**Full list of MF structure versions not enumerated.**~~ Enumerated in plan 0004 and completed in plan 0005 step B: 22 (form × unit × schema 1-0/1-2/1-3, plus CRWDE templates 13817, 13818, 13819 and 13821; 13820 is `JednostkaOp`, outside v1 scope), listed in `config/mappings/structures/` (12 mapped) and `structure_catalog.yaml` (10 recognised, not mapped).
7. ~~**Stale wording** about structures applying "from 2026".~~ Fixed in plan 0004.
8. **Average employment has no structured source.** No MF structure carries it as a field.
   - **Possible source (owner, 2026-09-17): the management report** (*Sprawozdanie zarządu / Sprawozdanie z działalności*, RDF types 20 and 5), which states employment and can be downloaded from RDF like the statements. It is noted for future use and **probably out of v1 scope**.
   - **Caveat: coverage is worst exactly where it matters.** In the 17-entity seed, all 9 companies with a distress hint file these reports years late (last one for 2018–2020) or not at all. 6 of the other 8 are current through 2025.
   - **Candidate feature:** a management-report gap could be a filing-behaviour signal alongside the statement gaps (SPEC §6H).
   - **Why it may be a false correlation:** micro entities are exempt from the report, and small entities can be under UoR art. 49. So a missing report is not always a lapse; e.g. `0000041651` files micro-form statements and has no reports since 2017. Any such feature must be conditioned on the entity's size class.
   - **Until decided,** size classification (§4.4) needs another route: employment from the attached notes (C3/G), or GUS employment bands as a proxy.
   - **Consequence (plan 0010 owner decision 4, 2026-09-24):** `entity_size_class_history` and `size_thresholds.yaml` are deferred, and Phase 5's features carry no size class. Classifying on two of the three statutory inputs was rejected.
9. **RDF's `czyMSR` flag (`filing_index.is_ifrs`) is unreliable.** 29 seed statements flagged IFRS are UoR structures. Never route on it.
10. ~~**Raw downloads contain natural persons' data.**~~ Decided 2026-09-17: signer data is redacted at acquisition, before hashing, and stored files were replaced (ADR 0009). Residual: free text in notes, and signatories' first names in some uploaded file names (plan 0011, draft).
11. **Notes and user-defined breakdowns are not captured yet.** Attached notes (`Plik`, base64 PDFs/docs) wait for C3/G. Breakdowns of a single line (`PozycjaUszczegolawiajaca` inside a leaf, `Podpozycja`) are skipped because the line's total is already a fact.
