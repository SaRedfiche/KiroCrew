# Scan log — feature/project-coordination-phase1

Auditable record of pre-merge gates per the `auto/ship-it` SOP. The user merges;
these entries record the GO/NO-GO decisions and accepted risks against each head SHA.

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
