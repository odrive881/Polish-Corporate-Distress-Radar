"""Label the golden set — plan 0013 step E (decision 5: the owner labels).

Reads and writes a local queue `<LABELLING_DIR>/<version>.jsonl` that
`python -m distress_radar.extraction.label_queue build` makes, one per golden sample: the notes
(`golden_sample_v1`), the auditor reports (`golden_sample_v2`), the notes targeted at the
signals with no positive yet (`golden_sample_v3`), or a stratified sample of list v3's draw (v4 the
notes, v5 the auditor reports), chosen at the top. For each page: read the masked text, mask by
hand any name the masker missed (highlighted in red when the masker itself would still replace
it), then label each signal the page is asked as present or absent, with a verbatim evidence span
for each present one. "All absent" saves a page with no signal in one click; it refuses while any
signal is marked present. Definitions: `evals/text_signals/labelling_guide.md`. Candidate
sentences the prefilter matched are highlighted in yellow; they are hints, not labels.

On a stratified sample (plan 0013 decisions 12–23):
- the pages come in draw order, and the next one is the first unlabelled: do not skip, the export
  refuses a page labelled past a gap. The banner says how far the targets are, and when the
  stopping rule is met;
- the form shows only the signals the document kind is asked (decision 15);
- on an answered-present page the form starts from the model's stored answers, its proposals,
  unless the page is one of the fixed share labelled blind; every other page is blind (decision 17).
  A signal the sample parks starts as "absent";
- tick "unsure" on a call you doubt, and pick a reason where you overrule the model (decision 21);
- the first `pilot_pages` pages are the pilot: then `make label-review`, and tighten the guide;
- "Rechecks due" lists pages drawn to be labelled again, blind, a week on (decision 22).

The page records `proposed_by` only when the proposals were shown, the guide's version, the day,
and how long it was on screen. Nothing here leaves the machine. `label_queue export` writes the
labelled pages to `evals/`.

    uv run marimo edit notebooks/labelling/golden_set.py
"""

import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium")


@app.cell
def _():
    import html
    import time
    from datetime import UTC, datetime

    import marimo as mo

    from distress_radar.extraction import golden, masking
    from distress_radar.extraction.label_queue import (
        DEFAULT_VERSION,
        progress_lines,
        queue_path,
        update_queue,
        versions,
    )
    from distress_radar.settings import Settings

    _queues = {v: queue_path(Settings(), v) for v in versions()}
    sample_picker = mo.ui.dropdown(
        options={v: str(q) for v, q in _queues.items() if q.exists()},
        value=DEFAULT_VERSION if _queues[DEFAULT_VERSION].exists() else None,
        label="golden sample",
    )
    mode = mo.ui.radio(["label", "rechecks due"], value="label", inline=True)
    nlp = masking.load_model()
    guide = golden.guide_version()
    get_rev, set_rev = mo.state(0)
    shown: dict[str, float] = {}  # page id → when it was first on screen (decision 23)
    return (
        UTC,
        datetime,
        get_rev,
        golden,
        guide,
        html,
        masking,
        mo,
        mode,
        nlp,
        progress_lines,
        sample_picker,
        set_rev,
        shown,
        time,
        update_queue,
    )


@app.cell
def _(golden, mo, mode, sample_picker):
    from pathlib import Path as _Path

    mo.output.replace(mo.hstack([sample_picker, mode]))
    mo.stop(sample_picker.value is None, mo.md("No queue yet: run `make label-queue`."))
    path = _Path(sample_picker.value)
    sample = golden.load_golden_sample(path.stem)
    stratified = sample.stratified is not None
    # whose answers a shown proposal is: the stratified sample's answering extractor
    proposer = sample.stratified.answers if sample.stratified is not None else None
    rechecking = stratified and mode.value == "rechecks due"
    return path, proposer, rechecking, sample, stratified


@app.cell
def _(UTC, datetime, get_rev, golden, mo, path, progress_lines, rechecking, sample, stratified):
    get_rev()  # reload after every save
    rows = golden.load_queue(path.read_bytes())
    ordered = golden.in_draw_order(rows) if stratified else rows
    if rechecking:
        assert sample.stratified is not None
        _today = datetime.now(UTC).date()
        ordered = [
            r
            for r in ordered
            if golden.recheck_due(r, _today, sample.stratified.recheck_after_days)
        ]
        mo.stop(not ordered, mo.md("No recheck is due."))
        _done = 0
        _next = ordered[0].page_id
    else:
        _done = sum(r.complete for r in rows)
        _next = next((r.page_id for r in ordered if not r.complete), ordered[0].page_id)
    options = {
        f"{i:3d} {'✓' if r.complete else '·'} {r.stratum or r.sample:16} {r.page_id}": r.page_id
        for i, r in enumerate(ordered, start=1)
    }
    picker = mo.ui.dropdown(
        options=options,
        value=next(k for k, v in options.items() if v == _next),
        label=(
            f"recheck ({len(ordered)} due)"
            if rechecking
            else f"page ({_done} of {len(rows)} labelled)"
        ),
        full_width=True,
    )
    _lines = [] if rechecking else progress_lines(rows, sample)
    mo.output.replace(
        mo.vstack(
            [picker, *([mo.callout(mo.md("  \n".join(_lines)), kind="neutral")] if _lines else [])]
        )
    )
    return picker, rows


@app.cell
def _(golden, html, mo, nlp, picker, rechecking, rows, shown, time):
    from itertools import pairwise as _pairwise

    row = next(r for r in rows if r.page_id == picker.value)
    shown.setdefault(row.page_id, time.monotonic())
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
    if rechecking:
        _note = mo.callout(
            mo.md("Recheck: label the page again as if new. Your first labels are not shown."),
            kind="info",
        )
    elif row.stratum is not None and not row.labels:
        _note = mo.callout(
            mo.md(
                "The form starts from the model's answers: read the page and correct them."
                if golden.shows_proposals(row)
                else "Blind page: label it without the model's answers."
            ),
            kind="info",
        )
    else:
        _note = mo.md("")
    mo.vstack(
        [
            mo.md(
                f"**{row.stratum or row.sample}** · attachment {row.attachment}, page {row.page} · "
                f"prefilter: {', '.join(row.candidates) or 'none'} · masked {row.masked}"
                f" · by hand {row.hand_masked or {}}"
            ),
            _note,
            mo.callout(
                mo.md(
                    f"The masker would still replace {len(remaining)} span(s), in red. Mask a "
                    "real name before saving; whatever red you save over is recorded as a "
                    "reviewed false positive."
                ),
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
def _(golden, mo):
    mask_target = mo.ui.text(label="text to mask (exact)", full_width=True)
    mask_kind = mo.ui.dropdown(options=sorted(golden.HAND_TOKENS), value="person", label="as")
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
def _(golden, mo, rechecking, row, sample, stratified):
    asked = row.required
    # saved labels, else proposals and parked "absent"; a recheck starts empty
    start = {} if rechecking else golden.prefill(row, sample.parked)

    def _state(signal: str) -> str | None:
        label = start.get(signal)
        return None if label is None else ("present" if label.present else "absent")

    presence = mo.ui.dictionary(
        {s: mo.ui.radio(["absent", "present"], value=_state(s), inline=True) for s in asked}
    )
    evidence = mo.ui.dictionary(
        {
            s: mo.ui.text_area(
                value=(start[s].evidence or "") if s in start else "",
                placeholder="verbatim span of the page, when present",
                full_width=True,
                rows=2,
            )
            for s in asked
        }
    )
    # A value for each signal that takes one (opinion; kind of event, decision 9), when present.
    values = mo.ui.dictionary(
        {
            s: mo.ui.dropdown(
                options=list(golden.SIGNAL_VALUES[s]),
                value=start[s].value if s in start else None,
                label="value",
            )
            for s in asked
            if s in golden.SIGNAL_VALUES
        }
    )
    # Decision 21: a doubted call, and why a label overrules the model (stratified samples).
    unsure = mo.ui.dictionary(
        {
            s: mo.ui.checkbox(value=start[s].unsure if s in start else False, label="unsure")
            for s in asked
        }
    )
    reasons = mo.ui.dictionary(
        {
            s: mo.ui.dropdown(
                options=list(golden.REASONS),
                value=start[s].reason if s in start else None,
                label="reason",
            )
            for s in asked
        }
    )
    _extras = stratified and not rechecking
    labelled_by = mo.ui.text(value=row.labelled_by or "owner", label="labelled by")
    save_button = mo.ui.run_button(label="Save recheck" if rechecking else "Save labels")
    absent_button = mo.ui.run_button(label="All absent", kind="neutral")
    mo.vstack(
        [
            *(
                mo.hstack(
                    [
                        mo.md(f"`{s}`"),
                        presence[s],
                        *([values[s]] if s in golden.SIGNAL_VALUES else []),
                        *([] if rechecking else [evidence[s]]),
                        *([unsure[s], reasons[s]] if _extras else []),
                    ],
                    widths=[
                        2,
                        1,
                        *([1] if s in golden.SIGNAL_VALUES else []),
                        *([] if rechecking else [4]),
                        *([1, 1] if _extras else []),
                    ],
                )
                for s in asked
            ),
            mo.hstack([labelled_by, save_button, absent_button]),
        ]
    )
    return (
        absent_button,
        asked,
        evidence,
        labelled_by,
        presence,
        reasons,
        save_button,
        unsure,
        values,
    )


@app.cell
def _(
    UTC,
    absent_button,
    asked,
    datetime,
    evidence,
    golden,
    guide,
    labelled_by,
    mo,
    nlp,
    path,
    presence,
    proposer,
    reasons,
    rechecking,
    row,
    save_button,
    set_rev,
    shown,
    stratified,
    time,
    unsure,
    update_queue,
    values,
):
    mo.stop(not save_button.value and not absent_button.value)
    _today = datetime.now(UTC).date()
    try:
        if absent_button.value:
            _present = [s for s in asked if presence.value[s] == "present"]
            if _present:
                raise ValueError(f"marked present, so not all absent: {_present}")
            _choice = {s: "absent" for s in asked}
        else:
            _missing = [s for s in asked if presence.value[s] is None]
            if _missing:
                raise ValueError(f"not labelled yet: {_missing}")
            _choice = {s: presence.value[s] for s in asked}
        if rechecking:
            _checks = {
                s: golden.Recheck(
                    present=_choice[s] == "present",
                    value=values.value[s]
                    if _choice[s] == "present" and s in golden.SIGNAL_VALUES
                    else None,
                )
                for s in asked
            }
            update_queue(path, golden.record_recheck(row, _checks, _today))
            set_rev(lambda n: n + 1)
            mo.stop(True)
        _labels = {
            s: golden.Label(
                present=False,
                unsure=stratified and bool(unsure.value[s]),
                reason=reasons.value[s] if stratified else None,
            )
            if _choice[s] == "absent"
            else golden.Label(
                present=True,
                value=values.value[s] if s in golden.SIGNAL_VALUES else None,
                evidence=evidence.value[s].strip(),
                unsure=stratified and bool(unsure.value[s]),
                reason=reasons.value[s] if stratified else None,
            )
            for s in asked
        }
        _started = shown.pop(row.page_id, None)
        update_queue(
            path,
            golden.clear_reviewed(
                golden.label(
                    row,
                    _labels,
                    labelled_by.value,
                    # a page saved with the model's answers in view says so (decision 13)
                    proposer if golden.shows_proposals(row) else None,
                    guide=guide if stratified else None,
                    on=_today if stratified else None,
                    seconds=None
                    if _started is None or not stratified
                    else time.monotonic() - _started,
                ),
                nlp,
            ),
        )
        set_rev(lambda n: n + 1)
    except ValueError as _exc:
        mo.output.replace(mo.callout(str(_exc), kind="warn"))


if __name__ == "__main__":
    app.run()
