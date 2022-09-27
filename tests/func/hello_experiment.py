from artiq.experiment import EnvExperiment


class HelloExperiment(EnvExperiment):
    def build(self):
        pass

    def run(self):
        print("Hello!")
