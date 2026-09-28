"""Domain routers under `app/api/`.

`cooklog.py` is the Cooking Log route pair; `recipes.py`, `pantry.py`, and
`shortlists.py` arrive with their own tickets. Each module exposes a
`build_*_router()` factory and reads its collaborators from `app.state`, because
`app/main.py`'s `lifespan` is what opens and closes them (§9.19).
"""
