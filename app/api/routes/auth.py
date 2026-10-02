from fastapi import APIRouter, Depends

from app.models.auth.user import User
from app.services.auth import sync_current_user

router = APIRouter(prefix="/auth", tags=["Auth"])

@router.get("/me", response_model=User)
async def get_me(user: User = Depends(sync_current_user)) -> User:
    return user
