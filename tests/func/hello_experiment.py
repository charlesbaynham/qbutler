from artiq.experiment import EnvExperiment


class HelloExperiment(EnvExperiment):
    def build(self):
        pass

    def run(self):
        print("Hello!")


from ndscan.experiment import ExpFragment


class HelloFragment(ExpFragment):
    def build_fragment(self):
        pass

    def run_once(self):
        print("Hello!")


from ndscan.experiment.entry_point import make_fragment_scan_exp

HelloFragmentExperiment = make_fragment_scan_exp(HelloFragment)
# Rename, otherwise ARTIQ will try to import the Fragment because ndscan copies its name
HelloFragmentExperiment.__name__ = "HelloFragmentExperiment"
