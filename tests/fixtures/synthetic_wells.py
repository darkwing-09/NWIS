from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import List, Optional
import uuid
from sqlalchemy.orm import Session

from database.models.events import Event, IncidentEvent
from database.models.wells import Well, WellFormationInterval


@dataclass
class DatasetFixture:
    wells: List[Well] = field(default_factory=list)
    intervals: List[WellFormationInterval] = field(default_factory=list)
    events: List[Event] = field(default_factory=list)
    target_well_id: uuid.UUID = field(default_factory=uuid.uuid4)
    planted_event_type: str = "loss_circulation"
    planted_depth: float = 2450.0
    planted_formation: str = "Barail"
    planted_distinct_wells: int = 3

    def populate_db(self, db: Session) -> None:
        """Insert all fixture objects into the database session."""
        for w in self.wells:
            db.add(w)
        db.flush()

        for interval in self.intervals:
            db.add(interval)

        for ev in self.events:
            db.add(ev)

        db.commit()


def generate_synthetic_dataset(
    num_wells: int = 8,
    planted_overlaps: int = 3,
) -> DatasetFixture:
    """
    Programmatic synthetic fixture generator for NWIS correlation & alert testing.
    All data is strictly synthetic and labeled as synthetic.
    Injects `planted_overlaps` offset wells sharing an incident in the target formation and depth window.
    """
    base_lat = 27.35  # Nahorkatiya, Assam
    base_lon = 95.32
    target_formation = "Barail"
    planted_depth = 2450.0
    planted_event_type = "loss_circulation"

    wells: List[Well] = []
    intervals: List[WellFormationInterval] = []
    events: List[Event] = []

    # 1. Create Target (Origin) Active Well
    target_well_id = uuid.uuid4()
    target_well = Well(
        well_id=target_well_id,
        name="SYN-NHK-TARGET",
        status="active",
        latitude=base_lat,
        longitude=base_lon,
        spud_date=date(2024, 1, 10),
        operator_name="Oil India Limited (Synthetic)",
        field_name="Nahorkatiya",
        basin_name="Assam-Arakan",
    )
    wells.append(target_well)

    # Formation intervals for target well
    intervals.append(
        WellFormationInterval(
            interval_id=uuid.uuid4(),
            well_id=target_well_id,
            formation_name=target_formation,
            top_depth=2100.0,
            bottom_depth=2650.0,
        )
    )

    # 2. Create Offset Wells
    # The first `planted_overlaps` wells will deliberately contain the planted overlap
    for i in range(1, num_wells):
        wid = uuid.uuid4()
        # Place within ~2 to 8 km
        dlat = (i * 0.015) - 0.03
        dlon = (i * 0.012) - 0.02
        is_planted = i <= planted_overlaps

        w = Well(
            well_id=wid,
            name=f"SYN-NHK-OFFSET-{i:02d}",
            status="active" if i != num_wells - 1 else "abandoned",  # last well is abandoned for filter tests
            latitude=base_lat + dlat,
            longitude=base_lon + dlon,
            spud_date=date(2020 + (i % 3), 3, 15),
            operator_name="Oil India Limited (Synthetic)",
            field_name="Nahorkatiya",
            basin_name="Assam-Arakan",
        )
        wells.append(w)

        # Formation intervals
        # Planted wells MUST have target_formation penetrated
        if is_planted:
            intervals.append(
                WellFormationInterval(
                    interval_id=uuid.uuid4(),
                    well_id=wid,
                    formation_name=target_formation,
                    top_depth=2080.0 + (i * 10),
                    bottom_depth=2620.0 + (i * 10),
                )
            )
            # Inject planted incident within depth window (2450 ± 25m)
            ev = Event(
                event_id=uuid.uuid4(),
                well_id=wid,
                depth=planted_depth + (i * 10 - 20),  # 2440m, 2450m, 2460m
                event_type=planted_event_type,
                severity="high",
                description_raw=f"Synthetic historical incident: severe mud loss into fractured {target_formation} sand",
                mitigation_taken="Pumped 40 bbl high-fluid-loss LCM pill and waited 4 hours",
                confidence_score=0.98,
                extractor_version="synthetic_fixture_v1",
                model_version="fixture_model",
                valid_from=datetime.now(timezone.utc),
            )
            events.append(ev)
        else:
            # Non-planted offset wells: different formation or different depths
            intervals.append(
                WellFormationInterval(
                    interval_id=uuid.uuid4(),
                    well_id=wid,
                    formation_name="Tipam" if i % 2 == 0 else target_formation,
                    top_depth=1200.0,
                    bottom_depth=1900.0,
                )
            )
            # Distractor event at different depth (e.g. 1450m)
            ev = Event(
                event_id=uuid.uuid4(),
                well_id=wid,
                depth=1450.0 + i * 20,
                event_type="equipment_failure" if i % 2 == 0 else "tight_hole",
                severity="medium",
                description_raw=f"Synthetic background event on offset well {i}",
                confidence_score=0.9,
                extractor_version="synthetic_fixture_v1",
                model_version="fixture_model",
                valid_from=datetime.now(timezone.utc),
            )
            events.append(ev)

    return DatasetFixture(
        wells=wells,
        intervals=intervals,
        events=events,
        target_well_id=target_well_id,
        planted_event_type=planted_event_type,
        planted_depth=planted_depth,
        planted_formation=target_formation,
        planted_distinct_wells=planted_overlaps,
    )
