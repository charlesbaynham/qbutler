import pytest
from artiq.coredevice.core import CompileError
from artiq.experiment import EnvExperiment
from artiq.experiment import kernel


class SimplePrecompile(EnvExperiment):
    def build(self):
        self.setattr_device("core")

    @kernel
    def printer(self):
        print("This is a message from a core")

    def run(self):
        precompiled = self.core.precompile(self.printer)
        print("Experiment was precompiled:")
        print(precompiled)


class FailingPrecompile(EnvExperiment):
    def build(self):
        self.setattr_device("core")

    @kernel
    def failer(self):
        # Try to add incompatible types
        return 123 + "hello"

    def run(self):
        precompiled = self.core.precompile(self.failer)
        print("Experiment was precompiled:")
        print(precompiled)


def test_precompilation(build_and_run_experiment):
    build_and_run_experiment(SimplePrecompile)


def test_failing_precompilation(build_and_run_experiment):
    with pytest.raises(CompileError):
        build_and_run_experiment(FailingPrecompile)
