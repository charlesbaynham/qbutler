"""The host walk driving per-node precompiled kernels through a PrecompilePool.

Needs the ARTIQ emulator (real compiles run). Covers: seeding a DAG into a pool
and pulling compiled check/fix callables; the subkernel-free precondition that
makes background compilation thread-safe; and an end-to-end pooled fix walk that
optimises a kernel-checked calibration.
"""

import gc

import pytest

from qbutler import dag
from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationResult
from qbutler.precompile import PrecompilePool
from tests.func import kernel_calibrations


@pytest.fixture(autouse=True)
def _clear_dag():
    gc.collect()
    dag._dependency_map.clear()
    yield
    dag._dependency_map.clear()


def _arm_nodes(cal):
    for node in dag.get_dependencies(cal):
        node.host_setup()


@pytest.mark.withartiq
def test_pool_seeds_and_compiles_whole_dag(fragment_factory):
    top = fragment_factory(kernel_calibrations.DagTopCalibration)
    _arm_nodes(top)

    pool = PrecompilePool(top.core)
    top.seed_precompile_pool(pool)
    pool.drain()

    nodes = dag.get_dependencies(top)
    assert len(nodes) == 3  # Base <- Mid <- Top
    for node in nodes:
        # every node has a @kernel check and a default-optimizer fix
        assert callable(pool.get(node._precompile_check_key))
        assert callable(pool.get(node._precompile_fix_key))
    pool.shutdown()


@pytest.mark.withartiq
def test_precompiled_kernels_have_no_subkernels(fragment_factory):
    """The thread-safety precondition: a background compile touches no comm
    state only when the kernel has no subkernels."""
    top = fragment_factory(kernel_calibrations.DagTopCalibration)
    _arm_nodes(top)

    for node in dag.get_dependencies(top):
        embedding_map = node.core.compile(node.check_own_state, (), {})[0]
        assert embedding_map.subkernels() == {}, (
            f"{type(node).__name__}.check_own_state has subkernels; background "
            "precompilation is not thread-safe for it"
        )


@pytest.mark.withartiq
def test_pooled_fix_walk_optimises_dag(fragment_factory):
    top = fragment_factory(kernel_calibrations.DagTopCalibration)
    _arm_nodes(top)

    pool = PrecompilePool(top.core)
    top.seed_precompile_pool(pool)

    # Broken at defaults; a fix walk (driven by pooled kernels) fixes all levels.
    assert top.check_state(force=True)[0] != CalibrationResult.OK
    top.fix_state()
    assert top.check_state(force=True)[0] == CalibrationResult.OK

    by_name = {type(d).__name__: d for d in dag.get_dependencies(top)}
    assert by_name["DagBaseCalibration"].base_param.get() == pytest.approx(2.0)
    assert by_name["DagMidCalibration"].mid_param.get() == pytest.approx(7.0)
    assert by_name["DagTopCalibration"].top_param.get() == pytest.approx(4.0)
    pool.shutdown()


class _HostOnlyCal(Calibration):
    """A host-mode calibration: no pool is ever armed, so the walk must call
    check_own_state directly (the fallback that keeps plain qbutler working)."""

    def build_calibration(self):
        self.checks = 0

    def check_own_state(self):
        self.checks += 1
        return CalibrationResult.OK, 1.0


def test_host_mode_walk_bypasses_pool(fragment_factory):
    cal = fragment_factory(_HostOnlyCal)
    assert cal._precompile_pool is None
    result, _ = cal.check_state()
    assert result == CalibrationResult.OK
    assert cal.checks == 1
