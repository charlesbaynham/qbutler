"""force=True as "mark all fixable nodes UNCALIBRATED, then walk ordinarily".

The old force walk was its own scheduling rule ("every node not yet
successfully fixed this walk") that never consulted node state again — so a
node that could not come good was rescanned forever while expired or suspect
dependencies were never revisited. Now a forced walk just seeds persistent
UNCALIBRATED marks and lets the ordinary walk do what it always does; the
walk backtracks, expiry is honoured, and the marks survive interruption.
"""

from types import SimpleNamespace

import pytest

from qbutler import dag
from qbutler.calibration import STATUS_DATASET
from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationError
from qbutler.calibration import CalibrationResult
from qbutler.calibration import _mark_uncalibrated


def test_force_skips_unfixable_nodes(fragment_factory):
    """A check-only node (no optimizable params, no fix_own_state override)
    cannot be re-optimised, so a forced walk must not mark it — under the old
    force logic it was selected for a fix anyway and crashed with the base
    fix_own_state ValueError."""
    hw = SimpleNamespace(root_fixes=0, checkonly_checks=0)

    class CheckOnly(Calibration):
        def build_calibration(self):
            self.set_check_timeout(60)

        def check_own_state(self):
            hw.checkonly_checks += 1
            return CalibrationResult.OK, None

    class Root(Calibration):
        def build_calibration(self):
            self.set_check_timeout(60)
            self.add_dependency(CheckOnly)

        def check_own_state(self):
            return CalibrationResult.OK, None

        def fix_own_state(self) -> None:
            hw.root_fixes += 1

    root = fragment_factory(Root)
    root.fix_state(force=True)  # would raise ValueError under the old logic

    assert hw.root_fixes == 1
    assert hw.checkonly_checks >= 1  # checked as usual, never fixed
    assert not root.CheckOnly._needs_reoptimise()


def test_interrupted_force_walk_leaves_marks_for_the_next_ordinary_walk(
    fragment_factory, dataset_db
):
    """The marks are persisted up front, so a forced walk that dies before
    reaching a node leaves its intent behind: the next PLAIN walk still
    re-fixes the un-reached node."""
    hw = SimpleNamespace(dep_fixable=False, dep_fixes=0, root_fixes=0)

    class Dep(Calibration):
        def build_calibration(self):
            self.set_check_timeout(60)
            self.set_max_fix_attempts(1)

        def check_own_state(self):
            state = (
                CalibrationResult.OK if hw.dep_fixable else CalibrationResult.BAD_DATA
            )
            return state, None

        def fix_own_state(self) -> None:
            hw.dep_fixes += 1

    class Root(Calibration):
        def build_calibration(self):
            self.set_check_timeout(60)
            self.add_dependency(Dep)

        def check_own_state(self):
            return CalibrationResult.OK, None

        def fix_own_state(self) -> None:
            hw.root_fixes += 1

    root = fragment_factory(Root)

    # The forced walk dies on Dep (budget 1, fix cannot succeed) before ever
    # reaching Root — but Root's mark is already in the dataset
    with pytest.raises(CalibrationError):
        root.fix_state(force=True)
    assert hw.root_fixes == 0
    assert dataset_db.get(STATUS_DATASET)["Root"]["uncalibrated"] is True

    # Hardware repaired: a subsequent ORDINARY walk finishes the forced
    # walk's intent
    hw.dep_fixable = True
    root.fix_state()
    assert hw.root_fixes == 1
    assert not root._needs_reoptimise()


def test_force_marking_is_one_batched_status_write(fragment_factory, monkeypatch):
    """Marking N nodes must cost one status-table write, not N — the table
    write is an unlocked read-modify-write racing monitor threads."""

    class Leaf(Calibration):
        def build_calibration(self):
            self.set_check_timeout(60)

        def check_own_state(self):
            return CalibrationResult.OK, None

        def fix_own_state(self) -> None:
            pass

    class Mid(Calibration):
        def build_calibration(self):
            self.set_check_timeout(60)
            self.add_dependency(Leaf)

        def check_own_state(self):
            return CalibrationResult.OK, None

        def fix_own_state(self) -> None:
            pass

    class Top(Calibration):
        def build_calibration(self):
            self.set_check_timeout(60)
            self.add_dependency(Mid)

        def check_own_state(self):
            return CalibrationResult.OK, None

        def fix_own_state(self) -> None:
            pass

    top = fragment_factory(Top)
    nodes = dag.get_dependencies(top)
    assert len(nodes) == 3

    writes = []
    original = Calibration.set_dataset

    def counting_set_dataset(self, key, *args, **kwargs):
        writes.append(key)
        return original(self, key, *args, **kwargs)

    monkeypatch.setattr(Calibration, "set_dataset", counting_set_dataset)
    _mark_uncalibrated(nodes)

    assert writes.count(STATUS_DATASET) == 1
    assert all(node._needs_reoptimise() for node in nodes)
