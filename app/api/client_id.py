"""`X-Client-Id` — the page outbox's exactly-once key, validated in one place.

**Why this is a module and not two copies of a four-line function.** Two routes
read this header, and they read it for opposite reasons, which is exactly the
condition under which a duplicated validator drifts. `app/api/cooklog.py` reads
it and then **ignores** it (F5: cook logs are not on the outbox, so there is no
replay to deduplicate — the header is defence in depth against a user-driven
double submit and the real guarantee is the note plus `cook_log_receipts`).
`app/api/shortlist_intents.py` reads it and **binds** it: the same key is the
ledger's primary key, and a key reused for a different intent is a 409. If the
bound, the trim, or the "present but unusable is 400, not 422" rule were written
twice, the two would agree today and diverge the first time either moved.

**The rule, once.** Absent → `None`, and the caller takes the no-ledger path.
Present → trimmed; empty after trimming or longer than `MAX_CLIENT_ID_LENGTH` →
`""`, which every caller answers with **400 `client_id_invalid`**. That status is
`pwa-deals`' and it is deliberate: a present-but-unusable idempotency key is a
malformed request, not a semantic failure of the mutation, and answering 422
would make a client retry the same broken key forever.

**The bound is a ceiling on a header, not a policy about a domain.** 200
characters is comfortably under what any proxy or browser will put in a header,
and it is the same number `pwa-deals` and `pwa-obsidian-daily` use — the value
travels as an opaque idempotency token, so there is nothing about it for this
app to be clever with. The outbox mints `crypto.randomUUID()` (36 characters),
with a timestamped fallback for a browser without it, so the real traffic sits
far below the ceiling either way.

**Nothing here touches the database.** Validation is a pure function of the raw
header value, which is what lets `tests/api/test_cooklog_api.py` parametrise it
without an app and what stops a route from "helpfully" trimming a key before
hashing it and thereby binding a different intent than the one that arrived.
"""

from __future__ import annotations

from typing import Final

#: The ceiling on `X-Client-Id`. Same number, same reason, as `pwa-deals`.
MAX_CLIENT_ID_LENGTH: Final = 200


def normalize_client_id(raw: str | None) -> str | None:
    """Trim, require non-empty, and bound — returning `None`, `""`, or the key.

    Three outcomes, and the caller has to tell them apart, which is why the
    invalid case is `""` rather than an exception: `None` means "this request
    carries no idempotency key at all" (the direct-call path, no ledger
    involved) and `""` means "it carries one that cannot be used" (a 400). An
    exception would collapse the second into a control-flow event the caller has
    to remember to catch, and the first would have to be re-derived from `raw`.
    """
    if raw is None:
        return None
    normalized = raw.strip()
    if not normalized or len(normalized) > MAX_CLIENT_ID_LENGTH:
        return ""
    return normalized


__all__ = ["MAX_CLIENT_ID_LENGTH", "normalize_client_id"]
