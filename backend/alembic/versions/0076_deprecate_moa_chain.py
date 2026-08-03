"""Deprecate is_moa/is_chain — migrate to workflow DAG.

The visual workflow canvas (Profile.workflow) supersedes the legacy MoA
(parallel fan-out + merge) and Chain (serial relay) toggles. This migration
converts any existing MoA/Chain profile into an equivalent workflow DAG so
the old fields can be dropped in a follow-up.

is_research is NOT migrated — its cascade-termination semantics can't be
expressed as a plain DAG layer, so it stays as a standalone toggle.

Revision ID: 0076
Revises: 0075
Create Date: 2026-07-31
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def _moa_to_workflow(target_ids: list[str]) -> dict:
    """MoA = one parallel layer (all targets) + a merge node."""
    nodes = [
        {"id": f"n{i}", "type": "agent", "position": {"x": 100 + i * 250, "y": 100},
         "data": {"profile_id": pid, "label": f"助手 {i + 1}", "kind": "agent"}}
        for i, pid in enumerate(target_ids)
    ]
    nodes.append({
        "id": "merge", "type": "merge", "position": {"x": 100, "y": 250},
        "data": {"label": "合并", "strategy": "synthesize"},
    })
    edges = [{"id": f"e{i}", "source": f"n{i}", "target": "merge"} for i in range(len(target_ids))]
    return {"nodes": nodes, "edges": edges}


def _chain_to_workflow(target_ids: list[str]) -> dict:
    """Chain = sequential single-node layers."""
    nodes = [
        {"id": f"n{i}", "type": "agent", "position": {"x": 100 + i * 250, "y": 100},
         "data": {"profile_id": pid, "label": f"环节 {i + 1}", "kind": "agent"}}
        for i, pid in enumerate(target_ids)
    ]
    edges = [{"id": f"e{i}", "source": f"n{i}", "target": f"n{i + 1}"} for i in range(len(target_ids) - 1)]
    return {"nodes": nodes, "edges": edges}


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Migrate MoA profiles → workflow DAG.
    rows = conn.execute(sa.text(
        "SELECT id, moa_target_profile_ids FROM profiles WHERE is_moa = true"
    )).fetchall()
    for row in rows:
        ids = row[1] if isinstance(row[1], list) else json.loads(row[1] or "[]")
        if ids and len(ids) >= 2:
            wf = _moa_to_workflow([str(pid) for pid in ids])
            conn.execute(sa.text(
                "UPDATE profiles SET workflow = CAST(:wf AS jsonb) WHERE id = :id"
            ), {"wf": json.dumps(wf), "id": str(row[0])})

    # 2. Migrate Chain profiles → workflow DAG.
    rows = conn.execute(sa.text(
        "SELECT id, chain_target_profile_ids FROM profiles WHERE is_chain = true"
    )).fetchall()
    for row in rows:
        ids = row[1] if isinstance(row[1], list) else json.loads(row[1] or "[]")
        if ids and len(ids) >= 2:
            wf = _chain_to_workflow([str(pid) for pid in ids])
            conn.execute(sa.text(
                "UPDATE profiles SET workflow = CAST(:wf AS jsonb) WHERE id = :id"
            ), {"wf": json.dumps(wf), "id": str(row[0])})


def downgrade() -> None:
    # Can't reverse the DAG→toggle conversion reliably (node data may have been
    # edited). Best-effort: clear workflow for migrated profiles.
    pass
