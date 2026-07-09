"""The kernel-mode calibration API: a @kernel run_once can call check_state()
and fix_state() directly on a Calibration, and the whole check/fix runs in that
one kernel (one compile + upload). Mirrors the icl_experiments
qbutler_kernel_demo.py spec fragments.
"""

import pytest
from artiq.experiment import kernel
from ndscan.experiment import ExpFragment

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult


class ApiCheckCal(Calibration):
    """Trivially-healthy; kernel check."""

    def build_calibration(self):
        self.setattr_device("core")
        self.set_timeout(300.0)

    @kernel
    def check_own_state(self):
        return CalibrationResult.OK, 0.0


class ApiFixCal(Calibration):
    """Fails its kernel check until the kernel fix has run."""

    def build_calibration(self):
        self.setattr_device("core")
        self.set_timeout(300.0)
        self._fixed = False

    @kernel
    def check_own_state(self):
        if self._fixed:
            return CalibrationResult.OK, 1.0
        return CalibrationResult.BAD_DATA, 0.0

    @kernel
    def fix_own_state(self):
        self._mark_fixed()

    @kernel
    def _mark_fixed(self):
        self._fixed = True


class ApiRunOnceFrag(ExpFragment):
    def build_fragment(self):
        self.setattr_calibration(ApiCheckCal)
        self.ApiCheckCal: ApiCheckCal
        self.setattr_calibration(ApiFixCal)
        self.ApiFixCal: ApiFixCal
        self.setattr_device("core")
        self.ok = False

    @kernel
    def run_once(self):
        result, data = self.ApiCheckCal.check_state(force=True)
        if result != CalibrationResult.OK:
            return
        self.ApiFixCal.fix_state(force=True)
        result, data = self.ApiFixCal.check_state()
        self._report(result == CalibrationResult.OK)

    def _report(self, ok) -> None:
        self.ok = ok


@pytest.mark.withartiq
def test_kernel_run_once_check_and_fix(fragment_factory):
    frag = fragment_factory(ApiRunOnceFrag)
    frag.host_setup()
    frag.run_once()
    assert frag.ok is True


@pytest.mark.withartiq
def test_kernel_run_once_single_kernel_call(fragment_factory, mock_core):
    """The whole run_once — two checks and a fix across two calibrations —
    is one kernel call, no recompile."""
    frag = fragment_factory(ApiRunOnceFrag)
    frag.host_setup()
    initial = mock_core.call_count
    frag.run_once()
    assert mock_core.call_count - initial == 1
