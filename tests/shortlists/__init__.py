"""The Meal Shortlist store's own suite (D3, F12).

`tests/shortlists/` holds the store seam; `tests/api/test_shortlists_api.py` holds
the four routes. Split the same way `tests/mapping/` and `tests/api/` are split,
and for the same reason: the store's refusals are properties of the state it
writes, and the router's statuses are properties of what it does with them.
"""
