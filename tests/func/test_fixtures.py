from artiq.experiment import EnvExperiment

from qbutler.calibration import Calibration


def test_dataset_db(dataset_db):
    return dataset_db


def test_dataset_mgr(dataset_mgr):
    return dataset_mgr


def test_fragment_factory(fragment_factory):
    class MinimalCalibration(Calibration):
        def build_calibration(self):
            pass

    fragment_factory(MinimalCalibration)


def test_full_experiment_runner(build_and_run_experiment):
    from hello_experiment import HelloExperiment

    build_and_run_experiment(HelloExperiment)
