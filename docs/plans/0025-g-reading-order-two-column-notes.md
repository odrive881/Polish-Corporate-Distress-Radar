# 0025 — G: the reading order of notes laid out as a two-column table

**Stage:** G (text signals; the page text of plan 0013 step C), before plan 0013's K4 labelling resumes. **Status:
proposed (2026-11-10): written on the owner's request (option C of the three put to the owner the same day:
keep the text as it is, read every page with `sort=False`, or fix only the pages a rule detects); owner
decisions 1 and 2 taken (2026-11-10: v4 kept, the cost approved); decisions 3–8 this plan's own (flag any to change).
2026-11-10: step A done (Progress): 303 pages to reorder, method (d) chosen, 164 requests to send (about $0.72).**

- **Invariants:** 3 (lineage: a page's text records how it was read), 4 (no silent loss: a reordered page holds
  exactly the words it held before, none dropped or doubled), 5 (idempotence: the reading order is versioned, a
  new version is a new manifest row, and the same bytes give the same text), 6 (the page text stays raw until
  `extraction.masking`; tests use synthetic PDFs, no real names).
- **Spec:** AGENT_SPEC §5 (`text_signals`, `text_coverage`), §6 G ("As built": the sources' text, read by G1–G3).
- **Inputs:** the stored statements' embedded notes and the stored auditor reports; `text_signals` from run
  `be562e44` (`extractor_v7`, 2026-11-09); `golden_sample_v4`'s queue as built 2026-11-10 (2,357 pages, 2
  labelled).

## Why

The owner, labelling `golden_sample_v4`'s first pages (2026-11-10), found the text garbled: sentences cut off
and resumed at odd points, e.g.

```
10) Propozycje co do sposobu podziału Z uwagi na toczące się postępowanie upadłościowe nie
zysku lub pokrycia straty za rok obro- przedstawia się sposobu pokrycia straty.
towy
```

**Cause.** `page_text.pages` reads each page with PyMuPDF's `get_text("text", sort=True)`, which rebuilds the
page line by line from top to bottom across its full width. Many notes are a two-column table: the item and its
heading on the left, the company's answer on the right. Where both cells wrap, each output line is a piece of the
heading followed by a piece of the answer. The PDF is sound: it holds the answer as one block, `sort=False` reads
this page heading first and answer whole, and `page.find_tables()` (PyMuPDF 1.28.2, already locked) reads the row
as three cells: `10)`, the heading, and the answer intact.

**How much (read-only, 2026-11-10).** A page is counted when at least two consecutive printed rows hold running
text (three or more words) in two side-by-side places; a label beside its figures does not count, and neither
does a one-line answer such as "Nie dotyczy." beside a heading.
- `golden_sample_v4`'s queue: **63 of 2,357 pages** (2.7%), 17 more borderline. They cluster in a few companies'
  notes, likely one template: three entities hold 37 of them, and at least two of the companies are in
  bankruptcy.
- The labelled pages of `golden_sample_v1`, `v2` and `v4` (195): **3** affected, the 2 the owner labelled in v4
  and 1 seed page in v1.
- The model read the same text in the `text` run, so the defect touches the signals as well as the labelling,
  and it falls on the distressed companies' pages, where the positives are. Over the whole store it is not yet
  measured (step A).

## Owner decisions (taken 2026-11-10, as recommended)

1. **`golden_sample_v4` stays v4.** Its rates (set 2026-11-10) and its frame's definition do not change; a page's
   id is `(document_hash, source_member, attachment, page)`, not its text, so draw positions stay as they are.
   The 2 labels already saved, both on affected pages, are set aside (kept in the plan's Progress by page id, not
   exported) and the pages labelled again on the new text, first in draw order. *Alternative:* a new
   `golden_sample_v6` with v4's settings. *Recommended: keep v4.* **Taken (owner, 2026-11-10): v4 kept.** The rule that a change makes a new version
   guards against an allocation chosen after seeing labels; a reading-order fix depends on no label, and only 2
   exist. A page whose stratum changes after the re-run (step C) moves with its new stratum, recorded.
2. **The cost of re-sending the changed pages' requests.** Every request is keyed by a hash of its masked text,
   so only pages whose text changes are sent again; the rest replay. Step A counts them exactly, read-only, before
   anything runs. *Rough expectation:* a few hundred to a thousand requests, a few dollars at October's rate (868
   for $3.80); the owner approves the count step A prints. **Taken (owner, 2026-11-10): step A's 164 requests, about $0.72, approved.**

## Decisions this plan makes (flag any you disagree with)

3. **Only detected pages change.** The detection rule above, made precise and tested, picks the pages. Every
   other page is read exactly as now, with `sort=True`, byte for byte. That bounds the cost, keeps every other
   label's evidence verbatim, and is checked over the whole store in step C, not assumed.
4. **The reading method is chosen in step A by measurement, from three candidates:** (a) `find_tables()` cells,
   row by row and left to right, each cell's text whole, with the text outside the tables in `sort=True` order;
   (b) `sort=False` for the whole page; (c) a column split from the page's text blocks. The method kept must, on
   every detected page: leave no prose beside prose in its output; hold exactly the words `sort=True` held (the
   same multiset, invariant 4); and give the same string on every run. (a) is expected to pass where the table
   has rules and (b) where it has none; a combination is allowed if it is one rule, written down. Not
   considered: Docling (the PDF tier plan 0006 defers, and model-based), and the `pymupdf_layout` package PyMuPDF
   now suggests (a new dependency; ask first if step A finds no candidate passes).
5. **A reading-order version.** `page_text.READING_ORDER_VERSION` (`1` today, `2` with this plan) joins the run's
   manifest parts beside `masking=` and `text_page_chars=`, so the run that reads the new text is a new
   `ingestion_run_id`. `text_coverage` (one row per document and signal) gains `pages_reordered`, the count of the
   document's pages read by the new method, inside its counts-add-up check, so a later reader can tell which text
   a signal came from (invariant 3).
6. **No dehyphenation.** "obro- towy" stays as printed: joining hyphenated words would change nearly every page
   and re-send every request. Out of scope here.
7. **Labelled pages.** For each affected labelled page (3 today), every present label's evidence is checked
   against the new text. Verbatim still: the label stands. Not: the page goes back to unlabelled (v4) or to the
   owner to label again (v1's seed page), and the eval files are re-exported (`make label-export`) so they hold
   the new text.
8. **The eval gate.** `make check`'s gate (v1, hold or improve) runs on the re-exported eval files. With 1
   affected labelled page outside v4 it cannot show the fix's benefit; that is shown in step D on the affected
   pages themselves, and measured properly once v4's labels include them.

## Steps

### A. Census and the method (read-only, needs `make dev-up`)

1. The detection rule over every stored notes page and auditor-report page: pages detected, by entity, form and
   stratum, and how many are in `golden_sample_v4`'s frame.
2. Each candidate of decision 4 on every detected page, against its criteria; the counts printed, a handful of
   pages compared by eye with the owner (masked text only).
3. For the method kept: which pages' masked text changes (only detected ones, decision 3) and how many requests
   that re-keys, by diffing request keys against the response store. The cost to the owner (decision 2).

### B. Build (`src/distress_radar/extraction/page_text.py`)

- The detection rule and the method kept; `READING_ORDER_VERSION`; the manifest part
  (`text_signals.pipeline_hash`); `pages_reordered` and its contract (`extraction/contracts.py`, AGENT_SPEC §5).
- Tests (`tests/extraction/test_page_text.py`), on PDFs the tests draw with PyMuPDF, no real text: a two-column
  table whose cells both wrap is detected and read cell by cell; a label-and-figures table and a single-column
  page are not detected and read byte-identical to `sort=True`; a one-line answer beside a heading is not
  detected; the words of a reordered page equal `sort=True`'s; two runs give the same string.

### C. Run

1. The `text` job (decision 2's count approved first): the changed pages' requests sent, the rest replayed;
   `evidence_masked` and the other checks must pass. Then `features` (by the sensor chain, `feature_set_v8`),
   `leakage` must pass.
2. A whole-store check that only detected pages' text changed (decision 3).
3. Decision 7 on the 3 labelled pages; `make label-export`, `make eval`, `make check`.
4. `make label-queue` (about 50 minutes), then the owner resumes labelling.

### D. Result

For the detected pages: the signals answered present before and after, by signal and entity; the requests sent
and their cost; any page whose stratum in v4 changed.

### E. Docs sweep and close-out

Plan 0013 (step C's description, the K4 pick-up), AGENT_SPEC §6 G ("As built", Sources), README (pick-up, the text-signals line),
`docs/data_inventory.md` (the notes' status), and the owner's `docs/NEXT_STEPS.md`.

## Progress

- **2026-11-10:** written. Found and measured as in § "Why"; `find_tables()` checked on the owner's example page.
  Labelling of `golden_sample_v4` paused on the owner's affected pages until this plan's step C.
- **Step A (2026-11-10, read-only, scratch scripts; counts only, page text shown only masked).**
  - **A.1, census.** Every stored notes page and dated auditor report: 22,834 and 229 text pages. The detection
    rule of § "Why" first missed the owner's example company, whose columns are 5 points apart (the census asked
    for 8): the gutter is now any gap over 2 points. Detected: **345 pages in 93 entities' notes** (210 pages, 47
    entities in list v3's draw); no auditor report. Of these, `sort=True` actually puts two different side-by-side
    segments on one output line on **303**; the other 42 read correctly now. So the rule that picks a page is the
    measured fault itself: two consecutive rows of prose side by side **and** `sort=True` joining a pair of them
    (a pair whose texts are equal, or one inside the other, as in a current-year and a prior-year column, cannot
    be judged this way and is not counted). The most affected: …3781 22 pages, …9502 13, …5448 13, …6563 12,
    …3537 12.
  - **A.2, the method.** On the 345 pages: (a) `find_tables()` with `sort=True` bands passed 216 (31 still
    interleaved, 74 with text doubled where a band overlapped a table, 37 with no table found); (b) `sort=False`,
    (c) blocks by column, and **(d)** passed all 345 on the three criteria (no interleaving, the same characters,
    the same string twice). (d), added in step A, is one rule combining (a) and (c): every text line of the page
    is emitted exactly once; a line inside a `find_tables()` cell goes with its cell (rows top to bottom, cells
    left to right), the rest is split into bands by vertical whitespace and each band read column by column. The
    criteria could not tell (b), (c) and (d) apart (they agree word for word on 43 of 303 pages), so the pages
    where they differ most were read, masked: on a ruled table (…7849, an off-balance-sheet register) (b) reads
    column by column, every title, then every amount, then every method, and moves the page's heading; (d) reads
    it row by row, heading first. (d) also reads the owner's example heading first and the answer whole, and
    leaves a page without a table as `sort=True` had it. **(d) is the method kept** (decision 4's "a combination
    is allowed if it is one rule").
  - **A.3, the cost.** The 303 pages' masked text all changes. Today they make 162 model requests, all found in
    the response store (so the keys were rebuilt exactly). With (d) they make **164, all new**: 57
    `going_concern_uncertainty`, 46 `post_balance_sheet_event`, 36 `loss_coverage_resolution`, 15 `litigation`,
    9 `continued_existence_vote`, 1 `key_customer_loss`; 127 of them on the draw's entities. The prefilter's
    selection changes on 8 pages. At October's rate (868 for $3.80): **about $0.72** (decision 2).

## What this plan does not do

- Join hyphenated words (decision 6), or read scanned pages (`needs_ocr`, plan 0006's tier).
- Change the masker, the prefilter, the prompts or the model: a request changes only because its page's text did.
- Fix a heading split by a one-line answer beside it ("warun- Nie występują. / kowych"): the answer, which the
  signals read, is intact there, and the heading is not evidence for any signal.
