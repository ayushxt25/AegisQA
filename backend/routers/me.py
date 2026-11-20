"""Authenticated current-user endpoint."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from backend.auth import get_current_user
from backend.models import User


router = APIRouter(prefix="/v1", tags=["identity"])


class CurrentUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    display_name: str
    status: str


@router.get("/me", response_model=CurrentUserResponse)
def current_user(user: User = Depends(get_current_user)) -> CurrentUserResponse:
    return CurrentUserResponse(id=str(user.id), email=user.email, display_name=user.display_name, status=user.status)
