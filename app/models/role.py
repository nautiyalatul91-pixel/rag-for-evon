from typing import Optional
from pydantic import BaseModel, Field

class RoleResponse(BaseModel):
    role_id: str = Field(..., description="Unique slug or identifier for the role (e.g. 'admin', 'programmer')")
    role_name: str = Field(..., description="Human-readable display name of the role (e.g. 'Programmer')")
    hierarchy_position: int = Field(..., description="Position in the organization hierarchy (1 is highest/most privileged)")
    can_upload: bool = Field(..., description="Whether users with this role are permitted to upload documents")
    is_protected: bool = Field(..., description="Whether this role is protected from deletion or repositioning")

class RoleCreateRequest(BaseModel):
    role_name: str = Field(..., min_length=1, max_length=50, description="Display name for the new role")
    role_id: Optional[str] = Field(None, min_length=1, max_length=50, description="Optional custom ID. Auto-generated from role_name if omitted.")
    insert_below_role_id: Optional[str] = Field(None, description="Role ID below which this new role should be positioned")
    can_upload: bool = Field(False, description="Whether this role has document upload permissions")

class RoleUpdateRequest(BaseModel):
    role_name: Optional[str] = Field(None, min_length=1, max_length=50, description="Updated display name")
    can_upload: Optional[bool] = Field(None, description="Updated upload permission")
    insert_below_role_id: Optional[str] = Field(None, description="Move role immediately below this role ID")
    hierarchy_position: Optional[int] = Field(None, ge=1, description="Explicit target hierarchy position")

class RoleDeleteResponse(BaseModel):
    success: bool
    message: str
    deleted_role_id: str
