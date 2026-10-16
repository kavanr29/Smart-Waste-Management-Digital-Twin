def run_simulation(
    bins,
    frequency: float = 2.0,
    segregation: float = 15.0,
    recovery: float = 0.0,
    current_recycling: float = 0.0,
    current_frequency: float = 0.0,
) -> dict:
    """Deterministic, mathematically sound what-if simulation engine.

    Models operational interventions:
    - Scenario A: Frequency change -> affects uncollected fill, overflow exposure, and collection workload.
    - Scenario B: Segregation improvement -> reduces contamination and increases recyclable stream purity.
    - Scenario C: Recovery improvement -> boosts recycling line recovery efficiency.
    - Scenario D: Combined operational interventions.
    """
    n = max(len(bins), 1)

    # 1. Baseline Calculations from actual bin states
    total_fill = sum(float(getattr(b, "fill_kg", 0.0) or 0.0) for b in bins)
    total_cap = sum(float(getattr(b, "capacity_kg", 0.0) or 0.0) for b in bins)
    avg_fill = (total_fill / total_cap * 100.0) if total_cap > 0 else 0.0

    # Measured contamination across bins with positive fill
    non_empty_bins = [b for b in bins if float(getattr(b, "fill_kg", 0.0) or 0.0) > 0]
    if non_empty_bins:
        contamination = sum(float(getattr(b, "contamination", 0.0) or 0.0) for b in non_empty_bins) / len(non_empty_bins)
    else:
        contamination = 0.0

    # Baseline overflow risk
    overflow_risks = [
        float(getattr(b, "overflow_risk", 0.0) or 0.0)
        for b in non_empty_bins
        if getattr(b, "overflow_risk", None) is not None
    ]
    avg_overflow_risk = (sum(overflow_risks) / len(overflow_risks)) if overflow_risks else 0.0

    # Baseline workload: collection trips per week
    base_freq = max(0.0, float(current_frequency or 0.0))
    sim_freq = max(0.5, min(7.0, float(frequency or 2.0)))
    base_workload_trips = round(base_freq * 7.0, 1)
    sim_workload_trips = round(sim_freq * 7.0, 1)

    seg_improvement = max(0.0, min(50.0, float(segregation or 0.0)))
    rec_improvement = max(0.0, min(30.0, float(recovery or 0.0)))
    base_recycling = max(0.0, min(100.0, float(current_recycling or 0.0)))

    # 2. Deterministic Intervention Models
    # Collection frequency impact:
    effective_base = base_freq if base_freq > 0 else 1.0
    freq_delta = sim_freq - effective_base
    if freq_delta >= 0:
        # Increasing frequency reduces average bin fill and overflow risk
        sim_avg_fill = max(0.0, avg_fill * (1.0 - 0.20 * min(3.0, freq_delta)))
        sim_overflow_risk = max(0.0, avg_overflow_risk * (1.0 - 0.25 * min(3.0, freq_delta)))
    else:
        # Decreasing frequency increases average uncollected fill and overflow risk
        abs_delta = abs(freq_delta)
        sim_avg_fill = min(100.0, avg_fill * (1.0 + 0.25 * min(3.0, abs_delta)))
        sim_overflow_risk = min(100.0, avg_overflow_risk * (1.0 + 0.35 * min(3.0, abs_delta)))

    # Segregation improvement impact:
    sim_contamination = max(0.0, contamination * (1.0 - seg_improvement / 100.0))

    # Recycling recovery impact:
    seg_recovery_boost = seg_improvement * 0.35
    freq_recovery_boost = max(0.0, freq_delta) * 1.5
    sim_recycling = min(99.0, max(0.0, base_recycling + seg_recovery_boost + freq_recovery_boost + rec_improvement))

    # Check data sufficiency
    has_sufficient_data = len(bins) > 0 and (total_fill > 0 or base_freq > 0 or base_recycling > 0)
    data_notes = (
        "Model computed using active SQLite-backed digital twin bins."
        if has_sufficient_data
        else "INSUFFICIENT DATA: No recorded waste deposits or collection events in the current baseline window."
    )

    current = {
        "overflow_risk": round(avg_overflow_risk, 1),
        "recycling_efficiency": round(base_recycling, 1),
        "collection_frequency": round(base_freq, 2),
        "average_fill_pct": round(avg_fill, 1),
        "contamination": round(contamination, 1),
        "workload_trips_week": base_workload_trips,
    }

    after = {
        "overflow_risk": round(sim_overflow_risk, 1),
        "recycling_efficiency": round(sim_recycling, 1),
        "collection_frequency": round(sim_freq, 2),
        "average_fill_pct": round(sim_avg_fill, 1),
        "contamination": round(sim_contamination, 1),
        "workload_trips_week": sim_workload_trips,
    }

    delta = {k: round(after[k] - current[k], 1) for k in current}

    assumptions = [
        "Each +1.0 collection/day reduces average uncollected fill by ~20% and decreases overflow exposure by ~25%.",
        "Segregation improvement directly scales down contamination by the selected percentage.",
        "Cleaner segregated streams yield +0.35 points of recycling recovery per segregation percentage point.",
        "Operational recovery interventions directly add to recycling efficiency at the sorting/recycling line.",
        "Workload is deterministically estimated as frequency (times/day) * 7 operating days/week.",
    ]

    limitations = [
        "Estimates assume uniform bin deposit distribution across campus zones.",
        "Simulated improvements represent steady-state potential, not instantaneous changes.",
        "Actual recovery also depends on physical sorting line throughput and contamination thresholds.",
    ]

    return {
        "label": "SIMULATED WHAT-IF RESULT",
        "before": current,
        "after": after,
        "delta": delta,
        "current": current,
        "simulated": after,
        "assumptions": assumptions,
        "limitations": limitations,
        "data_sufficiency": {
            "sufficient": has_sufficient_data,
            "notes": data_notes,
        },
        "scenarios": {
            "A_frequency": "Increase collection frequency to mitigate overflow risk and reduce average fill.",
            "B_segregation": "Source-segregation campaigns reduce contamination and increase pure recyclable feed.",
            "C_recovery": "Upgrade sorting facility recovery efficiency without altering campus bins.",
            "D_combined": "Simultaneous frequency and segregation improvements deliver synergistic performance.",
        },
    }
