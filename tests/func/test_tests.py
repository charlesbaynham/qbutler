"""Tests for the (Env)Experiment-facing dataset interface."""
import copy
import unittest

from artiq.experiment import EnvExperiment
from artiq.master.worker_db import DatasetManager
from pytest import fixture
from pytest import raises
from sipyco.sync_struct import process_mod


class DatasetExperiment(EnvExperiment):
    def get(self, key):
        return self.get_dataset(key)

    def set(self, key, value, **kwargs):
        self.set_dataset(key, value, **kwargs)

    def append(self, key, value):
        self.append_to_dataset(key, value)


KEY = "foo"


@fixture
def test_experiment(dataset_db):
    dataset_mgr = DatasetManager(dataset_db)

    return DatasetExperiment((None, dataset_mgr, None, None))


def test_set_local(test_experiment, dataset_db):
    with raises(KeyError):
        test_experiment.get(KEY)

    for i in range(2):
        test_experiment.set(KEY, i)
        assert test_experiment.get(KEY) == i
        with raises(KeyError):
            dataset_db.get(KEY)


def test_set_broadcast(test_experiment, dataset_db):
    with raises(KeyError):
        test_experiment.get(KEY)

    test_experiment.set(KEY, 0, broadcast=True)
    assert test_experiment.get(KEY) == 0
    assert dataset_db.get(KEY) == 0

    test_experiment.set(KEY, 1, broadcast=False)
    assert test_experiment.get(KEY) == 1
    with raises(KeyError):
        dataset_db.get(KEY)


def test_append_local(test_experiment):
    test_experiment.set(KEY, [])
    test_experiment.append(KEY, 0)
    assert test_experiment.get(KEY) == [0]
    test_experiment.append(KEY, 1)
    assert test_experiment.get(KEY) == [0, 1]


def test_append_broadcast(test_experiment, dataset_db):
    test_experiment.set(KEY, [], broadcast=True)
    test_experiment.append(KEY, 0)
    assert dataset_db.data[KEY][1] == [0]
    test_experiment.append(KEY, 1)
    assert dataset_db.data[KEY][1] == [0, 1]


def test_append_array(test_experiment):
    for broadcast in (True, False):
        test_experiment.set(KEY, [], broadcast=broadcast)
        test_experiment.append(KEY, [])
        test_experiment.append(KEY, [])

        assert test_experiment.get(KEY) == [[], []]


def test_append_scalar_fails(test_experiment):
    for broadcast in (True, False):
        with raises(AttributeError):
            test_experiment.set(KEY, 0, broadcast=broadcast)
            test_experiment.append(KEY, 1)


def test_append_nonexistent_fails(test_experiment):
    with raises(KeyError):
        test_experiment.append(KEY, 0)
