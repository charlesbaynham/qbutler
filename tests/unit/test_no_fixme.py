"""Test that no Python files contain FIXME markers."""

import pathlib

import pytest

# Root of the repository (two levels above the 'tests/unit' directory)
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _python_files():
    this_file = pathlib.Path(__file__).resolve()
    return sorted(
        p.relative_to(_REPO_ROOT)
        for p in _REPO_ROOT.rglob("*.py")
        if p.resolve() != this_file
    )


_all_files = _python_files()


@pytest.mark.parametrize(
    "python_file",
    _all_files,
    ids=[str(f) for f in _all_files],
)
def test_no_fixme(python_file):
    """Python files must not contain FIXME markers."""
    content = (_REPO_ROOT / python_file).read_text()
    assert "FIXME" not in content, f"{python_file} contains FIXME"
