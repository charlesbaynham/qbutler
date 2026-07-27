qbutler
=======

**Manage a complex research experiment with lots of moving parts and drifting calibrations automatically and repeatably.**

*Charles Baynham*

Getting started
---------------

This project uses `Poetry <https://python-poetry.org/>`_ for dependency management.

.. code-block:: bash

    pip install poetry
    poetry install          # installs all dependencies into .venv
    poetry shell            # activates the virtual environment

To install pre-commit hooks so linting runs automatically on each commit:

.. code-block:: bash

    pre-commit install

Dependencies
------------

Runtime and development dependencies are declared in ``pyproject.toml``. To add or
change a dependency, edit that file and run ``poetry lock``, then commit both files.
The ``poetry.lock`` file ensures reproducible installs across all machines and CI.

Testing
-------

Tests use `pytest <https://docs.pytest.org>`_:

.. code-block:: bash

    poetry run pytest              # default suite (no ARTIQ tooling required)
    poetry run pytest --withartiq  # include tests that need the ARTIQ kernel emulator

The ``--withartiq`` flag requires ``LIBARTIQ_EMULATOR`` to be set, which is
provided by the Nix dev shell (``nix develop``). Mark such tests with
``@pytest.mark.withartiq``. CI runs the full suite (``--withartiq``) inside the
Nix dev shell.

Linting
-------

Code style is enforced by `black <https://black.readthedocs.io>`_, import order by
`isort <https://pycqa.github.io/isort/>`_, and unused imports removed by
`autoflake <https://github.com/PyCQA/autoflake>`_, all wired up via
`pre-commit <https://pre-commit.com/>`_.

Run all hooks manually:

.. code-block:: bash

    poetry run pre-commit run --all-files

CI
--

GitHub Actions runs two workflows on every push and pull request:

* **CI** (``.github/workflows/ci.yml``): pre-commit checks and the full pytest suite.
* **Deploy Docs** (``.github/workflows/docs.yml``): builds Sphinx docs and publishes
  to GitHub Pages on pushes to ``master`` or tagged commits.

Documentation
-------------

Docs are built with `Sphinx <https://www.sphinx-doc.org>`_ and hosted on GitHub Pages.

Build locally:

.. code-block:: bash

    poetry run sphinx-build docs public -b html
    # then open public/index.html

Authors
-------

`qbutler` was written by `Charles Baynham <charles.baynham@gmail.com>`_.
