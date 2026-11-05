import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _find_version(path: Path, pattern: str) -> str:
    match = re.search(pattern, path.read_text(), re.MULTILINE)
    assert match, f"no version found in {path}"
    return match.group(1)


def test_package_version_matches_pyproject():
    pyproject = _find_version(ROOT / "pyproject.toml", r'^version = "([^"]+)"')
    package = _find_version(
        ROOT / "qbutler" / "__init__.py", r'^__version__ = "([^"]+)"'
    )
    assert package == pyproject, (
        f"qbutler.__version__ ({package}) and pyproject.toml ({pyproject}) differ: "
        "bump both together"
    )
