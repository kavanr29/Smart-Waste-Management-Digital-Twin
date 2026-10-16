from __future__ import annotations
from math import hypot
from typing import Optional, Set

from .digital_twin.engine import fill_percentage, overflow, priority, status

RECYCLABLE = {"plastic", "paper", "cardboard", "glass", "metal", "e-waste"}
ZONE_COORDINATES = {
    "academic zone": (-9, -6), "hostel zone": (8, -7), "cafeteria zone": (-8, 6),
    "sports zone": (9, 10), "parking zone": (-15, 1), "administration zone": (-14, -8),
    "library zone": (0, -11), "garden / outdoor zone": (14, -1), "central plaza": (0, 3),
    "block a": (-9, -6), "block b": (0, -11), "block c": (-8, 6),
}


def compatible_categories(target_stream: Optional[str]) -> set[str]:
    stream = (target_stream or "recyclable").strip().lower()
    return {
        "recyclable": RECYCLABLE, "recycling": RECYCLABLE,
        "paper": {"paper", "cardboard"}, "cardboard": {"paper", "cardboard"},
        "plastic": {"plastic"}, "glass": {"glass"}, "metal": {"metal"},
        "organic": {"organic"}, "food": {"organic"}, "e-waste": {"e-waste"},
        "hazardous": {"hazardous"}, "other": {"other", "styrofoam"}, "general": {"other", "styrofoam"},
        "non-recyclable": {"other", "styrofoam", "hazardous"},
    }.get(stream, RECYCLABLE)


def is_compatible(target_stream: Optional[str], category: str) -> bool:
    return category.strip().lower() in compatible_categories(target_stream)


def _xy(zone: str):
    return ZONE_COORDINATES.get((zone or "").strip().lower(), (0, 0))


def recommend_bins(bins, category: str, weight_kg: float, current_zone: str = "Central Plaza"):
    """Rank only existing, compatible, available bins without conflating risk and fill."""
    category_key = category.strip().lower()
    origin = _xy(current_zone)
    candidates = []
    for item in bins:
        if not is_compatible(item.target_stream, category_key):
            continue
        if (item.status or "").upper() in {"OUT_OF_SERVICE", "MAINTENANCE", "INACTIVE"}:
            continue
        capacity = float(item.capacity_kg or 0)
        fill_kg = float(item.fill_kg or 0)
        available = max(0.0, capacity - fill_kg)
        if available + 1e-9 < weight_kg:
            continue
        zone = item.location.zone if item.location else "Unassigned"
        x, y = _xy(zone)
        distance = round(hypot(x - origin[0], y - origin[1]), 1)
        fill_pct = fill_percentage(item)
        contamination = float(item.contamination or 0)
        forecast = overflow(item)
        overflow_risk = forecast["risk"]
        risk_score = overflow_risk if overflow_risk is not None else 0.0
        p_res = priority(item, overflow_forecast=forecast)
        operational_priority = p_res["score"]
        score = distance * .35 + fill_pct * .35 + risk_score * .2 + contamination * .7 + operational_priority * .1
        candidates.append({
            "id": item.id, "code": item.code, "name": item.name, "zone": zone,
            "location": item.location.name if item.location else "Unassigned",
            "target_stream": item.target_stream, "fill_pct": round(fill_pct, 1),
            "fill_kg": round(fill_kg, 3), "capacity_kg": round(capacity, 3),
            "available_kg": round(available, 3), "status": status(fill_pct),
            "contamination": round(contamination, 1), "overflow_risk": overflow_risk,
            "overflow_hours": forecast["hours"], "overflow_status": forecast["label"],
            "priority_score": round(operational_priority, 1), "approx_distance_m": distance,
            "score": round(score, 2),
            "reasons": [f"Accepts {category}", f"{available:.2f} kg available", f"{fill_pct:.1f}% fill ({status(fill_pct)})",
                        f"{contamination:.1f}% recorded contamination", f"{distance:.1f} m approximate distance",
                        f"Overflow risk: {overflow_risk:.1f}%" if overflow_risk is not None else "Overflow risk: INSUFFICIENT DATA"],
        })
    candidates.sort(key=lambda row: (row["score"], row["id"]))
    return candidates
