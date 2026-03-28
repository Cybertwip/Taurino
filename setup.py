"""Minimal setup.py for Taurino package."""

from setuptools import setup, find_packages

setup(
    name="taurino",
    version="1.0.0",
    description="PDP Xbox Controller Driver for macOS",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "pyusb>=1.2.1",
        "pygame>=2.5",
    ],
    entry_points={
        "console_scripts": [
            "taurino=taurino.__main__:main",
        ],
    },
)
