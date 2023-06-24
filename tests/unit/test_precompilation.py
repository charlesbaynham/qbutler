from artiq.experiment import EnvExperiment
from artiq.experiment import kernel


class Precompile(EnvExperiment):
    def build(self):
        self.setattr_device("core")
        self.x = 1
        self.y = 2
        self.z = 3

    def set_attr(self, value):
        self.x = value

    @kernel
    def the_kernel(self, arg):
        self.set_attr(arg + self.y)
        self.z = 23

    def run(self):
        precompiled = self.core.precompile(self.the_kernel, 40)
        print("Experiment was precompiled:")
        print(precompiled)


def test_precompilation(build_and_run_experiment):
    build_and_run_experiment(Precompile)
