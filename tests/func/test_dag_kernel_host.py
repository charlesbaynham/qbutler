"""Functional tests for calibration DAGs of three shapes:

1. an all-kernel DAG (every ``check_own_state`` is a ``@kernel``),
2. an all-host DAG (plain host calibrations), and
3. a mixed DAG: a kernel primary that depends on a mixture of kernel-checked
   and host-only calibrations.
"""

import pytest

from qbutler.calibration import CalibrationResult
from tests.func import mixed_dag_calibrations as cals

# --------------------------------------------------------------------------
# Scenario 1: an all-kernel DAG
# --------------------------------------------------------------------------


@pytest.mark.withartiq
def test_kernel_only_dag_check_reports_broken(fragment_factory):
    """An all-kernel DAG that is broken at its defaults reports not-OK."""
    top = fragment_factory(cals.KOnlyTop)
    top.host_setup()

    result, _ = top.check_state()
    assert result != CalibrationResult.OK


@pytest.mark.withartiq
def test_kernel_only_dag_fix_repairs_all_levels(fragment_factory):
    """Fixing an all-kernel DAG drives every level to its optimum."""
    top = fragment_factory(cals.KOnlyTop)
    top.host_setup()

    ok = top.fix_state()
    assert ok == CalibrationResult.OK

    result, _ = top.check_state()
    assert result == CalibrationResult.OK

    by_class = {dep.__class__.__name__: dep for dep in top._get_dependencies()}
    assert by_class["KOnlyLeaf"].leaf_param.get() == pytest.approx(2.0)
    assert by_class["KOnlyMid"].mid_param.get() == pytest.approx(7.0)
    assert by_class["KOnlyTop"].top_param.get() == pytest.approx(4.0)


@pytest.mark.withartiq
def test_kernel_only_dag_fix_single_kernel_call(fragment_factory, mock_core):
    """A @kernel run_once fixes the whole all-kernel DAG in one kernel call."""
    frag = fragment_factory(cals.KOnlyDagFragment)
    frag.host_setup()

    initial_calls = mock_core.call_count
    frag.run_once()

    assert mock_core.call_count - initial_calls == 1
    assert frag.fix_ok is True


# --------------------------------------------------------------------------
# Scenario 2: an all-host DAG
# --------------------------------------------------------------------------


def test_host_only_dag_check_reports_broken(fragment_factory):
    """An all-host DAG that is broken reports a bad dependency."""
    top = fragment_factory(cals.HostTop)

    result, _ = top.check_state()
    assert result == CalibrationResult.BAD_DEPS


def test_host_only_dag_fix_repairs_all_levels(fragment_factory):
    """Fixing an all-host DAG repairs every level, deepest first."""
    top = fragment_factory(cals.HostTop)

    assert top.check_state()[0] == CalibrationResult.BAD_DEPS

    top.fix_state()

    assert top.check_state()[0] == CalibrationResult.OK

    by_class = {dep.__class__.__name__: dep for dep in top._get_dependencies()}
    assert by_class["HostLeaf"].broken is False
    assert by_class["HostMid"].broken is False
    assert by_class["HostTop"].broken is False


# --------------------------------------------------------------------------
# Scenario 3: a kernel primary with a mixture of kernel and host-only deps
# --------------------------------------------------------------------------



@pytest.mark.withartiq
def test_mixed_dag_check_reports_broken(fragment_factory):
    """Checking a broken mixed DAG reports not-OK."""
    top = fragment_factory(cals.MixedKernelTop)
    top.host_setup()

    result, _ = top.check_state()
    assert result != CalibrationResult.OK


@pytest.mark.withartiq
def test_mixed_dag_fix_repairs_kernel_and_host_deps(fragment_factory):
    """Fixing a mixed DAG optimizes the kernel nodes and repairs the host-only
    node over synchronous RPC."""
    top = fragment_factory(cals.MixedKernelTop)
    top.host_setup()

    ok = top.fix_state()
    assert ok == CalibrationResult.OK

    result, _ = top.check_state()
    assert result == CalibrationResult.OK

    by_class = {dep.__class__.__name__: dep for dep in top._get_dependencies()}
    assert by_class["MixedKernelDep"].k_param.get() == pytest.approx(2.0)
    assert by_class["MixedHostDep"].broken is False
    assert by_class["MixedKernelTop"].top_param.get() == pytest.approx(4.0)


@pytest.mark.withartiq
def test_mixed_dag_fix_single_kernel_call(fragment_factory, mock_core):
    """A @kernel run_once fixes the mixed DAG — kernel nodes in the resident
    kernel, the host-only node over synchronous RPC — in one kernel call."""
    frag = fragment_factory(cals.MixedDagFixFragment)
    frag.host_setup()

    initial_calls = mock_core.call_count
    frag.run_once()

    assert mock_core.call_count - initial_calls == 1
    assert frag.fix_ok is True
