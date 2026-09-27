import uuid
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.auth import Permission, Role, User
from domain.models.auth import TokenClaims


def get_or_create_user(db: Session, claims: TokenClaims) -> User:
    """
    Look up user by external_idp_id (claims.sub); if not found, auto-provision
    a new User and assign default permissions. NWIS never manages passwords directly.
    """
    user = db.execute(
        select(User).where(User.external_idp_id == claims.sub)
    ).scalar_one_or_none()

    if user:
        return user

    # Create new user
    new_user = User(
        user_id=uuid.uuid4(),
        external_idp_id=claims.sub,
        name=claims.name,
        email=claims.email,
    )
    db.add(new_user)
    db.flush()

    # Assign default roles and permissions
    for role_name in claims.roles or ["engineer"]:
        role = db.execute(select(Role).where(Role.name == role_name)).scalar_one_or_none()
        if not role:
            role = Role(role_id=uuid.uuid4(), name=role_name)
            db.add(role)
            db.flush()

        if claims.allowed_fields:
            for field in claims.allowed_fields:
                perm = Permission(
                    permission_id=uuid.uuid4(),
                    user_id=new_user.user_id,
                    role_id=role.role_id,
                    field_name=field,
                )
                db.add(perm)
        else:
            # All fields access
            perm = Permission(
                permission_id=uuid.uuid4(),
                user_id=new_user.user_id,
                role_id=role.role_id,
                field_name=None,
            )
            db.add(perm)

    db.commit()
    db.refresh(new_user)
    return new_user


def get_user_by_id(db: Session, user_id: uuid.UUID) -> Optional[User]:
    """Retrieve user by ID."""
    return db.execute(select(User).where(User.user_id == user_id)).scalar_one_or_none()


def list_users(db: Session) -> List[User]:
    """List all registered users."""
    return list(db.execute(select(User)).scalars().all())
