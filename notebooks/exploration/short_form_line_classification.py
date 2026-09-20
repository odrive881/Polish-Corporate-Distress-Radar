"""Short-form (JednostkaMala / JednostkaMikro) line classification — plan 0005 step B.

Regenerates the evidence behind the ADR 0005 second addendum: how every
statutory line of the small and micro forms relates to the full form's, and
which schema versions share an element tree.

Read-only. Reads the vendored XSDs under `config/xsd/` and the committed
full-form body; touches no database, no object store and no network. Run it
again when a schema version is added, to check the addendum has not gone stale.

    uv run marimo edit notebooks/exploration/short_form_line_classification.py
"""

import marimo

__generated_with = "0.9"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo

    from distress_radar.parsing.canonical_schema import CONFIG_DIR, load_mapping_config
    from distress_radar.parsing.xsd_inventory import STATEMENT_TYPES, statement_line_items

    return CONFIG_DIR, STATEMENT_TYPES, load_mapping_config, mo, statement_line_items


@app.cell
def _(CONFIG_DIR):
    XSD = CONFIG_DIR / "xsd"
    GOV = XSD / "www.gov.pl/documents/2034621/2182793"
    MF = XSD / "www.mf.gov.pl/documents/764034/6464789"
    NEW = XSD / "www.gov.pl/static/finanse/SF/DefinicjeTypySprawozdaniaFinansowe/2024/10/24"
    CRD = XSD / "crd.gov.pl/xml/schematy/dziedzinowe/mf/2025/07/31/eD"

    # (form, schema version) -> the …Struktury… schema declaring its statements
    SCHEMAS = {
        ("Inna", "1-2"): GOV / "JednostkaInnaStrukturyDanychSprFin_v1-2.xsd",
        ("Inna", "1-3"): NEW / "JednostkaInnaStrukturyDanychSprFin_v1-3.xsd",
        ("Inna", "w2"): CRD / "JednostkaInnaStruktury/JednostkaInnaStrukturyDanychSprFin_v2-0E.xsd",
        ("Mala", "1-0E"): MF / "JednostkaMalaStrukturyDanychSprFin_v1-0.xsd",
        ("Mala", "1-2"): GOV / "JednostkaMalaStrukturyDanychSprFin_v1-2.xsd",
        ("Mala", "1-3"): NEW / "JednostkaMalaStrukturyDanychSprFin_v1-3.xsd",
        ("Mala", "w2"): CRD / "JednostkaMalaStruktury/JednostkaMalaStrukturyDanychSprFin_v2-0E.xsd",
        ("Mikro", "1-0E"): MF / "JednostkaMikroStrukturyDanychSprFin_v1-0.xsd",
        ("Mikro", "1-2"): GOV / "JednostkaMikroStrukturyDanychSprFin_v1-2.xsd",
        ("Mikro", "1-3"): NEW / "JednostkaMikroStrukturyDanychSprFin_v1-3.xsd",
        ("Mikro", "w2"): CRD
        / "JednostkaMikroStruktury/JednostkaMikroStrukturyDanychSprFin_v2-0E.xsd",
    }
    return (SCHEMAS,)


@app.cell
def _(SCHEMAS, statement_line_items):
    def lines(form: str, version: str, statement: str):
        """Statutory items; None when this form/version has no such schema or statement."""
        schema = SCHEMAS.get((form, version))
        if schema is None:
            return None
        try:
            return statement_line_items(schema, f"{statement}Jednostka{form}")
        except ValueError:
            return None

    def normalise(label: str) -> str:
        """Compare labels ignoring dash style, spacing and a trailing colon."""
        return " ".join(label.replace("–", "-").replace("—", "-").split()).rstrip(":").lower()

    return lines, normalise


@app.cell
def _(STATEMENT_TYPES, lines, mo):
    # 1. Which schema versions share an element tree, and which only look like it.
    def build_stability() -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for form in ("Inna", "Mala", "Mikro"):
            for statement in STATEMENT_TYPES:
                base = lines(form, "1-2", statement)
                if base is None:
                    out.append(
                        {
                            "form": form,
                            "statement": statement,
                            "pair": "—",
                            "verdict": "statement not in this form",
                        }
                    )
                    continue
                for version in ("1-0E", "1-3", "w2"):
                    other = lines(form, version, statement)
                    if other is None:
                        continue
                    same_tree = [i.path for i in base] == [i.path for i in other] and all(
                        (a.has_amounts, a.user_slots) == (b.has_amounts, b.user_slots)
                        for a, b in zip(base, other)
                    )
                    relabelled = same_tree and sum(a.label != b.label for a, b in zip(base, other))
                    out.append(
                        {
                            "form": form,
                            "statement": statement,
                            "pair": f"1-2 vs {version}",
                            "verdict": f"{relabelled} line(s) relabelled"
                            if same_tree and relabelled
                            else "identical"
                            if same_tree
                            else f"different tree ({len(base)} -> {len(other)})",
                        }
                    )
        return out

    stability = build_stability()
    mo.ui.table(stability)
    return (stability,)


@app.cell
def _(lines, load_mapping_config, mo, normalise):
    # 2. Every short-form line against the full form's, by element path.
    #
    # A path present in both forms does NOT mean the same line: the label is
    # the only signal. This is the classification the ADR addendum records, and
    # the trap plan 0005 finding 3 names.
    def classify() -> list[dict[str, str]]:
        body = load_mapping_config().bodies["jednostka_inna"]
        code_of = {
            (name, item.path): item.code
            for name, items in body.statements.items()
            for item in items
            if item.code
        }
        label_of = {
            (name, "/".join(d.path)): d.label
            for name in ("Bilans", "RZiS")
            for d in (lines("Inna", "1-2", name) or [])
        }
        out: list[dict[str, str]] = []
        for form in ("Mala", "Mikro"):
            for name in ("Bilans", "RZiS"):
                for item in lines(form, "1-2", name) or []:
                    key = (name, "/".join(item.path))
                    theirs, ours = label_of.get(key), " ".join(item.label.split())
                    verdict = (
                        "no such path in the full form"
                        if theirs is None
                        else "same line"
                        if normalise(theirs) == normalise(ours)
                        else "path collides, meaning differs"
                    )
                    out.append(
                        {
                            "form": form,
                            "statement": name,
                            "path": "/".join(item.path),
                            "verdict": verdict,
                            "full_code": code_of.get(key, "") if verdict == "same line" else "",
                            "short_label": ours,
                            "full_label": " ".join(theirs.split()) if theirs else "",
                        }
                    )
        return out

    rows = classify()
    mo.ui.table(rows)
    return (rows,)


@app.cell
def _(mo, rows):
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        counts[(row["form"], row["verdict"])] = counts.get((row["form"], row["verdict"]), 0) + 1
    mo.md(
        "### Summary\n\n"
        + "\n".join(f"- **{f}** — {v}: {n}" for (f, v), n in sorted(counts.items()))
    )


if __name__ == "__main__":
    app.run()
