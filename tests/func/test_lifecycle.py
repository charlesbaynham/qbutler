"""The serialised calibration-measurement lifecycle.

ndscan's fragment contract says ``run_once`` only executes after the fragment's
own ``host_setup`` (and its effects still hold). Calibration measurements share
physical hardware, so their setups must not coexist: qbutler serialises them —
at most one node is set up at a time, and every switch is a host_cleanup /
host_setup handover (``Calibration._activate``).

The regression these tests pin down is the live Andor failure of 2026-07-30
(RID 79494): every measurement wrapper was host_setup up front, each later one
reconfiguring the one physical camera, so by the time a node's kernel ran, its
own setup had been trampled ("Timeout waiting for new image").
"""

from qbutler import calibration as calibration_module
from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult
from qbutler.calibration import deactivate_active_calibration

#: Stand-in for a piece of shared physical hardware (the camera): host_setup
#: claims it, host_cleanup releases it. The contract under test: whenever a
#: node measures, the "hardware" is configured for that node.
_hardware_mode = {"owner": None}

#: Chronological log of lifecycle events, ("setup"|"cleanup"|"check", name).
_events = []


class _FakeEmbedded:
    """Just enough ``artiq_embedded`` metadata to satisfy ``is_kernel``, so
    the check joins the serialised (kernel-mode) lifecycle while executing as
    plain host code — no emulator needed."""

    core_name = "core"
    portable = False


class _SharedHardwareCal(Calibration):
    """A kernel-checked calibration whose host_setup configures shared
    hardware, exactly like the Andor imaging wrappers."""

    def build_calibration(self):
        pass

    def host_setup(self):
        _events.append(("setup", type(self).__name__))
        _hardware_mode["owner"] = type(self).__name__
        super().host_setup()

    def host_cleanup(self):
        _events.append(("cleanup", type(self).__name__))
        if _hardware_mode["owner"] == type(self).__name__:
            _hardware_mode["owner"] = None
        super().host_cleanup()

    def check_own_state(self):
        _events.append(("check", type(self).__name__))
        if _hardware_mode["owner"] != type(self).__name__:
            # The Andor bug: measuring on hardware another fragment configured
            return CalibrationResult.INVALID_DATA, 0.0
        return CalibrationResult.OK, 1.0

    check_own_state.artiq_embedded = _FakeEmbedded()


class HardwareCalA(_SharedHardwareCal):
    pass


class HardwareCalB(_SharedHardwareCal):
    def build_calibration(self):
        super().build_calibration()
        self.add_dependency(HardwareCalA)


def _reset_state():
    _hardware_mode["owner"] = None
    _events.clear()


def test_each_check_runs_on_its_own_setup(fragment_factory):
    """Walking a two-node chain, every node measures on hardware ITS OWN
    host_setup configured — the regression test for the Andor camera bug."""
    _reset_state()
    top = fragment_factory(HardwareCalB)

    result, _ = top.check_state(force=True)

    assert (
        result == CalibrationResult.OK
    ), "a node measured on hardware another node's host_setup had configured"


def test_handover_orders_cleanup_before_setup(fragment_factory):
    """Switching nodes is a full handover: the outgoing node's host_cleanup
    runs before the incoming node's host_setup."""
    _reset_state()
    top = fragment_factory(HardwareCalB)

    top.check_state(force=True)

    assert _events == [
        ("setup", "HardwareCalA"),
        ("check", "HardwareCalA"),
        ("cleanup", "HardwareCalA"),
        ("setup", "HardwareCalB"),
        ("check", "HardwareCalB"),
        ("cleanup", "HardwareCalB"),  # walk-end deactivation
    ]


def test_walk_deactivates_on_exit(fragment_factory):
    """A walk leaves no calibration active: the last node is cleaned up, so
    the main experiment fragment re-enters on untouched hardware."""
    _reset_state()
    top = fragment_factory(HardwareCalB)

    top.check_state(force=True)

    assert calibration_module._active_node is None
    assert _hardware_mode["owner"] is None


def test_repeat_activation_is_a_no_op(fragment_factory):
    """Re-measuring the active node does not thrash setup/cleanup."""
    _reset_state()
    cal = fragment_factory(HardwareCalA)

    cal._do_check_own_state()
    cal._do_check_own_state()

    setups = [e for e in _events if e == ("setup", "HardwareCalA")]
    assert len(setups) == 1

    deactivate_active_calibration()
    assert _hardware_mode["owner"] is None


class _HostModeDep(Calibration):
    """A host-mode node (like a monitor): it never joins the handover, but a
    never-set-up instance in a walk gets a one-time host_setup."""

    setup_count = 0

    def build_calibration(self):
        pass

    def host_setup(self):
        type(self).setup_count += 1
        super().host_setup()

    def check_own_state(self):
        return CalibrationResult.OK, None


def test_host_mode_node_gets_one_lazy_setup(fragment_factory):
    _HostModeDep.setup_count = 0
    cal = fragment_factory(_HostModeDep)

    cal.check_state(force=True)
    cal.check_state(force=True)

    # One lazy setup on first use; never again, and never any slot handover
    assert _HostModeDep.setup_count == 1
    assert calibration_module._active_node is None


def test_host_mode_node_already_set_up_is_left_alone(fragment_factory):
    """An ndscan-managed host-mode node (an attached monitor, set up at
    experiment start) is not set up again by the walk."""
    _HostModeDep.setup_count = 0
    cal = fragment_factory(_HostModeDep)

    cal.host_setup()  # ndscan's tree walk
    assert _HostModeDep.setup_count == 1

    cal.check_state(force=True)
    assert _HostModeDep.setup_count == 1
