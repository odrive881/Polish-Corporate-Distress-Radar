# MSiG notices, reduced (plan 0008 step E)

Stored by the `msig_notices` asset on 2026-09-23 (Dagster run `26029b09`) and copied here from the raw store.
They are exactly what the pipeline stores. A notice's text is never stored: each file is the person-free record
`msig_client.reduce_notice` makes (ADR 0009 addendum, item 3). The search page is stored as received, because
it holds no personal fields. `tests/acquisition/test_legal_fixtures.py` is the gate.

| File | Chapter | Why it is here |
|---|---|---|
| `notice_0000070294_5420042.json` | III/9 | petition-stage order (temporary court supervisor), 2017-02-09, a month before the declaration |
| `notice_0000070294_5448048.json` | III/1 | bankruptcy declared 2017-03-08, published 2017-03-17, three weeks before the registry entry |
| `notice_0000070294_335644.json` | III/6 | bankruptcy proceeding ended 2021-07-27 |
| `notice_0000188883_6703250.json` | III/1 | bankruptcy declared 2014-01-21 (a 2003-act declaration, liquidating) |
| `notice_0000277937_2155799.json` | IX | COVID-era simplified restructuring announced; arrangement day 2020-10-26; not in the registry |
| `notice_0000277937_1274439.json` | IX | sanacja petition, asset security 2021-04-01 |
| `notice_0000277937_300337.json` | IX | sanacja opened 2021-07-02 |
| `notice_0000386777_4089653.json` | IX | a 2018 sanacja petition with asset security (VI GR 27/18); not in the registry |
| `notice_0000386777_3376311.json` | III/9 | bankruptcy-petition asset security 2019-10-10 |
| `notice_0000386777_2109633.json` | IX | sanacja opened 2020-04-17 on a bankruptcy petition's file (VI GU 751/19, VI GRs 3/20) |
| `notice_0000397658_935723.json` | I/2 | liquidation opened 2021-10-01 (creditors called) |
| `notice_0000440028_13039478.json` | I/2 | liquidation opened by resolution of 2023-07-21, a date the registry lacks |
| `notice_0000225506_8260486.json` | I/2 | a company-law notice that is not an outcome (2008, before the entity's distress) |
| `search_0000070294_page1.json` | — | one search page, as received |

Each notice record carries the vocabulary hash it was reduced with. When `config/mappings/msig_vocabulary.yaml`
changes, `test_fixture_records_match_the_current_vocabulary` fails. Re-materialize `msig_notices` and copy the
records again.
