"""Aggregate v1 API router."""
from fastapi import APIRouter

from app.api.v1 import admin, agents, analytics, auth, branding, conversations, files_browser, health, memory, notifications, presence, teams, terminal, users

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(agents.router, tags=["agents"])
api_router.include_router(
    conversations.router, prefix="/conversations", tags=["conversations"]
)
api_router.include_router(teams.router, tags=["teams"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(branding.router, tags=["branding"])
api_router.include_router(analytics.router, tags=["analytics"])
api_router.include_router(files_browser.router, tags=["files"])
api_router.include_router(terminal.router, tags=["terminal"])
api_router.include_router(presence.router, tags=["presence"])
api_router.include_router(notifications.router, tags=["notifications"])
api_router.include_router(memory.router)
