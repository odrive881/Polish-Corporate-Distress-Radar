"""L — error reporting to Sentry, off unless the owner sets `SENTRY_DSN` (plan 0018 step H, decision 8).

Sentry is an external service, so what reaches it is held to a whitelist rather than a filter:
`scrub` rebuilds every event from the exception's type and module, the stack's file and function
names and line numbers, and the Dagster job and asset it failed in. Everything else is dropped:
exception messages (ours carry KRS numbers, document refs and, on the import path, text a filer
wrote), local variables, source lines, extra, request data, breadcrumbs, the host's name and
absolute paths. An event with no exception (a logged message) is not sent at all.

Without a DSN `sentry_sdk.init` is never called: `init(dsn=None)` would still install a client
that reports itself active (plan 0018 step A). A DSN outside Sentry's EU region is refused.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, cast
from urllib.parse import urlsplit

import sentry_sdk
from sentry_sdk.transport import Transport
from sentry_sdk.types import Event, Hint

from distress_radar.settings import Settings

EU_INGEST = ".ingest.de.sentry.io"
TAGS = ("dagster_job", "dagster_asset")
_EVENT_KEYS = ("event_id", "timestamp", "platform", "level", "release", "environment", "sdk")
_FRAME_KEYS = ("function", "module", "lineno", "in_app")


def init_error_reporting(
    settings: Settings | None = None, *, transport: Transport | None = None
) -> bool:
    """Start reporting if `SENTRY_DSN` is set; True if it did. `transport` is for tests."""
    settings = settings or Settings()
    if settings.sentry_dsn is None:
        return False
    dsn = settings.sentry_dsn.get_secret_value()
    host = urlsplit(dsn).hostname or ""
    if not host.endswith(EU_INGEST):
        raise ValueError(f"SENTRY_DSN must be in Sentry's EU region (*{EU_INGEST}), not {host!r}")
    sentry_sdk.init(
        dsn=dsn,
        transport=transport,
        before_send=scrub,
        before_send_transaction=lambda _event, _hint: None,
        send_default_pii=False,
        include_local_variables=False,
        include_source_context=False,
        max_breadcrumbs=0,
        max_request_body_size="never",
        server_name="",
        auto_session_tracking=False,
        send_client_reports=False,
    )
    return True


def report(exc: BaseException, *, job: str | None = None, asset: str | None = None) -> None:
    """Send one exception with where it failed; nothing when reporting is off."""
    if not sentry_sdk.get_client().is_active():
        return
    with sentry_sdk.new_scope() as scope:
        if job is not None:
            scope.set_tag("dagster_job", job)
        if asset is not None:
            scope.set_tag("dagster_asset", asset)
        scope.capture_exception(exc)


def scrub(event: Event, _hint: Hint) -> Event | None:
    """The event rebuilt from the whitelist above, or None when it carries no exception."""
    raw = _mapping(event)
    values = _items(_mapping(raw.get("exception")).get("values"))
    if not values:
        return None
    kept: dict[str, Any] = {key: raw[key] for key in _EVENT_KEYS if key in raw}
    kept["exception"] = {"values": [_exception(value) for value in values]}
    tags = _mapping(raw.get("tags"))
    kept["tags"] = {key: str(tags[key]) for key in TAGS if key in tags}
    return cast("Event", kept)


def _exception(value: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"type": value.get("type")}
    if value.get("module"):
        out["module"] = value["module"]
    mechanism = _mapping(value.get("mechanism"))
    if mechanism:
        out["mechanism"] = {k: mechanism[k] for k in ("type", "handled") if k in mechanism}
    frames = _items(_mapping(value.get("stacktrace")).get("frames"))
    out["stacktrace"] = {"frames": [_frame(frame) for frame in frames]}
    return out


def _frame(frame: Mapping[str, Any]) -> dict[str, Any]:
    out = {key: frame[key] for key in _FRAME_KEYS if key in frame}
    filename = frame.get("filename")
    if isinstance(filename, str):
        # Relative to the import path as Sentry gives it; an absolute one keeps its base name only.
        out["filename"] = os.path.basename(filename) if os.path.isabs(filename) else filename
    return out


def _mapping(value: object) -> Mapping[str, Any]:
    return cast("Mapping[str, Any]", value) if isinstance(value, Mapping) else {}


def _items(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [_mapping(item) for item in cast("list[object]", value)]
