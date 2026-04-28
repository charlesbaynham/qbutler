import logging

from artiq.experiment import EnvExperiment

logger = logging.getLogger(__name__)
MARKER_STRING = "1IR1DI28YC5QUFG8UR5IM1Z93LJF6R3G49Q7S"


class MarkerExperiment(EnvExperiment):
    def build(self):
        pass

    def run(self):
        print("Marker experiment completed")
        print(MARKER_STRING)
        logger.error("Hello?")
