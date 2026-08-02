"""SUSPECT: a failed fix casts doubt on dependencies that still look good.

When a calibration cannot be brought good, the likeliest physical explanation
is often a dependency that is within its timeout — so nominally OK — but has
silently drifted. Such dependencies are marked suspect: their cached OK is no
longer trusted, they are re-measured despite their timeout, and the fix walk
backtracks to them (once per node per walk) before retrying the failing node.

Suspicion clears when the suspect node is re-measured (whatever the result:
a fresh measurement supersedes doubt), or when the dependent that cast it
comes good (its problem evidently lay elsewhere).
"""

from types import SimpleNamespace

import pytest

from qbutler import dag
from qbutler.applets.dag_applet import _node_state
from qbutler.calibration import STATUS_DATASET
from qbutler.calibration import Calibration
from qbutler.calibration import CalibrationError
from qbutler.calibration import CalibrationResult
from qbutler.calibration import _suspect_dependencies_of
from qbutler.calibration import fix_targets


def make_pair(b_fixable=True, b_max_fix_attempts="unset"):
    """An Upstream <- Downstream chain over shared mock "hardware".

    Upstream owns ``hw.a_good`` and repairs it when fixed. Downstream checks
    good only once it has been calibrated (``hw.b_good``), and calibrating it
    against broken upstream hardware cannot succeed — the situation the
    SUSPECT machinery exists for. Both have long timeouts, so nothing here
    expires during a test.
    """
    hw = SimpleNamespace(
        a_good=True, b_good=False, a_checks=0, a_fixes=0, b_checks=0, b_fixes=0
    )

    class Upstream(Calibration):
        def build_calibration(self):
            self.set_check_timeout(60)

        def check_own_state(self):
            hw.a_checks += 1
            state = CalibrationResult.OK if hw.a_good else CalibrationResult.BAD_DATA
            return state, None

        def fix_own_state(self) -> None:
            hw.a_fixes += 1
            hw.a_good = True

    class Downstream(Calibration):
        def build_calibration(self):
            self.set_check_timeout(60)
            self.add_dependency(Upstream)
            if b_max_fix_attempts != "unset":
                self.set_max_fix_attempts(b_max_fix_attempts)

        def check_own_state(self):
            hw.b_checks += 1
            state = CalibrationResult.OK if hw.b_good else CalibrationResult.BAD_DATA
            return state, None

        def fix_own_state(self) -> None:
            hw.b_fixes += 1
            if b_fixable:
                # Calibrating against broken upstream hardware cannot work
                hw.b_good = hw.a_good

    return hw, Upstream, Downstream


def test_backtrack_finds_the_silently_broken_dependency(fragment_factory):
    """The headline behaviour: Downstream cannot be fixed because Upstream has
    silently drifted inside its timeout. The walk must suspect Upstream,
    re-measure it, repair it, and only then bring Downstream good."""
    hw, Upstream, Downstream = make_pair()
    b = fragment_factory(Downstream)
    a = b.Upstream

    a.check_state()
    assert a._guess_own_state() == CalibrationResult.OK
    hw.a_good = False  # the silent drift qbutler cannot see

    b.fix_state()

    assert hw.a_fixes == 1  # the real culprit was found and repaired
    assert hw.b_good and hw.a_good
    assert not a.is_suspect()  # re-measured, so the doubt is answered
    assert b.check_state()[0] == CalibrationResult.OK


def test_fix_targets_backtracks_too(fragment_factory):
    hw, Upstream, Downstream = make_pair()
    b = fragment_factory(Downstream)
    a = b.Upstream

    a.check_state()
    hw.a_good = False

    fix_targets([b])

    assert hw.a_fixes == 1
    assert hw.b_good
    assert not a.is_suspect()


def test_suspicion_survives_a_failed_walk(fragment_factory, dataset_db):
    """If Downstream keeps failing even after its dependencies were
    re-verified, the walk gives up (finite budget) — but the suspicion stays
    published, so monitors and the next walk keep re-examining Upstream."""
    hw, Upstream, Downstream = make_pair(b_fixable=False, b_max_fix_attempts=2)
    b = fragment_factory(Downstream)
    a = b.Upstream

    a.check_state()
    checks_before_walk = hw.a_checks

    with pytest.raises(CalibrationError, match="failed after 2 attempts"):
        b.fix_state()

    # The backtrack re-verified Upstream once, despite its valid timeout
    assert hw.a_checks == checks_before_walk + 1
    # Downstream failed again afterwards, so Upstream is left suspect
    assert a.is_suspect()
    entry = dataset_db.get(STATUS_DATASET)["Upstream"]
    assert entry["suspected_by"] == ["Downstream"]


def test_marking_skips_deps_that_already_guess_bad(fragment_factory):
    """Only a dependency whose cached OK is being *trusted* can be suspect —
    one that is expired (or never checked) will be re-measured anyway."""
    hw, Upstream, Downstream = make_pair()
    b = fragment_factory(Downstream)
    a = b.Upstream

    # Upstream never checked: guesses BAD_EXPIRED, so there is nothing to mark
    assert _suspect_dependencies_of(b) is False
    assert not a.is_suspect()

    a.check_state()
    assert _suspect_dependencies_of(b) is True
    assert a.is_suspect()
    assert not b.is_suspect()  # a node never suspects itself

    # Already-suspect deps are not "newly" marked: the walk must not loop
    assert _suspect_dependencies_of(b) is False


def make_chain():
    """Grandparent <- Parent <- Child over shared mock hardware, for the
    one-hop suspicion tests. Child cannot come good unless the parent's
    hardware is good; grandparent and parent check good independently."""
    hw = SimpleNamespace(
        g_good=True,
        p_good=True,
        c_good=False,
        g_checks=0,
        g_fixes=0,
        p_checks=0,
        p_fixes=0,
        c_fixes=0,
    )

    class Grandparent(Calibration):
        def build_calibration(self):
            self.set_check_timeout(60)

        def check_own_state(self):
            hw.g_checks += 1
            return (
                CalibrationResult.OK if hw.g_good else CalibrationResult.BAD_DATA
            ), None

        def fix_own_state(self) -> None:
            hw.g_fixes += 1
            hw.g_good = True

    class Parent(Calibration):
        def build_calibration(self):
            self.set_check_timeout(60)
            self.add_dependency(Grandparent)

        def check_own_state(self):
            hw.p_checks += 1
            return (
                CalibrationResult.OK if hw.p_good else CalibrationResult.BAD_DATA
            ), None

        def fix_own_state(self) -> None:
            hw.p_fixes += 1
            hw.p_good = hw.g_good

    class Child(Calibration):
        def build_calibration(self):
            self.set_check_timeout(60)
            self.add_dependency(Parent)
            self.set_max_fix_attempts(2)

        def check_own_state(self):
            return (
                CalibrationResult.OK if hw.c_good else CalibrationResult.BAD_DATA
            ), None

        def fix_own_state(self) -> None:
            hw.c_fixes += 1
            hw.c_good = hw.p_good

    return hw, Grandparent, Parent, Child


def test_suspicion_travels_one_hop_only(fragment_factory):
    """A failure in Child impugns only its DIRECT dependency (Parent), never
    the grandparent: doubt propagates one edge per failure (Charles,
    2026-08-02). Live case: CoarseClockCentre's failed fix wrongly marked
    both the XODT *and* the red MOT behind it."""
    hw, Grandparent, Parent, Child = make_chain()
    c = fragment_factory(Child)
    p = c.Parent
    g = p.Grandparent

    g.check_state()
    p.check_state()
    assert _suspect_dependencies_of(c) is True

    assert p.is_suspect()
    assert not g.is_suspect()  # one hop only — the grandparent is not accused


def test_suspicion_cascades_hop_by_hop_through_the_walk(fragment_factory):
    """When the parent genuinely IS broken because of the grandparent, the
    cascade still reaches the grandparent — one failed fix per hop: Child's
    failure suspects Parent; Parent re-measures bad and its own failed fix
    suspects Grandparent; Grandparent is repaired and the chain unwinds."""
    hw, Grandparent, Parent, Child = make_chain()
    c = fragment_factory(Child)
    p = c.Parent
    g = p.Grandparent

    g.check_state()
    p.check_state()
    # The silent double drift: both look good, both are actually broken
    hw.g_good = False
    hw.p_good = False

    c.fix_state()

    # Each hop was accused exactly by its own dependent's failure, and the
    # whole chain came good bottom-up
    assert hw.g_fixes == 1
    assert hw.p_fixes >= 1
    assert hw.c_good and hw.p_good and hw.g_good
    assert not p.is_suspect() and not g.is_suspect()
    assert c.check_state()[0] == CalibrationResult.OK


def test_suspect_guess_leaves_the_cached_result_intact(fragment_factory):
    hw, Upstream, _ = make_pair()
    a = fragment_factory(Upstream)
    a.check_state()

    a._add_suspicion("SomethingDownstream")
    assert a.is_suspect()
    # The guess reports "do not trust the cache" without destroying the cache
    assert a._guess_own_state() == CalibrationResult.BAD_EXPIRED

    a._retract_suspicion("SomethingDownstream")
    # The still-in-timeout OK is trusted again, unmeasured
    assert not a.is_suspect()
    assert a._guess_own_state() == CalibrationResult.OK


def test_a_fresh_measurement_clears_suspicion(fragment_factory):
    hw, Upstream, _ = make_pair()
    a = fragment_factory(Upstream)
    a.check_state()
    a._add_suspicion("SomethingDownstream")

    a.check_state()  # suspect, so this re-measures despite the timeout

    assert not a.is_suspect()
    assert a._guess_own_state() == CalibrationResult.OK


def test_a_fresh_bad_measurement_also_clears_suspicion(fragment_factory):
    """A suspect node that measures bad is genuinely bad — the ordinary fix
    path owns it from there, and suspicion is moot."""
    hw, Upstream, _ = make_pair()
    a = fragment_factory(Upstream)
    a.check_state()
    hw.a_good = False
    a._add_suspicion("SomethingDownstream")

    result, _ = a.check_state()

    assert result == CalibrationResult.BAD_DATA
    assert not a.is_suspect()


def test_dependent_coming_good_retracts_its_suspicion(fragment_factory, dataset_db):
    """Rule (b): the dependent that cast the suspicion comes good, so the
    suspect node's still-valid OK is trusted again without a measurement."""
    hw, Upstream, Downstream = make_pair()
    b = fragment_factory(Downstream)
    a = b.Upstream

    a.check_state()
    a._add_suspicion("Downstream")
    checks_before = hw.a_checks

    # Downstream records a good check (e.g. from another walk's measurement)
    hw.b_good = True
    b._record_own_check(CalibrationResult.OK, None)

    assert not a.is_suspect()
    assert hw.a_checks == checks_before  # no re-measurement was needed
    assert a._guess_own_state() == CalibrationResult.OK
    # The dataset was pruned too, for readers in other processes
    assert dataset_db.get(STATUS_DATASET)["Upstream"]["suspected_by"] == []


def test_diamond_suspicion_clears_per_suspector(fragment_factory):
    """D suspected by both X and Y stays suspect until *both* are answered."""

    class D(Calibration):
        def build_calibration(self):
            self.set_check_timeout(60)

        def check_own_state(self):
            return CalibrationResult.OK, None

    class X(Calibration):
        def build_calibration(self):
            self.set_check_timeout(60)
            self.add_dependency(D)

        def check_own_state(self):
            return CalibrationResult.OK, None

    class Y(Calibration):
        def build_calibration(self):
            self.set_check_timeout(60)
            self.add_dependency(D)

        def check_own_state(self):
            return CalibrationResult.OK, None

    # Both targets in one build scope, as one experiment's shim would do:
    # sharing an instance is per-tree (see dag.building)
    with dag.building():
        x = fragment_factory(X)
        y = fragment_factory(Y)
    d = x.D
    assert y.D is d  # shared instance: this is a diamond, not two chains

    d._add_suspicion("X")
    d._add_suspicion("Y")

    x._record_own_check(CalibrationResult.OK, None)
    assert d.is_suspect()  # Y's doubt is not answered by X coming good

    y._record_own_check(CalibrationResult.OK, None)
    assert not d.is_suspect()


def test_suspicion_survives_a_new_worker_process(fragment_factory):
    """Suspicion rides the status dataset, so a fresh instance (a new worker
    process) still distrusts the cached OK."""
    hw, Upstream, _ = make_pair()
    a = fragment_factory(Upstream)
    a.check_state()
    a._add_suspicion("SomethingDownstream")
    del a

    a2 = fragment_factory(Upstream)
    assert a2._guess_own_state() == CalibrationResult.BAD_EXPIRED
    assert a2.is_suspect()


def test_status_entries_without_suspected_by_are_tolerated(
    fragment_factory, dataset_db
):
    """Entries written before this feature existed simply mean "not suspect"."""
    hw, Upstream, _ = make_pair()
    a = fragment_factory(Upstream)
    a.check_state()
    del dataset_db.data[STATUS_DATASET][1]["Upstream"]["suspected_by"]
    del a

    a2 = fragment_factory(Upstream)
    assert a2._guess_own_state() == CalibrationResult.OK
    assert not a2.is_suspect()


def test_force_marks_all_then_walks_ordinarily(fragment_factory):
    """force=True marks every fixable node UNCALIBRATED and then runs the
    ordinary walk — so, unlike the old special-cased force walk, it DOES
    backtrack: Downstream's failed fix casts suspicion on Upstream, which is
    re-measured before Downstream is retried."""
    hw, Upstream, Downstream = make_pair(b_fixable=False, b_max_fix_attempts=2)
    b = fragment_factory(Downstream)

    with pytest.raises(CalibrationError, match="failed after 2 attempts"):
        b.fix_state(force=True)

    # Upstream got its one forced re-fix (mark cleared by its success)...
    assert hw.a_fixes == 1
    assert hw.b_fixes == 2
    # ...and was then re-measured when Downstream's failure suspected it —
    # the walk backtracked instead of latching Upstream done for the walk
    assert hw.a_checks >= 2


def test_applet_reports_suspect_only_for_trusted_looking_entries():
    now = 1000.0
    ok = {"status": 0, "last_check": now - 5, "timeout": 60}

    assert _node_state({**ok, "suspected_by": ["X"]}, now)[0] == "suspect"
    assert _node_state({**ok, "suspected_by": []}, now)[0] == "ok"
    assert _node_state(ok, now)[0] == "ok"  # pre-feature entry: no key

    # Suspicion never masks a state that already forces attention
    bad = {"status": 4, "last_check": now - 5, "timeout": 60, "suspected_by": ["X"]}
    assert _node_state(bad, now)[0] == "bad"
    stale = {"status": 0, "last_check": now - 500, "timeout": 60, "suspected_by": ["X"]}
    assert _node_state(stale, now)[0] == "expired"


def test_the_walk_keeps_re_examining_upstream_not_just_once(fragment_factory):
    """The walk must be able to change its mind more than once.

    Downstream needs three good calibration rounds, and each one leaves
    Upstream drifted again. So the walk has to go back to Upstream, repair it,
    and return to Downstream repeatedly. A walk that plans once and then
    retries only the node it is on gets stuck re-fixing Downstream forever
    against hardware it can never satisfy — which is what a live run did:
    suspects were flagged correctly but never re-measured, and the furthest-on
    calibration ran over and over.
    """
    hw = SimpleNamespace(a_good=True, b_rounds=0, a_fixes=0, b_fixes=0)

    class Upstream(Calibration):
        def build_calibration(self):
            self.set_check_timeout(60)

        def check_own_state(self):
            return (
                CalibrationResult.OK if hw.a_good else CalibrationResult.BAD_DATA
            ), None

        def fix_own_state(self) -> None:
            hw.a_fixes += 1
            hw.a_good = True

    class Downstream(Calibration):
        def build_calibration(self):
            self.set_check_timeout(60)
            self.add_dependency(Upstream)
            # A budget only so a regression fails the test instead of hanging
            self.set_max_fix_attempts(12)

        def check_own_state(self):
            return (
                CalibrationResult.OK if hw.b_rounds >= 3 else CalibrationResult.BAD_DATA
            ), None

        def fix_own_state(self) -> None:
            hw.b_fixes += 1
            if hw.a_good:
                hw.b_rounds += 1
                hw.a_good = False  # calibrating Downstream drifts Upstream again

    b = fragment_factory(Downstream)
    b.Upstream.check_state()  # Upstream's OK is cached and inside its timeout

    b.fix_state()

    assert hw.b_rounds == 3
    assert hw.a_fixes == 2  # went back to Upstream after each drift
    assert b.check_state()[0] == CalibrationResult.OK


def test_a_node_is_retried_only_after_more_basic_work_is_done(fragment_factory):
    """Ordering: the most basic not-OK node is always the next one measured."""
    order = []
    hw = SimpleNamespace(a_good=True, b_good=False)

    class Upstream(Calibration):
        def build_calibration(self):
            self.set_check_timeout(60)

        def check_own_state(self):
            order.append("check A")
            return (
                CalibrationResult.OK if hw.a_good else CalibrationResult.BAD_DATA
            ), None

        def fix_own_state(self) -> None:
            order.append("fix A")
            hw.a_good = True

    class Downstream(Calibration):
        def build_calibration(self):
            self.set_check_timeout(60)
            self.add_dependency(Upstream)

        def check_own_state(self):
            order.append("check B")
            return (
                CalibrationResult.OK if hw.b_good else CalibrationResult.BAD_DATA
            ), None

        def fix_own_state(self) -> None:
            order.append("fix B")
            hw.b_good = hw.a_good

    b = fragment_factory(Downstream)
    b.Upstream.check_state()
    order.clear()
    hw.a_good = False  # the silent drift

    b.fix_state()

    # B is attempted, fails, blames A; A is then re-measured and repaired
    # before B is tried again — never two B attempts in a row.
    assert "fix A" in order
    last_fix_b = len(order) - 1 - order[::-1].index("fix B")
    assert order.index("fix A") < last_fix_b
    for first, second in zip(order, order[1:]):
        assert not (first == "fix B" and second == "fix B")
