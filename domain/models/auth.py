import uuid
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class TokenClaims(BaseModel):
    """Normalized claims extracted from validated JWT token."""

    sub: str
    email: str
    name: str
    roles: List[str] = Field(default_factory=list)
    allowed_fields: Optional[List[str]] = None  # None indicates all fields
    exp: int
    iss: str
    aud: str

    model_config = ConfigDict(from_attributes=True)


class UserSummary(BaseModel):
    user_id: uuid.UUID
    external_idp_id: str
    name: str
    email: str
    roles: List[str] = Field(default_factory=list)
    allowed_fields: Optional[List[str]] = None

    model_config = ConfigDict(from_attributes=True)


class PermissionGrant(BaseModel):
    role_name: str
    field_name: Optional[str] = None
    asset_scope: Optional[str] = None
