"""Legal events (plan 0008 step F; AGENT_SPEC §4.6, §4.7, §5): KRS extracts and MSiG notices → rows.

Pure functions, one per source, over documents already in the raw store:
- `from_krs_extract` walks a redacted KRS extract;
- `from_msig_notice` reads a reduced MSiG notice record (never the text, ADR 0009 addendum).

Each emits `legal_events` rows and rejects (quarantine reasons). The taxonomy
(`config/statutory/procedure_taxonomy.yaml`) decides what a source record means, and nothing
is mapped by guess.

**Two dates, never mixed (plan 0008 decision 2).** `event_date` is when the court or the
shareholders decided, and labels use it. `known_from` is when the source made the fact public,
and features use it:
- for KRS, the date of the registry entry that introduced the record (`nrWpisuWprow`), or a
  header entry's own date;
- for MSiG, the publication date.

A record whose entry has no date is rejected (`krs_entry_date_missing`), never dated by guess.
An event with no decision date keeps `event_date` null (the owner's rule, 2026-09-23).

**One proceeding, one `proceeding_id`.** A proceeding has several case files (the petition's
`GU`, the proceeding's `GUp`; a sanacja's `GR` and `GRs`). An MSiG notice lists the files it
belongs to in its signature field, and `finalise` links the signatures listed together.
Every row then carries the signature of the linked set that was published first, and keeps
its own as `case_signature`; a row without one takes its group's (below).

**Deduplication keeps every row.** `dedup_group_id` groups rows describing the same event
across sources, by entity, the event's kind, and an anchor:
- the kind is the event type, except that openings group by outcome class: the KRS
  extract's generic "restructuring opened" and MSiG's "sanacja opened" are one opening;
- the anchor is the `proceeding_id` if there is one;
- a row without one joins the single group of its kind with the same event date;
- a row with no date either joins the single group of its kind whose event date is at most
  a year before the row became known (0000440028's registry entry for a liquidation whose
  resolution date only MSiG gives);
- otherwise the row's own date, or its `known_from`, is the anchor.

The group's canonical event is derived downstream, never stored in place of its rows.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, cast

import polars as pl

from distress_radar.parsing.legal_taxonomy import (
    ProcedureTaxonomy,
    SourceMapping,
)
from distress_radar.parsing.msig_notice_kinds import NoticeKinds, event_date

LEGAL_EVENTS_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "event_type": pl.String,
    "outcome_class": pl.String,
    "stage": pl.String,
    # From the taxonomy, so labels need nothing but this table: the classes the event closes,
    # and whether it rules out a later silent exit (plan 0008 decision 5).
    "ends": pl.List(pl.String),
    "precludes_silent_exit": pl.Boolean,
    "event_date": pl.Date,
    "known_from": pl.Date,
    "removed_on": pl.Date,
    "source": pl.String,
    "case_signature": pl.String,
    "proceeding_id": pl.String,
    "statute": pl.String,
    "dedup_group_id": pl.String,
    "source_document_hash": pl.String,
    "source_element_path": pl.String,
    "ingestion_run_id": pl.String,
    "event_year": pl.Int32,
}
SORT_KEY = ["krs", "known_from", "event_type", "source", "source_element_path"]

_DMY = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")
_SIGNATURE_FIELDS = ("sygnatura", "sygnaturaSprawy")
# dzial4 carries petition-stage orders in this section only (ADR 0011).
_SECURITY_SECTION = "dane.dzial4.zabezpieczenieMajatkuOddalenieWnioskuOUpadlosc"
# One case signature in MSiG's `signatureOfCase` field ("VIII GU 45/17,  VIII GUp 41/17.").
_SIGNATURE_FIELD = re.compile(
    r"[IVXL]+\s+G[A-Za-z]{1,3}\s+\d+/\d{2,4}|[A-Z]{2}\d[A-Z]/G[A-Za-z]{1,3}/\d+/\d{4}"
)
_ADOPTION_WINDOW_DAYS = 366


@dataclass(frozen=True)
class LegalEvent:
    krs: str
    event_type: str
    outcome_class: str | None
    stage: str
    ends: tuple[str, ...]
    precludes_silent_exit: bool
    event_date: date | None
    known_from: date
    removed_on: date | None
    source: str
    case_signature: str | None  # the row's own, normalised
    statute: str
    source_document_hash: str
    source_element_path: str
    ingestion_run_id: str
    # Set by `finalise`, across all rows of the entity.
    proceeding_id: str | None = None
    dedup_group_id: str = ""

    @property
    def dedup_kind(self) -> str:
        return f"opening:{self.outcome_class}" if self.stage == "opening" else self.event_type


@dataclass(frozen=True)
class LegalEventReject:
    krs: str
    source: str
    source_document_hash: str
    source_element_path: str
    reason_code: str
    detail: str


@dataclass
class Normalised:
    events: list[LegalEvent] = field(default_factory=list[LegalEvent])
    rejects: list[LegalEventReject] = field(default_factory=list[LegalEventReject])
    # Signatures one MSiG notice lists together: case files of one proceeding, per KRS.
    aliases: list[tuple[str, tuple[str, ...]]] = field(
        default_factory=list[tuple[str, tuple[str, ...]]]
    )

    def extend(self, other: Normalised) -> None:
        self.events.extend(other.events)
        self.rejects.extend(other.rejects)
        self.aliases.extend(other.aliases)


def normalise_signature(raw: str | None) -> str | None:
    """`IX GU 103/13.` → `IX/GU/103/13`; `KI1L/GU/43/2025` stays.

    Case and separators are folded, and a signature is its four parts (court unit, department,
    number, year): a sub-file suffix (`VI GU 751/19/W`) or a registry typo
    (`RZ1Z/GU/5/2022/20`) after the year is dropped.
    """
    if raw is None:
        return None
    tokens = [t for t in re.split(r"[\s/]+", raw.strip().rstrip(".").upper()) if t]
    if len(tokens) > 4 and re.fullmatch(r"\d{2}|\d{4}", tokens[3]):
        tokens = tokens[:4]
    return "/".join(tokens) or None


def _parse_dmy(text: str | None, *, leading: bool) -> date | None:
    if not text:
        return None
    match = _DMY.search(text) if leading else _DMY.fullmatch(text.strip())
    if match is None:
        return None
    day, month, year = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


# --- KRS -----------------------------------------------------------------------------------------


def _records(node: object, parts: list[str], path: str) -> Iterator[tuple[str, dict[str, Any]]]:
    """(element path, record) for every dict at `parts` below `node`, list indices kept."""
    if isinstance(node, list):
        for index, item in enumerate(cast("list[object]", node)):
            yield from _records(item, parts, f"{path}[{index}]")
        return
    if not isinstance(node, dict):
        return
    record = cast("dict[str, Any]", node)
    if not parts:
        yield path, record
        return
    yield from _records(record.get(parts[0]), parts[1:], f"{path}.{parts[0]}")


def _sections(locator: str) -> str:
    """The section a locator belongs to, for spotting sections the taxonomy does not know."""
    parts = locator.split(".")
    depth = 4 if locator.startswith(_SECURITY_SECTION) else 3
    return ".".join(parts[:depth])


def _unknown_sections(odpis: Mapping[str, Any], known: set[str]) -> Iterator[str]:
    dane = cast("dict[str, Any]", odpis.get("dane") or {})
    dzial6 = cast("dict[str, Any]", dane.get("dzial6") or {})
    for key, value in dzial6.items():
        section = f"dane.dzial6.{key}"
        if value and section not in known:
            yield section
    dzial4 = cast("dict[str, Any]", dane.get("dzial4") or {})
    orders = cast("list[dict[str, Any]]", dzial4.get(_SECURITY_SECTION.rsplit(".", 1)[1]) or [])
    for index, item in enumerate(orders):
        for key, value in item.items():
            section = f"{_SECURITY_SECTION}.{key}"
            if value and section not in known:
                yield f"{section}[{index}]"


def from_krs_extract(
    extract: Mapping[str, Any],
    *,
    krs: str,
    taxonomy: ProcedureTaxonomy,
    source_document_hash: str,
    ingestion_run_id: str,
) -> Normalised:
    """Events in one redacted KRS extract (every entry of its history)."""
    out = Normalised()
    odpis = cast("dict[str, Any]", extract["odpis"])
    entry_dates = {
        str(e["numerWpisu"]): _parse_dmy(e.get("dataWpisu"), leading=False)
        for e in odpis["naglowekP"]["wpis"]
    }

    def reject(path: str, reason: str, detail: str) -> None:
        out.rejects.append(LegalEventReject(krs, "KRS", source_document_hash, path, reason, detail))

    by_locator: dict[str, list[SourceMapping]] = {}
    for mapping in taxonomy.mappings:
        if mapping.source == "KRS":
            by_locator.setdefault(mapping.locator, []).append(mapping)

    known_sections = {_sections(loc) for loc in by_locator}
    for section in _unknown_sections(odpis, known_sections):
        reject(
            section, "legal_event_type_unmapped", "a proceeding section the taxonomy does not map"
        )

    for locator, mappings in by_locator.items():
        for path, record in _records(odpis, locator.split("."), ""):
            path = path.lstrip(".")
            fields = {k: v for k, v in record.items() if isinstance(v, str)}
            matching = [m for m in mappings if m.when is None or m.when.matches(fields)]
            if not matching:
                categorical = [m.when for m in mappings if m.when is not None and m.when.equals]
                if categorical:
                    field_name = categorical[0].field
                    reject(
                        path,
                        "legal_event_type_unmapped",
                        f"{field_name}={fields.get(field_name)!r}",
                    )
                continue  # a `contains` / `present` test that fails: not an event
            if "dataWpisu" in fields:  # a header entry carries its own date
                known_from = _parse_dmy(fields["dataWpisu"], leading=False)
            else:
                known_from = entry_dates.get(fields.get("nrWpisuWprow", ""))
            if known_from is None:
                reject(
                    path,
                    "krs_entry_date_missing",
                    f"entry {fields.get('nrWpisuWprow')!r} has no date",
                )
                continue
            template = matching[0]
            if template.date_field is None:
                decided = known_from  # the registry's own act, e.g. a deregistration
            else:
                decided = _parse_dmy(
                    fields.get(template.date_field), leading=template.date_parse == "leading"
                )
            resolved = taxonomy.resolve("KRS", locator, fields, decided or known_from)
            if resolved is None:
                reject(
                    path,
                    "legal_event_type_unmapped",
                    f"no mapping in force on {decided or known_from}",
                )
                continue
            removed = fields.get("nrWpisuWykr")
            signature = next((fields[f] for f in _SIGNATURE_FIELDS if fields.get(f)), None)
            out.events.append(
                LegalEvent(
                    krs=krs,
                    event_type=resolved.event_type.event_type,
                    outcome_class=resolved.event_type.outcome_class,
                    ends=resolved.event_type.ends,
                    precludes_silent_exit=resolved.event_type.precludes_silent_exit,
                    stage=resolved.event_type.stage,
                    event_date=decided,
                    known_from=known_from,
                    removed_on=entry_dates.get(removed) if removed else None,
                    source="KRS",
                    case_signature=normalise_signature(signature),
                    statute=resolved.statute.id,
                    source_document_hash=source_document_hash,
                    source_element_path=path,
                    ingestion_run_id=ingestion_run_id,
                )
            )
    return out


# --- MSiG ----------------------------------------------------------------------------------------


def from_msig_notice(
    record: Mapping[str, Any],
    *,
    taxonomy: ProcedureTaxonomy,
    kinds: NoticeKinds,
    source_document_hash: str,
    ingestion_run_id: str,
) -> Normalised:
    """The events in one reduced MSiG notice: its kind's, and any further one it records."""
    out = Normalised()
    notice = cast("dict[str, Any]", record["notice"])
    extracted = cast("dict[str, Any]", record["extracted"])
    krs = str(notice["krs"]).strip()
    path = f"msig:{notice['monitorNumber']}:{notice['id']}"
    published = date.fromisoformat(str(notice["dateOfPublication"])[:10])

    def reject(reason: str, detail: str) -> Normalised:
        out.rejects.append(
            LegalEventReject(krs, "MSiG", source_document_hash, path, reason, detail)
        )
        return out

    rule = kinds.classify(extracted)
    if rule is None:
        return reject(
            "msig_notice_unclassified",
            f"chapter {extracted.get('chapter_code')!r}, terms {extracted.get('terms')}",
        )
    signatures = cast("list[str]", extracted.get("signatures") or [])
    # Aliases come from the structured field only; the text can cite other cases.
    listed = [
        normalise_signature(m)
        for m in _SIGNATURE_FIELD.findall(str(notice.get("signatureOfCase") or ""))
    ]
    linked = tuple(sorted({s for s in listed if s}))
    if len(linked) > 1:
        out.aliases.append((krs, linked))
    # The notice's kind, then any further event the same decision records.
    rules = ([rule] if rule.event else []) + kinds.additional(extracted)
    for index, current in enumerate(rules):
        decided = event_date(current, extracted, published)
        on = decided or published
        resolved = taxonomy.resolve("MSiG", "notice", {"notice_kind": current.kind}, on)
        if resolved is None:
            reject("legal_event_type_unmapped", f"{current.kind} has no mapping in force on {on}")
            continue
        out.events.append(
            LegalEvent(
                krs=krs,
                event_type=resolved.event_type.event_type,
                outcome_class=resolved.event_type.outcome_class,
                ends=resolved.event_type.ends,
                precludes_silent_exit=resolved.event_type.precludes_silent_exit,
                stage=resolved.event_type.stage,
                event_date=decided,
                known_from=published,
                removed_on=None,
                source="MSiG",
                case_signature=normalise_signature(signatures[0]) if signatures else None,
                statute=resolved.statute.id,
                source_document_hash=source_document_hash,
                # One element per event: a further event is addressed by its kind.
                source_element_path=path if index == 0 and rule.event else f"{path}#{current.kind}",
                ingestion_run_id=ingestion_run_id,
            )
        )
    return out
    decided = event_date(rule, extracted, published)
    resolved = taxonomy.resolve("MSiG", "notice", {"notice_kind": rule.kind}, decided or published)
    if resolved is None:
        return reject(
            "legal_event_type_unmapped", f"{rule.kind} has no mapping in force on {decided}"
        )
    signatures = cast("list[str]", extracted.get("signatures") or [])
    # Aliases come from the structured field only; the text can cite other cases.
    listed = [
        normalise_signature(m)
        for m in _SIGNATURE_FIELD.findall(str(notice.get("signatureOfCase") or ""))
    ]
    linked = tuple(sorted({s for s in listed if s}))
    if len(linked) > 1:
        out.aliases.append((krs, linked))
    out.events.append(
        LegalEvent(
            krs=krs,
            event_type=resolved.event_type.event_type,
            outcome_class=resolved.event_type.outcome_class,
            ends=resolved.event_type.ends,
            precludes_silent_exit=resolved.event_type.precludes_silent_exit,
            stage=resolved.event_type.stage,
            event_date=decided,
            known_from=published,
            removed_on=None,
            source="MSiG",
            case_signature=normalise_signature(signatures[0]) if signatures else None,
            statute=resolved.statute.id,
            source_document_hash=source_document_hash,
            source_element_path=path,
            ingestion_run_id=ingestion_run_id,
        )
    )
    return out


# --- the dataset ---------------------------------------------------------------------------------


def _link(normalised: Normalised) -> dict[tuple[str, str], str]:
    """(krs, signature) → the proceeding it belongs to, per linked set of signatures."""
    parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(key: tuple[str, str]) -> tuple[str, str]:
        while parent.setdefault(key, key) != key:
            key = parent[key]
        return key

    for krs, linked in normalised.aliases:
        first = find((krs, linked[0]))
        for other in linked[1:]:
            parent[find((krs, other))] = first
    for e in normalised.events:
        if e.case_signature:
            find((e.krs, e.case_signature))
    # Name each set by its signature published first (then alphabetically): stable across runs.
    first_seen: dict[tuple[str, str], date] = {}
    for e in normalised.events:
        if e.case_signature:
            key = (e.krs, e.case_signature)
            first_seen[key] = min(first_seen.get(key, e.known_from), e.known_from)
    members: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for key in list(parent):
        members.setdefault(find(key), []).append(key)
    names: dict[tuple[str, str], str] = {}
    for group in members.values():
        named = min(group, key=lambda k: (first_seen.get(k, date.max), k[1]))
        for key in group:
            names[key] = named[1]
    return names


def _group_id(krs: str, kind: str, anchor: str) -> str:
    return hashlib.sha256(f"{krs}|{kind}|{anchor}".encode()).hexdigest()


def finalise(normalised: Normalised) -> list[LegalEvent]:
    """Every row with its `proceeding_id` and `dedup_group_id` (see the module docstring)."""
    names = _link(normalised)
    linked = [
        replace(e, proceeding_id=names[(e.krs, e.case_signature)] if e.case_signature else None)
        for e in normalised.events
    ]
    anchored: dict[int, str] = {}
    for i, e in enumerate(linked):
        if e.proceeding_id:
            anchored[i] = f"case:{e.proceeding_id}"
        elif e.event_date is not None:
            anchored[i] = f"date:{e.event_date.isoformat()}"
    # A dated row without a signature joins the one signed group of its kind on that date.
    for i, e in enumerate(linked):
        if e.proceeding_id or e.event_date is None:
            continue
        signed = {
            anchored[j]
            for j, o in enumerate(linked)
            if o.proceeding_id
            and o.krs == e.krs
            and o.dedup_kind == e.dedup_kind
            and o.event_date == e.event_date
        }
        if len(signed) == 1:
            anchored[i] = signed.pop()
    # An undated row joins the one group of its kind decided in the year before it was known.
    for i, e in enumerate(linked):
        if i in anchored:
            continue
        candidates = {
            anchored[j]
            for j, o in enumerate(linked)
            if j in anchored
            and o.krs == e.krs
            and o.dedup_kind == e.dedup_kind
            and o.event_date is not None
            and 0 <= (e.known_from - o.event_date).days <= _ADOPTION_WINDOW_DAYS
        }
        anchored[i] = (
            candidates.pop() if len(candidates) == 1 else f"known:{e.known_from.isoformat()}"
        )
    grouped = [
        replace(e, dedup_group_id=_group_id(e.krs, e.dedup_kind, anchored[i]))
        for i, e in enumerate(linked)
    ]
    # A row that joined a signed group belongs to that group's proceeding.
    proceeding = {e.dedup_group_id: e.proceeding_id for e in grouped if e.proceeding_id}
    return [
        e if e.proceeding_id else replace(e, proceeding_id=proceeding.get(e.dedup_group_id))
        for e in grouped
    ]


def to_frame(events: list[LegalEvent]) -> pl.DataFrame:
    """`legal_events` rows in a fixed order, so equal input writes equal bytes."""
    rows = [
        {
            **{k: getattr(e, k) for k in LEGAL_EVENTS_SCHEMA if k not in ("event_year", "ends")},
            "ends": list(e.ends),
            "event_year": (e.event_date or e.known_from).year,
        }
        for e in events
    ]
    frame = pl.DataFrame(rows, schema=LEGAL_EVENTS_SCHEMA, orient="row")
    return frame.sort(SORT_KEY, nulls_last=True)
