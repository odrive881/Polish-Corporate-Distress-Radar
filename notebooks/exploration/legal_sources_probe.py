"""Legal-event sources probe (plan 0008 step A) — a time-boxed spike, not production code.

Three sources, for the 17-entity seed only, at human pace:

- the open KRS API (`api-krs.ms.gov.pl`): full extract (`OdpisPelny`) per KRS,
  JSON. What sections carry proceedings, how entries are dated, where natural
  persons sit, how deregistration shows;
- KRZ (`krz.ms.gov.pl`) and the MSiG notice search (`wyszukiwarka-msig.ms.gov.pl`):
  what a plain client gets, whether there is a JSON API behind the portal, and
  any bot protection. The probe reads each portal's own public page and the
  script bundles it loads, the way a browser would, and stops at the first gate.

Responses name natural persons (board members, shareholders, liquidators,
trustees). Redacting them before storage is plan 0008 decision 3, not yet
signed off, so **nothing fetched here is written anywhere**: bodies stay in
memory, and only structure, dates, entry numbers and case signatures are
printed. Free-text fields are reduced to a leading date. Findings:
`docs/adr/0011-legal-event-sources.md`.

Run interactively: `uv run marimo edit notebooks/exploration/legal_sources_probe.py`
Run headless:      `uv run python notebooks/exploration/legal_sources_probe.py`
"""

import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium")


@app.cell
def _():
    import re
    import time
    from collections import Counter, defaultdict
    from pathlib import Path
    from typing import Any

    import httpx
    import yaml

    from distress_radar.acquisition.base import USER_AGENT

    ROOT = Path(__file__).resolve().parents[2]
    SEED = yaml.safe_load((ROOT / "config/segments/construction_sme_v1_seed.yaml").read_text())
    ENTITIES = [(e["krs"], e.get("status_hint")) for e in SEED["entities"]]
    PAUSE_SECONDS = 4.0  # human pace: ~15 requests a minute, 17 entities in ~70 s
    PERSON_KEYS = {"imie", "imieDrugie", "nazwiskoICzlon", "nazwiskoIICzlon", "pesel"}
    DATE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")
    FULL_DATE = re.compile(r"\d{2}\.\d{2}\.\d{4}")
    # Short, non-personal categorical fields worth printing verbatim.
    CATEGORY_KEYS = {"rodzajPostepowania", "sposobProwadzeniaPostepowania", "charakterZaleglosci"}
    # Entry descriptions are printed only as the keywords they contain.
    ENTRY_KEYWORDS = ("WYKREŚL", "UPADŁ", "LIKWID", "RESTRUKTUR", "UKŁAD", "SANAC", "ROZWIĄZ", "KURATOR")
    client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=False)
    return (
        Any,
        CATEGORY_KEYS,
        Counter,
        DATE,
        ENTRY_KEYWORDS,
        FULL_DATE,
        ENTITIES,
        PAUSE_SECONDS,
        PERSON_KEYS,
        client,
        defaultdict,
        re,
        time,
    )


@app.cell
def _(ENTITIES, PAUSE_SECONDS, client, time):
    # KRS open API: one full extract per seed entity, held in memory only.
    extracts = {}
    fetch_log = []
    for _krs, _hint in ENTITIES:
        _r = client.get(
            f"https://api-krs.ms.gov.pl/api/krs/OdpisPelny/{_krs}",
            params={"rejestr": "P", "format": "json"},
        )
        fetch_log.append((_krs, _r.status_code, _r.headers.get("content-type"), len(_r.content)))
        if _r.status_code == 200:
            extracts[_krs] = _r.json()
        time.sleep(PAUSE_SECONDS)
    for _row in fetch_log:
        print("KRS API", *_row)
    return (extracts,)


@app.cell
def _(Any, CATEGORY_KEYS, DATE, FULL_DATE, PERSON_KEYS, defaultdict):
    def walk(obj: Any, path: str = ""):
        """Yield (path, key, value) for every leaf."""
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, (dict, list)):
                    yield from walk(value, f"{path}.{key}")
                else:
                    yield f"{path}.{key}", key, value
        elif isinstance(obj, list):
            for item in obj:
                yield from walk(item, f"{path}[]")

    def entry_dates(extract: dict) -> dict[str, str]:
        return {
            str(w["numerWpisu"]): w["dataWpisu"]
            for w in extract["odpis"]["naglowekP"].get("wpis", [])
        }

    def leading_date(text: str) -> str:
        m = DATE.search(text or "")
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else "(no date in text)"

    def proceedings(extract: dict) -> list[dict[str, str]]:
        """Every dated fact in dzial4, dzial5 and dzial6, reduced to non-personal fields."""
        dates = entry_dates(extract)
        rows = []
        dane = extract["odpis"]["dane"]
        for section in ("dzial4", "dzial5", "dzial6"):
            for path, key, value in walk(dane.get(section, {}), section):
                if key in PERSON_KEYS or key == "nrWpisuWykr":
                    continue
                if key == "nrWpisuWprow":
                    rows.append(
                        {
                            "fact": path.rsplit(".", 1)[0],
                            "entry": str(value),
                            "entry_date": dates.get(str(value), "MISSING"),
                        }
                    )
                elif isinstance(value, str) and FULL_DATE.fullmatch(value.strip()):
                    rows.append({"fact": path, "date": value.strip()})
                elif key in CATEGORY_KEYS:
                    rows.append({"fact": path, "category": str(value)})
                elif key in ("sygnatura", "sygnaturaSprawy"):
                    rows.append({"fact": path, "signature": str(value)})
                elif isinstance(value, str) and len(value) > 20:
                    rows.append({"fact": path, "text_date": leading_date(value)})
        return rows

    def person_paths(extract: dict) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for path, key, _value in walk(extract):
            if key in PERSON_KEYS:
                counts[path.rsplit(".", 2)[0]] += 1
        return dict(counts)

    return entry_dates, person_paths, proceedings, walk


@app.cell
def _(ENTITIES, ENTRY_KEYWORDS, entry_dates, extracts, proceedings):
    # Per entity: which proceeding sections exist, their dates and signatures.
    for _krs, _hint in ENTITIES:
        if _krs not in extracts:
            print(f"\n{_krs}: no extract")
            continue
        _odpis = extracts[_krs]["odpis"]
        _dates = entry_dates(extracts[_krs])
        _sections = sorted(
            f"{s}.{k}"
            for s in ("dzial4", "dzial5", "dzial6")
            for k in (_odpis["dane"].get(s) or {})
        )
        _iso = sorted("-".join(reversed(d.split("."))) for d in _dates.values())
        print(f"\n{_krs}  hint={_hint!r}  entries={len(_dates)}  "
              f"first={_iso[0] if _iso else '-'}  last={_iso[-1] if _iso else '-'}  "
              f"stanZDnia={_odpis['naglowekP'].get('stanZDnia')}")
        for _w in _odpis["naglowekP"].get("wpis", []):
            _hits = [k for k in ENTRY_KEYWORDS if k in (_w.get("opis") or "").upper()]
            if _hits:
                print(f"   entry {_w['numerWpisu']} ({_w['dataWpisu']}): {_hits}")
        print("   sections:", _sections or "none")
        for _row in proceedings(extracts[_krs]):
            print("   ", _row)


@app.cell
def _(Counter, extracts, person_paths, walk):
    # Where natural persons sit (for the decision-3 redactor), and which keys
    # anywhere suggest deregistration.
    _census: Counter[str] = Counter()
    for _extract in extracts.values():
        _census.update(person_paths(_extract))
    print("person-bearing objects (paths, count across the seed):")
    for _path, _n in sorted(_census.items()):
        print(f"   {_n:4}  {_path}")
    _dereg: Counter[str] = Counter()
    for _extract in extracts.values():
        for _path, _key, _value in walk(_extract):
            if "wykresl" in _key.lower() and _key != "nrWpisuWykr":
                _dereg[_path] += 1
    print("keys mentioning deregistration:", dict(_dereg) or "none")
    _header_keys = sorted({k for e in extracts.values() for k in e["odpis"]["naglowekP"]})
    print("naglowekP keys:", _header_keys)


@app.cell
def _(PERSON_KEYS, extracts, walk):
    # Redacted KRS fixtures for tests/fixtures/legal/ — only when asked
    # (PROBE_WRITE_FIXTURES=1). Person-keyed leaves become a placeholder; then
    # every removed name is searched for in the rest of the document, and any
    # free-text field that still contains one is blanked too. A fixture is
    # written only if nothing removed survives anywhere.
    import json as _json
    import os as _os
    import re as _re
    from pathlib import Path as _Path

    PLACEHOLDER = "[REDACTED]"
    # Free text that is generic by construction: court names, PKD descriptions,
    # share counts, reporting periods, dated resolution headers. Any other string
    # longer than TEXT_LIMIT is reduced to its leading date: notarial citations
    # in the articles, representation clauses and security orders can name
    # people (notaries, supervisors) who appear in no structured field.
    # Liquidation and dissolution resolutions are NOT on it: they cite the
    # notarial deed, and with it the notary (seen in the seed).
    TEXT_ALLOWLIST = {
        "nazwa", "organWydajacy", "organWydajacyTytulWykonawczy", "oznaczenieSaduDokonujacegoWpisu",
        "posiadaneUdzialy", "zaOkresOdDo", "sposobProwadzeniaPostepowania", "rodzajPostepowania",
    }
    TEXT_LIMIT = 40
    DATE_IN_TEXT = _re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b")
    FIXTURE_ENTITIES = {
        "0000181328": "bankruptcy declared with a prior asset-security order and a 2010 liquidation",
        "0000507997": "bankruptcy entered without a decision date or signature",
        "0000277937": "restructuring (sanacja) opened after an asset-security order",
        "0000440028": "voluntary liquidation opened, closed and deregistered",
    }

    def redact(obj, removed: set[str], reduced: list[str], path: str = ""):
        if isinstance(obj, dict):
            out = {}
            for key, value in obj.items():
                here = f"{path}.{key}"
                if key in PERSON_KEYS and isinstance(value, str) and value:
                    removed.add(value)
                    out[key] = PLACEHOLDER
                elif (
                    isinstance(value, str)
                    and len(value) > TEXT_LIMIT
                    and key not in TEXT_ALLOWLIST
                    # PKD activity descriptions; an entry's `opis` is not generic
                    and not (key == "opis" and ".przedmiot" in path)
                ):
                    dated = DATE_IN_TEXT.search(value)
                    reduced.append(here)
                    out[key] = f"{PLACEHOLDER} {dated.group(0)}" if dated else PLACEHOLDER
                else:
                    out[key] = redact(value, removed, reduced, here)
            return out
        if isinstance(obj, list):
            return [redact(v, removed, reduced, f"{path}[]") for v in obj]
        return obj

    def blank_leaks(obj, tokens: list[str], blanked: list[str], path: str = ""):
        if isinstance(obj, dict):
            return {k: blank_leaks(v, tokens, blanked, f"{path}.{k}") for k, v in obj.items()}
        if isinstance(obj, list):
            return [blank_leaks(v, tokens, blanked, f"{path}[]") for v in obj]
        if isinstance(obj, str) and obj != PLACEHOLDER and any(
            _re.search(rf"\b{_re.escape(t)}\b", obj, _re.IGNORECASE) for t in tokens
        ):
            blanked.append(path)
            return PLACEHOLDER
        return obj

    if _os.environ.get("PROBE_WRITE_FIXTURES") == "1":
        _out = _Path(__file__).resolve().parents[2] / "tests/fixtures/legal/krs"
        _out.mkdir(parents=True, exist_ok=True)
        for _krs, _why in FIXTURE_ENTITIES.items():
            _removed: set[str] = set()
            _reduced: list[str] = []
            _doc = redact(extracts[_krs], _removed, _reduced)
            _tokens = sorted({w for v in _removed if not v.isdigit() for w in v.split() if len(w) >= 3})
            _blanked: list[str] = []
            _doc = blank_leaks(_doc, _tokens, _blanked)
            _text = _json.dumps(_doc, ensure_ascii=False, indent=1, sort_keys=True)
            _survivors = [v for v in _removed if v in _text]
            assert not _survivors, f"{_krs}: {len(_survivors)} removed values still present"
            assert not _re.search(r"\b\d{11}\b", _text), f"{_krs}: 11-digit run"
            (_out / f"odpis_pelny_{_krs}.json").write_text(_text + "\n", encoding="utf-8")
            _kinds = sorted({_re.sub(r"\[\]", "", p).rsplit(".", 1)[-1] for p in _reduced})
            print(f"{_krs} ({_why}): {len(_removed)} person values redacted, {len(_reduced)} texts reduced "
                  f"to their date {_kinds}, {len(_blanked)} further fields blanked")
    else:
        print("fixtures not written (set PROBE_WRITE_FIXTURES=1)")


@app.cell
def _(PAUSE_SECONDS, client, re, time):
    # KRZ and MSiG portals: what a plain, honestly identified client gets. One
    # page each, plus the script bundles that page itself loads; stop on any gate.
    def probe_portal(url: str) -> None:
        r = client.get(url)
        gate = [h for h in r.headers if h.lower().startswith(("x-iinfo", "x-cdn", "cf-"))]
        body = r.text
        print(f"\n{url}: HTTP {r.status_code}  {r.headers.get('content-type')}  "
              f"{len(r.content)} bytes  server={r.headers.get('server')}  gate_headers={gate}")
        if any(m in body for m in ("Incapsula", "_Incapsula_Resource", "hcaptcha", "captcha")):
            print("   bot protection seen: stopping here")
            return
        scripts = re.findall(r'<script[^>]+src="([^"]+)"', body)
        print("   scripts:", scripts[:6])
        for src in [s for s in scripts if "main" in s or "vendor" in s][:2]:
            time.sleep(PAUSE_SECONDS)
            full = src if src.startswith("http") else url.rstrip("/") + "/" + src.lstrip("/")
            js = client.get(full)
            apis = sorted(set(re.findall(r'["\'`]([A-Za-z0-9_/\-{}.:]*(?:api|Api|rest|service)[A-Za-z0-9_/\-{}.]*)["\'`]', js.text)))
            hosts = sorted(set(re.findall(r'https://[a-z0-9.\-]+\.gov\.pl[A-Za-z0-9_/\-]*', js.text)))
            print(f"   {src}: HTTP {js.status_code}, {len(js.content)} bytes")
            print("      api-like paths:", apis[:40])
            print("      gov.pl URLs:", hosts[:20])

    probe_portal("https://krz.ms.gov.pl/")
    time.sleep(PAUSE_SECONDS)
    probe_portal("https://wyszukiwarka-msig.ms.gov.pl/")


if __name__ == "__main__":
    app.run()
