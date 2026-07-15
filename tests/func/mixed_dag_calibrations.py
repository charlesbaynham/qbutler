"""Calibrations for the kernel-only / host-only / mixed DAG functional tests.

Three DAG shapes are exercised by ``test_dag_kernel_host.py``:

1. an all-kernel DAG (``KOnlyTop`` -> ``KOnlyMid`` -> ``KOnlyLeaf``): every
   ``check_own_state`` is a ``@kernel`` and every fix is the default kernel
   optimizer, so the whole check/fix walk runs in a single resident kernel.

2. an all-host DAG (``HostTop`` -> ``HostMid`` -> ``HostLeaf``): plain host
   calibrations, checked and fixed on the host.

3. a mixed DAG: a kernel primary (``MixedKernelTop``) that depends on a
   kernel-checked node (``MixedKernelDep``) and a host-only node
   (``MixedHostDep``). The kernel-checked nodes are measured in the resident
   kernel; the host-only node is reached over a synchronous RPC.

The kernel nodes carry a single optimizable parameter whose default is broken
(outside the OK window) and whose optimum sits on the 11-point grid the
default ``grid_search_optimizer`` sweeps, so a fix reliably drives them to OK.
The host nodes are simply broken until their ``fix_own_state`` flips them.
"""

from artiq.experiment import kernel
from ndscan.experiment import ExpFragment

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult

# --------------------------------------------------------------------------
# Scenario 1: an all-kernel DAG
# --------------------------------------------------------------------------


class KOnlyLeaf(Calibration):
    """All-kernel DAG leaf. Optimum at 2.0, broken at the 5.0 default."""

    def build_calibration(self):
        self.setattr_device("core")
        self.setattr_param_optimizable(
            "leaf_param", "Leaf param", min=0.0, max=10.0, default=5.0
        )

    @kernel
    def check_own_state(self):
        p = self.leaf_param.get()
        data = 10.0 - abs(p - 2.0)
        if data > 9.5:
            return CalibrationResult.OK, data
        else:
            return CalibrationResult.BAD_DATA, data


class KOnlyMid(Calibration):
    """All-kernel DAG middle node; depends on KOnlyLeaf. Optimum at 7.0."""

    def build_calibration(self):
        self.setattr_device("core")
        self.add_dependency(KOnlyLeaf)
        self.setattr_param_optimizable(
            "mid_param", "Mid param", min=0.0, max=10.0, default=3.0
        )

    @kernel
    def check_own_state(self):
        p = self.mid_param.get()
        data = 10.0 - abs(p - 7.0)
        if data > 9.5:
            return CalibrationResult.OK, data
        else:
            return CalibrationResult.BAD_DATA, data


class KOnlyTop(Calibration):
    """All-kernel DAG top node; depends on KOnlyMid. Optimum at 4.0."""

    def build_calibration(self):
        self.setattr_device("core")
        self.add_dependency(KOnlyMid)
        self.setattr_param_optimizable(
            "top_param", "Top param", min=0.0, max=10.0, default=8.0
        )

    @kernel
    def check_own_state(self):
        p = self.top_param.get()
        data = 10.0 - abs(p - 4.0)
        if data > 9.5:
            return CalibrationResult.OK, data
        else:
            return CalibrationResult.BAD_DATA, data


class KOnlyDagFragment(ExpFragment):
    """A @kernel run_once that checks/fixes the all-kernel DAG in one kernel
    call."""

    def build_fragment(self) -> None:
        self.setattr_calibration(KOnlyTop)
        self.KOnlyTop: KOnlyTop
        self.setattr_device("core")
        self.fix_ok = False


    @kernel
    def run_once(self):
        ok = self.KOnlyTop.fix_state_kernel(False)
        self._report(ok)

    def _report(self, ok) -> None:
        self.fix_ok = ok


# --------------------------------------------------------------------------
# Scenario 2: an all-host DAG
# --------------------------------------------------------------------------


class HostLeaf(Calibration):
    """All-host DAG leaf. Broken until its host fix_own_state flips it."""

    def build_calibration(self):
        self.broken = True

    def check_own_state(self):
        if self.broken:
            return CalibrationResult.BAD_DATA, None
        else:
            return CalibrationResult.OK, None

    def fix_own_state(self) -> None:
        self.broken = False


class HostMid(Calibration):
    """All-host DAG middle node; depends on HostLeaf."""

    def build_calibration(self):
        self.add_dependency(HostLeaf)
        self.broken = True

    def check_own_state(self):
        if self.broken:
            return CalibrationResult.BAD_DATA, None
        else:
            return CalibrationResult.OK, None

    def fix_own_state(self) -> None:
        self.broken = False


class HostTop(Calibration):
    """All-host DAG top node; depends on HostMid."""

    def build_calibration(self):
        self.add_dependency(HostMid)
        self.broken = True

    def check_own_state(self):
        if self.broken:
            return CalibrationResult.BAD_DATA, None
        else:
            return CalibrationResult.OK, None

    def fix_own_state(self) -> None:
        self.broken = False


# --------------------------------------------------------------------------
# Scenario 3: a kernel primary with a mixture of kernel and host-only deps
# --------------------------------------------------------------------------


class MixedKernelDep(Calibration):
    """Kernel-checked dependency (optimizable, broken at the 5.0 default,
    optimum at 2.0). Measured in the resident kernel."""

    def build_calibration(self):
        self.setattr_device("core")
        self.setattr_param_optimizable(
            "k_param", "Kernel dep param", min=0.0, max=10.0, default=5.0
        )

    @kernel
    def check_own_state(self):
        p = self.k_param.get()
        data = 10.0 - abs(p - 2.0)
        if data > 9.5:
            return CalibrationResult.OK, data
        else:
            return CalibrationResult.BAD_DATA, data


class MixedHostDep(Calibration):
    """Host-only dependency. Broken until its host fix_own_state flips it.
    A kernel-mode primary reaches this over a synchronous RPC."""

    def build_calibration(self):
        self.broken = True

    def check_own_state(self):
        if self.broken:
            return CalibrationResult.BAD_DATA, None
        else:
            return CalibrationResult.OK, None

    def fix_own_state(self) -> None:
        self.broken = False


class MixedKernelTop(Calibration):
    """Kernel primary depending on one kernel-checked node and one host-only
    node. Optimizable, broken at the 8.0 default, optimum at 4.0."""

    def build_calibration(self):
        self.setattr_device("core")
        self.add_dependency(MixedKernelDep)
        self.add_dependency(MixedHostDep)
        self.setattr_param_optimizable(
            "top_param", "Top param", min=0.0, max=10.0, default=8.0
        )

    @kernel
    def check_own_state(self):
        p = self.top_param.get()
        data = 10.0 - abs(p - 4.0)
        if data > 9.5:
            return CalibrationResult.OK, data
        else:
            return CalibrationResult.BAD_DATA, data


class MixedDagFixFragment(ExpFragment):
    """A @kernel run_once that fixes the mixed DAG in one kernel call: the
    kernel-checked nodes are measured in the resident kernel, the host-only
    node over a synchronous RPC."""

    def build_fragment(self) -> None:
        self.setattr_calibration(MixedKernelTop)
        self.MixedKernelTop: MixedKernelTop
        self.setattr_device("core")
        self.fix_ok = False

    @kernel
    def run_once(self):
        ok = self.MixedKernelTop.fix_state(False)
        self._report(ok)

    def _report(self, ok) -> None:
        self.fix_ok = ok
