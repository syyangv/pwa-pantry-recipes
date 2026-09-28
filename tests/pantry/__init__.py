"""Pantry Item catalog: read-only access, the frozen snapshot, and the freeze tool.

`test_catalog.py` is the product's read path and its six indexes.
`test_snapshot_script.py` is the contract of the developer tool that freezes the
catalog fixture those golden assertions read (`scripts/snapshot_pantry_catalog.py`),
so the two are one package: the fixture and the thing that writes it.
"""
