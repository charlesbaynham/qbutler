from artiq.experiment import kernel
from ndscan.experiment import ExpFragment
from ndscan.experiment.entry_point import make_fragment_scan_exp

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult
from qbutler.optimizers import coordinate_descent_optimizer


class MinimalKernelCalibration(Calibration):
    def build_calibration(self):
        self.setattr_device("core")

    @kernel
    def check_own_state(self):
        return CalibrationResult.OK, None


class MinimalKernelCalibrationExperiment(ExpFragment):
    def build_fragment(self) -> None:
        self.setattr_calibration(MinimalKernelCalibration)
        self.MinimalKernelCalibration: MinimalKernelCalibration

    def run_once(self):
        state = self.MinimalKernelCalibration.check_state()
        print(f"State : {state}")


MinimalKernelCalibrationExperiment = make_fragment_scan_exp(
    MinimalKernelCalibrationExperiment
)


class KernelOptimizableCalibration(Calibration):
    """A calibration with kernel check_own_state that can be optimized.

    The optimal parameter value is 7.0, where the data peaks.
    """

    def build_calibration(self):
        self.setattr_device("core")
        self.setattr_param_optimizable(
            "param1",
            "Test param",
            min=0.0,
            max=10.0,
            default=5.0,
        )

    @kernel
    def check_own_state(self):
        p = self.param1.get()
        data = 10.0 - abs(p - 7.0)
        if data > 8.0:
            return CalibrationResult.OK, data
        else:
            return CalibrationResult.BAD_DATA, data


class KernelFeedbackOptimizableCalibration(Calibration):
    """As KernelOptimizableCalibration, but with a feedback (non-batchable)
    optimizer, which cannot run as a single kernel sweep."""

    def build_calibration(self):
        self.setattr_device("core")
        self.setattr_param_optimizable(
            "param1",
            "Test param",
            min=0.0,
            max=10.0,
            default=5.0,
        )
        self.set_optimizer(coordinate_descent_optimizer)

    @kernel
    def check_own_state(self):
        p = self.param1.get()
        data = 10.0 - abs(p - 7.0)
        if data > 8.0:
            return CalibrationResult.OK, data
        else:
            return CalibrationResult.BAD_DATA, data


class KernelOptimizableCalibrationExperiment(ExpFragment):
    def build_fragment(self) -> None:
        self.setattr_calibration(KernelOptimizableCalibration)
        self.KernelOptimizableCalibration: KernelOptimizableCalibration

    def run_once(self):
        state = self.KernelOptimizableCalibration.check_state()
        print(f"State : {state}")


KernelOptimizableCalibrationExperiment = make_fragment_scan_exp(
    KernelOptimizableCalibrationExperiment
)


class DagBaseCalibration(Calibration):
    """Deepest level of the 3-deep kernel DAG. Optimum at 2.0, default 5.0
    (broken until fixed — the OK window excludes the default)."""

    def build_calibration(self):
        self.setattr_device("core")
        self.setattr_param_optimizable(
            "base_param", "Base param", min=0.0, max=10.0, default=5.0
        )

    @kernel
    def check_own_state(self):
        p = self.base_param.get()
        data = 10.0 - abs(p - 2.0)
        if data > 9.5:
            return CalibrationResult.OK, data
        else:
            return CalibrationResult.BAD_DATA, data


class DagMidCalibration(Calibration):
    """Middle level; depends on DagBaseCalibration. Optimum at 7.0."""

    def build_calibration(self):
        self.setattr_device("core")
        self.add_dependency(DagBaseCalibration)
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


class DagTopCalibration(Calibration):
    """Top level; depends on DagMidCalibration. Optimum at 4.0."""

    def build_calibration(self):
        self.setattr_device("core")
        self.add_dependency(DagMidCalibration)
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


class UnfixableCalibration(Calibration):
    """A check-only kernel calibration that is always broken: no optimizable
    params and no fix_own_state, so a fix walk must fail (raise)."""

    def build_calibration(self):
        self.setattr_device("core")

    @kernel
    def check_own_state(self):
        return CalibrationResult.BAD_DATA, 0.0


class KernelFixOwnStateCalibration(Calibration):
    """A calibration with kernel fix_own_state."""

    def build_calibration(self):
        self.setattr_device("core")
        self.broken = True

    def check_own_state(self):
        if self.broken:
            return CalibrationResult.BAD_DATA, None
        else:
            return CalibrationResult.OK, None

    @kernel
    def fix_own_state(self):
        self.broken = False


class KernelFixOwnStateCalibrationExperiment(ExpFragment):
    def build_fragment(self) -> None:
        self.setattr_calibration(KernelFixOwnStateCalibration)
        self.KernelFixOwnStateCalibration: KernelFixOwnStateCalibration

    def run_once(self):
        state = self.KernelFixOwnStateCalibration.check_state()
        print(f"State : {state}")


KernelFixOwnStateCalibrationExperiment = make_fragment_scan_exp(
    KernelFixOwnStateCalibrationExperiment
)
