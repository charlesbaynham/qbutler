"""Host-side dispatch of the calibration escape/re-enter loop.

Exercises :func:`qbutler.client.drive_with_recalibration` with fake ``main`` /
``fix`` callables — no kernel, no core — covering normal return, one escape, N
escapes, and the bounded-livelock guard.
"""

import pytest

from qbutler.calibration import CalibrationError
from qbutler.calibration import CalibrationEscape
from qbutler.client import drive_with_recalibration


def _escaping_main(n_escapes, calls, fixes):
    """A fake main kernel that raises CalibrationEscape its first ``n_escapes``
    calls, then returns normally."""

    def main():
        calls.append(len(calls))
        if len(calls) <= n_escapes:
            raise CalibrationEscape("needs cal")

    return main


def test_normal_return_never_fixes():
    calls, fixes = [], []
    drive_with_recalibration(
        _escaping_main(0, calls, fixes), lambda: fixes.append(1), max_recalibrations=5
    )
    assert len(calls) == 1
    assert fixes == []


def test_single_escape_fixes_once_then_returns():
    calls, fixes = [], []
    drive_with_recalibration(
        _escaping_main(1, calls, fixes), lambda: fixes.append(1), max_recalibrations=5
    )
    assert len(calls) == 2  # escaped once, re-entered once
    assert len(fixes) == 1


def test_n_escapes_fix_n_times():
    calls, fixes = [], []
    drive_with_recalibration(
        _escaping_main(3, calls, fixes), lambda: fixes.append(1), max_recalibrations=10
    )
    assert len(calls) == 4
    assert len(fixes) == 3


def test_bounded_livelock_guard():
    calls, fixes = [], []
    # main always escapes; fix never helps
    def main():
        calls.append(1)
        raise CalibrationEscape("never settles")

    with pytest.raises(CalibrationError, match="not converging"):
        drive_with_recalibration(
            main, lambda: fixes.append(1), max_recalibrations=3, describe=" from Test"
        )
    # max_recalibrations + 1 attempts, a fix after each
    assert len(calls) == 4
    assert len(fixes) == 4
