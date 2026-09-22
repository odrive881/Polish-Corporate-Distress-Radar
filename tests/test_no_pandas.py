"""pandas arrives with SQLMesh (plan 0007 decision 8), but project code uses Polars.

The dependency is allowed; importing it from project code is not. This scans
every module under `src/`, `dagster_defs/` and `transform/` for an import of
`pandas`, which is where the idiom matters.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCANNED = ("src", "dagster_defs", "transform")


def _imports_pandas(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            a.name.split(".")[0] == "pandas" for a in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "pandas":
            return True
    return False


def test_no_project_module_imports_pandas() -> None:
    modules = [p for d in SCANNED for p in sorted((ROOT / d).rglob("*.py"))]
    assert modules, "nothing scanned: the directory layout changed"
    offenders = [str(p.relative_to(ROOT)) for p in modules if _imports_pandas(p)]
    assert offenders == []


def test_the_scan_catches_both_import_forms(tmp_path: Path) -> None:
    for source in ("import pandas as pd\n", "from pandas import DataFrame\n"):
        module = tmp_path / "m.py"
        module.write_text(source)
        assert _imports_pandas(module)
    (tmp_path / "m.py").write_text("import polars as pl\n")
    assert not _imports_pandas(tmp_path / "m.py")
