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
