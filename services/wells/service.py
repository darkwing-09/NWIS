import uuid
from typing import List, Optional
from sqlalchemy.orm import Session

from config.errors import AuthorizationError, NotFoundError, ValidationError
from database.models.wells import Well, WellFormationInterval
from database.repositories import wells as well_repo
from database.repositories import formations as formation_repo
from domain.models.wells import (
    AuthenticatedUser,
    FormationIntervalCreate,
    WellCreate,
    WellRef,
)

VALID_STATUS_SEQUENCE = ["active", "completed", "abandoned"]


def create_well(data: WellCreate, user: AuthenticatedUser, db: Session) -> Well:
    """Create a new well with RBAC authorization check."""
    if not user.can_access_field(data.field_name):
        raise AuthorizationError(
            f"User not authorized to create wells in field '{data.field_name}'",
            detail={"field_name": data.field_name},
        )
    return well_repo.create_well(db, data)


def get_well(well_id: uuid.UUID, user: AuthenticatedUser, db: Session) -> Well:
    """Retrieve a well, enforcing field-level access control."""
    well = well_repo.get_well_by_id(db, well_id)
    if not well:
        raise NotFoundError(f"Well '{well_id}' not found", detail={"well_id": str(well_id)})

    if not user.can_access_field(well.field_name):
        raise AuthorizationError(
            f"User not authorized to access well in field '{well.field_name}'",
            detail={"well_id": str(well_id), "field_name": well.field_name},
        )
    return well


def list_wells(
    user: AuthenticatedUser,
    field: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = None,  # type: ignore[assignment]
) -> List[Well]:
    """List wells accessible to user with optional field and status filters."""
    if field and not user.can_access_field(field):
        raise AuthorizationError(
            f"User not authorized to access field '{field}'", detail={"field": field}
        )

    allowed_fields = user.allowed_fields if "admin" not in user.roles else None
    return well_repo.list_wells(db, field=field, status=status, allowed_fields=allowed_fields)


def list_formations(
    well_id: uuid.UUID, user: AuthenticatedUser, db: Session
) -> List[WellFormationInterval]:
    """List formation intervals for a well after verifying access."""
    # Ensure user has access to the parent well
    get_well(well_id, user, db)
    return formation_repo.list_intervals_for_well(db, well_id)


def create_formation_interval(
    well_id: uuid.UUID,
    data: FormationIntervalCreate,
    user: AuthenticatedUser,
    db: Session,
) -> WellFormationInterval:
    """Create a formation interval for a well."""
    get_well(well_id, user, db)
    if data.top_depth >= data.bottom_depth:
        raise ValidationError(
            "top_depth must be less than bottom_depth",
            detail={"top_depth": data.top_depth, "bottom_depth": data.bottom_depth},
        )
    return formation_repo.create_interval(db, well_id, data)


def update_well_status(
    well_id: uuid.UUID,
    status: str,
    user: AuthenticatedUser,
    admin_override: bool = False,
    db: Session = None,  # type: ignore[assignment]
) -> Well:
    """
    Validate and update well status.
    Valid sequence: active -> completed -> abandoned.
    No reverse transition or skipping without admin_override=True.
    """
    well = get_well(well_id, user, db)
    target_status = status.lower()

    if target_status not in VALID_STATUS_SEQUENCE and target_status != "archived":
        raise ValidationError(f"Invalid status '{target_status}'. Must be one of {VALID_STATUS_SEQUENCE}")

    current_status = well.status.lower()
    if current_status == target_status:
        return well

    if not admin_override:
        if current_status in VALID_STATUS_SEQUENCE and target_status in VALID_STATUS_SEQUENCE:
            current_idx = VALID_STATUS_SEQUENCE.index(current_status)
            target_idx = VALID_STATUS_SEQUENCE.index(target_status)
            if target_idx != current_idx + 1:
                raise ValidationError(
                    f"Invalid status transition from '{current_status}' to '{target_status}'. Sequence must be active -> completed -> abandoned.",
                    detail={"current": current_status, "target": target_status},
                )
        else:
            raise ValidationError(
                f"Transition from '{current_status}' to '{target_status}' requires administrative override",
                detail={"current": current_status, "target": target_status},
            )

    well.status = target_status
    db.commit()
    db.refresh(well)
    return well
