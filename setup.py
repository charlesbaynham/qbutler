import io
import os
import re

import setuptools

import package_versioning


def myversion():
    from qbutler._version import get_version

    return get_version()


def read(filename):
    filename = os.path.join(os.path.dirname(__file__), filename)
    text_type = type("")
    with io.open(filename, mode="r", encoding="utf-8") as fd:
        return re.sub(text_type(r":[a-z]+:`~?(.*?)`"), text_type(r"``\1``"), fd.read())


setuptools.setup(
    version=myversion(),
    cmdclass=package_versioning.get_cmdclass(),
    name="qbutler",
    url="https://gitlab.com/aion-physics/code/qbutler",
    license="None",
    author="Charles Baynham",
    author_email="charles.baynham@gmail.com",
    description="Manage a complex research experiment with lots of moving parts and drifting calibrations automatically and repeatably. ",
    long_description=read("README.rst"),
    packages=setuptools.find_packages(exclude=("tests",)),
    install_requires=[
        r
        for r in open("requirements.in").read().splitlines()
        if r and not re.match(r"\s*\#", r)
    ],
    extras_require={
        "dev": [
            r
            for r in open("requirementsDev.in").read().splitlines()
            if r and not re.match(r"\s*\#", r)
        ]
    },
    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
    ],
)
