import numpy as np
from ndscan.experiment import ExpFragment
from ndscan.experiment.parameters import FloatParamHandle

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult

# Centre and width of the Gaussian
CENTER_X = 0.3
CENTER_Y = -0.2
CENTER_Z = 0.4
SIGMA = 0.5


class Gaussian3DMax(Calibration):
    """Maximise a 3-D Gaussian over (x, y, z).

    In a real experiment the ``check_own_state`` method would drive hardware
    and measure a signal; here it just evaluates a function
    """

    def build_calibration(self):
        self.setattr_param_optimizable("x", "X", -1, 1, default=0.0)
        self.setattr_param_optimizable("y", "Y", -1, 1, default=0.0)
        self.setattr_param_optimizable("z", "Z", -1, 1, default=0.0)
        self.x: FloatParamHandle
        self.y: FloatParamHandle
        self.z: FloatParamHandle

        self.set_optimization_type("max")

    def check_own_state(self):
        x = self.x.get()
        y = self.y.get()
        z = self.z.get()
        g = np.exp(
            -((x - CENTER_X) ** 2 + (y - CENTER_Y) ** 2 + (z - CENTER_Z) ** 2)
            / (2 * SIGMA**2)
        )
        return CalibrationResult.OK, g


class OptimizeGaussian3DMax(ExpFragment):
    def build_experiment(self):
        self.setattr_calibration("gaussian", Gaussian3DMax)
        self.gaussian: Gaussian3DMax

    def run_once(self):
        print("Starting optimization...")
        self.gaussian.fix_state(force=True)
        print(
            f"Optimized parameters: x={self.gaussian.x.get():.3f}, "
            f"y={self.gaussian.y.get():.3f}, z={self.gaussian.z.get():.3f}"
        )
