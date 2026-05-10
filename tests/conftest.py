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
    parser.addoption(
        "--no-fullstack",
        action="store_true",
        default=False,
        help="skip full-stack tests that require a live artiq_master (e.g. no IPv6)",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "withartiq: mark test as requiring ARTIQ tooling and the kernel emulator",
    )
    config.addinivalue_line(
        "markers",
        "fullstack: mark test as requiring a live artiq_master process",
    )

    if config.getoption("--withartiq") and not os.environ.get("LIBARTIQ_EMULATOR"):
        raise pytest.UsageError(
            "--withartiq requires LIBARTIQ_EMULATOR to point at the ARTIQ kernel "
            "emulator library. Did you forget `nix develop --impure`?"
        )


def pytest_collection_modifyitems(config, items):
    skip_no_artiq = pytest.mark.skip(reason="need --withartiq option to run")
    skip_no_fullstack = pytest.mark.skip(
        reason="skipped by --no-fullstack (needs live artiq_master)"
    )
    no_fullstack = config.getoption("--no-fullstack")
    with_artiq = config.getoption("--withartiq")

    for item in items:
        if "withartiq" in item.keywords and not with_artiq:
            item.add_marker(skip_no_artiq)
        elif "fullstack" in item.keywords and no_fullstack:
            item.add_marker(skip_no_fullstack)
