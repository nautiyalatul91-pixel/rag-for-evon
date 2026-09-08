from typing import Optional
from pydantic import BaseModel, Field, field_validator

class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, description="Unique username")
    password: str = Field(..., min_length=6, max_length=100, description="Plaintext password")
    role: str = Field(..., min_length=1, max_length=50, description="Role ID or name from the roles table")

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        value_clean = value.strip()
        if not value_clean:
            raise ValueError("Role cannot be empty.")
        return value_clean

class UserLogin(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: Optional[str] = None
    can_upload: Optional[bool] = False
    is_admin: Optional[bool] = False

class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    created_at: str

class UserMeResponse(BaseModel):
    id: int
    username: str
    role: str
    can_upload: bool
    is_admin: bool
    created_at: str

class AvailableRoleResponse(BaseModel):
    role_id: str = Field(..., description="Unique slug or identifier for the role")
    role_name: str = Field(..., description="Human-readable display name of the role")

