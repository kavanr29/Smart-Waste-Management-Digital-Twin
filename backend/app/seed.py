from datetime import timedelta

from .models import (
    Alert,
    Bin,
    Collection,
    Location,
    RecyclingEvent,
    SortingEvent,
    WasteCategory,
    WasteEvent,
    WasteFlowEvent,
    now,
)

# Semantic campus zones are software locations, not physical sensor/device records.
CAMPUS_ZONES = [
    ("Academic Zone", "Academic Block"),
    ("Hostel Zone", "Student Residences"),
    ("Cafeteria Zone", "Dining Commons"),
    ("Sports Zone", "Sports Ground"),
    ("Parking Zone", "North Parking"),
    ("Administration Zone", "Administration"),
    ("Library Zone", "Central Library"),
    ("Garden / Outdoor Zone", "Campus Gardens"),
    ("Central Plaza", "Central Plaza"),
]


STREAMS = ["plastic", "paper", "glass", "metal", "organic", "recyclable", "other", "e-waste"]
BIN_NAMES = [
    "Academic Block",
    "Library",
    "Cafeteria",
    "Hostel",
    "Sports Ground",
    "Main Entrance",
    "Parking",
    "Admin Block",
    "Central Plaza",
    "Garden Zone",
]


def _ensure_location(db, name, zone):
    location = db.query(Location).filter(Location.zone == zone).first()
    if location:
        return location
    location = Location(name=name, zone=zone)
    db.add(location)
    db.flush()
    return location


def seed(db):
    """Safely extend an existing database; repeated runs never duplicate seeded entities."""
    # Preserve existing Location records, while adding missing semantic campus zones.
    existing_zones = {location.zone: location for location in db.query(Location).all()}
   
    existing_zones.update({location.zone: location for location in db.query(Location).all()})
    for zone, name in CAMPUS_ZONES:
        if zone not in existing_zones:
            existing_zones[zone] = _ensure_location(db, name, zone)

    categories = [
        ("Plastic", True), ("Paper", True), ("Glass", True), ("Metal", True),
        ("Organic", False), ("Cardboard", True), ("E-waste", True),
        ("Hazardous", False), ("Other", False),
    ]
    for name, recyclable in categories:
        if not db.query(WasteCategory).filter(WasteCategory.name == name).first():
            db.add(WasteCategory(name=name, recyclable=recyclable))

    by_code = {item.code: item for item in db.query(Bin).all()}


    semantic_zones = [zone for zone, _ in CAMPUS_ZONES]
    for number, name in enumerate(BIN_NAMES, start=1):
        code = f"BIN-{number:02d}"
        if code in by_code:
            continue
        zone = semantic_zones[(number - 1) % len(semantic_zones)]
        location = existing_zones[zone]
        stream = STREAMS[(number - 5) % len(STREAMS)]
        capacity = 65 + ((number * 13) % 56)
        item = Bin(code=code, name=name, capacity_kg=capacity, fill_kg=0,
                   target_stream=stream, status="EMPTY", location_id=location.id)
        db.add(item)
        db.flush()
        by_code[code] = item

    db.flush()
    # Install the original demo seed only in a genuinely empty event store. Existing
    # uploaded/user records are never deleted, rewritten, or duplicated.
    if db.query(WasteEvent).count() == 0:
        bins = [by_code[f"BIN-{n:02d}"] for n in range(1, 5)]
        historical = [("Plastic", 40, -9), ("Metal", 40, -9), ("Paper", 15, -1)]
        current = [
            ("Paper", 12, 1, 0), ("Cardboard", 9, 1, 0), ("Plastic", 13, 1, 0),
            ("Organic", 8, 2, 0), ("Plastic", 14, 2, 0), ("Organic", 5, 2, 0),
            ("Glass", 7, 3, 0), ("Organic", 6, 3, 0), ("Glass", 4, 3, 0),
        ]
        source_ids = []
        for category, weight, hours in historical:
            created = now() + timedelta(hours=hours)
            event = WasteEvent(object_name=f"{category} item", category=category,
                               confidence=.9, weight_kg=weight,
                               source="SIMULATED DEMO DATA", bin_id=bins[0].id,
                               created_at=created)
            db.add(event)
            db.flush()
            source_ids.append(event.id)
            db.add_all([
                WasteFlowEvent(stage="detection", quantity_kg=weight,
                               source_event_id=event.id, created_at=created),
                WasteFlowEvent(stage="segregation", quantity_kg=weight * .89,
                               source_event_id=event.id, created_at=created),
            ])
        for index, (category, weight, bin_index, hours) in enumerate(current):
            created = now() + timedelta(hours=hours)
            event = WasteEvent(object_name=f"{category} item", category=category,
                               confidence=.82 + index * .01, weight_kg=weight,
                               source="SIMULATED DEMO DATA", bin_id=bins[bin_index].id,
                               created_at=created)
            db.add(event)
            db.flush()
            db.add_all([
                WasteFlowEvent(stage="detection", quantity_kg=weight,
                               source_event_id=event.id, created_at=created),
                WasteFlowEvent(stage="segregation", quantity_kg=weight * .89,
                               source_event_id=event.id, created_at=created),
            ])
        created = now() - timedelta(hours=8)
        collection = Collection(bin_id=bins[0].id, weight_kg=80,
                                collected_by="SIMULATED DEMO DATA",
                                notes="Seeded historical batch sourced from WasteEvents " + ",".join(map(str, source_ids)),
                                status="COMPLETED", created_at=created)
        db.add(collection)
        db.flush()
        db.add(WasteFlowEvent(stage="collection", quantity_kg=80,
                              source_event_id=collection.id, created_at=created))
        sorting = SortingEvent(collection_id=collection.id, input_kg=80, sorted_kg=70,
                               rejected_kg=10, category="mixed",
                               created_at=now() - timedelta(hours=7))
        db.add(sorting)
        db.flush()
        db.add(WasteFlowEvent(stage="sorting", quantity_kg=70,
                              source_event_id=sorting.id, created_at=sorting.created_at))
        recycling = RecyclingEvent(category="Mixed recyclables", input_kg=60,
                                   recovered_kg=50, rejected_kg=10, contamination=8,
                                   destination="SIMULATED recovery line", sorting_id=sorting.id,
                                   created_at=now() - timedelta(hours=6))
        db.add(recycling)
        db.flush()
        for stage, quantity in [("recycling", 60), ("recovery", 50), ("disposal", 10)]:
            db.add(WasteFlowEvent(stage=stage, quantity_kg=quantity,
                                  source_event_id=recycling.id,
                                  created_at=recycling.created_at))
        db.flush()
        for item in bins:
            latest = (db.query(Collection).filter(Collection.bin_id == item.id)
                      .order_by(Collection.created_at.desc()).first())
            query = db.query(WasteEvent).filter(WasteEvent.bin_id == item.id)
            if latest:
                query = query.filter(WasteEvent.created_at > latest.created_at)
            item.fill_kg = round(sum(event.weight_kg for event in query.all()), 3)
            item.status = "EMPTY" if item.fill_kg == 0 else "LOW"

    if not db.query(Alert).filter(Alert.title == "SIMULATED DEMO DATA").first():
        db.add(Alert(severity="warning", title="SIMULATED DEMO DATA",
                     detail="Seeded records are software-generated for demonstration, not sensor measurements."))
    db.commit()
