"""User schemas."""
from app.schemas.common import TimestampsMixin
from pydantic import BaseModel, ConfigDict, EmailStr

UserRole = str  # "admin" | "user"


class UserBase(BaseModel):
    username: str
    email: EmailStr | None = None
    name: str | None = None
    role: str = "user"


class UserCreate(BaseModel):
    username: str
    password: str
    email: EmailStr | None = None
    name: str | None = None
    role: str = "user"


class UserUpdate(BaseModel):
    username: str
    password: str | None = None
    email: EmailStr | None = None
    name: str | None = None
    role: str = "user"


class UserRead(UserBase, TimestampsMixin):
    id: int

    model_config = ConfigDict(from_attributes=True, exclude={"hashed_password"})
