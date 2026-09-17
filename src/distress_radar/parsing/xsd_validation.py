"""C1: validate a statement against its official XSD, offline (AGENT_SPEC §6C1).

Every schema a spec names, and everything it imports, is vendored under
`config/xsd/` and listed in `config/xsd/catalog.yaml` by the URL documents and
schemas cite. `xmlschema` resolves every URL through that catalog and may only
open local files, so validation never touches the network.

Enveloped XAdES signatures (`ds:Signature` inside the statement) are not part
of the MF schemas; they are removed from an in-memory copy before validation.
"""

from __future__ import annotations

import copy
from pathlib import Path

import xmlschema
import yaml

from distress_radar.parsing.canonical_schema import CONFIG_DIR, StructureSpec
from distress_radar.parsing.containers import DS_NS, Element

MAX_ERRORS = 5
MAX_ERROR_LENGTH = 300


def load_xsd_catalog(xsd_dir: Path) -> dict[str, Path]:
    raw = yaml.safe_load((xsd_dir / "catalog.yaml").read_text(encoding="utf-8"))
    schemas: dict[str, dict[str, str]] = raw["schemas"]
    return {url: (xsd_dir / entry["path"]).resolve() for url, entry in schemas.items()}


class XsdValidator:
    def __init__(self, xsd_dir: Path = CONFIG_DIR / "xsd") -> None:
        self._files = load_xsd_catalog(xsd_dir)
        self._uri_map = {url: path.as_uri() for url, path in self._files.items()}
        self._schemas: dict[str, xmlschema.XMLSchema] = {}

    def has_schema(self, url: str) -> bool:
        return url in self._files

    def schema(self, url: str) -> xmlschema.XMLSchema:
        if url not in self._schemas:
            if url not in self._files:
                raise KeyError(f"XSD {url} is not vendored in config/xsd/catalog.yaml")
            self._schemas[url] = xmlschema.XMLSchema(
                self._uri_map[url], uri_mapper=self._uri_map, allow="local", defuse="always"
            )
        return self._schemas[url]

    def validate(self, root: Element, spec: StructureSpec) -> list[str]:
        """Up to `MAX_ERRORS` validation messages; empty when the document is valid."""
        document = copy.deepcopy(root)
        for signature in list(document.iter(f"{{{DS_NS}}}Signature")):
            parent = signature.getparent()
            if parent is not None:
                parent.remove(signature)
        errors: list[str] = []
        for error in self.schema(spec.xsd).iter_errors(document):
            message = f"{error.path}: {error.reason}" if error.path else str(error.reason)
            errors.append(message[:MAX_ERROR_LENGTH])
            if len(errors) >= MAX_ERRORS:
                break
        return errors
