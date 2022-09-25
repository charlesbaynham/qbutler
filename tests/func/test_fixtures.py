from qbutler.calibration import Calibration


def test_dataset_db(dataset_db):
    return dataset_db


def test_dataset_mgr(dataset_mgr):
    return dataset_mgr


def test_calibration_factory(calibration_factory):
    class MinimalCalibration(Calibration):
        def build_calibration(self):
            pass

    calibration_factory(MinimalCalibration)
