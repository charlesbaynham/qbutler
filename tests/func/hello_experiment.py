from artiq.experiment import EnvExperiment
from ndscan.experiment import ExpFragment
from ndscan.experiment.entry_point import make_fragment_scan_exp


class HelloExperiment(EnvExperiment):
    def build(self):
        pass

    def run(self):
        print("Hello!")


class ErrorExperiment(EnvExperiment):
    def build(self):
        pass

    def run(self):
        raise ValueError


class ImporterExperiment(EnvExperiment):
    def build(self):
        pass

    def run(self):
        import qbutler


class HelloFragment(ExpFragment):
    def build_fragment(self):
        pass

    def run_once(self):
        print("Hello!")


HelloFragmentExperiment = make_fragment_scan_exp(HelloFragment)
# Rename, otherwise ARTIQ will try to import the Fragment because ndscan copies its name
HelloFragmentExperiment.__name__ = "HelloFragmentExperiment"
