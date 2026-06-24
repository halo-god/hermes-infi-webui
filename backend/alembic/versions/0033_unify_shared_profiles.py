"""unify shared_agents → shared_profile_ids, pinned_agents → pinned_profile_ids

Revision ID: 0033
Revises: 0032
Create Date: 2026-06-24

Data migration:
- For each team, migrate entries from shared_agents into shared_profile_ids:
  - If entry is a valid UUID matching a Profile.id → add directly
  - If entry is an agent id (e.g. "hermes") → reverse-lookup Profile by
    default_agent_id, add the first match's id
  - If no matching profile → skip (spawn-time "hermes" fallback covers it)
- Same for projects.pinned_agents → new pinned_profile_ids column.
Then drops shared_agents (teams) and pinned_agents (projects), adds
pinned_profile_ids (projects).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── Step 1: Migrate teams.shared_agents → shared_profile_ids ──
    teams = conn.execute(
        sa.text("SELECT id, shared_agents, shared_profile_ids FROM teams")
    ).fetchall()

    for team_id, shared_agents, shared_profile_ids in teams:
        if not shared_agents:
            continue
        existing = set(shared_profile_ids or [])
        for entry in shared_agents:
            if entry in existing:
                continue
            # Try as a Profile UUID directly (the NewTeamModal bug case).
            prof = conn.execute(
                sa.text("SELECT id FROM profiles WHERE id::text = :entry LIMIT 1"),
                {"entry": entry},
            ).fetchone()
            if prof:
                existing.add(str(prof[0]))
                continue
            # Try as an agent id → reverse-lookup Profile.default_agent_id.
            prof2 = conn.execute(
                sa.text(
                    "SELECT id FROM profiles WHERE default_agent_id = :entry "
                    "ORDER BY created_at LIMIT 1"
                ),
                {"entry": entry},
            ).fetchone()
            if prof2:
                existing.add(str(prof2[0]))
            # else: skip — no profile for this agent id; spawn-time fallback covers it.

        conn.execute(
            sa.text("UPDATE teams SET shared_profile_ids = CAST(:ids AS jsonb) WHERE id = :tid"),
            {"ids": __import__("json").dumps(list(existing)), "tid": team_id},
        )

    # ── Step 2: Add projects.pinned_profile_ids ──
    op.add_column(
        "projects",
        sa.Column("pinned_profile_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
    )

    # ── Step 3: Migrate projects.pinned_agents → pinned_profile_ids ──
    projects = conn.execute(
        sa.text("SELECT id, pinned_agents FROM projects")
    ).fetchall()

    for proj_id, pinned_agents in projects:
        if not pinned_agents:
            continue
        profile_ids = []
        for entry in pinned_agents:
            prof = conn.execute(
                sa.text("SELECT id FROM profiles WHERE id::text = :entry LIMIT 1"),
                {"entry": entry},
            ).fetchone()
            if prof:
                pid = str(prof[0])
                if pid not in profile_ids:
                    profile_ids.append(pid)
                continue
            prof2 = conn.execute(
                sa.text(
                    "SELECT id FROM profiles WHERE default_agent_id = :entry "
                    "ORDER BY created_at LIMIT 1"
                ),
                {"entry": entry},
            ).fetchone()
            if prof2:
                pid = str(prof2[0])
                if pid not in profile_ids:
                    profile_ids.append(pid)

        conn.execute(
            sa.text("UPDATE projects SET pinned_profile_ids = CAST(:ids AS jsonb) WHERE id = :pid"),
            {"ids": __import__("json").dumps(profile_ids), "pid": proj_id},
        )

    # ── Step 4: Drop old columns ──
    op.drop_column("teams", "shared_agents")
    op.drop_column("projects", "pinned_agents")


def downgrade() -> None:
    # Re-add columns (data is not reversible — shared_agents/pinned_agents
    # content is lost after migration).
    op.add_column(
        "teams",
        sa.Column("shared_agents", postgresql.JSONB(astext_type=sa.Text()), server_default='["hermes"]'),
    )
    op.add_column(
        "projects",
        sa.Column("pinned_agents", postgresql.JSONB(astext_type=sa.Text()), server_default="[]"),
    )
    op.drop_column("projects", "pinned_profile_ids")
