"""Two prefilter versions, compared offline — plan 0013, "Open for the owner", item 4 (2026-10-21).

A prefilter decides which pages an extractor reads, so a new one changes what the `text` job sends
to the model. Before any extractor version uses it, this measures it with no API call:

1. **On the committed golden set** (`evals/text_signals/`, masked text, labelled for every
   signal): per signal, the labelled positives, how many each version selects (its recall on the
   prefilter's side), and how many pages each selects at all.
2. **On every page of the local queues** (`LABELLING_DIR`, masked, labelled or not): pages selected
   per signal by each version, and the pages only the new one selects. A page selected for a
   signal is one model request for it, so the difference is the extra requests' share; the
   store-wide count comes from the `text` job's own read-only count.

Counts and page ids only; no page text is printed. Needs `make models` (the lemmatiser), not the
stores.

    uv run python notebooks/exploration/prefilter_comparison.py
"""

import marimo

__generated_with = "0.9"
app = marimo.App(width="medium")


@app.cell
def _():
    import json

    from distress_radar.extraction import golden
    from distress_radar.extraction.label_queue import queue_path, versions
    from distress_radar.extraction.preprocessing import (
        SIGNAL_TYPES,
        analyse,
        candidates,
        load_model,
        load_prefilter,
    )
    from distress_radar.settings import Settings

    OLD, NEW = "prefilter_v3", "prefilter_v4"
    old, new = load_prefilter(OLD), load_prefilter(NEW)
    changed = [s for s in SIGNAL_TYPES if old.signals[s] != new.signals[s]]
    nlp = load_model()
    print(f"{OLD} against {NEW}; signals whose terms differ: {changed}")

    def selected(text: str) -> tuple[set[str], set[str]]:
        sentences = analyse(text, nlp)
        return set(candidates(sentences, old)), set(candidates(sentences, new))

    return NEW, OLD, SIGNAL_TYPES, Settings, changed, golden, json, queue_path, selected, versions


@app.cell
def _(NEW, OLD, SIGNAL_TYPES, golden, json, selected):
    _pages = {
        (r := json.loads(line))["page_id"]: r["text"]
        for line in (golden.GOLDEN_DIR / golden.PAGES_FILE).read_text("utf-8").splitlines()
        if line.strip()
    }
    _by_page = {pid: selected(text) for pid, text in _pages.items()}
    print(f"\n1. The golden set: {len(_pages)} pages")
    print(f"   signal: positives, selected by {OLD} / {NEW}; pages selected {OLD} / {NEW}")
    for _signal in SIGNAL_TYPES:
        _positives = [
            r["page_id"]
            for line in (golden.GOLDEN_DIR / f"{_signal}.jsonl").read_text("utf-8").splitlines()
            if line.strip() and (r := json.loads(line))["present"]
        ]
        _o = sum(_signal in _by_page[p][0] for p in _positives)
        _n = sum(_signal in _by_page[p][1] for p in _positives)
        _po = sum(_signal in o for o, _ in _by_page.values())
        _pn = sum(_signal in n for _, n in _by_page.values())
        _mark = "  <- differs" if (_o, _po) != (_n, _pn) else ""
        print(f"   {_signal}: {len(_positives)}, {_o} / {_n}; {_po} / {_pn}{_mark}")


@app.cell
def _(NEW, OLD, Settings, changed, golden, queue_path, selected, versions):
    _settings = Settings()
    _rows = {
        row.page_id: row
        for v in versions()
        if (path := queue_path(_settings, v)).exists()
        for row in golden.load_queue(path.read_bytes())
    }
    print(f"\n2. The local queues: {len(_rows)} distinct pages")
    _by_page = {pid: selected(row.text) for pid, row in _rows.items()}
    for _signal in changed:
        _o = {p for p, (o, _) in _by_page.items() if _signal in o}
        _n = {p for p, (_, n) in _by_page.items() if _signal in n}
        print(
            f"   {_signal}: {OLD} {len(_o)}, {NEW} {len(_n)}; only {NEW} {len(_n - _o)}, "
            f"only {OLD} {len(_o - _n)}"
        )
        if _n - _o:
            print(f"      pages only {NEW} selects: {sorted(_n - _o)}")


if __name__ == "__main__":
    app.run()
