"""User endpoints: self-service profile."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.db.models.user import User
from app.deps import get_current_user
from app.schemas.user import UserOut, UserUpdate

router = APIRouter()


@router.get("/me", response_model=UserOut)
async def get_me(user: User = Depends(get_current_user)) -> User:
    return user


@router.patch("/me", response_model=UserOut)
async def update_me(
    payload: UserUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return user
