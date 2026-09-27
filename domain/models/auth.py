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


class AuthenticatedUser(BaseModel):
    """Authenticated user context containing identity, roles, and asset/field scopes."""

    user_id: uuid.UUID
    email: str
    name: str = "User"
    roles: List[str] = Field(default_factory=list)
    allowed_fields: Optional[List[str]] = None  # None = all fields

    model_config = ConfigDict(from_attributes=True)

    def can_access_field(self, field_name: Optional[str]) -> bool:
        """Return True if user has access to field_name or has all-fields grant."""
        if "admin" in self.roles:
            return True
        if self.allowed_fields is None:
            return True
        if field_name is None:
            return True
        return field_name.strip().upper() in [f.strip().upper() for f in self.allowed_fields]
