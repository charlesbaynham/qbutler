"""Per-pipeline namespacing of the live-view datasets and applets.

See :mod:`qbutler.scoping`: without this, two calibration runs in different
pipelines publish over each other's DAG and optimizer datasets and share one
contended dashboard applet.
"""

import pytest

from qbutler import scoping


class FakeEnv:
    """An environment with a scheduler cached the way Calibration caches one."""

    def __init__(self, pipeline_name):
        self._scheduler = type("S", (), {"pipeline_name": pipeline_name})()


class NoSchedulerEnv:
    """An environment with no scheduler at all (a bare unit test, artiq_run)."""

    _scheduler = None

    def get_device(self, name):
        raise KeyError(name)


class LookupEnv:
    """An environment that has not cached a scheduler, but can look one up."""

    def __init__(self, pipeline_name):
        self._pipeline_name = pipeline_name

    def get_device(self, name):
        assert name == "scheduler"
        return type("S", (), {"pipeline_name": self._pipeline_name})()


def test_pipeline_name_from_cached_scheduler():
    assert scoping.pipeline_name(FakeEnv("monitors")) == "monitors"


def test_pipeline_name_falls_back_to_device_lookup():
    assert scoping.pipeline_name(LookupEnv("main")) == "main"


@pytest.mark.parametrize(
    "env",
    [NoSchedulerEnv(), FakeEnv(None), FakeEnv(""), object()],
    ids=["no-scheduler", "no-name", "empty-name", "bare-object"],
)
def test_pipeline_name_unknown(env):
    """Anything we cannot read a usable pipeline name from is simply unscoped,
    never an error: visualisation must not be able to break a calibration."""
    assert scoping.pipeline_name(env) is None


def test_scoped_key_suffixes_the_pipeline():
    assert scoping.scoped_key("calibrations.dag", FakeEnv("main")) == (
        "calibrations.dag.main"
    )


def test_scoped_key_sanitises_the_pipeline_name():
    # A dot is the dashboard's dataset-tree separator, so it must not survive
    # into the slug and silently add a tree level.
    key = scoping.scoped_key("calibrations.dag", FakeEnv("odd name.v2/x"))
    assert key == "calibrations.dag.odd_name_v2_x"


def test_scoped_key_unchanged_without_a_pipeline():
    assert (
        scoping.scoped_key("calibrations.dag", NoSchedulerEnv()) == "calibrations.dag"
    )


def test_different_pipelines_get_different_keys():
    assert scoping.scoped_key(
        "calibrations.dag", FakeEnv("main")
    ) != scoping.scoped_key("calibrations.dag", FakeEnv("monitors"))


def test_applet_group_inserts_the_pipeline_level():
    env = FakeEnv("monitors")
    assert scoping.applet_group(env) == ["Calibrations", "monitors"]
    assert scoping.applet_group(env, "Optimizers") == [
        "Calibrations",
        "monitors",
        "Optimizers",
    ]


def test_applet_group_without_a_pipeline():
    # A single-element path collapses to the bare string ARTIQ accepts for a
    # top-level group, matching the pre-scoping behaviour.
    env = NoSchedulerEnv()
    assert scoping.applet_group(env) == "Calibrations"
    assert scoping.applet_group(env, "Optimizers") == ["Calibrations", "Optimizers"]
