"""RDF access probe (plan 0002 §G) — a time-boxed spike, not production code.

Drives the shared `distress_radar.acquisition.base` client against the public
RDF lookup hosts for a few seed KRS numbers at a very low rate, and records
status codes, anti-bot gating, response shape, and download-URL / submission
date exposure. Every fetched body goes through `put_raw` (MinIO when reachable,
otherwise an in-memory store — the probe must not need `make dev-up`).

It never tries to get past anti-bot controls: it identifies itself honestly
and stops at the first gate. Findings: docs/adr/0007-rdf-access-probe-results.md.

Run interactively: `uv run marimo edit notebooks/exploration/rdf_access_probe.py`
Run headless:      `uv run python notebooks/exploration/rdf_access_probe.py`
"""

import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium")


@app.cell
def _():
    import asyncio
    import uuid
    from datetime import UTC, datetime
    from pathlib import Path

    import httpx

    from distress_radar.acquisition.base import (
        PermanentSourceError,
        SourcePolicy,
        TransientSourceError,
        build_source_client,
        in_memory_limiter,
    )
    from distress_radar.acquisition.raw_store import (
        InMemoryObjectStore,
        ObjectStore,
        RawDocumentMeta,
        S3ObjectStore,
        put_raw,
    )
    from distress_radar.settings import Settings

    return (
        InMemoryObjectStore,
        ObjectStore,
        Path,
        PermanentSourceError,
        RawDocumentMeta,
        S3ObjectStore,
        Settings,
        SourcePolicy,
        TransientSourceError,
        UTC,
        asyncio,
        build_source_client,
        datetime,
        httpx,
        in_memory_limiter,
        put_raw,
        uuid,
    )


@app.cell
def _(mo_md):
    mo_md(
        """
        # RDF access probe

        Question for ADR 0007: can A3 read RDF with plain `httpx`, does it need a
        Playwright tier, or is access blocked (CAPTCHA / WAF)?
        """
    )


@app.cell
def _():
    def mo_md(text: str) -> None:
        import marimo as mo

        if mo.running_in_notebook():
            mo.output.append(mo.md(text))
        else:
            print(text)

    return (mo_md,)


@app.cell
def _(SourcePolicy):
    # Seed KRS numbers from config/segments/construction_sme_v1_seed.yaml
    PROBE_KRS = ["0000163893", "0000507997", "0000277937"]

    # Hosts, per ADR 0004 and the PRS portal's own env.js (rdfSearchUrl, rdfUrl).
    TARGETS = [
        ("legacy lookup (ADR 0004)", "https://ekrs.ms.gov.pl/rdf/rd/"),
        ("RDF portal", "https://rdf.ms.gov.pl/"),
        ("RDF search UI", "https://rdf-przegladarka.ms.gov.pl/"),
    ]
    PER_KRS_PATH = "https://rdf-przegladarka.ms.gov.pl/wyszukaj-podmiot?krs={krs}"

    # 3 requests/minute: this is a probe, not acquisition.
    POLICY = SourcePolicy(name="rdf_probe", requests_per_minute=3, max_attempts=2)
    return PER_KRS_PATH, POLICY, PROBE_KRS, TARGETS


@app.cell
def _(httpx):
    def detect_gate(response: httpx.Response) -> str | None:
        """The "did we get real content" check. Returns a reason when gated."""
        body = response.content[:20_000].lower()
        cookies = " ".join(response.headers.get_list("set-cookie")).lower()
        if b"_incapsula_resource" in body or b"incapsula incident id" in body:
            return "imperva_incapsula_block_page"
        if "incap_ses" in cookies and len(response.content) < 2_000:
            return "imperva_incapsula_cookie_challenge"
        if b"captcha" in body:
            return "captcha"
        if b"enable javascript" in body:
            return "javascript_required"
        return None

    def describe_shape(response: httpx.Response) -> str:
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            return "json"
        if "xml" in content_type:
            return "xml"
        if b"<app-root" in response.content or b"main-es2015" in response.content:
            return "html_spa_shell"
        return "html" if "html" in content_type else content_type or "unknown"

    return describe_shape, detect_gate


@app.cell
def _(InMemoryObjectStore, ObjectStore, S3ObjectStore, Settings):
    def choose_store() -> tuple[ObjectStore, str]:
        settings = Settings()
        s3 = S3ObjectStore.from_endpoint(
            settings.minio_endpoint,
            settings.minio_access_key,
            settings.minio_secret_key.get_secret_value(),
            settings.minio_bucket,
        )
        try:
            s3.exists("raw/probe-connectivity-check")
        except Exception as exc:  # noqa: BLE001 - any failure means "no MinIO here"
            return InMemoryObjectStore(), f"in-memory (MinIO unavailable: {type(exc).__name__})"
        return s3, f"MinIO bucket {settings.minio_bucket}"

    return (choose_store,)


@app.cell
def _(
    PER_KRS_PATH,
    POLICY,
    PROBE_KRS,
    Path,
    PermanentSourceError,
    RawDocumentMeta,
    Settings,
    TARGETS,
    TransientSourceError,
    UTC,
    asyncio,
    build_source_client,
    choose_store,
    datetime,
    describe_shape,
    detect_gate,
    in_memory_limiter,
    put_raw,
    uuid,
):
    async def probe() -> tuple[list[dict[str, object]], str]:
        store, store_label = choose_store()
        run_id = f"rdf-probe-{uuid.uuid4().hex[:8]}"
        rows: list[dict[str, object]] = []
        client = build_source_client(
            POLICY,
            cache_dir=Path(Settings().http_cache_dir),
            limiter=in_memory_limiter(POLICY),
        )
        targets = list(TARGETS) + [
            (f"per-KRS lookup {krs}", PER_KRS_PATH.format(krs=krs)) for krs in PROBE_KRS
        ]
        async with client:
            for label, url in targets:
                row: dict[str, object] = {"target": label, "url": url}
                try:
                    response = await client.get(url)
                except (TransientSourceError, PermanentSourceError) as exc:
                    row["error"] = str(exc)
                    rows.append(row)
                    continue
                # Raw-first: store whatever came back (block pages included) before judging it.
                digest = put_raw(
                    store,
                    response.content,
                    RawDocumentMeta(
                        source="rdf_probe",
                        source_url=str(response.url),
                        content_type=response.headers.get(
                            "content-type", "application/octet-stream"
                        ),
                        fetched_at=datetime.now(UTC),
                        http_headers={
                            k: v for k, v in response.headers.items() if k != "set-cookie"
                        },
                        ingestion_run_id=run_id,
                    ),
                )
                gate = detect_gate(response)
                row.update(
                    redirects=" -> ".join(
                        f"{r.status_code} {r.headers.get('location')}" for r in response.history
                    )
                    or None,
                    final_url=str(response.url),
                    status=response.status_code,
                    bytes=len(response.content),
                    shape=describe_shape(response),
                    server=response.headers.get("server"),
                    waf="incapsula" if "x-iinfo" in response.headers else None,
                    gate=gate,
                    # What base.SourceClient raises when given this check (plan 0003's seam).
                    content_check=(
                        f"ContentCheckFailed({gate})" if gate is not None else "real content"
                    ),
                    raw_sha256=digest,
                )
                rows.append(row)
        return rows, store_label

    results, store_used = asyncio.run(probe())
    return results, store_used


@app.cell
def _(mo_md, results, store_used):
    lines = [f"Raw bodies stored in: {store_used}", ""]
    for row in results:
        lines.append(
            " | ".join(
                f"{key}={row.get(key)}"
                for key in (
                    "target",
                    "redirects",
                    "final_url",
                    "status",
                    "bytes",
                    "shape",
                    "waf",
                    "gate",
                    "content_check",
                    "error",
                )
                if row.get(key) is not None
            )
        )
    mo_md("\n\n".join(lines))


if __name__ == "__main__":
    app.run()
