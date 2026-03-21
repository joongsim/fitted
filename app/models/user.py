from pydantic import BaseModel, EmailStr, field_validator
from uuid import UUID
from datetime import datetime
from typing import Optional, List

class UserBase(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None

class UserCreate(UserBase):
    password: str

class User(UserBase):
    user_id: UUID
    is_active: bool
    created_at: datetime
    last_login: Optional[datetime] = None

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    user_id: Optional[str] = None


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("token")
    @classmethod
    def token_must_be_43_chars(cls, v: str) -> str:
        if len(v) != 43:
            raise ValueError("token must be exactly 43 characters")
        return v

    @field_validator("new_password")
    @classmethod
    def password_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        if len(v) > 128:
            raise ValueError("password must be at most 128 characters")
        return v
