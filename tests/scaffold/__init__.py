"""Scaffold test package.

Exists so `tests/` is a package-free directory on sys.path (pytest inserts the
directory of every loaded conftest) and so the scaffold suite is grouped apart
from the feature suites that arrive with the implementation.
"""
