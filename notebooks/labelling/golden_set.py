"""Label the golden set — plan 0013 step E (decision 5: the owner labels).

Reads and writes a local queue `<LABELLING_DIR>/<version>.jsonl` that
`python -m distress_radar.extraction.label_queue build` makes, one per golden sample: the notes
(`golden_sample_v1`) or the auditor reports (`golden_sample_v2`), chosen at the top. For each page: read the masked
text, mask by hand any name the masker missed (highlighted in red when the masker itself would
still replace it), then label every signal_type as present or absent, with a verbatim evidence
span for each present one. Definitions: `evals/text_signals/labelling_guide.md`. Candidate
sentences the prefilter matched are highlighted in yellow; they are hints, not labels.

Nothing here leaves the machine. `label_queue export` writes the labelled pages to `evals/`.

    uv run marimo edit notebooks/labelling/golden_set.py
"""

import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium")


@app.cell
def _():
    import html

    import marimo as mo

    from distress_radar.extraction import golden, masking
    from distress_radar.extraction.label_queue import (
        DEFAULT_VERSION,
        queue_path,
        update_queue,
        versions,
    )
    from distress_radar.extraction.preprocessing import SIGNAL_TYPES
    from distress_radar.settings import Settings

    _queues = {v: queue_path(Settings(), v) for v in versions()}
    sample_picker = mo.ui.dropdown(
        options={v: str(q) for v, q in _queues.items() if q.exists()},
        value=DEFAULT_VERSION if _queues[DEFAULT_VERSION].exists() else None,
        label="golden sample",
    )
    nlp = masking.load_model()
    get_rev, set_rev = mo.state(0)
    return (
        SIGNAL_TYPES,
        get_rev,
        golden,
        html,
        masking,
        mo,
        nlp,
        sample_picker,
        set_rev,
        update_queue,
    )


@app.cell
def _(mo, sample_picker):
    from pathlib import Path as _Path

    mo.output.replace(sample_picker)
    mo.stop(sample_picker.value is None, mo.md("No queue yet: run `make label-queue`."))
    path = _Path(sample_picker.value)
    return (path,)


@app.cell
def _(get_rev, golden, mo, path):
    get_rev()  # reload after every save
    rows = golden.load_queue(path.read_bytes())
    _done = sum(r.complete for r in rows)
    _next = next((r.page_id for r in rows if not r.complete), rows[0].page_id)
    options = {
        f"{i:3d} {'✓' if r.complete else '·'} {r.sample:8} {r.page_id}": r.page_id
        for i, r in enumerate(rows, start=1)
    }
    picker = mo.ui.dropdown(
        options=options,
        value=next(k for k, v in options.items() if v == _next),
        label=f"page ({_done} of {len(rows)} labelled)",
        full_width=True,
    )
    mo.output.replace(picker)
    return picker, rows


@app.cell
def _(golden, html, mo, nlp, picker, rows):
    from itertools import pairwise as _pairwise

    row = next(r for r in rows if r.page_id == picker.value)
    remaining = golden.remaining(row.text, nlp)
    _marks = [(s, e, "#fde68a") for spans in row.candidates.values() for s, e in spans]
    _marks += [(s, e, "#fca5a5") for s, e, _kind in remaining]
    _bounds = sorted({0, len(row.text), *(b for s, e, _c in _marks for b in (s, e))})
    _parts: list[str] = []
    for _a, _b in _pairwise(_bounds):
        _colours = [c for s, e, c in _marks if s <= _a and _b <= e]
        _chunk = html.escape(row.text[_a:_b])
        _parts.append(
            f'<span style="background:{_colours[-1]}">{_chunk}</span>' if _colours else _chunk
        )
    mo.vstack(
        [
            mo.md(
                f"**{row.sample}** · attachment {row.attachment}, page {row.page} · "
                f"prefilter: {', '.join(row.candidates) or 'none'} · masked {row.masked}"
                f" · by hand {row.hand_masked or {}}"
            ),
            mo.callout(
                mo.md(f"The masker would still replace {len(remaining)} span(s), in red."),
                kind="danger",
            )
            if remaining
            else mo.md(""),
            mo.Html(
                '<pre style="white-space:pre-wrap;font-size:0.85em;max-height:36em;'
                f'overflow:auto">{"".join(_parts)}</pre>'
            ),
        ]
    )
    return (row,)


@app.cell
def _(masking, mo):
    mask_target = mo.ui.text(label="text to mask (exact)", full_width=True)
    mask_kind = mo.ui.dropdown(options=sorted(masking.TOKENS), value="person", label="as")
    mask_button = mo.ui.run_button(label="Mask on this page")
    mo.hstack([mask_target, mask_kind, mask_button], widths=[6, 1, 1])
    return mask_button, mask_kind, mask_target


@app.cell
def _(golden, mask_button, mask_kind, mask_target, mo, path, row, set_rev, update_queue):
    mo.stop(not mask_button.value)
    try:
        update_queue(path, golden.hand_mask(row, mask_target.value, mask_kind.value))
        set_rev(lambda n: n + 1)
    except ValueError as _exc:
        mo.output.replace(mo.callout(str(_exc), kind="warn"))


@app.cell
def _(SIGNAL_TYPES, golden, mo, row):
    def _state(signal: str) -> str | None:
        label = row.labels.get(signal)
        return None if label is None else ("present" if label.present else "absent")

    presence = mo.ui.dictionary(
        {s: mo.ui.radio(["absent", "present"], value=_state(s), inline=True) for s in SIGNAL_TYPES}
    )
    evidence = mo.ui.dictionary(
        {
            s: mo.ui.text_area(
                value=(row.labels[s].evidence or "") if s in row.labels else "",
                placeholder="verbatim span of the page, when present",
                full_width=True,
                rows=2,
            )
            for s in SIGNAL_TYPES
        }
    )
    # A value for each signal that takes one (opinion; kind of event, decision 9), when present.
    values = mo.ui.dictionary(
        {
            s: mo.ui.dropdown(
                options=list(golden.SIGNAL_VALUES[s]),
                value=row.labels[s].value if s in row.labels else None,
                label="value",
            )
            for s in SIGNAL_TYPES
            if s in golden.SIGNAL_VALUES
        }
    )
    labelled_by = mo.ui.text(value=row.labelled_by or "owner", label="labelled by")
    save_button = mo.ui.run_button(label="Save labels")
    mo.vstack(
        [
            *(
                mo.hstack(
                    [
                        mo.md(f"`{s}`"),
                        presence[s],
                        *([values[s]] if s in golden.SIGNAL_VALUES else []),
                        evidence[s],
                    ],
                    widths=[2, 1, *([1] if s in golden.SIGNAL_VALUES else []), 4],
                )
                for s in SIGNAL_TYPES
            ),
            mo.hstack([labelled_by, save_button]),
        ]
    )
    return evidence, labelled_by, presence, save_button, values


@app.cell
def _(
    SIGNAL_TYPES,
    evidence,
    golden,
    labelled_by,
    mo,
    path,
    presence,
    row,
    save_button,
    set_rev,
    update_queue,
    values,
):
    mo.stop(not save_button.value)
    try:
        _missing = [s for s in SIGNAL_TYPES if presence.value[s] is None]
        if _missing:
            raise ValueError(f"not labelled yet: {_missing}")
        _labels = {
            s: golden.Label(present=False)
            if presence.value[s] == "absent"
            else golden.Label(
                present=True,
                value=values.value[s] if s in golden.SIGNAL_VALUES else None,
                evidence=evidence.value[s].strip(),
            )
            for s in SIGNAL_TYPES
        }
        update_queue(path, golden.label(row, _labels, labelled_by.value))
        set_rev(lambda n: n + 1)
    except ValueError as _exc:
        mo.output.replace(mo.callout(str(_exc), kind="warn"))


if __name__ == "__main__":
    app.run()
