import os
import subprocess
from pathlib import Path


COMMAND = "git describe --tags --long --dirty --always".split(" ")
UNTRACKED_CMD = "git status --porcelain".split(" ")
VERSION_FILE = Path(__file__, "../../VERSION.json").resolve()
OVERRIDE_ENVVAR = "PYTHON_VERSION_OVERRIDE"


def get_version() -> str:
    """
    Returns a string describing the git version of the given project.
    """
    # If the override env var is set, use it
    if OVERRIDE_ENVVAR in os.environ and os.environ[OVERRIDE_ENVVAR]:
        return os.environ[OVERRIDE_ENVVAR]

    import json

    semver = json.loads(VERSION_FILE.read_text())["version"]

    try:
        gitver = subprocess.check_output(COMMAND, universal_newlines=True).strip()

        # Thanks to pyfidelity/setuptools-git-version
        try:
            parts = gitver.split("-")
            assert len(parts) in (3, 4)
            dirty = len(parts) == 4
            tag, count, sha = parts[:3]

            # Check for untracked files too
            if not dirty:
                if subprocess.check_output(
                    UNTRACKED_CMD, universal_newlines=True
                ).strip():
                    dirty = True

            if dirty:
                dirty_str = ".d"
            else:
                dirty_str = ""

            return f"{semver}+{sha.lstrip('g')}{dirty_str}"

        except AssertionError:
            return semver
    except (FileNotFoundError, subprocess.CalledProcessError):
        # Git is not installed or this directory wasn't copied as a git repo for some reason
        # Fall back to the baked-in semver
        return semver
