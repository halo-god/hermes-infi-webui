# Roadmap: Distinctive & Innovative Features for hermes-infi-webui

## Context

You asked to study three mature agent web UIs (AionUi, hermes-webui,
hermes-workspace) and borrow good ideas. The key finding: **hermes-infi-webui is
already the most feature-complete of the four.** Chasing the reference projects'
commodity features (voice input, PWA, multi-provider) would only achieve parity.

So instead, this roadmap pursues **distinctive innovation** — capabilities none of
the three reference projects have, built on infrastructure that is *unique to
hermes-infi-webui*: per-user **memory consolidation**, **team governance/RBAC**,
**roundtable** multi-agent chat, **message reactions**, and the **clarify/confirm**
loop. The strategy is to compound these existing strengths into features that are
hard for others to copy.

You chose a **prioritized roadmap** (not an immediate build). The **skills
marketplace** is the foundation; layered on top are three **flagship**
innovations and three **frontier** innovations, all selected by you:

- Foundation: **Skills Marketplace**
- Flagship: **Skill Mining** · **Conductor Orchestration** · **Team Soul**
- Frontier: **Reaction Self-Improvement** · **Skill Evolution** · **Adversarial Debate**

Deliverable: this roadmap committed as `docs/ROADMAP-innovations.md` on branch
`claude/vibrant-shannon-xwuly0`. No application code is part of this plan;
each item is implemented in a follow-up session.

---

## The thesis: a self-improving, governed agent collective

These six features are not independent — they form one reinforcing loop:

```
        ┌─────────────────────────────────────────────────────────┐
        │                                                          │
   Conversations ──▶ Skill Mining ──▶ Skills Marketplace ──▶ Profiles/Agents
        ▲                                    ▲                      │
        │                                    │ A/B winners          │ dispatch
   Reaction signals ◀── Reaction Self-Improve │                     ▼
        │                              Skill Evolution ◀── outcome metrics
        │                                                          │
   Team Soul (institutional memory) ◀── Adversarial Debate / Conductor (multi-agent work)
```

Conversations produce skills (mining); skills get attached to agents and dispatched;
outcomes + reactions feed evolution and self-improvement; multi-agent work
(Conductor, Debate) generates richer conversations; team-level consolidation
(Team Soul) makes the whole system smarter over time. Skills Marketplace is the
shared substrate that makes the loop legible and reusable.

---

## Build order & dependencies

| # | Feature | Depends on | Effort |
|---|---------|-----------|--------|
| 0 | Skills Marketplace (foundation) | existing `profiles.skills` | M |
| 1 | Skill Mining | #0 + memory consolidation | M |
| 2 | Team Soul | memory consolidation | M |
| 3 | Conductor Orchestration | roundtable infra | L |
| 4 | Reaction Self-Improvement | reactions + memory consolidation | S–M |
| 5 | Skill Evolution | #0, #1 + analytics | M–L |
| 6 | Adversarial Debate | roundtable infra | M |

Recommended sequence: **0 → 1 → 2 → 4 → 3 → 6 → 5** (foundation first, then the
cheap learning loops, then the heavier orchestration, with Skill Evolution last
since it needs usage data from everything above).

---

## Foundation — Skills Marketplace

Promote the cosmetic `profiles.skills` tag list (`backend/app/db/models/agent.py:31-48`,
parsed by `_parse_skills` in `backend/app/schemas/agent.py`) into first-class,
browsable, attachable skills whose markdown `content` is actually injected into
the agent at dispatch.

- **ORM** — new `backend/app/db/models/skill.py` (`UUIDPrimaryKey + Timestamps`),
  registered in `app/db/models/__init__.py`:
  - `Skill`: `slug` (unique), `name`, `description`, `category`, `icon`, `color`,
    `content` (Text markdown), `scope` (global|team|personal), `team_id`,
    `author_id`, `featured`, `install_count`, `is_active`.
  - `ProfileSkill` junction: `profile_id`, `skill_id`.
- **Migration** — hand-written `0034_skills.py` (after `0033_unify_shared_profiles.py`).
- **Schemas / Service / Routes** — `app/schemas/skill.py`,
  `app/services/skill_service.py` (thick service per CLAUDE.md),
  `app/api/v1/skills.py` registered in `app/api/v1/__init__.py`. CRUD +
  list/filter + attach/detach; writes guarded by `_require_admin` /
  `team_service.require_permission`.
- **Dispatch wiring (makes skills real)** — in `conversations.py` where the task
  is enqueued to `acp:prompt`, concatenate attached skills' `content` into the
  `system_prompt` already consumed by `agent_runner/runner.py` `handle_single`.
  No ACP protocol change.
- **Frontend** — `frontend/src/api/skills.ts`, `Skill` type in `types/index.ts`,
  new `views/SkillsView.vue` gallery + route, and a real skill-picker in the
  AdminView "助手管理" profile form (replacing the free-text chip input ~lines
  1222-1296).

---

## Flagship innovations

### 1. Skill Mining — skills that write themselves
Auto-synthesize installable skills from *successful* conversations. Extend the
existing memory-consolidation pipeline (`app/api/v1/memory.py`,
`mem:consolidate:*` Redis keys) with a second analyzer pass that detects a
reusable pattern (a well-received multi-step solution) and drafts a `Skill`
record (name/category/content) in a **pending review** state.

- New `skill_service.mine_from_conversation(conv_id)`, triggered after
  consolidation or on demand from the conversation menu.
- Mined skills land in SkillsView with a "draft / proposed" badge; an admin or
  the author approves → it joins the marketplace.
- *Why distinctive:* turns ephemeral chat success into durable, shareable
  capability — a flywheel no reference project has.

### 2. Conductor Orchestration — roundtable that decomposes
Evolve roundtable (`stores/chat.ts` roundtable WS, `conversations.py` roundtable
routing) from "N agents reply in parallel" into a **leader agent that decomposes a
task and dispatches subtasks** to teammate agents sharing one workspace.

- New conversation mode `conductor` alongside `single`/`group`/`roundtable`.
- Leader emits a `plan` (reuse existing `Message.plan` / `PlanEntry`
  pending→in_progress→completed) whose entries are dispatched as child prompt
  tasks on `acp:prompt`; results stream back into the shared workspace and a
  synthesis step.
- *Why distinctive:* genuine task decomposition over multi-agent infra you
  already own; surfaces live via the plan UI you already render.

### 3. Team Soul — institutional memory
You consolidate memory per *user* (notes/user_profile/soul). Add a **team-level**
consolidation that aggregates across a team's conversations into shared
`team_notes` / `team_soul`, injected for agents acting in that team's context.

- New table `team_memory` (team_id, notes, user_profile-equiv, soul) +
  `team_service` consolidation entry point reusing the existing consolidation
  machinery and `mem:consolidate:*` lock pattern (scoped to team).
- Surfaced in `TeamDetailView` (new "团队记忆" panel) with admin-gated edit.
- *Why distinctive:* organizational knowledge that compounds; pairs with Skill
  Mining (team-scoped mined skills) and governance.

---

## Frontier innovations

### 4. Reaction Self-Improvement — a closed learning loop
You already store 👍/👎 in `Message.reactions`. Aggregate reaction signal per
agent/profile/skill and feed it into consolidation so the agent's `soul`/notes
encode "do more of X, less of Y."

- `skill_service` / `memory` consolidation reads reaction tallies as a quality
  signal; negative-reacted patterns are demoted, positive ones reinforced.
- Also becomes the **outcome metric** powering Skill Evolution (#5).
- *Why distinctive:* RLHF-lite from organic in-product feedback, no labeling.

### 5. Skill Evolution — A/B variants that win
Mined/authored skills accrue outcome metrics (reactions, task success, usage).
When a skill underperforms, propose an improved `content` variant and **A/B** it
across dispatches; promote the winner, keeping lineage.

- Extend `Skill` with `parent_skill_id` (lineage) and a lightweight variant/
  metrics table; reuse `analytics.py` aggregation patterns for scoring.
- *Why distinctive:* skills that measurably improve over time with provenance —
  beyond a static catalog.

### 6. Adversarial Debate — converge to verified answers
Add a roundtable sub-mode where agents **critique each other across rounds**
before a final synthesized, higher-confidence answer, instead of independent
parallel replies.

- New `debate` roundtable variant in `conversations.py` dispatch + `stores/chat.ts`
  WS handling; bounded rounds; final synthesis message flagged as "consensus."
- *Why distinctive:* a correctness mechanism (self-critique) layered onto existing
  multi-agent UI.

---

## Verification

Roadmap doc (this deliverable):
- Confirm `docs/ROADMAP-innovations.md` is committed to
  `claude/vibrant-shannon-xwuly0` and renders (tables, the loop diagram, headings).

Per-feature, when later implemented (apply the relevant subset):
- Backend: `alembic upgrade head` then `downgrade -1` clean; `ruff check .` clean;
  `pytest` green with a new `tests/test_<feature>.py` (CRUD + scope/permission for
  marketplace; mining draft creation; team consolidation lock; reaction
  aggregation; conductor child-task dispatch; debate round bounding).
- Frontend: `cd frontend && npm run build` (type-check gate) passes; respect
  `noUnusedLocals`.
- End-to-end via `make up`:
  - Marketplace: create skill → browse in SkillsView → attach to profile → start
    a conversation and confirm the skill `content` shapes agent behavior.
  - Skill Mining: run a successful conversation → consolidate → a draft skill
    appears for review.
  - Team Soul: hold team conversations → trigger team consolidation → team memory
    populated and visible in TeamDetailView.
  - Conductor: start a `conductor` conversation → observe leader plan entries
    dispatched and synthesized.
  - Reaction loop: 👍/👎 messages → consolidation reflects the signal.
  - Debate: start `debate` mode → observe critique rounds → consensus message.
