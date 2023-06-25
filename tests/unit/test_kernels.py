from artiq.experiment import EnvExperiment
from artiq.experiment import kernel


class SimpleKernel(EnvExperiment):
    def build(self):
        self.setattr_device("core")

    @kernel
    def run(self):
        print("Hello world")


class FailingKernel(EnvExperiment):
    def build(self):
        self.setattr_device("core")

    @kernel
    def run(self):
        print("Hello" + 123)


def test_kernel_success(build_and_run_experiment):
    build_and_run_experiment(SimpleKernel)


def test_kernel_failure(build_and_run_experiment):
    build_and_run_experiment(FailingKernel)
