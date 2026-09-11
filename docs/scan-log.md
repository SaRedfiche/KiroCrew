# Scan log — feature/project-coordination-phase1

Auditable record of pre-merge gates per the `auto/ship-it` SOP. The user merges;
these entries record the GO/NO-GO decisions and accepted risks against each head SHA.

---

## Phase-1 Signal 2 (same-worktree) + notify + panel — `acb89c387` (branch `feature/project-coordination-collision-worktree-notify`)

Completes the two-signal collision model: the same-worktree strong-warn signal
(`worktree_index.py`), the notify path with noise discipline (`collision_notify.py`),
the per-turn evaluate-and-notify wiring (`chat_runner.py` flush), and the read-only
`GET /api/projects/{id}/panel` (`project_panel.py`). Diff base is the Signal-1 tip `f08dfbc4d`.

| Gate | Verdict | Notes |
|---|---|---|
| Build + tests | GO | 43 collision/panel tests + 776 dashboard neighbors green |
| ASH (`03f12423`, 10 scanners) | GO for diff | 0 actionable on changed files (bandit note-level subprocess advisories only) |
| Adversarial crew (5 axes, on `2ec25efe9`) | GO after fixes | Security Blocker (panel authz gate) + High (id oracle); Correctness High (per-tree dedupe); AI (dead `forget`/`drop_session` removed, `_all_fork_paired` deduped); Tests (stranger-breaks-fork + both-signals-one-note + panel same-worktree/cross-project). All applied + re-verified. |
| Multimodel panel (`acb89c387`) | GO | GPT PASS, Opus PASS, Design PASS, First-Principles CONCERNS (per-flush-vs-eager scoping = accepted taste) |

**Paired record:** `acb89c387 | crew: GO (all 5 axes; Security/Correctness/AI/Tests findings applied) | panel: GO (GPT/Opus/Design PASS, First-Principles CONCERNS=taste)`
(Crew ran on `2ec25efe9`; findings fixed in the commits since. Panel GO is on the
final `acb89c387`.)

**Panel loop (eval corpus — seven GPT rounds, honestly split):** rounds 1–5 were
REAL delivery-surface defects, each fixed at root: panel served any project's
name/session-titles with no authz → app-ownership gate + uniform 404 (no id
oracle); the per-turn dedupe bool wrongly suppressed a cross-worktree same-file
collision → per-tree dedupe (`fsessions <= members`); `NotifyOnce` grew unbounded
→ liveness prune (drop a signature once ANY participant dies); Signal 2 evaluated
only on write turns → evaluate on every flush; the panel same-worktree flag leaked
another project's session ids → own-project filter; a retagged session leaked into
its old project's same-file flag → scope to currently-tagged sessions; same
repo_rel_path in two repos deduped to one note → fold `repo_id` into the signature.
Rounds 6–7 were DOC-HONESTY: docstrings claimed a "standing / eager re-derived on
every slot.project commit" view and a "last activity" field the per-flush code does
not provide; fixed by aligning all docstrings/comments to the per-flush,
eventually-consistent reality (the whole class swept at once, which broke the loop).
Opus PASSed every round; the code was correct by round 5.

**Accepted follow-ups (do not block):**
1. Eager `worktree_root` re-derive on every confirmed `slot.project` commit (the
   8 mutation sites), replacing the per-flush approximation — closes the
   "two just-opened sessions don't flag until each flushes" window. Documented
   in the code as the Phase-1 approximation.
2. Notify `bus.push` is same-failure-domain (Design CONCERNS): wrapped in
   try/except with the panel's collision flags as the independent advisory
   backstop, so a lost push is not silent data loss — but no durable-write
   fallback. Revisit if the notify path becomes load-bearing.
3. Same-file churn suppression: a 2-session collision that grows to 3 notified at
   the 2-set then goes panel-only (intended; the panel still shows it).

---
---

## Phase-1 Signal 1 — same-file collision detection — `1698578cf` (branch `feature/project-coordination-collision-samefile`)

Same-file merge-risk signal: ≥2 distinct LIVE sessions tagged into one project
editing the same repo-relative file within 30 min. New
`src/kiro_crew/dashboard/collision_index.py` (in-memory index) +
`collision_derive.py` (off-loop git derivation), wired into the turn hot path
(`chat_runner.py`), `DashboardState.collisions` (`state.py`), turn-start reset
(`chat_handlers.py`). Diff base is the tagging-API tip `222ce4a86`.

| Gate | Verdict | Notes |
|---|---|---|
| Build + tests | GO | 35 collision tests + 776 dashboard neighbors green |
| ASH (`f982f266`, dashboard dir, 10 scanners) | GO for diff | 0 actionable on changed files; only bandit `note`-level subprocess advisories (B404/B603/B607 on intentional git calls, B112 on the deliberate error-swallow) |
| Adversarial crew (5 axes, on `43b816f91`) | GO after fixes | No Blocker. Security-Medium (per-path git-spawn storm) → memoize repo-lookup once per cwd + per-flush path cap. AI-Medium → drop write-only `worktree_root`. Tests-High → cap/sensitive-skip/git-failure/fork tests. All applied + re-verified. |
| Multimodel panel (`1698578cf`) | GO | GPT PASS, Opus PASS, First-Principles + Design CONCERNS (unwired read-surface / ships one commit ahead of the Signal-2 consumer — the deliberate two-commit split) |

**Paired record:** `1698578cf | crew: GO (all 5 axes; Security/AI/Tests findings applied on the commits after 43b816f91, re-verified) | panel: GO (GPT/Opus PASS, First-Principles/Design CONCERNS = scoping/taste)`
(Crew ran on `43b816f91`; every finding was fixed in the commits since. Panel GO
is on the final `1698578cf`, which the crew fixes are folded into.)

**Panel loop (eval corpus — panel-caught data-loss/leak class):** the panel
BLOCKed three successive SHAs, each a distinct real defect in my own hot-path
code, none a false positive: `aa21f4163` — a newest-N-rows cap could evict a
distinct session's row (false-negative collision) → fixed at root by storing
ONE row per session per key (bounded by distinct-session-count, cap removed);
same SHA — a `state.py` comment claimed a wired panel/notify consumer that does
not exist → corrected; `79c4d2541` — `record_edit` pruned only the touched key,
leaking stale keys for the process lifetime → fixed by calling `index.prune()`
(all-key sweep) every turn-exit, off-loop. `1698578cf` cleared it.

**Accepted follow-ups (do not block; land with Signal 2 / the panel consumer):**
1. The index read surface (`contested_files`, `prune` caller for reads,
   `distinct_session_count`) ships one commit ahead of its panel/notify consumer
   — a deliberate two-commit split (Signal 1 = detect+index; Signal 2 + delivery
   = next commit). First-Principles/Design flag this as scoping; the consumer
   lands next.
2. `is_fork_pair` symmetry is a documented contract; the real predicate (built
   from `forked_from`) lands with the panel/notify consumer that supplies it.

---

## Phase-1 Step 4 — project-group tagging API — `0c1a3d3d6` (branch `feature/project-coordination-tagging-api`)

New `POST /api/chat/slots/{slot}/project-group` (`api_chat_slot_project_group` in
`src/kiro_crew/dashboard/chat_folders.py`) — the validated create-or-attach-or-untag
interface and first caller of `ProjectStore`, wired as a shared `DashboardState.projects`
instance. Diff base is the store branch tip `b04ccbad4` (store + field plumb gated GO
separately below).

| Gate | Verdict | Notes |
|---|---|---|
| Build + tests | GO | 16 endpoint tests green (`./.venv/bin/pytest test/test_chat_slot_project_group.py`); neighbors + body-guard ratchet green |
| ASH (`888404a5`, dashboard dir, 10 scanners) | GO for diff | 279 dir-wide findings, **0 on any changed file** (`chat_folders.py`/`state.py`/`routes/sessions.py`); the 4 criticals are pre-existing `token_*.py` |
| Adversarial crew (5 axes, on `fb7eca370`) | GO after fix | Correctness HIGH (create-then-fail orphan record) — FIXED (`7d89a3c67`: create inside the lock + compensating delete) and re-verified with tests. Security/Docs/AI/Tests GO. |
| Multimodel panel (`479804636`) | GO | GPT PASS, Opus PASS, First-Principles PASS, Design CONCERNS (watch, non-blocking) |

**Paired record:** `0c1a3d3d6 | crew: GO (all 5 axes; Correctness HIGH orphan-record fixed on 7d89a3c67, re-verified) | panel: GO on 479804636 (GPT/Opus/First-Principles PASS, Design CONCERNS=watch)`
(Crew ran on `fb7eca370`; its one HIGH was fixed in the commits since. Panel GO is on
`479804636`; `0c1a3d3d6` adds only a tests-only mock-hardening delta — handler bytes
identical — so the panel GO stands without a re-gate, per the ship-it tests-only exemption.)

**Panel loop (kept for the eval corpus — panel-caught data-loss class):** the panel
BLOCKed three successive SHAs, each a distinct instance of one class — a
"present-but-not-a-real-target silently untags an existing project" data-loss path:
`fb7eca370`→ N/A (crew round); `7d89a3c67` GPT BLOCK (blank/whitespace `name` → silent
untag) + Design BLOCK (duplicate shadowed test); `52dbc9458` GPT BLOCK (present JSON
`null` name/id → silent untag). Root fix: untag is the key-ABSENT signal ONLY; a present
key must carry a non-empty string, else 400. `479804636` cleared it.

**Accepted follow-ups (do not block; land with later Phase-1/2 work):**
1. Per-caller rate-limit on the create branch (crew Security Nit) — cap-bounded at
   `_MAX_PROJECTS`=500 → 503 today; add `allow_create(...)` for parity with folder-create.
2. Dangling-tag reconciliation (Design watch): the attach `get_project` pre-check is
   advisory not transactional; a project deleted between check and commit leaves a dangling
   tag. Tolerated by the store's delete-always/dangling-id-is-a-reader-concern contract, but
   there is no GC/scrub. Add a lazy-clear or periodic reconcile, and assert the reader
   tolerance as a store class-invariant, when the reader/notify path lands.
3. 48-bit id mint × idempotent-by-id create (crew Correctness Nit): a `uuid4().hex[:12]`
   collision would silently attach to a pre-existing different project; negligible at the
   500 cap, retry-on-collision if ever tightened.

---

## SHA `c255a4a1a` (field plumb + round-trip tests) — GATE: GO

**Change:** Phase-1 `project_group_id` slot field plumb (`85b2ebb26`) + round-trip
tests (`c255a4a1a`). New persisted grouping tag on `_ChatSlot`, distinct from the
`project` cwd path; plumbed through `__slots__`/init, `SLOT_OWNED_META_KEYS`, both
metadata writers, all four rehydrate sites, fork copy, and workspace-switch
clear+rollback. 7 tests, green under `.venv` pytest.

| Gate | Verdict | Notes |
|---|---|---|
| Build / tests | GO | 6 files compile; `test_project_group_id.py` 7 passed under `.venv` pytest |
| Multimodel panel (`85b2ebb26`, code unchanged since) | GO | GPT PASS · Opus PASS · Design PASS · First-Principles CONCERNS (taste/subtraction lane — terminating condition, not a harm path) |
| Adversarial crew — Correctness | GO | Round-trip verified all 4 read sites (identical one-line mirrors); `__slots__`/owned-keys/rollback/fork all correct |
| Adversarial crew — Security/Docs-honesty/AI-necessity | GO | Zero downstream consumers; `str()` coercion more defensive than sibling `project`; every commit-msg claim verified; no LLM lines |
| Adversarial crew — Tests (initial `85b2ebb26`) | NO-GO → fixed | HIGH: no round-trip test |
| Adversarial crew — Tests (re-review `c255a4a1a`) | GO | HIGH closed with real assertions against production code |

**Paired record:** `c255a4a1a | crew: GO (all axes; Tests HIGH fixed by round-trip tests) | panel: GO (GPT/Opus/Design PASS, First-Principles CONCERNS=taste)`

**Security / ASH / Holmes:** additive Python field plumb, no IaC, no new deps
(lock file unchanged). ASH/Holmes not separately run this round — the change adds
no attack surface (a persisted string with zero consumers) and the crew Security
axis covered injection/trust (hostile metadata line coerced via `str()`, field
inert). Recorded honestly: ASH/Holmes NOT RUN this round; crew Security = GO.

**Adjudicated (not fixed, by design):**
- Correctness "High" `slack/handler.py:930` — reads `project` into a module-level
  `_thread_projects` cache, not onto a `_ChatSlot`; no slot to tag, so it is out
  of the slot-field-plumb scope by construction. Deliberate non-site.
- Panel First-Principles CONCERNS — subtraction/taste lane; the documented
  terminating condition for the fix-all loop.

**Accepted follow-ups (do not block this checkpoint):**
1. Fork test is tautological (asserts the copy line directly) — strengthen to
   drive `api_chat_slot_fork` so a dropped `chat_fork.py` copy line fails.
2. Workspace-switch rollback uses a value guard (`if not slot.project_group_id`)
   where `project`/`workspace` use `_CommitToken` identity — only matters once a
   concurrent tagging writer exists (Phase 2). Revisit when the tagging API lands.

**Scope note:** this SHA is the FIELD PLUMB only — not full Phase 1. The
`projects.json` store, tagging API, collision signals (same-file + same-worktree),
notify, and panel are later commits and will gate separately.

---

## Phase-1 Step 2 — `projects.json` store (`ProjectStore`) — `6f5d6a985`

New `src/kiro_crew/dashboard/project_store.py` + `test/test_project_store.py`.
A machine-owned durable record table (`{id, name, repos[]}`) a session's
`project_group_id` points at. Only writer is `ProjectStore` (json.dumps + atomic
write); mirrors the crons.json durability pattern (flock, blake2b
`_sync_for_write` RMW, atomic tmp→rename) with none of the scheduler machinery.

**Design correction that ended a 6-round GPT data-loss loop (user decision):**
the file is MACHINE-OWNED — we do not accept hand-authored records. So the store
validates its OWN invariants (a failed write must not lose data; a concurrent
process must not be clobbered; our own file failing to read/parse is corruption,
not user input → fail loud) and does NOT defensively coerce foreign input. The
prior rounds' per-field coercion (scalar `repos`→`[]`, invented `"None"` ids,
salvaged half-parsed rows) was defending an input class that cannot occur, and
was deleted. `from_dict` enforces the EXACT shape and raises `ProjectStoreCorrupt`
on any deviation (missing/wrong-typed field OR unknown key).

| Gate | Verdict | Notes |
|---|---|---|
| Build + tests | GO | 28 store + 7 field tests green (`./.venv/bin/pytest`) |
| Multimodel panel (`6f5d6a985`) | GO | GPT PASS, Opus PASS, Design PASS; First-Principles CONCERNS (unwired-store/Step-4-split = taste) |
| Adversarial crew — Correctness | GO | No Blocker/High/Medium; idempotency, TOCTOU-close, digest-ordering, transactional-rollback all hold, each pinned by a non-tautological test |
| Adversarial crew — Security | GO | Paths derive from `config_dir()`/`base_dir`, never from id/name; json-only deser; fd lifecycle clean; caps enforced under lock |
| Adversarial crew — Tests | GO | Contract covered; strengthened cross-instance TOCTOU + cap + observe-empty per crew Medium/Nits |
| Adversarial crew — Docs | GO | Docstrings match code; no stale coerce/degrade phrasing |
| Adversarial crew — AI/over-engineering | GO | M1 (from_dict unknown-key contradiction) fixed by enforcing exact shape; M2 double-raise collapsed |

**Paired record:** `6f5d6a985 | crew: GO (all 5 axes; M1 exact-shape fix + Tests-Medium cross-instance TOCTOU test applied) | panel: GO (GPT/Opus/Design PASS, First-Principles CONCERNS=taste)`
(Final branch SHA `99786eec8` = `6f5d6a985` + this scan-log entry, docs-only; the
gated code bytes are identical, so both verdicts stand without a re-gate.)

**Security / ASH / Holmes:** additive Python + JSON persistence, no IaC, no new
deps. Crew Security axis = GO (no injection/traversal/deser/fd-leak on changed
lines). **ASH RUN** (`9211b8df`, dashboard dir, MEDIUM threshold, 10 scanners):
279 findings dir-wide but **ZERO reference `project_store.py`** — the 4 criticals
(detect-secrets) are in pre-existing `token_secret.py`/`token_auth.py`, and the
bandit medium/low are in other dashboard files. This diff adds **0 actionable
findings**. Gate: PASS for the diff.

**Loop history (kept for the eval corpus — panel-caught / crew-caught):** the
panel BLOCKed 6 successive SHAs, each a distinct GPT data-loss finding on the
store's defensive parsing (`{"projects":null}` → `repos:5` → trailing corrupt
byte → failed-save cache divergence → `{"id":null}`→`"None"` → digest poison-on-
raise). Opus PASSed throughout; First-Principles CONCERNS-then-BLOCK on the
unwired store the whole time. Root cause was a wrong unit-of-review + a wrong
posture (defending foreign input into a machine-owned file), fixed by the
machine-owned reframing, NOT by a 7th patch. The final digest-poison finding
(`18ab7c0c3`) was a genuine ordering bug in our own code and was fixed.

**Accepted follow-ups (do not block; land with the Step-4 caller):**
1. `_MAX_REPOS` / per-repo-length cap on `observe_repo` (crew Security Nit) —
   mirror the `_MAX_PROJECTS`/`_MAX_NAME_CHARS` discipline once the tagging API
   drives it.
2. Shared-dir lock-file permission caveat — only relevant if a future caller
   points `base_dir` at a world-writable dir; `config_dir()` is user-owned.

**Scope note:** this SHA is the STORE only. Its validated creation interface
(the tagging API — the store's only intended caller), collision signals, notify,
and panel are later commits and gate separately. The store is intentionally
UNWIRED this commit (acknowledged Step-2/Step-4 split).
