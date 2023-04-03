"""Module for reporting the package version."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("obsplus")
except PackageNotFoundError:
    # package is not installed
    __version__ = "0.0.0"

__last_version__ = ".".join(__version__.split(".")[:3])