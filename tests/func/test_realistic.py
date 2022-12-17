"""
Test calibration performance using default settings in a realistic scenario: a
Rabi flop of a single ion (example stolen from ndscan).
"""
import random
import time
from typing import Tuple

import numpy as np
import pytest
from artiq.experiment import MHz
from artiq.experiment import us
from ndscan.experiment import *
from ndscan.experiment.entry_point import make_fragment_scan_exp
from oitg.errorbars import binom_onesided

from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult


class Readout(Fragment):
    def build_fragment(self):
        self.setattr_param(
            "num_shots", IntParam, "Number of shots", 100, is_scannable=False
        )
        self.setattr_param(
            "mean_0", FloatParam, "Dark counts over readout duration", 0.1
        )
        self.setattr_param(
            "mean_1", FloatParam, "Bright counts over readout duration", 20.0
        )
        self.setattr_param("threshold", IntParam, "Threshold", 5)

        self.setattr_result("counts", OpaqueChannel)
        self.setattr_result("p")
        self.setattr_result("p_err", display_hints={"error_bar_for": self.p.path})

    def simulate_shots(self, p):
        num_shots = self.num_shots.get()

        counts = np.empty(num_shots, dtype=np.int16)
        for i in range(num_shots):
            mean = self.mean_0.get() if random.random() > p else self.mean_1.get()
            counts[i] = np.random.poisson(mean)
        self.counts.push(counts)

        num_brights = np.sum(counts >= self.threshold.get())
        p, p_err = binom_onesided(num_brights, num_shots)

        self.p.push(p)
        self.p_err.push(p_err)

        return p, p_err


class RabiFlopSim(Calibration):
    def build_calibration(self):
        self.setattr_fragment("readout", Readout)

        self.setattr_param(
            "rabi_freq", FloatParam, "Rabi frequency", 1.0 * MHz, unit="MHz", min=0.0
        )
        self.setattr_param("detuning", FloatParam, "Detuning", 0.0 * MHz, unit="MHz")

        self.setattr_param_optimizable(
            "duration", "Pulse duration", 0.25 * us, 1.5 * us, default=0.8 * us
        )

    def do_rabi_flop(self) -> Tuple[float, float]:
        """
        Simulate a Rabi flop and return the inferred probability of excitation

        Returns:
            Tuple[float, float]: The excitation fraction and error
        """
        omega0 = 2 * np.pi * self.rabi_freq.get()
        delta = 2 * np.pi * self.detuning.get()
        omega = np.sqrt(omega0**2 + delta**2)
        p = 1 - (omega0 / omega * np.sin(omega / 2 * self.duration.get())) ** 2

        time.sleep(0.01)
        return self.readout.simulate_shots(p)

    def run_once(self):
        p, _ = self.do_rabi_flop()

        self.data.push(p)
        self.status.push(
            CalibrationResult.OK if p > 0.75 else CalibrationResult.BAD_DATA
        )

    def get_default_analyses(self):
        return [
            OnlineFit(
                "sinusoid",
                data={
                    "x": self.duration,
                    "y": self.readout.p,
                    "y_err": self.readout.p_err,
                },
                constants={
                    "t_dead": 0,
                },
            )
        ]


def test_build_rabi_flob_calibration(fragment_factory):
    fragment_factory(RabiFlopSim)


def test_measure_bad_rabi_flob_calibration(fragment_factory):
    c = fragment_factory(RabiFlopSim)

    assert c.guess_state() == CalibrationResult.BAD_EXPIRED

    state, data = c.check_state()

    assert state == CalibrationResult.BAD_DATA


def test_fix_bad_rabi_flob_calibration(fragment_factory):
    c = fragment_factory(RabiFlopSim)

    state, data = c.check_state()
    assert state == CalibrationResult.BAD_DATA

    c.fix_state()

    state, data = c.check_state()
    assert state == CalibrationResult.OK


RabiFlopSimScanner = make_fragment_scan_exp(RabiFlopSim)
RabiFlopSimScanner.__name__ = "RabiFlopSimScanner"


def test_run_rabi_flop_as_scan(build_and_run_experiment):
    build_and_run_experiment(RabiFlopSimScanner, experiment_file=__file__)


@pytest.mark.slow
def test_run_rabi_flop_as_scan_full_stack(build_and_run_full_stack):
    build_and_run_full_stack("RabiFlopSimScanner", __file__)
