import pytest
from artiq.experiment import EnvExperiment
from ndscan.experiment import ExpFragment
from ndscan.experiment import run_fragment_once
from ndscan.experiment.parameters import FloatParam


class MyFragment(ExpFragment):
    def build_fragment(self) -> None:
        self.setattr_param("myparam", FloatParam, "A test parameter", default=123)

    def run_once(self) -> None:
        print(f"myparam was {self.myparam.get()}")


class MyEnvExperiment(EnvExperiment):
    def build(self):
        self.myfragment = MyFragment(self, [])

    def run_fragment(self):
        run_fragment_once(self.myfragment)


@pytest.mark.xfail
def test_accumulation_of_default_params(experiment_factory):
    # See https://github.com/OxfordIonTrapGroup/ndscan/pull/310 for discussion
    exp = experiment_factory(MyEnvExperiment)
    frag = exp.myfragment

    exp.run_fragment()
    assert len(frag._default_params) == 1

    exp.run_fragment()
    assert len(frag._default_params) == 1
