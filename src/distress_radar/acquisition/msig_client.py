"""A4 — MSiG notices through the public search API (plan 0008 step E, ADR 0011).

`wyszukiwarka-msig.ms.gov.pl/api` serves the Monitor Sądowy i Gospodarczy notice base as JSON:
- `Monitor/SearchCount` gives the number of result pages for a KRS and date range;
- `Monitor/Search` gives a page of up to 20 notices (id, issue, date, entity, signature);
- `Monitor/Detalis` gives one notice, with its text.

**Search pages are stored as received.** They hold no personal fields.

**A notice body is never stored.** It names trustees, supervisors and judges (ADR 0009
addendum, item 3). At fetch time the notice is reduced to a person-free record, and that
record is what the raw store holds:
- the structured fields, except the text and the neighbouring notices' ids;
- the chapter code, e.g. `III/1` for "III. … PRAWO UPADŁOŚCIOWE/1. Postanowienie o ogłoszeniu
  upadłości";
- the case signatures, from the signature field and from the text;
- which terms of `config/mappings/msig_vocabulary.yaml` occur in the notice;
- every date in it, as ISO, with the vocabulary terms that occur just before it.

Step F types notices from that record. The record carries its extraction version and the
vocabulary's hash, so a notice reduced under an older vocabulary is fetched and reduced again.

Notices are immutable once published: a run fetches every search page, and only the details
of notices not yet stored under the current extraction version and vocabulary. A notice whose KRS is not the
one searched for is quarantined (`msig_krs_mismatch`) and not stored: search is by KRS, so
this should never happen, and a natural person's notice must never be stored (invariant 6).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field

from distress_radar.acquisition.base import PermanentSourceError, SourceClient, SourcePolicy
from distress_radar.acquisition.models import (
    LegalSourceFetch,
    MsigNoticeRow,
    QuarantineRecord,
    RawFetchRecord,
)
from distress_radar.acquisition.raw_store import ObjectStore, RawDocumentMeta, put_raw, sha256_hex
from distress_radar.acquisition.redaction import RedactionError

MSIG_API = "https://wyszukiwarka-msig.ms.gov.pl/api"
RAW_SOURCE = "msig_api"
MSIG_EXTRACTION_VERSION = "msig-notice-1"
VOCABULARY_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "mappings" / "msig_vocabulary.yaml"
)
SEARCH_FROM = "2001-01-01"  # the notice base's earliest searchable date
# Structured fields kept from a notice; the text and `idNext` / `idPrevious` are not.
KEPT_FIELDS = (
    "id",
    "krs",
    "entityName",
    "numberOfNotice",
    "page",
    "monitorNumber",
    "dateOfPublication",
    "chapterName",
    "signatureOfCase",
)

_MONTHS = {
    "stycznia": 1,
    "lutego": 2,
    "marca": 3,
    "kwietnia": 4,
    "maja": 5,
    "czerwca": 6,
    "lipca": 7,
    "sierpnia": 8,
    "września": 9,
    "października": 10,
    "listopada": 11,
    "grudnia": 12,
}
_DATE = re.compile(
    rf"\b(\d{{1,2}})\s+({'|'.join(_MONTHS)})\s+(\d{{4}})\b|\b(\d{{1,2}})\.(\d{{1,2}})\.(\d{{4}})\b",
    re.IGNORECASE,
)
# "IX GU 103/13", "VIII GRs 1/21", "VI GU 751/19/W" (the suffix is a sub-file); and the
# KRZ-era form "KI1L/GU/43/2025".
_SIGNATURE = re.compile(
    r"\b([IVXL]+\s+G[A-Za-z]{1,3}\s+\d+/\d{2,4})\b|\b([A-Z]{2}\d[A-Z]/G[A-Za-z]{1,3}/\d+/\d{4})\b"
)
_CHAPTER = re.compile(r"^\s*([IVXL]+)\.")
_SUBCHAPTER = re.compile(r"/\s*(\d+)\.")
_PESEL_SHAPED = re.compile(r"(?<!\d)\d{11}(?!\d)")


def msig_policy(requests_per_minute: int) -> SourcePolicy:
    return SourcePolicy(name=RAW_SOURCE, requests_per_minute=requests_per_minute)


class Vocabulary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    context_chars: int = Field(gt=0)
    terms: tuple[str, ...]
    sha256: str = ""

    def found(self, text: str) -> list[str]:
        low = text.lower()
        return [t for t in self.terms if t in low]


def extraction_key(vocabulary: Vocabulary) -> str:
    """What a stored record was reduced under: the extraction version and the vocabulary.

    `msig_notices.extraction_version` holds this, so editing the vocabulary makes every
    notice unknown again, and the next run fetches and reduces it afresh.
    """
    return f"{MSIG_EXTRACTION_VERSION}+{vocabulary.sha256[:12]}"


def load_vocabulary(path: Path = VOCABULARY_PATH) -> Vocabulary:
    raw = path.read_bytes()
    vocabulary = Vocabulary.model_validate({**yaml.safe_load(raw), "sha256": sha256_hex(raw)})
    bad = [t for t in vocabulary.terms if t != t.lower() or not t.strip()]
    if bad or len(set(vocabulary.terms)) != len(vocabulary.terms):
        raise ValueError(f"{path}: terms must be distinct, lowercase and non-empty: {bad}")
    return vocabulary


# --- the person-free notice record ---------------------------------------------------------


def chapter_code(chapter_name: str) -> str | None:
    """`III/1` for "III. PRAWO UPADŁOŚCIOWE/1. Postanowienie…", `IX` for a chapter without parts."""
    chapter = _CHAPTER.match(chapter_name)
    if chapter is None:
        return None
    part = _SUBCHAPTER.search(chapter_name)
    return chapter.group(1) if part is None else f"{chapter.group(1)}/{part.group(1)}"


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def case_signatures(text: str) -> list[str]:
    """Court case signatures in `text`, in order, each once. The one definition of the pattern:
    `parsing.legal_events` reads a notice's signature field with it too."""
    found: list[str] = []
    for match in _SIGNATURE.finditer(_normalise(text)):
        signature = match.group(1) or match.group(2)
        if signature not in found:
            found.append(signature)
    return found


def _signatures(field_value: str | None, text: str) -> list[str]:
    found = case_signatures(field_value or "")
    return found + [s for s in case_signatures(text) if s not in found]


def _dates(text: str, where: str, vocabulary: Vocabulary) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for match in _DATE.finditer(text):
        day, month_name, year, d2, m2, y2 = match.groups()
        try:
            if month_name is not None:
                found = date(int(year), _MONTHS[month_name.lower()], int(day))
            else:
                found = date(int(y2), int(m2), int(d2))
        except ValueError:
            continue  # not a calendar date (a typo in the notice): no date to record
        context = text[max(0, match.start() - vocabulary.context_chars) : match.start()]
        out.append(
            {"date": found.isoformat(), "in": where, "terms_before": vocabulary.found(context)}
        )
    return out


def reduce_notice(detail: dict[str, Any], vocabulary: Vocabulary) -> bytes:
    """The stored form of a notice: structured fields plus what step F needs from the text.

    Deterministic; raises `RedactionError` if anything PESEL-shaped survives.
    """
    position = _normalise(str(detail.get("textInPosition") or ""))
    body = _normalise(str(detail.get("textInBody") or ""))
    text = f"{position} {body}"
    chapter_name = str(detail.get("chapterName") or "")
    record = {
        "notice": {k: detail.get(k) for k in KEPT_FIELDS},
        "extracted": {
            "extraction_version": MSIG_EXTRACTION_VERSION,
            "vocabulary_sha256": vocabulary.sha256,
            "chapter_code": chapter_code(chapter_name),
            "signatures": _signatures(cast("str | None", detail.get("signatureOfCase")), text),
            "terms": vocabulary.found(text),
            "dates": _dates(position, "position", vocabulary) + _dates(body, "body", vocabulary),
        },
    }
    data = json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if _PESEL_SHAPED.search(data):
        raise RedactionError("an 11-digit (PESEL-shaped) run survives notice reduction")
    return data.encode("utf-8")


# --- fetching --------------------------------------------------------------------------------


@dataclass
class MsigResult:
    krs: str
    raw_fetches: list[RawFetchRecord] = field(default_factory=list[RawFetchRecord])
    notices: list[MsigNoticeRow] = field(default_factory=list[MsigNoticeRow])
    fetch: LegalSourceFetch | None = None
    quarantine: list[QuarantineRecord] = field(default_factory=list[QuarantineRecord])


def _json(response: httpx.Response, what: str) -> Any:
    try:
        return response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PermanentSourceError(f"MSiG {what}: not JSON ({exc})") from exc


def _page(body: Any, krs: str, page: int) -> list[dict[str, Any]]:
    items = cast("dict[str, Any]", body).get("list") if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise PermanentSourceError(f"MSiG search for {krs}, page {page}: no `list`")
    notices = cast("list[dict[str, Any]]", items)
    if any(not isinstance(n.get("id"), int) for n in notices):
        raise PermanentSourceError(f"MSiG search for {krs}, page {page}: a notice has no id")
    return notices


async def fetch_entity_notices(
    krs: str,
    *,
    client: SourceClient,
    store: ObjectStore,
    vocabulary: Vocabulary,
    ingestion_run_id: str,
    known: dict[int, str],
    today: date,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> MsigResult:
    """Search MSiG by KRS, store every search page, and store new notices in reduced form.

    `known` maps notice ids already stored under `extraction_key(vocabulary)` to their
    objects; those are not fetched again.
    """
    result = MsigResult(krs)
    params = {"krs": krs, "signatureType": "A", "from": SEARCH_FROM, "to": today.isoformat()}

    def store_raw(url: str, data: bytes, fetched_at: datetime, **extra: str) -> str:
        meta = RawDocumentMeta(
            source=RAW_SOURCE,
            source_url=url,
            content_type="application/json",
            fetched_at=fetched_at,
            http_headers={},
            ingestion_run_id=ingestion_run_id,
            **extra,
        )
        digest = put_raw(store, data, meta)
        result.raw_fetches.append(RawFetchRecord(sha256=digest, byte_size=len(data), meta=meta))
        return digest

    count = _json(await client.get(f"{MSIG_API}/Monitor/SearchCount", params=params), "SearchCount")
    if not isinstance(count, int) or count < 1:
        raise PermanentSourceError(f"MSiG SearchCount for {krs}: unexpected {count!r}")
    listing_sha: str | None = None
    listed: list[dict[str, Any]] = []
    for page in range(1, count + 1):
        page_params = {**params, "page": str(page)}
        response = await client.get(f"{MSIG_API}/Monitor/Search", params=page_params)
        listed += _page(_json(response, "Search"), krs, page)
        url = str(httpx.URL(f"{MSIG_API}/Monitor/Search", params=page_params))
        digest = store_raw(url, response.content, now())
        listing_sha = listing_sha or digest
    assert listing_sha is not None

    held: dict[int, str] = {}
    for item in listed:
        notice_id = cast("int", item["id"])
        if notice_id in held:
            continue
        if notice_id in known:
            held[notice_id] = known[notice_id]
            continue
        response = await client.get(f"{MSIG_API}/Monitor/Detalis", params={"Id": str(notice_id)})
        fetched_at = now()
        detail = _json(response, f"notice {notice_id}")
        if not isinstance(detail, dict):
            raise PermanentSourceError(f"MSiG notice {notice_id}: not an object")
        detail = cast("dict[str, Any]", detail)
        entity_key = f"{krs}:msig:{notice_id}"
        if str(detail.get("krs") or "").strip() != krs:
            result.quarantine.append(
                _quarantine(
                    krs,
                    entity_key,
                    "msig_krs_mismatch",
                    f"notice {notice_id} is for KRS {detail.get('krs')!r}",
                    ingestion_run_id,
                    now(),
                )
            )
            continue
        try:
            reduced = reduce_notice(detail, vocabulary)
        except RedactionError as exc:
            result.quarantine.append(
                _quarantine(
                    krs, entity_key, "msig_notice_unredactable", str(exc), ingestion_run_id, now()
                )
            )
            continue
        url = str(httpx.URL(f"{MSIG_API}/Monitor/Detalis", params={"Id": str(notice_id)}))
        digest = store_raw(
            url,
            reduced,
            fetched_at,
            # The extraction key, not the bare version: a vocabulary change is a new reduction.
            redaction_version=extraction_key(vocabulary),
            received_sha256=sha256_hex(response.content),
        )
        held[notice_id] = digest
        published = datetime.fromisoformat(str(detail["dateOfPublication"])).date()
        result.notices.append(
            MsigNoticeRow(
                krs=krs,
                notice_id=notice_id,
                sha256=digest,
                published_on=published,
                chapter_code=chapter_code(str(detail.get("chapterName") or "")),
                extraction_version=extraction_key(vocabulary),
                ingestion_run_id=ingestion_run_id,
            )
        )

    content = "\n".join(f"{i}:{held[i]}" for i in sorted(held))
    result.fetch = LegalSourceFetch(
        krs=krs,
        source="MSiG",
        sha256=listing_sha,
        content_sha256=sha256_hex(content.encode("utf-8")),
        fetched_at=now(),
        ingestion_run_id=ingestion_run_id,
        stored_new=bool(result.notices),
    )
    return result


def _quarantine(
    krs: str, entity_key: str, reason: str, detail: str, run_id: str, at: datetime
) -> QuarantineRecord:
    return QuarantineRecord(
        stage="A4",
        entity_key=entity_key,
        reason_code=reason,
        detail=detail,
        source_document_hash=None,
        ingestion_run_id=run_id,
        created_at=at,
        krs=krs,
        document_ref=None,
    )
