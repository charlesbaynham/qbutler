"""Test that no Python files contain FIXME markers (except known stubs)."""

import pathlib

import pytest

# Root of the repository (two levels above the 'tests/unit' directory)
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Files known to contain intentional FIXME stubs; remove an entry here when
# the corresponding stub is resolved.
_KNOWN_FIXME_FILES = {
    pathlib.Path("qbutler/calibration.py"),
    pathlib.Path("tests/func/test_kernel_optimization.py"),
}


def _python_files():
    this_file = pathlib.Path(__file__).resolve()
    return sorted(
        p.relative_to(_REPO_ROOT)
        for p in _REPO_ROOT.rglob("*.py")
        if p.resolve() != this_file
    )


def _make_param(rel_path):
    if rel_path in _KNOWN_FIXME_FILES:
        return pytest.param(
            rel_path,
            marks=pytest.mark.xfail(
                reason=f"{rel_path} contains a known FIXME stub; "
                "remove this xfail when the stub is resolved",
                strict=True,
            ),
        )
    return rel_path


_all_files = _python_files()


@pytest.mark.parametrize(
    "python_file",
    [_make_param(f) for f in _all_files],
    ids=[str(f) for f in _all_files],
)
def test_no_fixme(python_file):
    """Python files must not contain FIXME markers."""
    content = (_REPO_ROOT / python_file).read_text()
    assert "FIXME" not in content, f"{python_file} contains FIXME"
