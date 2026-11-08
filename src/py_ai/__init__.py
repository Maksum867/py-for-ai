"""
Py-For-AI: A Python CLI utility to package a codebase into a single text file/clipboard for AI context.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: the installed distribution metadata.
    __version__ = version("py-for-ai")
except PackageNotFoundError:
    # Running from a source checkout without installation.
    __version__ = "0.5.0"

__author__ = "Maksym Khlystun"
