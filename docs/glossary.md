# Glossary

Polish accounting and legal terms used in the data and the code. Keep the Polish term as the identifier — do not translate or rename.

| Term | Polish | English meaning |
|---|---|---|
| **KRS** | Krajowy Rejestr Sądowy | National Court Register — company registration |
| **NIP** | Numer Identyfikacji Podatkowej | Tax identification number |
| **REGON** | Rejestr Gospodarki Narodowej | Statistical identification number |
| **RDF** | Repozytorium Dokumentów Finansowych | Financial statement filing portal. Lookup-only, one entity at a time, not a bulk API |
| **KRZ** | Krajowy Rejestr Zadłużonych | Insolvency register, live since late 2021 |
| **MSiG** | Monitor Sądowy i Gospodarczy | Court and economic gazette; its notice base (from 2001) is searchable by KRS as JSON with text (ADR 0011) |
| **UoR** | Ustawa o rachunkowości | Accounting Act; defines size classes and statement formats |
| **KSH** | Kodeks spółek handlowych | Commercial Companies Code; source of the Art. 233/397 loss tripwires |
| **PKD** | Polska Klasyfikacja Działalności | Activity classification code (sector). 2007 and 2025 versions — cross-walk, don't assume stability |
| **sp. z o.o.** | Spółka z ograniczoną odpowiedzialnością | Limited liability company — the v1 legal form scope |
| **Jednostka Inna / Mała / Mikro** | jednostka inna, mała, mikro | The three MF statement structures: full form (UoR Annex 1), small entities' simplified form, micro entities' form |
| **KwotaA / KwotaB / KwotaB1** | kwota | Amount columns: current year, prior year, restated prior-year comparatives (`column` in the canonical table) |
| **Pozycja uszczegóławiająca** | pozycja uszczegóławiająca | A filer's own extra line inside a statement; captured as `….USER` totals |
| **Wariant porównawczy / kalkulacyjny** | rachunek zysków i strat | Income statement by nature (comparative) or by function (calculation) |
| **CRWDE** | Centralne Repozytorium Wzorów Dokumentów Elektronicznych | Government repository of e-document templates; publishes the 2025 statement structures |
| **Data dodania** | data dodania dokumentu | The date RDF published a document: its `known_from` (the detail's `dataDodania`). Never confuse it with *data sporządzenia* |
| **Data sporządzenia** | data sporządzenia dokumentu | The date the filer says the document was prepared; it precedes publication, so it is never `known_from` |
| **Korekta** | korekta dokumentu | A correction filed to RDF; reachable only inside the expanded row of the document it corrects, never a list row of its own |
| **Informacja dodatkowa** | informacja dodatkowa | The notes to the statement, usually a PDF attached inside the XML; Phase 7 reads their text layer |
| **Sprawozdanie z badania** | sprawozdanie z badania rocznego sprawozdania finansowego | The auditor's report (RDF type 19); its opinion is read by the report's KSB headings |
| **KSB** | Krajowe Standardy Badania | Polish auditing standards (ISA-based); KSB 700/705 fix the opinion headings the opinion rule reads |
| **Postępowanie naprawcze** | postępowanie naprawcze | Literally "recovery proceedings": the label KRS uses for a post-2016 *sanacja* (a restructuring), a trap for the outcome taxonomy |
