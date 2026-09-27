# Ticket breakdown — `2026-09-27-pantry-recipes`

- **Spec:** `docs/spec/2026-09-27-pantry-recipes.md` (4,369 lines, 17 sections, §17 **closed** — zero open questions)
- **Tracker:** GitHub issues on `syyangv/pwa-pantry-recipes` (the tracker is the durable state for multi-session work)
- **This file:** the local map. The issues are the record an implementation worker is handed.
- **Produced by:** `/to-tickets` under the `/idea-to-ship` §3 → §4 contract. No feature was implemented; no PR was opened; nothing was pushed.

Each worker receives **only** its issue, the spec, `AGENTS.md`, `CLAUDE.md`/`GEMINI.md`, and
`CONTEXT.md` — never a design-session transcript. Every issue carries its own
`## Read these spec sections` list so a fresh context does not have to read all 4,369 lines.

---

## 1. Phase ↔ ticket map

§11's P1–P15 are the starting decomposition. Four phases were **split** and one was **split twice**;
no phases were merged, because no pair of §11 phases was small enough to belong together. Every
P1–P15 is covered; every ticket carries a phase id and a spec anchor.

| §11 phase | Issue | Split? | What landed in the split |
|---|---|---|---|
| P1 | **#1** | split 2 ways | #1 = config surface + `tests/js/shell_assets.test.mjs` · #5 = auth middleware + `/api/session` |
| P2 | **#2** | no | — |
| P3 | **#6, #7** | split 2 ways | #6 = atomic_write / frontmatter / sections / daily_paths · #7 = the ported `Pantry.md` parser |
| P4 | **#8, #9, #10** | split 3 ways | #8 = `PantryCatalog` + `catalog_revision` · #9 = `PantryStockIndex` + Stock Join + per-unit money + parity · #10 = the fixture regeneration script |
| P5 | **#3** | no | — |
| P6 | **#12** | no | — |
| P7 | **#13, #14** | split 2 ways | #13 = `IngredientMappingStore` incl. F2's per-slot catch · #14 = the golden matcher test |
| P8 | **#11, #15** | split 2 ways | #11 = `RecipeIndex` / `parse_recipe` · #15 = the recipes + pantry routers |
| P9 | **#16** | no | — |
| P10 | **#17, #18** | split 2 ways | #17 = the write path (CAS, F3 splice, dedupe, receipt, both routes) · #18 = the F4 missing-note contract + the 409 race |
| P11 | **#19, #20, #21, #22** | split 4 ways | #19 = pure `logic/` + its `node --test` gates · #20 = shell/router/api/tokens/styles/settings · #21 = home + recipe views + F19 · #22 = provenance view + F1 Stock Join table |
| P12 | **#4** | no | (see §4 — the DAG says unblocked) |
| P13 | **#23** | no | — |
| P14 | **#24, #25** | split 2 ways | #24 = browse→inspect flow · #25 = log-a-cook flow (one flow each, per §10.5's "two flows only") |
| P15 | **#26** | no | — |

### Why each split, and why nothing was merged

- **P1 → #1 + #5.** A fail-typed `Settings` extension and a full auth-middleware port with a
  table-driven guard-order test are unrelated units, and only the first carries the
  `SHELL_ASSETS` gate that must precede the frontend. Splitting also keeps the build-order
  prerequisite in a ticket that touches no auth, so it cannot be held up by it.
- **P3 → #6 + #7.** Four write/read primitives and F1's state parser share only a port origin.
  `AtomicNoteStore` is on the Cooking Log path (#17); the pantry parser is on the Pantry Stock
  path (#10). They need nothing from each other.
- **P4 → #8 + #9 + #10.** §11 already says splitting P4 "would put a chip class in the frontend
  with nothing behind it" — so it was split *at* the layer boundary, not across it.
  `PantryCatalog` is one read-only SQLite reader; `PantryStockIndex` is the F1 blast-radius unit
  plus the three-way `💵` parity obligation; the fixture-regeneration script is a deliberate,
  human-run act (F14) and is a reviewable commit on its own.
- **P7 → #13 + #14.** The store is a runtime unit; the golden test's expected fixture is generated
  by a **deliberate, reviewed run** (F14). Splitting keeps a fixture-regeneration commit
  reviewable on its own and takes the golden test off the critical path.
- **P8 → #11 + #15.** The reader is a pure vault-read unit with no HTTP. The routers carry D4's
  payload contract and **F1's fail-closed 503** — the largest blast radius in the app — and
  deserve their own review.
- **P10 → #17 + #18.** #18 is the one additive change to the shared error envelope, and its
  obligation is that *every pre-existing code keeps emitting exactly `{"requestId","code"}`*. That
  is a cross-cutting regression risk that does not belong inside a byte-splice ticket.
- **P11 → 4 tickets.** §11 sized P11 for one fresh context at ~15 modules. Four was the minimum
  that keeps each slice inside one context. #19 is pure and DOM-free; #20 is the shell;
  #21 is the two data views; #22 is the diagnostic surface. **F19's pull-to-refresh rides on #21**
  (the recipe-list view), exactly as the spec places it.
- **P14 → #24 + #25.** §10.5 specifies two flows and calls a third a maintenance liability. One
  flow per ticket keeps each inside a single context and lets #24 start as soon as the views land.

**No merges.** The smallest §11 phase after splitting is #3 (P5, two pure modules) and #4 (P12,
four PNGs plus a CI assertion). Neither has a natural neighbour: #3 gates the matcher and #2 gates
the DB, and merging either with an unrelated unit would make one ticket depend on work it does
not need — which is exactly the dependency noise `idea-to-ship` §4 asks to avoid.

---

## 2. Ticket ↔ decisions ↔ spec sections

Full detail is in each issue. This is the index.

| Issue | Phase | Decisions | Governing spec sections | Verification command (every ticket) |
|---|---|---|---|---|
| #1 | P1a | F9, F10, F11, F18 | §3.1, §3.3, §6, §6.1, §9.2 | `pytest` → `ruff` → `mypy app` → `npm test && npm run check` → `vendor.py --check .` |
| #2 | P2 | F2, F6, F9, F12, F13 | §3.3, §6.1, §7.1–§7.6 | same |
| #3 | P5 | D1, F15 | §3.2 D1, §3.3 F15, §5.2, §9.6, §9.7 | same |
| #4 | P12 | F18 | §9.1, §9.2, §10.2, §12 | same |
| #5 | P1b | F4, F18 | §3.3, §9.16, §9.17 | same |
| #6 | P3a | F3, F4 | §4.2, §9.14, §9.15, §10.1 | same |
| #7 | P3b | F1 | §3.3 F1, §4.2, §9.11.3 | same |
| #8 | P4a | D1, F14 | §3.2 D1, §3.3 F14, §5.2, §9.11.1, §9.11.2 | same |
| #9 | (F14) | F1, F14 | §3.3 F1, §9.11.3, §10.2, §10.3, §13.14–16 | same |
| #10 | P4b | F1, F11 | §3.3 F1, §4.2, §9.11.3, §10.2, §10.3 | same |
| #11 | P8a | D2, D3 | §3.2, §4.2, §9.5, §10.1 | same |
| #12 | P6 | D1, F15 | §3.2 D1, §3.3 F15, §5.2, §9.7–§9.9, §10.2 | same |
| #13 | P7a | F2, F6, D1, F1, F3 | §3.3 F2/F6, §5.3, §7.3, §9.8, §9.10, §9.10.1, §10.2 | same |
| #14 | P7b | D1, F14 | §3.2 D1, §9.6, §9.8, §10.1, §10.3 | same |
| #15 | P8b | D1, D4, F1, F2, F7, F8, F17, F20 | §3.2 D4, §9.13, §9.16, §9.19, §10.2, §13.12, §13.15 | same |
| #16 | P9 | D3, F12 | §3.2 D3, §3.3 F12, §5.3, §7.4, §9.12, §9.16 | same |
| #17 | P10a | D2, F3, F4, F13 | §3.2 D2, §3.3 F3/F4/F13, §7.5, §9.14, §10.2 | same |
| #18 | P10b | F4, F18 | §3.3 F4, §9.15, §9.16, §9.17, §9.19, §10.2 | same |
| #19 | P11a | D4, F16, F17 | §3.2 D4, §3.3 F16/F17, §4.1, §9.13.1–§9.13.3, §10.4 | same — **plus the `npm test` glob decision below** |
| #20 | P11b | F7, F8, F18 | §3.3, §4.2, §9.1, §9.2, §9.3, §9.4, §10.4 | same |
| #21 | P11c | D4, F16, F17, F19 | §3.2 D4, §3.3, §8, §9.2, §9.3, §9.4, §9.13, §13.5, §15 | same |
| #22 | P11d | D4, F1, F2, F7 | §3.2 D4, §3.3, §8, §9.13.4, §9.16, §13.2, §13.14 | same |
| #23 | P13 | F5, F18 | §3.3 F5, §7.6, §9.18, §10.4, §13.6, §13.7 | same |
| #24 | P14a | F7, F10, F17 | §3.3, §4.1, §6.1, §9.3, §9.4, §10.5 | same, **plus** `pip install ".[browser]" && playwright install chromium` (opt-in) |
| #25 | P14b | D2, F3, F4, F5, F10 | §3.2 D2, §3.3, §6.1, §9.14, §9.15, §9.18.1, §10.5 | same, **plus** the opt-in browser command |
| #26 | P15 | F18 | §4, §9.19, §12 | same |

**Every command in that table exists in the repo today** — `AGENTS.md` § *Commands*,
`README.md` § *Verification*, and `.github/workflows/ci.yml` all carry them verbatim. No ticket
invents a command the repo cannot run. The two opt-in browser invocations are the only additions,
and both are opt-in by design (F10).

### Test obligations and where they live

| Obligation | Issue | Verifying command |
|---|---|---|
| Golden matcher test over the 16 real recipes, committed `pantry_items_snapshot.json` (F14) | #9 (snapshot) + #14 (test) | `pytest` |
| `空心菜 → 83` asserted by name (R1) | #14 | `pytest` |
| Three-way `💵` parity: the `existingPantryValue` dataviewjs, `Helper/scripts/pantry_snapshot.py`, and the PWA | #10 (test + the refuse-to-emit gate) + #9 (the script) | `pytest`; the live comparison is a human-run act |
| Must-not-match flavour collisions incl. `蒜`→id 110, plus a **negative control per guard** | #12 | `pytest` |
| F2 duplicate-slot conflict: 6 properties + the `OR ROLLBACK`/`OR FAIL` static check | #13 | `pytest` |
| **Fail-closed on an unreadable `Pantry.md` (F1 / R8)** — all three modes, on both routes, no degraded list | #10 (the raise) + #15 (the 503 assertion) | `pytest` |
| F16/F17 exact headline strings, verbatim equality | #19 | `npm test` |
| `SHELL_ASSETS` completeness — a `js/`/`css/` file MISSING from the list | **#1**, before #20/#21/#22 add ~15 modules | `npm test` |
| The four PWA icons exist and are asserted in the CI wheel check | #4 | `pytest` + the wheel check |
| No cook-log intent is ever enqueued (F5) | #23 | `npm test` |
| `recipeTracker` counts pages, not links — the wikilink, not the receipts table, is what keeps `cooking_count` correct | #17, #18, #25 | `pytest`; opt-in browser |

---

## 3. The DAG

Native GitHub issue dependencies are set on every edge, and every issue carries a text
`## Blocked by` line as the human-readable fallback. Verified: no dangling edges, acyclic.

**The native edges are the only trustworthy gate.** Read them with `blockedBy` on the
GraphQL `Issue` type — *not* `issue_dependencies_summary`, which does not exist on the type
and errors out:

```bash
gh api graphql -f query='{ repository(owner:"syyangv", name:"pwa-pantry-recipes") {
  issues(first:100) { nodes { number blockedBy(first:20) { nodes { number } } } } } }'
```

**Known drift: the text `## Blocked by` lines are stale in their numbering.** They were
written against a pre-split numbering, before §1's P1/P3/P4/P7/P8/P10/P11/P14 splits
renumbered the set. In **16 of 26 issues** the numbers in the text line do not match the
native edges, though in most cases the line's *prose* still names the right unit — e.g.
#10's text line reads `#3 (the PWA-owned DB layer)`, where the DB layer is #2 and #3 is the
parser. The two agree on only 10 issues: **#1–#8, #11, and #19.**

**Do not schedule from the text line.** This is a documentation defect in the issues, not
a graph defect, and it is recorded rather than fixed here: fixing it would mean editing 16
issue bodies, which is outside this file's remit. Three spots deserve a maintainer's eye
because there the native edge may be the one that is wrong, not the numbering:

- **#20** — the text asserts an edge on **#1** (the configuration surface) that the native
  `blockedBy` does not carry (native is `[#19]` only). Harmless in practice: #1 is a root.
- **#16** — the text names **#11** (`RecipeIndex`) but native blocks on **#15** (the routers).
- **#22** — the text names **#20** (the shell) but native blocks on **#21** (the views).

```
                 #1  P1a  config + SHELL_ASSETS gate
                  │
      ┌───────────┼───────────┬────────────┐
      ▼           ▼           ▼            ▼
     #5          #6          #7           #8  P4a PantryCatalog
   P1b auth    P3a vault   P3b pantry     │
    │          │          parser         ▼
    │          │ (P10a)   │            #9  frozen fixtures
    │          │          ▼             │
    │          │       #10 P4b ─────────┘
    │          │   stock+join+💵
    │          │          │
    │          │          ├──▶ #12 P6  tier ladder
    │          │          │        │
    │          │          ▼        ▼
    │          │      #13 P7a  mapping store (F2 catch)
    │          │          │        │
    │          │          └──▶ #14 P7b  golden matcher test
    │          │                   │
    │          │   #11 P8a RecipeIndex
    │          │          │        │
    │          │          ▼        ▼
    │          │      #15 P8b  recipes + pantry routers
    │          │          │
    │          │          ├──▶ #16 P9  Meal Shortlists
    │          │          │
    │          ▼          ├──▶ #21 P11c  home + recipe views (+F19)
    │       #17 P10a      │            │
    │       cook-log write │            ├──▶ #22 P11d  provenance view
    │          │          │            │
    │          ▼          │            ├──▶ #24 P14a  browse flow
    │       #18 P10b      │            │
    │       F4 404/409    │            ├──▶ #25 P14b  cook-log flow
    │                       │            │
    │      #19 P11a pure logic          │
    │          │                       │
    │          ▼                       │
    │       #20 P11b  frontend shell   │
    │          │                       │
    │          └──────────▶ #21 ◀─────┘
    │
    └──────▶ #18 (needs #5 for a second error-code family)
             │
#2  P2  DB layer ◀── #13, #10, #16, #17
#3  P5  parser + normalize ◀── #10, #12, #13
#4  P12 icons  (no blockers)
#23 P13 outbox ◀── #15, #16, #17
#26 P15 deploy ◀── #22, #4, #23, #25
```

### Critical path

Verified against the live native edges, not against the sketch above.

**#1 (P1a) → #8 (P4a) → #9 (frozen fixtures) → #10 (P4b) → #12 (P6) → #13 (P7a) → #15 (P8b) → #21 (P11c) → #22 (P11d) → #26 (P15)**

10 nodes, 9 links. **Max depth = 10, reached only at #26.**

The critical path is *not* the §11 phase chain, and it does not run through #24. Three
facts that the earlier draft of this section got wrong, recorded so the correction is
auditable:

1. **#24 does not block #26.** #26's native `blockedBy` is `[#4, #22, #23, #25]`. The
   previous text here claimed a `#21 → #24 → #26` tail; that edge does not exist, and the
   DAG sketch's own footer line (`#26 P15 deploy ◀── #22, #4, #23, #25`) already
   contradicted it. The claim is withdrawn.
2. **The path starts at #1, not #3.** The read-side spine **#1 → #8 → #9 → #10** is what
   sets the depth; #10's depth is pinned by #9, not by #3.
3. **Link arithmetic.** The old text called an 8-node chain "8 links" (it is 7) and
   reported max depth 9 (it is 10, counting #1 as depth 1).

**#26 has three co-critical feeders, not one.** `#22`, `#23`, and `#25` all sit at depth
9, each on its own 9-node chain, and #26 needs all three:

| Feeder | Its own longest chain |
|---|---|
| **#22** | #1 → #8 → #9 → #10 → #12 → #13 → #15 → #21 → #22 |
| **#23** | #1 → #8 → #9 → #10 → #12 → #13 → #15 → #16 → #23 |
| **#25** | #1 → #8 → #9 → #10 → #12 → #13 → #15 → #21 → #25 |

So #23 and #25 are **not** "off the critical path" — see the correction to §6 below. #24
reaches depth 9 too, but its chain dead-ends at #24, so it is genuinely off the ship path.

### Critical path, read at §11 phase granularity

§11's stated critical path was **P5 → P6 → P7 → P8 → P11 → P14**, which maps onto
#3 → #12 → #13 → #15 → #21 → #24 — a valid 6-node, 5-link chain in the live DAG. Read at
phase granularity this breakdown **inserts P4b (#10) between P5 and P6** (see §4.1 below),
which makes it 6 links.

At *ticket* granularity the live graph is **wider** than that, not narrower: #12 is a
**join** on #3, #8, *and* #10, so those three do not serialise into a single inserted
link. The phase reading is a useful summary; the 10-node chain above is the one to
schedule against.

---

## 4. Where §11 and the dependency DAG disagree

Per the instruction that **the DAG wins**, three edges and one blocker list were corrected.
Each is called out in the affected issue's own `## Blocked by` section.

### 4.1 P4b depends on P5 — a new edge, and it lengthens the critical path

§11 lists P4 as `deps: P2, P3` and P5 as `deps: —`. But F1 states the Stock Join "is a
normalized-name index built on `normalize_ingredient()` — the *same* normalization the matcher
uses, **which is the only reason the two sides can agree at all**" (§3.3 F1, repeated in §9.11.3).
`tests/vault/test_pantry_stock.py` (a #7 obligation) asserts that
`禾苑 蟹粉鱼肉狮子头 冷冻 280 克` yields a product core that **tier 4 finds at id 108** — true only
once #3's normalization exists.

Without the edge, #10 and #3 are dispatchable in parallel and one of them fails on an import that
does not yet exist. **This is the single most consequential correction in this breakdown**, and it
is why #10 is not a same-wave sibling of #12.

### 4.2 P4a does *not* depend on P2 or P3

§11 gives P4 `deps: P2, P3`. That is true of the P4b half. `PantryCatalog` reads one SQLite file
through a read-only URI and needs neither the PWA-owned DB layer nor any vault primitive, so #8 is
blocked only by #1.

### 4.3 P10a does *not* depend on P7

§11 gives P10 `deps: P3, P7` and glosses P7 as "the mapping store, for the receipt insert". The
receipt insert targets `cook_log_receipts`, which is #2's table. Nothing in the Cooking Log write
path calls `resolve_ingredient` or `set_manual`. Keeping the #13 edge would put the whole matcher
stack in front of the write path for no data dependency, so #17 is blocked by #2 and #6 only.

### 4.4 P12 (the icons) is unblocked

§11 gives P12 `deps: P11`. The four icons are referenced by `manifest.webmanifest` and by
`index.html`'s `apple-touch-icon` link **today**, `icons/` holds only a README, and no icon work
reads a view, a component, or a domain route. #4 is a **root** ticket and can start immediately.
It keeps the §11 phase number; only the blocker list changed.

---

## 5. The two easy-to-miss prerequisites

Both are the spec's own call-outs, and both are confirmed present in the ticket set.

1. **`tests/js/shell_assets.test.mjs` — lands in #1, before any new frontend module.**
   `tests/js/scaffold.test.mjs` today fails on a `SHELL_ASSETS` entry with **no file behind it**;
   it cannot detect a file with **no entry**, which is the damaging direction — a module that is
   imported, served, and precached by nothing, diagnosable only by a user on a subway. §11 already
   assigns it to P1 and R7 rates it "high without the fix"; it is in #1, and #20/#21/#22 (~15
   modules) all depend on #1 having landed. **Not duplicated** — §11 covers it, #1 implements it.

2. **The four missing PWA icons — #4.** `manifest.webmanifest` references three
   (`icon-192`, `icon-512`, `icon-monochrome-512`); `index.html`'s `apple-touch-icon` link
   references the fourth (`icon-180-apple`); `app/static/icons/` holds only a README. The CI
   wheel-content check does not assert them, which is why four empty references survived.
   §11 already covers this in P12; #4 implements it and **adds the four files to the CI wheel
   check** so a `.png` that fails to land in the wheel becomes a test failure. **Not duplicated.**

---

## 6. Proposed execution order, and parallel safety

`idea-to-ship` §4: *"Run tickets sequentially by default. Parallelize only independent tickets
when the harness provides isolated worktrees or equivalent filesystem isolation; **shared-workspace
subagents are not isolation**."* Every ticket below touches `app/`, `pyproject.toml`,
`app/static/sw.js`, or `package.json`, so **two concurrent tickets in one working tree will
conflict** — the `SHELL_ASSETS` + `CACHE_VERSION` coupling alone makes that certain.

**Therefore: run sequentially by default.** The waves below are the *ready order*, and each is a
parallel batch **only** under isolated worktrees (separate `git worktree` per ticket, merged back
in wave order). In a shared workspace, read the table left to right as a strict sequence.

| Wave | Tickets (all blockers closed) | Safe to parallelize? |
|---|---|---|
| 0 | **#1, #2, #3, #4, #19** | **Yes, with 5 isolated worktrees.** All five are roots. #4 touches only `app/static/icons/` + `ci.yml`; #3 only `app/recipes/`; #2 only `app/db/` + `pyproject.toml`. **#1 and #2 both edit `pyproject.toml`** and #1 also owns `app/config.py` + `tests/js/shell_assets.test.mjs`, so in a shared workspace do **#1 → #2**. #19 was parked here pending the glob decision; **that decision has landed (§7.1), so #19 joins the wave as a normal root.** |
| 1 | **#5, #6, #7, #8, #11** | Yes, with isolated worktrees — five disjoint areas (`app/auth.py` + `app/api/session.py`; `app/vault/`; `app/vault/pantry.py`; `app/pantry/catalog.py`; `app/recipes/reader.py`). **#6 and #7 both create files in `app/vault/`** — sequential in a shared tree. |
| 2 | **#9** | Single. Needs #8. |
| 3 | **#10** | Single. Needs #2, #3, #7, #9. This is the critical-path gate; start it as early as the frontier allows. |
| 4 | **#12** | Single. Needs #8, #3, #10. |
| 5 | **#13** | Single. Needs #2, #8, #3, #12. |
| 6 | **#14** | Single. Needs #9, #12, #13. **#11 does not belong in this wave** — it has no blocker but #1, is already a wave-1 ticket, and re-listing it here would have a worker run it twice. The old `#11 ∥ #14` cell was half-right: the two *are* independent (different modules, different fixtures), so the parallel claim is kept, but the wave assignment was not. |
| 7 | **#15** | Single. Critical path. Needs #10, #13, #11. |
| 8 | **#16, #17** | #16 (shortlists, `app/shortlists/` + `app/api/shortlists.py`) and #17 (cook log, `app/cooklog/` + `app/api/cooklog.py`) are **disjoint**. Yes with isolated worktrees. |
| 9 | **#20, #18** | #20 (the frontend shell) and #18 (the F4 error contract) are disjoint. Yes with isolated worktrees. #18 needs #5 and #17. |
| 10 | **#21, #23** | #21 (home + recipe views) and #23 (outbox) touch different frontend modules but **both bump `CACHE_VERSION` and edit `app/static/sw.js`** — that is a real conflict even with worktrees unless one rebases. Prefer sequential: #21 is on the critical path. |
| 11 | **#22** | Single. Needs #15, #21. |
| 12 | **#24** | Single. Needs #21. |
| 13 | **#25** | Single. Needs #17, #18, #21. |
| 14 | **#26** | Single. Needs #22, #4, #23, #25. Stops at the authorization gate. |

**Strictly sequential, no matter the isolation:** #1 → #2 (both `pyproject.toml`),
#6 → #7 (both `app/vault/`), and any pair of frontend tickets that each bump `CACHE_VERSION` and
add `SHELL_ASSETS` entries.

**Genuinely off the critical path (corrected):** #14 (the golden test — depth 7, and #15
does not wait on it), and #24 (the browse flow — depth 9, but #26 does not wait on *it*;
its chain dead-ends at #24).

**Not off the critical path, despite being listed here previously.** The earlier draft of
this list called #9, #23, and #25 "safe to defer". Per §3:

- **#9 is third on the critical path** (`#1 → #8 → #9 → #10 → …`). It is a single ticket with
  no parallel partner, so it has nothing to contend with — but it gates the deepest branch in
  the graph. Schedule it as early as the frontier allows; do not read "no partner" as "slack".
- **#23 and #25 are co-critical for #26.** Both sit at depth 9 and #26 needs both. They
  are as critical as #22, just via a different tail. Do not treat them as slack.

**Cheap but not deferrable:** #4 (the icons) is a root with no predecessors, so it is
instant work — but #26 does list it as a blocker, so it must land before the deploy.

---

## 7. Spec defects found while breaking this down

Two. **Neither was silently fixed in the spec.** One is now resolved and folded into its
ticket; one is assigned a durable engineering guard and reported.

### 7.1 RESOLVED — `npm test` cannot see the three new `logic/` test files. Issue **#19** (now `ready-for-agent`).

`package.json` today is `node --test tests/js/*.test.mjs`. That glob matches **one** directory
level, so `tests/js/logic/sort.test.mjs`, `chip-class.test.mjs`, and `format.test.mjs` — the three
files §10.4 names — are **silently never collected**. `npm test` would report 0 failures while
running none of the tests that carry F16's and F17's exact-string obligations, i.e. the four frozen
headline strings, including the strict-mode form with its three-space addendum.

The spec never mentions changing the glob, and §10.4 restates the one-level form as if it were
correct. A silently-unrun test file is worse than no test: it reads as coverage. Two resolutions
are defensible and the spec settles neither:

1. widen the glob to `tests/js/**/*.test.mjs`, or
2. flatten to `tests/js/sort.test.mjs` etc.

**RESOLVED — #19 is no longer parked.** The maintainer chose **option 1, widen the glob to
`tests/js/**/*.test.mjs`.** The rationale, the version-dependency caveat, and the
directory-argument fallback are recorded in a `## Resolved decision` block on #19 itself, so
the worker reads the decision where the work is. #19 now carries `ready-for-agent` and is
agent-grabbable like the rest of the set; the `needs-triage` label it held is removed (the
label stays defined — it is canonical, just currently unused).

The implementer's obligation is unchanged in substance and sharpened: **assert that all
three `logic/` test files were collected**, not merely that `npm test` exited 0. "0 failures"
was the exact signal that lied.

### 7.2 ASSIGNED — `pyproject.toml` will not package the eight new subpackages. Issue **#2** (`ready-for-agent`).

`[tool.setuptools] packages` is the single-element list `["app"]`, and §4.2 creates
`app/api`, `app/db`, `app/vault`, `app/pantry`, `app/recipes`, `app/mapping`, `app/shortlists`,
`app/cooklog`. Nothing in the spec updates that list. As written, a built wheel ships `app/` and
**silently omits every domain subpackage** — an installed app with no feature code, and a **green
CI**, because the wheel-content check only asserts `app/__init__.py`, `app/config.py`,
`app/main.py`, `app/pwa_version.py` and static files.

This is an engineering consequence rather than a product decision, so rather than park #2 the
ticket carries the durable resolution: extend `packages` in each phase that adds a subpackage,
**and** add a test to `tests/scaffold/test_app.py` asserting every subpackage directory under
`app/` is named in `pyproject.toml`, so the list cannot silently fall behind again. Flagged in
#2's acceptance criteria and in this file so it is on the record.

### 7.3 Also noted, not a defect: a third latent coverage gap

`npm run check` is `node --check app/static/js/main.js && node --check app/static/sw.js`, and
`node --check` does **not** follow import specifiers — so a syntax error in any new submodule is
invisible to it. The spec's stated intent ("`node --check` on the module-graph entry and the
worker") is clear, so #20 carries the obligation to extend the check to cover the new module
graph. Recorded here because it is the same class of gap as §7.1 and was found the same way.

---

## 8. Constraints honoured

- **No feature was implemented.** No app code was written. No file in `app/`, `tests/`, or
  `scripts/` was touched.
- **No PR was opened and nothing was pushed** during the breakdown itself. The follow-up
  housekeeping commits (§11) commit this file and the `AGENTS.md` triage section, and push to
  `main`. No PR, no force-push.
- **No decision was re-opened or re-litigated.** All 24 (D1–D4, F1–F20) are referenced by id only;
  the rationale stays in §3 and is not restated in the tickets. The three hard constraints in the
  spec are carried verbatim as prohibitions: `aiosqlite` is the **only** new runtime dependency
  (F9) and `playwright` the **only** extra, in `[browser]` and **not** in `[test]` (F10).
- **No new dependencies** beyond `aiosqlite` and `playwright`.
- **Canonical labels only**, from the house triage vocabulary in
  `pwa-template/docs/agents/triage-labels.md`. `ready-for-agent` and `needs-triage` were created
  during publication because they did not exist yet in this repo; `needs-info` and
  `ready-for-human` were added later, in the follow-up housekeeping pass, so that the live label
  set matches the documented five. `wontfix` already existed as a GitHub default and was left
  untouched. See §9 and §11.
- **No removed field, removed phase, or rejected behaviour is reintroduced.** Grepped every ticket
  body for the Obsidian CLI, `QuickAdd`, `OBSIDIAN_CLI_EXECUTABLE`, `TV_SYNC_COMMAND_ID`, the
  idempotency ledger, `CreationIdempotencyStore`, `create_new`, the `in_stock` column, the `snack`
  slot, `nutrition-intake`'s bare `UNIQUE`, and F2's old aborting-transaction behaviour. Every hit
  is either an explicit non-goal, a "must not reintroduce" statement, or — for `OR ROLLBACK` /
  `OR FAIL` in #13 — a static-contract assertion that they are **absent**.
- **Every pattern citation resolves.** Checked every `§N` reference into
  `pwa-template/docs/pwa-template.md` (Parts 1–6; §1a, §1c, §2a–§2i, §3a–§3e, §4a–§4f, §5a–§5d,
  §6a–§6d) and every Pattern letter the spec names (A, B, C, D, E, F, G, H, I, J, L, M). All
  resolve; there is no Pattern K in the reference and the spec never cites one.
- **Every §11 phase is covered** and **no ticket lacks a phase or a spec anchor.** Verified
  against the table in §1 above.
- **Every dependency edge points at an existing issue** and **the DAG is acyclic** — both verified
  programmatically against the live tracker state, not against this file.

## 9. Repo-convention gaps found (for `AGENTS.md`)

- **`AGENTS.md` had no `### Triage labels` section.** The new repo's `AGENTS.md` was modeled on
  `pwa-template/AGENTS.md` and carries its `### Issue tracker` section, but **omitted** the
  `### Triage labels` section that maps the five canonical triage roles to label strings. The
  labels themselves did not exist in the repo: `ready-for-agent` and `needs-triage` were created
  during publication, and `needs-info` / `ready-for-human` were still missing, while `wontfix`
  already existed as a GitHub default. **Both gaps are now closed** — the section is added and
  all five labels exist. See §11.
- **`AGENTS.md`'s `### Issue tracker` section** points at `syyangv/pwa-pantry-recipes` via the
  `gh` CLI and is correct. The template's version also cross-references
  `docs/agents/issue-tracker.md`; this repo has no `docs/agents/` directory. The tracker record
  for these tickets is the issue set itself plus this file.

## 10. Tracker record

26 issues, `#1`–`#26`, on `syyangv/pwa-pantry-recipes`, created in dependency order (blockers
first) so the numbering roughly follows the ready order. Every issue carries its phase id, its
locked decisions, its `## Read these spec sections` list, its `## Blocked by` (text **and** native
GitHub issue dependencies), concrete acceptance criteria naming files / functions / routes /
tables / columns / test files, its `## Explicit non-goals`, and its verification command with
what a pass looks like.

## 11. Housekeeping pass — what changed after publication

Three bookkeeping changes, **no feature work**. Recorded here so this file stays the durable
local map rather than a snapshot that quietly goes stale.

### 11.1 This file was committed, and three drifted claims were corrected

`docs/spec/tickets.md` existed only as an uncommitted local file. Before committing it, it was
checked against the live tracker on all five axes named for the pass — issue numbers, phase
mapping, blocked-by edges, critical path, and parallel-safety annotations. Phase mapping was
already exact (all 26 issues' `**Phase:**` lines match §1 and §2). The other three were not;
see §3 (critical path and the stale text `## Blocked by` lines) and §6 (waves 0 and 6).

### 11.2 #19's glob decision is recorded on the issue, and #19 is un-parked

The `npm test` one-level-glob defect (§7.1) was decided by the maintainer: **widen the glob
to `tests/js/**/*.test.mjs`.** The decision, the reason, the Node-version caveat, and the
`node --test tests/js/` fallback are written into a `## Resolved decision` block appended to
#19's body — appended, so the original ticket text is intact. `needs-triage` was removed from
#19 and `ready-for-agent` added.

**The fix itself is deliberately NOT in this repo.** It is #19's implementation work. No file
in `app/`, `tests/`, `scripts/`, or `package.json` was touched by this pass.

`needs-triage` now has zero issues and **stays defined** — it is one of the five canonical
roles, not a per-issue label.

### 11.3 The `### Triage labels` section is in `AGENTS.md`, and all five labels exist

`AGENTS.md` gained the `### Triage labels` section it was missing (§9), naming all five
canonical roles and pointing at `pwa-template/docs/agents/triage-labels.md` as the source of
truth. `needs-info` and `ready-for-human` were created so the live label set matches the
documented one. `ready-for-agent`, `needs-triage`, and `wontfix` already existed and were not
recreated or modified.

Note for the next person: `wontfix` is a GitHub default label, so its description reads
"This will not be worked on" where the triage doc's meaning column says "Will not be
actioned". Same role, different wording. Left as-is — the doc specifies no colors or
descriptions for label creation, so the two labels created here take their descriptions from
the doc's meaning column and their colors from this repo's existing semantic scheme.
