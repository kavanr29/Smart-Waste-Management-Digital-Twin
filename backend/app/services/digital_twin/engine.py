from __future__ import annotations


def fill_percentage(bin) -> float:
    capacity = float(getattr(bin, "capacity_kg", 0.0) or 0.0)
    if capacity <= 0:
        return 0.0
    fill = max(0.0, float(getattr(bin, "fill_kg", 0.0) or 0.0))
    return round(max(0.0, min(100.0, fill / capacity * 100.0)), 1)


def status(fill_pct: float) -> str:
    """Documented semantic fill status thresholds; overflow risk is a separate forecast."""
    fill = max(0.0, min(100.0, float(fill_pct or 0.0)))
    if fill <= 0.0:
        return "EMPTY"
    if fill < 25.0:
        return "LOW"
    if fill < 50.0:
        return "MEDIUM"
    if fill < 75.0:
        return "HIGH"
    if fill < 90.0:
        return "VERY HIGH"
    return "CRITICAL"


def overflow(bin) -> dict:
    capacity = float(getattr(bin, "capacity_kg", 0.0) or 0.0)
    fill = max(0.0, float(getattr(bin, "fill_kg", 0.0) or 0.0))
    rate_val = getattr(bin, "accumulation_rate", None)
    if rate_val is None:
        rate = 0.0
    else:
        try:
            rate = float(rate_val)
        except (ValueError, TypeError):
            rate = 0.0

    if capacity <= 0:
        return {"hours": None, "risk": None, "label": "INVALID CAPACITY"}
    if fill >= capacity:
        return {"hours": 0.0, "risk": 100.0, "label": "AT CAPACITY"}
    if fill <= 0.0:
        return {"hours": None, "risk": 0.0, "label": "EMPTY BIN"}
    if rate_val is None or rate <= 0.0:
        return {"hours": None, "risk": None, "label": "INSUFFICIENT DATA"}

    remaining = max(0.0, capacity - fill)
    hours = (remaining / rate) * 24.0
    risk = max(0.0, min(100.0, (1.0 - hours / 48.0) * 100.0))
    return {"hours": round(hours, 1), "risk": round(risk, 1), "label": "PREDICTED"}


def priority(bin, overflow_forecast: dict | None = None) -> dict:
    """Documented, deterministic, bounded collection priority score (0 to 100).

    Invariants:
    - If bin has zero fill, priority is strictly 0.0 and LOW.
    - An empty bin is NEVER marked CRITICAL or collection-required solely because of historical records.
    - Operational weights: Fill (50%), Accumulation Pressure (25%), Contamination (15%), Overflow Urgency (10%).
    """
    fill_pct = fill_percentage(bin)
    fill_kg = max(0.0, float(getattr(bin, "fill_kg", 0.0) or 0.0))
    capacity = float(getattr(bin, "capacity_kg", 100.0) or 100.0)
    rate_val = getattr(bin, "accumulation_rate", None)
    if rate_val is None:
        rate = 0.0
    else:
        try:
            rate = float(rate_val)
        except (ValueError, TypeError):
            rate = 0.0
    contam = float(getattr(bin, "contamination", 0.0) or 0.0)

    # Empty bin invariant: never critical, never required
    if fill_kg <= 0.0 or fill_pct <= 0.0:
        return {
            "score": 0.0,
            "label": "LOW",
            "is_required": False,
            "reasons": [
                "Current fill is 0.0% (EMPTY)",
                "No collection required for an empty bin",
            ],
        }

    # Signal 1: Fill component (0-100)
    s_fill = fill_pct

    # Signal 2: Accumulation pressure relative to capacity (0-100)
    if capacity > 0 and rate > 0:
        daily_capacity_fraction = (rate / capacity) * 100.0
        s_accum = min(100.0, daily_capacity_fraction * 1.5)
    else:
        s_accum = 0.0

    # Signal 3: Contamination rate (0-100)
    s_contam = max(0.0, min(100.0, contam))

    # Signal 4: Overflow urgency (0-100)
    if overflow_forecast is None:
        overflow_forecast = overflow(bin)
    hours = overflow_forecast.get("hours")
    if hours is not None and hours <= 12.0:
        s_urgency = 100.0
    elif hours is not None and hours <= 24.0:
        s_urgency = 75.0
    elif hours is not None and hours <= 48.0:
        s_urgency = 40.0
    else:
        s_urgency = 0.0

    raw_score = 0.50 * s_fill + 0.25 * s_accum + 0.15 * s_contam + 0.10 * s_urgency
    score = round(min(100.0, max(0.0, raw_score)), 1)

    if (score >= 80.0 and fill_pct >= 50.0) or fill_pct >= 90.0:
        label = "CRITICAL"
        is_required = True
    elif score >= 60.0 or fill_pct >= 75.0:
        label = "HIGH"
        is_required = True
    elif score >= 35.0 or fill_pct >= 50.0:
        label = "MEDIUM"
        is_required = False
    else:
        label = "LOW"
        is_required = False

    reasons = [f"Current fill is {fill_pct:.1f}% ({fill_kg:.2f}/{capacity:.1f} kg)"]
    if rate > 0:
        reasons.append(f"Accumulation rate: {rate:.1f} kg/day ({s_accum:.0f}% capacity pressure)")
    else:
        reasons.append("No active accumulation rate measured")

    if contam > 0:
        reasons.append(f"Current uncollected contamination: {contam:.1f}%")
    else:
        reasons.append("No uncollected contamination detected")

    if hours is not None:
        reasons.append(f"Predicted overflow in {hours:.1f}h")
    else:
        reasons.append(f"Overflow risk: {overflow_forecast.get('label', 'INSUFFICIENT DATA')}")

    return {
        "score": score,
        "label": label,
        "is_required": is_required,
        "reasons": reasons,
    }
