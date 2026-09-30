"""Toolbot: automated update requests for standalone tools and binary release artifacts."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("toolbot")
except PackageNotFoundError:  # running from a source checkout without installation
    __version__ = "0.0.0+local"
