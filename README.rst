qbutler
=======

**Manage a complex research experiment with lots of moving parts and drifting calibrations automatically and repeatably. **

*Charles Baynham 2022*

A package was generated automatically by an opinionated
`cookiecutter <https://github.com/audreyr/cookiecutter>`_
template:
`PyPackage <https://gitlab.com/aion-physics/code/pypackage-template>`_.

This repository should now be edited for your needs. It has been set up with
some best-practices for testing and linting using GitHub Actions - the rest of
this readme outlines these; feel free to disable any you don't want.

Explanation
-----------

The decisions `pypackage` makes should all be explained here.

Version control
---------------

* **Your package will use git for version control**
  You probably already agree that this is a good idea if you're reading this message.

* **Semantic package versioning is hard coded** The package version is defined
  by the `VERSION.json` file in the root of this project. The versioning system
  will also extract details from git to mark your packages with a hash of their
  most recent commit, and will be baked into any tarballs you create (e.g. for
  uploading to a PyPI registry). When updating the version, follow `semantic
  versioning guidelines <https://semver.org/>`_.

Virtual environment
-------------------

* **Poetry manages the virtual environment**
  This project uses `Poetry <https://python-poetry.org/>`_ for dependency management. To
  install all dependencies, run ``poetry install``. This creates a ``.venv`` in the project
  directory. Activate it with ``poetry shell`` or prefix commands with ``poetry run``.

README
------

* **README should use reStructuredText format**
  This is the format used by most Python tools, is expected by
  `setuptools <https://setuptools.readthedocs.io>`_, and can be used by
  `Sphinx <http://sphinx-doc.org/>`_.

* **As few README files as possible**
  Additional README files (AUTHORS, CHANGELOG, etc) should be left to the user to create when necessary.

LICENSE
-------

* **No license**
  This template is aimed at projects which remain internal. If you later publish this code publicly, you should make sure to choose a license so others can use it legally.

Dependencies
------------

* **Dependencies are managed by Poetry**
  Edit ``pyproject.toml`` to add or change dependencies, then run ``poetry lock``
  to update the lock file. Commit both files. The ``poetry.lock`` file pins exact
  versions for reproducible installs in CI and on other machines.

Documentation
-------------

* **Use `sphinx <https://www.sphinx-doc.org/en/master/>`_**
  Sphinx is a powerful documentation tool which can produce documentation in
  many formats from the same input files. It can even be configured to parse
  your project's code and extract documentation from specially formatted
  comments, allowing you to keep the documentation right next to the code and
  reducing the risk of them becoming out-of-sync.
* **Use GitHub Pages**
  GitHub Pages lets you host a static html website associated with your project.
  The ``Deploy Docs`` workflow will build your Sphinx documentation and publish
  it to GitHub Pages. The documentation will only be updated when you push to
  the master branch or tag a commit.
* **Build locally**
  To quickly compile the documentation locally, run
  ``poetry run sphinx-build docs public -b html`` and open ``public/index.html``.

Testing
-------

* **Uses** `pytest <https://docs.pytest.org>`_ **as the default test runner**
  This can be changed easily, though pytest is an easier, more powerful test
  library and runner than the standard library's unittest. Tests will be run
  automatically in GitHub Actions CI.
* **Define testing dependencies in** ``pyproject.toml``
  Use the ``[tool.poetry.group.dev.dependencies]`` section for dependencies
  required for testing but not for running your package.
* **Use** `coverage <https://coverage.readthedocs.io/>`_ **for test coverage calculation**
  Receive a report of how much coverage your tests have in your codebase when
  you run them.
* **Slow tests are gated with** ``--runslow``
  Some unit tests are really slow: you don't want to run these for every single
  commit. You can mark your tests as slow using the decorator
  ``@pytest.mark.slow``. These are always run in CI but can be skipped locally
  by omitting ``--runslow``.
* **`tests` directory should not be a package**
  The `tests` directory should not be a Python package unless you want to define
  some fixtures. But the best practices are to use `PyTest fixtures
  <https://docs.pytest.org/en/latest/fixture.html>`_ which provide a better
  solution. Therefore, the `tests` directory has no `__init__.py` file.

Linting
-------

* **Use** `black <https://black.readthedocs.io/en/stable/?badge=stable>`_ **for code styling**
  Code style is important: it makes it much easier for others to read your code.
  It's also boring and repetitive. `black` is a very opinionated code styler
  which makes all the decisions regarding code style, allowing you to focus on
  what you're actually writing. It will be run by the CI as a check stage.
* **Use** `pre-commit <https://pre-commit.com/>`_ **for automated styling**
  To prevent you from having to manually style your code, use `pre-commit` to
  configure your system to automatically run `black` every time you commit. This
  is not installed automatically since I don't want to alter your python
  environment without permission. To use it, run `pip install pre-commit &&
  pre-commit install`. This will also run automatically in the CI.

Authors
-------

`qbutler` was written by `Charles Baynham <charles.baynham@gmail.com>`_.

The `pypackage template <https://gitlab.com/aion-physics/code/pypackage-template>`_ from which this package was generated was written by Charles Baynham and inspired by `cookiecutter-pypackage-minimal <https://github.com/kragniz/cookiecutter-pypackage-minimal>`_
