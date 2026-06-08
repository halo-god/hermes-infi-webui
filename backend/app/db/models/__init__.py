"""ORM models. Import all here so Alembic autogenerate sees them."""
from app.db.models.agent import Agent  # noqa: F401
from app.db.models.audit import AuditLog  # noqa: F401
from app.db.models.conversation import Conversation, GroupMember, Message  # noqa: F401
from app.db.models.identity import DeptTeamMapping, IdentityProvider  # noqa: F401
from app.db.models.system import SystemSettings  # noqa: F401
from app.db.models.team import (  # noqa: F401
    Project,
    ProjectDoc,
    ProjectTask,
    Team,
    TeamKnowledge,
    TeamMember,
)
from app.db.models.user import User  # noqa: F401
from app.db.models.workspace import WorkspaceFile  # noqa: F401
