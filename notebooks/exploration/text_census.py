"""Census of the seed's text sources — plan 0013 step A.

What Phase 7 can read, counted before anything is built, so the owner decisions of plan 0013
rest on numbers:
1. `filing_index` by RDF document type: listed, detailed (submission date), downloaded;
2. every stored statement's introduction: the going-concern flags (`P_5A`, `P_5B`, `P_5C`),
   and in wariant 2 (FY2025+) average employment and whether an audit is required;
3. the attachments embedded in the statements (`Plik/Zawartosc`): kind, and for PDFs pages
   with a text layer against pages without;
4. per seed event: what was filed before it.

**Counts only.** No document text, no `P_5C` content, no attachment content is printed: the
free text can name people (ADR 0009). Read-only: reads Postgres, MinIO and `WAREHOUSE_DIR`,
writes nothing. Needs `make dev-up`.

    uv run python notebooks/exploration/text_census.py
    uv run marimo edit notebooks/exploration/text_census.py
"""

import marimo

__generated_with = "0.9"
app = marimo.App(width="medium")


@app.cell
def _():
    import base64
    import collections

    import marimo as mo
    import polars as pl
    import psycopg
    import pymupdf
    from lxml import etree

    from distress_radar.acquisition.raw_store import S3ObjectStore
    from distress_radar.models.dataset import event_day, load_label_set
    from distress_radar.models.splits import load_backtest_config
    from distress_radar.parsing.containers import safe_parser, unwrap
    from distress_radar.settings import Settings

    settings = Settings()
    return (
        S3ObjectStore,
        base64,
        collections,
        etree,
        event_day,
        load_backtest_config,
        load_label_set,
        mo,
        pl,
        psycopg,
        pymupdf,
        safe_parser,
        settings,
        unwrap,
    )


@app.cell
def _(pl, psycopg, settings):
    with psycopg.connect(settings.postgres_conninfo) as conn:
        filings = pl.DataFrame(
            conn.execute(
                "SELECT krs, document_ref, rdf_type_code, period_end, submission_date,"
                " deleted_on, sha256 FROM filing_index"
            ).fetchall(),
            schema=[
                "krs",
                "document_ref",
                "rdf_type_code",
                "period_end",
                "submission_date",
                "deleted_on",
                "sha256",
            ],
            orient="row",
            infer_schema_length=None,
        )
        statements = pl.DataFrame(
            conn.execute(
                "SELECT p.sha256, p.source_member, p.krs, p.document_ref, p.structure_version,"
                " p.status, r.object_key FROM parsed_documents p"
                " JOIN raw_documents r ON r.sha256 = p.sha256"
            ).fetchall(),
            schema=[
                "sha256",
                "source_member",
                "krs",
                "document_ref",
                "structure_version",
                "status",
                "object_key",
            ],
            orient="row",
            infer_schema_length=None,
        )
    filings = filings.with_columns(pl.col("krs").str.strip_chars())
    statements = statements.with_columns(pl.col("krs").str.strip_chars())
    return filings, statements


@app.cell
def _(filings, mo, pl):
    by_type = (
        filings.group_by("rdf_type_code")
        .agg(
            pl.len().alias("listed"),
            pl.col("krs").n_unique().alias("entities"),
            pl.col("submission_date").is_not_null().sum().alias("detailed"),
            pl.col("sha256").is_not_null().sum().alias("downloaded"),
            pl.col("deleted_on").is_not_null().sum().alias("deleted"),
            pl.col("period_end").dt.year().min().alias("first_year"),
            pl.col("period_end").dt.year().max().alias("last_year"),
        )
        .sort("listed", descending=True)
    )
    print("1. filing_index by RDF type")
    print(by_type)
    mo.ui.table(by_type.to_dicts())
    return (by_type,)


@app.cell
def _(etree):
    # Introduction variants (plan 0013 step A). Micro statements keep the flags in
    # `InformacjeOgolneJednostkaMikro`, the others in `WprowadzenieDoSprawozdania...`. Only
    # wariant 2 (FY2025+) has employment and the audit flag, numbered by the variant, not by one
    # element name (read from the 2025 XSDs' documentation, 13817-13821): the small and micro
    # blocks at P_7 and P_8; the full introduction, with its merger block P_6, at P_8 and P_9.
    # Before wariant 2, P_7 and P_8 are other things (accounting policies), never employment.
    SHORT_INTROS = (
        "WprowadzenieDoSprawozdaniaFinansowegoJednostkaMala",
        "InformacjeOgolneJednostkaMikro",
    )

    def local(el: etree._Element) -> str:
        return etree.QName(el).localname

    def child_text(parent: etree._Element, name: str) -> str | None:
        for el in parent:
            if isinstance(el.tag, str) and local(el) == name:
                return (el.text or "").strip()
        return None

    def is_intro(el: etree._Element) -> bool:
        return isinstance(el.tag, str) and (
            local(el).startswith("WprowadzenieDoSprawozdania")
            or local(el) == "InformacjeOgolneJednostkaMikro"
        )

    def introduction(root: etree._Element, wariant_2: bool) -> dict[str, object]:
        intro = next((el for el in root.iter() if is_intro(el)), None)
        if intro is None:
            return {"intro": None}
        p5 = next(
            (el for el in intro.iter() if isinstance(el.tag, str) and local(el) == "P_5"), intro
        )
        employment, audit = ("P_7", "P_8") if local(intro) in SHORT_INTROS else ("P_8", "P_9")
        p5c = child_text(p5, "P_5C")
        return {
            "intro": local(intro),
            "p5a": child_text(p5, "P_5A"),
            "p5b": child_text(p5, "P_5B"),
            "p5c_chars": None if p5c is None else len(p5c),
            "employment_present": wariant_2 and child_text(intro, employment) is not None,
            "audit_required": child_text(intro, audit) if wariant_2 else None,
        }

    def threat(p5a: str | None, p5b: str | None) -> bool | None:
        """Booleans before 2025, codes after: `P_5A` 2 = not a going concern, `P_5B` 2 = threats."""
        if p5a is None and p5b is None:
            return None
        return p5a in ("false", "2") or p5b in ("false", "2")

    return introduction, local, threat


@app.cell
def _(
    S3ObjectStore,
    base64,
    collections,
    etree,
    introduction,
    local,
    pl,
    pymupdf,
    safe_parser,
    settings,
    statements,
    threat,
    unwrap,
):
    store = S3ObjectStore.from_endpoint(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key.get_secret_value(),
        settings.minio_bucket,
    )
    TEXT_PAGE_CHARS = 200  # a page with fewer extractable characters is counted as having none

    def kind(data: bytes) -> str:
        if data.startswith(b"%PDF"):
            return "pdf"
        if data.startswith(b"PK"):
            return "zip/office"
        if data.lstrip().startswith(b"<"):
            return "xml/html"
        if data.startswith(b"{\\rtf"):
            return "rtf"
        return "other"

    rows: list[dict[str, object]] = []
    attachments: list[dict[str, object]] = []
    errors: collections.Counter[str] = collections.Counter()
    for doc in statements.iter_rows(named=True):
        members = {m.source_member: m for m in unwrap(store.get(doc["object_key"]))}
        member = members.get(doc["source_member"])
        if member is None:
            errors["member not found"] += 1
            continue
        if not member.data.lstrip().startswith(b"<"):
            errors[f"not XML ({doc['status']}), skipped"] += 1  # the PDF statement, plan 0006
            continue
        root = etree.fromstring(member.data, safe_parser())
        intro = introduction(root, "-w2-" in (doc["structure_version"] or ""))
        rows.append(
            {
                "krs": doc["krs"],
                "document_ref": doc["document_ref"],
                "structure_version": doc["structure_version"],
                "status": doc["status"],
                **intro,
                "threat": threat(intro.get("p5a"), intro.get("p5b")),  # type: ignore[arg-type]
            }
        )
        for plik in (el for el in root.iter() if isinstance(el.tag, str) and local(el) == "Plik"):
            content = next(
                (c for c in plik if isinstance(c.tag, str) and local(c) == "Zawartosc"), None
            )
            if content is None or not (content.text or "").strip():
                errors["attachment without content"] += 1
                continue
            data = base64.b64decode(content.text or "")
            record: dict[str, object] = {
                "krs": doc["krs"],
                "document_ref": doc["document_ref"],
                "kind": kind(data),
                "bytes": len(data),
            }
            if record["kind"] == "pdf":
                try:
                    with pymupdf.open(stream=data, filetype="pdf") as pdf:
                        chars = [len(page.get_text().strip()) for page in pdf]
                    record.update(
                        pages=len(chars),
                        text_pages=sum(c >= TEXT_PAGE_CHARS for c in chars),
                        chars=sum(chars),
                    )
                except (RuntimeError, ValueError):
                    errors["unreadable pdf"] += 1
            attachments.append(record)

    intros = pl.DataFrame(rows, infer_schema_length=None)
    embedded = pl.DataFrame(attachments, infer_schema_length=None)
    print("errors:", dict(errors) or "none")
    return embedded, intros


@app.cell
def _(intros, mo, pl):
    flags = (
        intros.group_by("structure_version")
        .agg(
            pl.len().alias("statements"),
            pl.col("intro").is_null().sum().alias("no_intro"),
            pl.col("threat").sum().alias("threat"),
            (pl.col("p5a").is_in(["false", "2"])).sum().alias("not_going_concern_basis"),
            pl.col("p5c_chars").is_not_null().sum().alias("p5c_present"),
            pl.col("employment_present").sum().alias("employment"),
            (pl.col("audit_required") == "1").sum().alias("audit_required"),
            (pl.col("audit_required") == "2").sum().alias("audit_not_required"),
        )
        .sort("structure_version")
    )
    print("2. introduction flags by structure version")
    print(flags)
    totals = flags.select(pl.exclude("structure_version").sum())
    print(totals)
    mo.ui.table(flags.to_dicts())
    return flags, totals


@app.cell
def _(embedded, mo, pl):
    if embedded.is_empty():
        print("3. no embedded attachments")
        attachment_kinds = embedded
    else:
        attachment_kinds = (
            embedded.group_by("kind")
            .agg(
                pl.len().alias("attachments"),
                pl.col("document_ref").n_unique().alias("statements"),
                pl.col("krs").n_unique().alias("entities"),
                pl.col("pages").sum().alias("pages") if "pages" in embedded.columns else pl.lit(0),
                pl.col("text_pages").sum().alias("text_pages")
                if "text_pages" in embedded.columns
                else pl.lit(0),
            )
            .sort("attachments", descending=True)
        )
        print("3. attachments embedded in statements")
        print(attachment_kinds)
        if "pages" in embedded.columns:
            pdfs = embedded.filter((pl.col("kind") == "pdf") & pl.col("pages").is_not_null())
            print(
                "PDFs by text layer:",
                pdfs.select(
                    (pl.col("text_pages") == pl.col("pages")).sum().alias("all_pages_text"),
                    ((pl.col("text_pages") > 0) & (pl.col("text_pages") < pl.col("pages")))
                    .sum()
                    .alias("mixed"),
                    (pl.col("text_pages") == 0).sum().alias("no_text_layer"),
                ).to_dicts()[0],
            )
    mo.ui.table(attachment_kinds.to_dicts())
    return (attachment_kinds,)


@app.cell
def _(event_day, filings, intros, load_backtest_config, load_label_set, pl, settings):
    config = load_backtest_config("backtest_v1")
    labels = load_label_set(settings.warehouse_dir, config.label_set_hash)
    seed_events = (
        labels.filter(pl.col("outcome_class").is_in(config.distress_classes))
        .select("krs", "outcome_class", event_day().alias("event_day"), "event_known_from")
        .unique()
        .sort("krs", "event_day")
    )
    dated = intros.join(
        filings.select("document_ref", "submission_date", "period_end"), on="document_ref"
    )
    _TYPES = {"19": "auditor", "20": "mgmt", "3": "approval", "4": "loss_cov"}
    _rows = []
    for _i, _ev in enumerate(seed_events.iter_rows(named=True), start=1):
        _own = dated.filter(pl.col("krs") == _ev["krs"])
        _before = _own.filter(pl.col("submission_date") < _ev["event_day"])
        _row = {
            "event": _i,
            "class": _ev["outcome_class"],
            "year": _ev["event_day"].year,
            "statements_before": _before.height,
            "threat_before": int(_before.get_column("threat").sum() or 0),
            "threat_ever": int(_own.get_column("threat").sum() or 0),
        }
        # Separately filed documents have no detail, so no submission date: counted by the
        # period they report on, an upper bound on what was public before the event.
        _other = filings.filter(
            (pl.col("krs") == _ev["krs"]) & (pl.col("period_end") < _ev["event_day"])
        )
        for _code, _name in _TYPES.items():
            _row[f"{_name}_periods_before"] = _other.filter(pl.col("rdf_type_code") == _code).height
        _rows.append(_row)
    per_event = pl.DataFrame(_rows)
    print("4. per seed event (distinct events, any horizon)")
    print(per_event)
    return per_event, seed_events


@app.cell
def _(embedded, intros, pl, seed_events):
    # 5. The flag's base rate: entities with a distress event against the rest, and where the
    # scanned notes are. A flag that fires as often without distress says little.
    distressed = set(seed_events.get_column("krs").to_list())
    group = (
        pl.when(pl.col("krs").is_in(list(distressed)))
        .then(pl.lit("distress"))
        .otherwise(pl.lit("no event"))
    )
    by_group = (
        intros.with_columns(group.alias("group"))
        .group_by("group")
        .agg(
            pl.col("krs").n_unique().alias("entities"),
            pl.len().alias("statements"),
            pl.col("threat").sum().alias("threat_statements"),
            pl.col("krs").filter(pl.col("threat")).n_unique().alias("entities_ever_flagging"),
        )
        .sort("group")
    )
    scans = (
        embedded.filter(pl.col("kind") == "pdf")
        .with_columns(group.alias("group"))
        .group_by("group")
        .agg(
            pl.len().alias("pdfs"),
            (pl.col("text_pages") == 0).sum().alias("no_text_layer"),
            pl.col("pages").sum().alias("pages"),
            pl.col("text_pages").sum().alias("text_pages"),
        )
        .sort("group")
    )
    print("5. flag and scans by group")
    print(by_group)
    print(scans)
    return by_group, scans


@app.cell
def _(S3ObjectStore, collections, pl, settings, statements, unwrap):
    # 6. Plan 0013 step C on the seed: the text layer by `extraction.page_text`, and what
    # `extraction.masking` replaces on each text page. Counts only; no text is printed.
    from distress_radar.extraction.masking import load_model as _load_model
    from distress_radar.extraction.masking import mask as _mask
    from distress_radar.extraction.page_text import AttachmentError as _AttachmentError
    from distress_radar.extraction.page_text import attachments as _attachments
    from distress_radar.extraction.page_text import pages as _pages

    _store = S3ObjectStore.from_endpoint(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key.get_secret_value(),
        settings.minio_bucket,
    )
    _nlp = _load_model()
    page_status: collections.Counter[str] = collections.Counter()
    masked_kinds: collections.Counter[str] = collections.Counter()
    _pages_with_person = 0
    _errors: collections.Counter[str] = collections.Counter()
    masked_pages: list[tuple[str, str]] = []  # (krs, masked text), for section 7; never printed
    for _doc in statements.iter_rows(named=True):
        _members = {m.source_member: m for m in unwrap(_store.get(_doc["object_key"]))}
        _member = _members.get(_doc["source_member"])
        if _member is None or not _member.data.lstrip().startswith(b"<"):
            continue
        for _att in _attachments(_member.data):
            if _att.kind != "pdf":
                page_status["unsupported attachment"] += 1
                continue
            try:
                for _page in _pages(_att.data):
                    page_status[_page.status] += 1
                    if _page.status != "text":
                        continue
                    _masked = _mask(_page.text, _nlp)
                    masked_pages.append((_doc["krs"], _masked.text))
                    masked_kinds.update(_masked.counts)
                    _pages_with_person += _masked.counts.get("person", 0) > 0
            except _AttachmentError as _exc:
                _errors[_exc.reason_code] += 1
    print("6. text layer and masking on the seed's embedded notes")
    print("pages by status:", dict(sorted(page_status.items())))
    print("replacements by kind:", dict(sorted(masked_kinds.items())))
    print("text pages with at least one person masked:", _pages_with_person)
    print("attachment errors:", dict(_errors) or "none")
    step_c = pl.DataFrame(
        {
            "measure": list(page_status) + list(masked_kinds),
            "count": [*page_status.values(), *masked_kinds.values()],
        }
    )
    return masked_kinds, masked_pages, page_status, step_c


@app.cell
def _(collections, masked_pages, pl, seed_events):
    # 7. Plan 0013 step D on the seed: text pages the prefilter selects, per signal_type, for
    # entities with a distress event and the rest. Counts only. Pages of any date: what the
    # prefilter picks, not what was known before an event.
    from distress_radar.extraction.preprocessing import SIGNAL_TYPES as _SIGNALS
    from distress_radar.extraction.preprocessing import analyse as _analyse
    from distress_radar.extraction.preprocessing import candidates as _candidates
    from distress_radar.extraction.preprocessing import load_model as _load_model
    from distress_radar.extraction.preprocessing import load_prefilter as _load_prefilter

    _prefilter = _load_prefilter("prefilter_v1")
    _nlp = _load_model()
    _distressed = set(seed_events.get_column("krs").to_list())
    _selected: collections.Counter[tuple[str, str]] = collections.Counter()
    _pages: collections.Counter[str] = collections.Counter()
    for _krs, _text in masked_pages:
        _group = "distress" if _krs in _distressed else "no event"
        _pages[_group] += 1
        _found = _candidates(_analyse(_text, _nlp), _prefilter)
        _selected[(_group, "any")] += bool(_found)
        for _signal in _found:
            _selected[(_group, _signal)] += 1
    step_d = pl.DataFrame(
        [
            {
                "signal_type": _s,
                **{_g: _selected[(_g, _s)] for _g in ("distress", "no event")},
            }
            for _s in ("any", *_SIGNALS)
        ]
    )
    print("7. text pages selected by prefilter_v1, by group; text pages:", dict(_pages))
    print(step_d)
    return (step_d,)


if __name__ == "__main__":
    app.run()
