"""Pre-commit scan: nothing staged holds natural persons' data (invariant 6, ADR 0009).

Installed as `.git/hooks/pre-commit` by `make hooks`. It reads each staged file's staged content
(what the commit will hold, not the working tree), refuses browser recordings (`.har`, which hold
the documents as filed) outright, and runs `redaction.personal_data_markers` over every data file:
signatures, PESEL markers, file names that are not tokens, attachment names, PDF metadata. It
prints the path and the kind of each finding, never the content.

The same scan runs over the committed fixtures in `make check` and over the raw store as a
blocking asset check; this one stops a leak before it reaches the public history, where removing
it means a rewrite.

Run by hand: `uv run python -m distress_radar.acquisition.personal_data_scan [paths...]`
(no paths: the staged files).
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath

from distress_radar.acquisition.redaction import personal_data_markers

DATA_SUFFIXES = frozenset({".xml", ".json", ".zip", ".pdf", ".xades", ".xsig"})
REFUSED_SUFFIXES = frozenset({".har"})


def staged_paths(cwd: Path | None = None) -> list[str]:
    """Paths added, copied, modified or renamed in the index."""
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
        cwd=cwd,
        capture_output=True,
        check=True,
    ).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def staged_bytes(path: str, cwd: Path | None = None) -> bytes:
    """The file's content as staged (`git show :path`), not as in the working tree."""
    return subprocess.run(
        ["git", "show", f":{path}"], cwd=cwd, capture_output=True, check=True
    ).stdout


def scan(paths: Iterable[str], read: Callable[[str], bytes]) -> dict[str, list[str]]:
    """Findings by path: `.har` files, and data files with personal-data markers."""
    found: dict[str, list[str]] = {}
    for path in paths:
        suffix = PurePosixPath(path).suffix.lower()
        if suffix in REFUSED_SUFFIXES:
            found[path] = ["a browser recording holds the documents as filed; never commit one"]
        elif suffix in DATA_SUFFIXES:
            markers = personal_data_markers(read(path))
            if markers:
                found[path] = markers
    return found


def main(argv: list[str] | None = None, cwd: Path | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        found = scan(args, lambda p: (cwd or Path()).joinpath(p).read_bytes())
    else:
        found = scan(staged_paths(cwd), lambda p: staged_bytes(p, cwd))
    for path, markers in sorted(found.items()):
        print(f"{path}: {len(markers)} finding(s), e.g. {markers[:3]}", file=sys.stderr)
    if found:
        print(
            "Personal data in files to be committed (ADR 0009). Redact them with "
            "`acquisition.redaction`, or unstage them.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
