from enum import Enum
from typing import Optional


class EventType(str, Enum):
    KICK = "kick"
    LOSS_CIRCULATION = "loss_circulation"
    STUCK_PIPE = "stuck_pipe"
    WELLBORE_INSTABILITY = "wellbore_instability"
    EQUIPMENT_FAILURE = "equipment_failure"
    GAS_SHOW = "gas_show"
    PACKOFF = "packoff"
    TIGHT_HOLE = "tight_hole"
    CASING_LEAK = "casing_leak"
    WATER_INFLUX = "water_influx"
    OTHER = "other"


EVENT_TYPE_SYNONYMS = {
    "kick": EventType.KICK,
    "gas kick": EventType.KICK,
    "well kick": EventType.KICK,
    "influx": EventType.KICK,
    "loss": EventType.LOSS_CIRCULATION,
    "losses": EventType.LOSS_CIRCULATION,
    "loss circulation": EventType.LOSS_CIRCULATION,
    "lost circulation": EventType.LOSS_CIRCULATION,
    "circulation loss": EventType.LOSS_CIRCULATION,
    "mud loss": EventType.LOSS_CIRCULATION,
    "stuck pipe": EventType.STUCK_PIPE,
    "pipe stuck": EventType.STUCK_PIPE,
    "stuck": EventType.STUCK_PIPE,
    "instability": EventType.WELLBORE_INSTABILITY,
    "wellbore instability": EventType.WELLBORE_INSTABILITY,
    "hole collapse": EventType.WELLBORE_INSTABILITY,
    "sloughing shale": EventType.WELLBORE_INSTABILITY,
    "sloughing": EventType.WELLBORE_INSTABILITY,
    "equipment failure": EventType.EQUIPMENT_FAILURE,
    "tool failure": EventType.EQUIPMENT_FAILURE,
    "mwd failure": EventType.EQUIPMENT_FAILURE,
    "pump failure": EventType.EQUIPMENT_FAILURE,
    "gas show": EventType.GAS_SHOW,
    "gas influx": EventType.GAS_SHOW,
    "packoff": EventType.PACKOFF,
    "pack off": EventType.PACKOFF,
    "tight hole": EventType.TIGHT_HOLE,
    "drag": EventType.TIGHT_HOLE,
    "casing leak": EventType.CASING_LEAK,
    "water influx": EventType.WATER_INFLUX,
    "other": EventType.OTHER,
}


def normalize_event_type(value: str) -> Optional[EventType]:
    """Normalize raw event string to canonical EventType enum."""
    if not value:
        return None
    val_clean = value.strip().lower()
    # Direct enum match
    for member in EventType:
        if member.value == val_clean:
            return member
    # Synonym match
    if val_clean in EVENT_TYPE_SYNONYMS:
        return EVENT_TYPE_SYNONYMS[val_clean]
    return None


class SeverityLevel(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class AlertStatus(str, Enum):
    TRIGGERED = "triggered"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    SUPPRESSED = "suppressed"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
