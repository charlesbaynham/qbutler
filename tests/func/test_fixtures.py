import pytest

from qbutler.calibration import Calibration


def test_dataset_db(dataset_db):
    pass


def test_dataset_mgr(dataset_mgr):
    pass


def test_fragment_factory(fragment_factory):
    class MinimalCalibration(Calibration):
        def build_calibration(self):
            pass

    fragment_factory(MinimalCalibration)


def test_full_experiment_runner(build_and_run_experiment):
    from hello_experiment import HelloExperiment

    build_and_run_experiment(HelloExperiment)


def test_full_experiment_runner_fragment(build_and_run_experiment):
    import hello_experiment

    build_and_run_experiment(
        hello_experiment.HelloFragmentExperiment, hello_experiment.__file__
    )


@pytest.mark.slow
def test_build_and_run_full_stack(build_and_run_full_stack):
    import hello_experiment

    print(build_and_run_full_stack("HelloExperiment", hello_experiment.__file__))


@pytest.mark.slow
def test_build_and_run_full_stack_error(build_and_run_full_stack):
    import hello_experiment

    with pytest.raises(RuntimeError):
        build_and_run_full_stack("ErrorExperiment", hello_experiment.__file__)


@pytest.mark.slow
def test_build_and_run_full_stack_importer(build_and_run_full_stack):
    import hello_experiment

    print(build_and_run_full_stack("ImporterExperiment", hello_experiment.__file__))
