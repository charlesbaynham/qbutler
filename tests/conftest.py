import logging
import os

import pytest
from fixtures import *  # noqa

logging.basicConfig(level=logging.WARNING)
logging.getLogger("qbutler").setLevel(logging.DEBUG)


def pytest_addoption(parser):
    parser.addoption(
        "--withartiq",
        action="store_true",
        default=False,
        help="run tests that require ARTIQ tooling and the kernel emulator",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "withartiq: mark test as requiring ARTIQ tooling and the kernel emulator",
    )

    if config.getoption("--withartiq") and not os.environ.get("LIBARTIQ_EMULATOR"):
        raise pytest.UsageError(
            "--withartiq requires LIBARTIQ_EMULATOR to point at the ARTIQ kernel "
            "emulator library. Did you forget `nix develop --impure`?"
        )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--withartiq"):
        return
    skip_marker = pytest.mark.skip(reason="need --withartiq option to run")
    for item in items:
        if "withartiq" in item.keywords:
            item.add_marker(skip_marker)
