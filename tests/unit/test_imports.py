"""Sample unit tests"""

import importlib
import pkgutil

import pytest

import qbutler


@pytest.mark.parametrize(
    "module_name",
    [
        name
        for _, name, _ in pkgutil.walk_packages(
            qbutler.__path__, qbutler.__name__ + "."
        )
    ],
)
def test_import_all_modules(module_name):
    importlib.import_module(module_name)
