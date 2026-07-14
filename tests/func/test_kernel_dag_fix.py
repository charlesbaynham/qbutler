"""Tests for the kernel-driven DAG fix (fix_state_kernel).

The headline capability: an ExpFragment with a @kernel run_once fixes a
3-deep calibration DAG from within a single kernel — one compile + one
upload for the whole fix, with the DAG walk and the optimizer strategies
running on the host, reached purely by RPC.
"""

import pytest

from qbutler.calibration import CalibrationResult
from tests.func import kernel_calibrations


@pytest.mark.withartiq
def test_kernel_dag_fix_single_kernel_call(fragment_factory, mock_core):
    """A @kernel run_once fixes all three DAG levels in exactly one kernel call."""
    frag = fragment_factory(kernel_calibrations.KernelDagFixFragment)
    frag.host_setup()

    initial_calls = mock_core.call_count

    frag.run_once()

    dag_fix_calls = mock_core.call_count - initial_calls
    assert (
        dag_fix_calls == 1
    ), f"Expected exactly 1 kernel call for the whole DAG fix, got {dag_fix_calls}"
    assert frag.fix_ok is True


@pytest.mark.withartiq
def test_kernel_dag_fix_optimizes_all_levels(fragment_factory):
    """All three levels end up at their optima (2.0 / 7.0 / 4.0)."""
    frag = fragment_factory(kernel_calibrations.KernelDagFixFragment)
    frag.host_setup()

    frag.run_once()
    assert frag.fix_ok is True

    top = frag.DagTopCalibration
    by_class = {dep.__class__.__name__: dep for dep in top._fsk_deps}
    assert by_class["DagBaseCalibration"].base_param.get() == pytest.approx(2.0)
    assert by_class["DagMidCalibration"].mid_param.get() == pytest.approx(7.0)
    assert by_class["DagTopCalibration"].top_param.get() == pytest.approx(4.0)

    result, data = top.check_state(force=True)
    assert result == CalibrationResult.OK


@pytest.mark.withartiq
def test_fix_state_kernel_from_host(fragment_factory, mock_core):
    """fix_state_kernel called from the host still costs one kernel call for
    the whole DAG."""
    frag = fragment_factory(kernel_calibrations.KernelDagFixFragment)
    frag.host_setup()
    top = frag.DagTopCalibration

    initial_calls = mock_core.call_count

    ok = top.fix_state_kernel(False)

    assert mock_core.call_count - initial_calls == 1
    assert ok is True


@pytest.mark.withartiq
def test_kernel_dag_fix_skips_healthy_nodes(fragment_factory):
    """A second fix right after the first finds everything OK and fixes
    nothing (no optimizer runs; the walk just re-checks / trusts timeouts)."""
    frag = fragment_factory(kernel_calibrations.KernelDagFixFragment)
    frag.host_setup()

    frag.run_once()
    assert frag.fix_ok is True

    top = frag.DagTopCalibration
    ok = top.fix_state_kernel(False)
    assert ok is True


@pytest.mark.withartiq
def test_kernel_dag_fix_unfixable_returns_false(fragment_factory):
    """A broken check-only node cannot be fixed: fix_state_kernel returns
    False instead of raising."""
    frag = fragment_factory(kernel_calibrations.KernelDagUnfixableFragment)
    frag.host_setup()

    frag.run_once()

    assert frag.fix_ok is False
    assert frag.UnfixableCalibration._fsk_failure is not None
