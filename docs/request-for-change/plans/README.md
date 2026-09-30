# Implementation Plans

Dated, task-by-task execution plans for documents in
[../](../README.md). A plan is the *how*; its RFC stays the *what* and the *why*.
Each plan names its RFC as its spec, so the two are read together.

Checkbox state is a progress record, not a status: read the RFC's `status` for
that, and read the **State** column here for whether a plan is being worked.

| Plan | Spec | State |
|---|---|---|
| [2026-08-22-durable-run-coordinator.md](2026-08-22-durable-run-coordinator.md) | [rfc-durable-run-coordinator.md](../rfc-durable-run-coordinator.md) | **Obsolete.** 0 of 52 checklist steps are done. The RFC is superseded by `rfc-overload-resilience.md`; its durable store now ships as `src/kiro_crew/taskq/`. |
| [2026-08-27-agentcore-identity-gateway.md](2026-08-27-agentcore-identity-gateway.md) | [rfc-agentcore-identity-gateway.md](../rfc-agentcore-identity-gateway.md) | **Partial on main.** 11 of 29 checklist steps are marked done, but current code contains only the AWS-free core seam (`platform/agentcore_schema.py`, `AgentIdentityProvider`, public Default, and governance row); later AWS/IAM/Gateway work is absent. |
| [2026-09-23-coordination-panel-converge-on-crew-log.md](2026-09-23-coordination-panel-converge-on-crew-log.md) | §12.6 project-group shared memory (design memory-only, not in this tree) | **Superseded 2026-09-30.** Its premise (group shared memory) was retired by `rfc-crew-projects.md` Revision 2 (the thin Project, 2026-09-18). Replaced by the re-baseline plan below. |
| [2026-09-30-rebaseline-thin-project-salvage-collision-signals.md](2026-09-30-rebaseline-thin-project-salvage-collision-signals.md) | [rfc-crew-projects.md](../rfc-crew-projects.md) (Revision 2 — the thin Project) | **Active.** 0 of 5 steps done. Parks the retired group-memory core; salvages the collision signals (same-file / same-worktree / remote-target) by re-keying them off a neutral `coordination_scope_id`, sourced from the thin Project's `project_id` when PR A ships upstream. |

Indexed from [../README.md](../README.md).
