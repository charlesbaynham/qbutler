"""Tests for the test fixtures themselves.

These tests verify that the test infrastructure (fixtures, helpers) works
correctly. This is important because the fixtures are used in many other
tests.
"""

import pytest
from artiq.coredevice.core import CompileError
from artiq.experiment import EnvExperiment
from artiq.experiment import kernel


class InvalidKernelExperiment(EnvExperiment):
    def build(self):
        self.setattr_device("core")

    @kernel
    def run(self):
        # This is a type error: can't add int and string
        return 1 + "hello"


def test_dataset_db(dataset_db):
    return dataset_db


def test_dataset_mgr(dataset_mgr):
    return dataset_mgr


def test_fragment_factory(fragment_factory):
    pass


def test_full_experiment_runner(build_experiment):
    pass


def test_full_experiment_runner_fragment(build_and_run_experiment):
    pass


def test_invalid_kernel(build_and_run_experiment):
    with pytest.raises(CompileError):
        build_and_run_experiment(InvalidKernelExperiment)


@pytest.mark.withartiq
@pytest.mark.fullstack
def test_build_and_run_full_stack_basic(build_and_run_full_stack):
    import hello_experiment

    print(build_and_run_full_stack("HelloExperiment", hello_experiment.__file__))


@pytest.mark.withartiq
@pytest.mark.fullstack
def test_build_and_run_full_stack_error(build_and_run_full_stack):
    import hello_experiment

    with pytest.raises(RuntimeError):
        build_and_run_full_stack("ErrorExperiment", hello_experiment.__file__)


@pytest.mark.withartiq
@pytest.mark.fullstack
def test_build_and_run_full_stack_kernel(build_and_run_full_stack):
    import hello_experiment

    print(build_and_run_full_stack("KernelExperiment", hello_experiment.__file__))


@pytest.mark.withartiq
@pytest.mark.fullstack
def test_build_and_run_full_stack_importer(build_and_run_full_stack):
    import hello_experiment

    print(build_and_run_full_stack("ImporterExperiment", hello_experiment.__file__))
