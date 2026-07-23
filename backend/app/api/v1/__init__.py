"""Aggregate v1 API router."""
from fastapi import APIRouter

from app.api.v1 import admin, agents, analytics, auth, branding, conversations, feedback, files_browser, health, logs, memory, notifications, presence, profile_evolution, scheduled, skill_evolution, teams, terminal, users

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
api_router.include_router(scheduled.router)
api_router.include_router(logs.router, tags=["logs"])
api_router.include_router(feedback.router)
api_router.include_router(skill_evolution.router)
api_router.include_router(profile_evolution.router)
