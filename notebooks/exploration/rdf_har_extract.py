"""RDF HAR → test fixtures (plan 0003 step A) — a one-off extraction, not production code.

Reads the manually captured, gitignored HAR of one human RDF session
(`tests/fixtures/rdf/rdf-przegladarka_ms_gov_pl.cleaned.har`) and writes the
small, committable fixtures `tests/acquisition/test_document_retrieval.py`
uses: the SPA's own JSON responses, byte for byte as recorded, plus metadata
(never the bytes) of the one document download.

Only the RDF API responses are kept. Imperva's bot-check exchange, cookies,
and every script/style/font/image entry stay behind in the HAR. Re-run after a
new capture; commit the JSON, never the HAR (`*.har` is gitignored).

Run interactively: `uv run marimo edit notebooks/exploration/rdf_har_extract.py`
Run headless:      `uv run python notebooks/exploration/rdf_har_extract.py`
"""

import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium")


@app.cell
def _():
    import base64
    import hashlib
    import io
    import json
    import zipfile
    from pathlib import Path

    fixtures = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "rdf"
    har_path = fixtures / "rdf-przegladarka_ms_gov_pl.cleaned.har"
    api = "/services/rdf/przegladarka-dokumentow-finansowych/"
    return api, base64, fixtures, har_path, hashlib, io, json, zipfile


@app.cell
def _(api, har_path, json):
    har = json.loads(har_path.read_text(encoding="utf-8"))
    entries = [e for e in har["log"]["entries"] if api in e["request"]["url"]]

    def one(suffix: str, method: str) -> dict:
        """The single API entry whose path ends with `suffix`."""
        matches = [
            e
            for e in entries
            if e["request"]["method"] == method
            and e["request"]["url"].split("?")[0].endswith(suffix)
        ]
        if len(matches) != 1:
            raise ValueError(f"expected one {method} ...{suffix}, found {len(matches)}")
        return matches[0]

    def detail_entry() -> dict:
        """`GET dokumenty/{id}`: the only GET under dokumenty/ with no further path segment."""
        matches = [
            e
            for e in entries
            if e["request"]["method"] == "GET"
            and "/dokumenty/" in e["request"]["url"]
            and e["request"]["url"].split("/dokumenty/")[1].count("/") == 0
        ]
        if len(matches) != 1:
            raise ValueError(f"expected one document-detail GET, found {len(matches)}")
        return matches[0]

    recorded = {
        "entity_found.json": one("podmioty/wyszukiwanie/dane-podstawowe", "POST"),
        "filing_list_page0.json": one("dokumenty/wyszukiwanie", "POST"),
        "document_corrections.json": one("/id-dokumentu-i-korekt", "GET"),
        "document_detail.json": detail_entry(),
    }
    download = one("dokumenty/tresc", "POST")
    return download, recorded


@app.cell
def _(fixtures, recorded):
    for name, entry in recorded.items():
        text = entry["response"]["content"]["text"]
        (fixtures / name).write_text(text, encoding="utf-8")
        print(f"{name}: {len(text.encode('utf-8'))} bytes from {entry['request']['url']}")


@app.cell
def _(base64, download, fixtures, hashlib, io, json, zipfile):
    content = download["response"]["content"]
    body = (
        base64.b64decode(content["text"])
        if content.get("encoding") == "base64"
        else content["text"].encode("utf-8")
    )
    kept = {"content-type", "content-length", "content-disposition"}
    members = []
    if body[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            members = [{"name": i.filename, "size": i.file_size} for i in archive.infolist()]
    download_meta = {
        "note": "metadata of the recorded download; the bytes are not committed",
        "request": {
            "method": download["request"]["method"],
            "url": download["request"]["url"],
            "body": download["request"]["postData"]["text"],
        },
        "response": {
            "status": download["response"]["status"],
            "headers": {
                h["name"].lower(): h["value"]
                for h in download["response"]["headers"]
                if h["name"].lower() in kept
            },
            "byte_size": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "magic_hex": body[:8].hex(),
            "zip_members": members,
        },
    }
    (fixtures / "document_download.meta.json").write_text(
        json.dumps(download_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(download_meta["response"], indent=2))


if __name__ == "__main__":
    app.run()
