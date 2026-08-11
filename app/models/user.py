from pydantic import BaseModel, Field, field_validator

class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, description="Unique username")
    password: str = Field(..., min_length=6, max_length=100, description="Plaintext password")
    role: str = Field(..., description="Role must be either 'admin' or 'employee'")

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        value_lower = value.lower().strip()
        if value_lower not in ("admin", "employee"):
            raise ValueError("Role must be 'admin' or 'employee'")
        return value_lower

class UserLogin(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    created_at: str
