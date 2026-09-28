"""Identity / Origin / CSRF / host / content-type / body-cap guards.

Ported from `pwa-obsidian-daily/tests/security/`, trimmed to this app's route
set (the only `/api/*` routes that exist are `/api/version`, `/api/session`, and
the 404 catch-all, so a "fully authorized mutation" is asserted by the fact
that it reaches the catch-all with `not_found`).

The load-bearing test is `test_each_guard_alone_*` plus
`test_the_earlier_guard_wins_*`: an ordering claim is circular if the only
evidence is that a request with two broken guards returns the first guard's
code, because that is also what a guard which never runs would return. So every
injected fault is first asserted to fail on its own, and only then paired.
"""
