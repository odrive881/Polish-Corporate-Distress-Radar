"""C1: which XSD violations a file may carry and still parse (plan 0017 step E, decision 2).

`config/mappings/xsd_tolerances_v<n>.yaml` lists element paths and error kinds no stage reads,
where filers' software leaves defects the statements themselves do not have (a placeholder
`KodPKD`, an empty address field). A file parses only if every violation matches an entry; any
other violation quarantines it as `xsd_invalid`, as before. Tolerated violations are returned
so the caller records them (`xsd_tolerated`) and caps the file's grade at `warn`.

`canonical_schema` refuses, at load, an entry that reaches the header or a statement a spec
binds; `check_introduction` adds the introduction items each version's generation reads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from distress_radar.parsing.canonical_schema import (
    MappingConfig,
    StructureSpec,
    XsdErrorKind,
    XsdTolerances,
    matches,
    overlaps,
    path_segments,
)
from distress_radar.parsing.statement_introduction import IntroductionConfig

# The validator's reason -> its kind. A reason no pattern matches has no kind: never tolerable.
_KINDS: tuple[tuple[XsdErrorKind, re.Pattern[str]], ...] = (
    ("child_in_simple_content", re.compile(r"a simple content element can't have child elements")),
    ("not_in_enumeration", re.compile(r"^value must be one of")),
    ("too_short", re.compile(r"^value length cannot be lesser than")),
    ("pattern_mismatch", re.compile(r"^value doesn't match any pattern")),
    ("content_incomplete", re.compile(r"^The content of element '[^']*' is not complete")),
    ("unexpected_child", re.compile(r"^Unexpected child with tag")),
    ("invalid_decimal", re.compile(r"^invalid value '.*' for xs:decimal")),
)
ERROR_KINDS: tuple[XsdErrorKind, ...] = tuple(kind for kind, _ in _KINDS)


@dataclass(frozen=True)
class Violation:
    """One XSD error: the validator's message, and what a tolerance is matched on."""

    message: str
    path: tuple[str, ...] | None  # local names below the statement root; None if outside it
    kind: XsdErrorKind | None


@dataclass(frozen=True)
class Tolerated:
    tolerance_id: str
    path: str
    kind: XsdErrorKind

    def describe(self) -> str:
        return f"{self.tolerance_id}: {self.path} ({self.kind})"


def error_kind(reason: str) -> XsdErrorKind | None:
    for kind, pattern in _KINDS:
        if pattern.search(reason):
            return kind
    return None


def _local_names(path: str) -> tuple[str, ...]:
    """`/tns:A/tns:B[2]/c` -> ("A", "B", "c"): prefixes and positions differ between filers."""
    return tuple(re.sub(r"\[\d+\]$", "", part).split(":")[-1] for part in path_segments(path))


def below_statement_root(error_path: str | None, spec: StructureSpec) -> tuple[str, ...] | None:
    """An error's path below the spec's statement root, or None when it is not inside it."""
    if not error_path:
        return None
    root = _local_names(spec.statement_root)
    names = _local_names(error_path)
    if names[: len(root)] != root or len(names) == len(root):
        return None
    return names[len(root) :]


def split(
    violations: list[Violation], spec: StructureSpec, tolerances: XsdTolerances
) -> tuple[list[Violation], list[Tolerated]]:
    """(the violations no entry tolerates, the ones tolerated, with the entry that does)."""
    entries = tolerances.for_spec(spec.structure_version)
    refused: list[Violation] = []
    tolerated: list[Tolerated] = []
    for v in violations:
        hit = next(
            (
                t
                for t in entries
                if v.path is not None
                and v.kind in t.errors
                and any(matches(p, v.path) for p in t.patterns)
            ),
            None,
        )
        if hit is None or v.path is None or v.kind is None:
            refused.append(v)
        else:
            tolerated.append(Tolerated(hit.id, "/".join(v.path), v.kind))
    return refused, tolerated


def introduction_paths(
    structure_version: str, introduction: IntroductionConfig
) -> tuple[tuple[str, ...], ...]:
    """Every introduction item a version's generation reads, in every block layout."""
    generation = introduction.generation_of(structure_version)
    return tuple(
        (block, *path_segments(item.path[layout]))
        for block, layout in sorted(introduction.blocks.items())
        for item in introduction.items.values()
        if generation in item.generations and layout in item.path
    )


def check_introduction(config: MappingConfig, introduction: IntroductionConfig) -> None:
    """Refuse a tolerance that reaches an introduction item a version it applies to reads."""
    for version in sorted(config.specs):
        for tol in config.tolerances.for_spec(version):
            for pattern in tol.patterns:
                for read in introduction_paths(version, introduction):
                    if overlaps(pattern, read):
                        raise ValueError(
                            f"tolerance {tol.id}: {'/'.join(pattern)} reaches "
                            f"{'/'.join(read)}, which {version} reads"
                        )
