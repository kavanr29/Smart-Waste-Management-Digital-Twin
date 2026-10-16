import json
import re
import urllib.error
import urllib.request

from ...core import settings

SYSTEM_PROMPT = (
    "You are the WasteWise AI campus waste-management operations copilot. Use only the supplied "
    "structured application context and supplied simulation result. Never invent statistics, bin IDs, "
    "percentages, quantities, locations, dates, or outcomes. If a requested value is absent or null, "
    "write INSUFFICIENT DATA. Structure recommendations as OBSERVATION, EVIDENCE, RECOMMENDATION, "
    "EXPECTED OPERATIONAL EFFECT, and LIMITATIONS / UNCERTAINTY. Distinguish DATABASE FACTS, "
    "SIMULATION RESULTS, and GENAI INTERPRETATION."
)


def _request(base_url, api_key, model, prompt, context):
    base = base_url.rstrip("/")
    is_anthropic = "anthropic.com" in base.lower()
    if is_anthropic:
        url = base if base.endswith("/messages") else base + "/v1/messages"
        body = {
            "model": model,
            "max_tokens": 1500,
            "temperature": 0.2,
            "system": SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": prompt + "\nSTRUCTURED_CONTEXT:\n" + json.dumps(context),
                }
            ],
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
    else:
        url = base if base.endswith("/chat/completions") else base + "/chat/completions"
        body = {
            "model": model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": prompt + "\nSTRUCTURED_CONTEXT:\n" + json.dumps(context),
                },
            ],
        }
        headers = {
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        }
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        payload = json.loads(response.read().decode())
    if is_anthropic:
        blocks = payload.get("content") or []
        message = "".join(
            block.get("text", "") for block in blocks if isinstance(block, dict)
        ).strip()
    else:
        message = (
            ((payload.get("choices") or [{}])[0]).get("message") or {}
        ).get("content", "")
    if not message:
        raise ValueError("Provider returned an empty response.")
    return message


def ask(prompt, context):
    if not settings.genai_api_key:
        return {
            "mode": "DEMO / GROUNDED RULES",
            "message": fallback(prompt, context),
            "source": "grounded local fallback",
        }
    try:
        message = _request(
            settings.genai_base_url,
            settings.genai_api_key,
            settings.genai_model,
            prompt,
            context,
        )
        return {
            "mode": "LIVE GENAI",
            "message": message,
            "source": "configured provider",
        }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        return {
            "mode": "DEMO / GROUNDED FALLBACK",
            "message": fallback(prompt, context),
            "warning": f"Live GenAI unavailable (HTTP {exc.code}). Grounded fallback shown. Provider detail: {detail}",
            "source": "grounded local fallback",
        }
    except Exception as exc:
        return {
            "mode": "DEMO / GROUNDED FALLBACK",
            "message": fallback(prompt, context),
            "warning": f"Live GenAI unavailable. Check provider configuration. Grounded fallback shown. ({exc})",
            "source": "grounded local fallback",
        }


def _section(observation, evidence, recommendation, effect, uncertainty=None):
    text = (
        f"OBSERVATION\n{observation}\n\n"
        f"EVIDENCE\n{evidence}\n\n"
        f"RECOMMENDATION\n{recommendation}\n\n"
        f"EXPECTED OPERATIONAL EFFECT\n{effect}"
    )
    if uncertainty:
        text += f"\n\nLIMITATIONS / UNCERTAINTY\n{uncertainty}"
    return text


def _kg(value):
    return "INSUFFICIENT DATA" if value is None else f"{float(value):.1f} kg"


def fallback(prompt, ctx):
    text = (prompt or "").lower()
    bins = ctx.get("bins") or []
    waste = ctx.get("waste_statistics") or {}
    recycling = ctx.get("recycling_statistics") or {}
    collection = ctx.get("collection_statistics") or {}
    contamination = (ctx.get("contamination") or {}).get("rate")
    sorting = ctx.get("sorting_statistics") or {}
    simulation = ctx.get("simulation_result")

    if not bins and not waste:
        return "INSUFFICIENT DATA: no persisted Digital Twin context is available."

    # Priority bins: non-empty bins requiring collection
    req_bins = [
        item for item in bins if item.get("collection_status") == "REQUIRED"
    ]
    non_empty = [item for item in bins if (item.get("fill_kg") or 0) > 0]
    top_bin = max(
        non_empty or bins,
        key=lambda item: item.get("priority_score") or 0,
        default=None,
    )

    by_category = waste.get("by_category") or {}
    category_top = (
        max(by_category.items(), key=lambda item: item[1], default=None)
        if by_category
        else None
    )
    by_location = waste.get("by_location") or {}
    location_top = (
        max(by_location.items(), key=lambda item: item[1], default=None)
        if by_location
        else None
    )

    # Answer common factual questions directly instead of returning the
    # complete operations template for every unclassified prompt.
    if any(word in text for word in ("how many bin", "number of bin", "bins are there", "bin count")):
        return _section(
            f"The Digital Twin currently tracks {len(bins)} campus bins.",
            f"DATABASE FACTS: {len(bins)} bin records are available in the current reporting window.",
            "Use the Digital Twin bin list to review fill and collection status by zone.",
            "Keeps the collection queue aligned with the current set of monitored assets.",
            "This is a count of persisted bin records, not a count of physical containers that may be offline.",
        )

    if any(word in text for word in ("total waste", "waste generated", "how much waste", "waste quantity")):
        total = waste.get("total_waste_kg")
        return _section(
            f"Recorded waste in the current reporting window is {_kg(total)}.",
            f"DATABASE FACTS: Reporting window total: {_kg(total)}; recyclable portion: {_kg(waste.get('recyclable_waste_kg'))}.",
            "Use the category and location breakdowns to target reduction and segregation work.",
            "Prioritizes interventions against measured waste volume rather than assumptions.",
            "The value includes only persisted waste events inside the configured reporting window.",
        )

    if any(word in text for word in ("where", "location", "zone")) and location_top:
        return _section(
            f"{location_top[0]} has the highest recorded waste volume in the current window.",
            f"DATABASE FACTS: {location_top[0]} accounts for {location_top[1]:.1f} kg of recorded waste.",
            f"Review receptacle placement and collection coverage in {location_top[0]}.",
            "Targets operational attention at the location with the strongest measured waste signal.",
            "The location ranking reflects persisted events and may not represent unrecorded waste.",
        )

    if any(word in text for word in ("which type", "what type", "most waste", "highest category", "largest category")) and category_top:
        return _section(
            f"{category_top[0]} is the highest-volume recorded waste category.",
            f"DATABASE FACTS: {category_top[0]} accounts for {category_top[1]:.1f} kg in the current window.",
            f"Review source segregation for the {category_top[0]} stream and confirm the assigned bin signage.",
            "Improves source separation for the category creating the largest measured material volume.",
            "The ranking is based on recorded events, not a complete physical audit.",
        )

    # 1. Check for specific bin query
    bin_match = re.search(r"\bbin-(\d+)\b", text, re.IGNORECASE)
    if bin_match and not any(
        w in text for w in ("report", "daily action plan", "simulation")
    ):
        code_matched = f"BIN-{int(bin_match.group(1)):02d}"
        target_bin = next(
            (b for b in bins if b.get("code", "").upper() == code_matched), None
        )
        if target_bin:
            fill_pct = target_bin.get("fill_pct", 0)
            fill_kg = target_bin.get("fill_kg", 0)
            cap = target_bin.get("capacity_kg", 0)
            p_label = target_bin.get("priority_label", "LOW")
            p_score = target_bin.get("priority_score", 0)
            status_val = target_bin.get("status", "EMPTY")
            c_rate = target_bin.get("contamination", 0)
            reasons = " · ".join(
                target_bin.get("priority_reasons", [])
            ) or "No specific reasons provided."
            overflow_h = target_bin.get("overflow_hours")
            overflow_str = (
                f"{overflow_h:.1f} hours"
                if overflow_h is not None
                else target_bin.get("overflow_status", "INSUFFICIENT DATA")
            )
            return _section(
                f"Virtual bin {code_matched} ({target_bin.get('name')}, {target_bin.get('zone')}) is currently {fill_pct:.1f}% full ({status_val}) with priority {p_label}.",
                f"DATABASE FACTS: Fill weight: {fill_kg:.2f} kg / {cap:.1f} kg. Contamination: {c_rate:.1f}%. Priority score: {p_score:.1f}/100. Overflow forecast: {overflow_str}. Priority rationale: {reasons}.",
                (
                    f"Dispatch collection crew to {code_matched}."
                    if target_bin.get("collection_status") == "REQUIRED"
                    else f"Keep {code_matched} in monitoring rotation; no immediate collection is required while fill remains low."
                ),
                (
                    f"Empties {fill_kg:.2f} kg of {target_bin.get('target_stream')} material into the collection queue."
                    if fill_kg > 0
                    else "Preserves crew capacity for bins that genuinely require collection."
                ),
                "Priority score is computed from fill, normalized accumulation pressure, contamination, and overflow urgency.",
            )

    # 2. Check for what-if simulation query
    question_sim = any(
        w in text for w in ("simulation", "what-if", "what if", "scenario")
    )
    if question_sim:
        if not simulation:
            return "INSUFFICIENT DATA: no deterministic simulation result was supplied."
        before = simulation.get("before") or {}
        after = simulation.get("after") or {}
        delta = simulation.get("delta") or {}
        return _section(
            "The deterministic what-if scenario projects the operational impact of intervention parameters.",
            (
                f"DATABASE FACTS baseline: Overflow risk {before.get('overflow_risk', 'INSUFFICIENT DATA')}%, "
                f"Recycling efficiency {before.get('recycling_efficiency', 'INSUFFICIENT DATA')}%, "
                f"Collection frequency {before.get('collection_frequency', 'INSUFFICIENT DATA')}x/day, "
                f"Average fill {before.get('average_fill_pct', 'INSUFFICIENT DATA')}%, "
                f"Contamination {before.get('contamination', 'INSUFFICIENT DATA')}%. "
                f"SIMULATION RESULTS: Overflow risk {after.get('overflow_risk', 'INSUFFICIENT DATA')}% ({delta.get('overflow_risk', 0):+0.1f}%), "
                f"Recycling efficiency {after.get('recycling_efficiency', 'INSUFFICIENT DATA')}% ({delta.get('recycling_efficiency', 0):+0.1f}%), "
                f"Contamination {after.get('contamination', 'INSUFFICIENT DATA')}% ({delta.get('contamination', 0):+0.1f}%), "
                f"Workload: {after.get('workload_trips_week', 'INSUFFICIENT DATA')} trips/week ({delta.get('workload_trips_week', 0):+0.1f} trips)."
            ),
            "Evaluate whether the projected recovery gain justifies the additional collection workload before updating shift rosters.",
            "Projections are deterministic estimates derived from model equations, not guaranteed future measurements.",
            "Assumes uniform generation across campus zones and steady-state crew availability.",
        )

    # 3. Check for full report query
    question_report = any(
        w in text
        for w in (
            "report",
            "daily action plan",
            "sustainability report",
            "full report",
        )
    )
    if question_report:
        tot_waste = _kg(waste.get("total_waste_kg"))
        rec_waste = _kg(waste.get("recyclable_waste_kg"))
        comp_str = (
            ", ".join(
                f"{k}: {v:.1f} kg"
                for k, v in sorted(
                    by_category.items(), key=lambda x: x[1], reverse=True
                )
            )
            or "INSUFFICIENT DATA"
        )
        rec_eff = (
            f"{recycling.get('recycling_efficiency'):.1f}%"
            if recycling.get("recycling_efficiency") is not None
            else "INSUFFICIENT DATA"
        )
        contam_str = (
            f"{contamination:.1f}%"
            if contamination is not None
            else "INSUFFICIENT DATA"
        )
        aff_zones = (
            ", ".join(ctx.get("affected_zones") or [])
            or "No affected zones currently flagged."
        )

        col_req_codes = (
            ", ".join(b["code"] for b in req_bins)
            if req_bins
            else "None (all bins currently within capacity)"
        )
        top_code = top_bin["code"] if top_bin else "None"

        return (
            "========================================================\n"
            "WASTEWISE AI — CAMPUS WASTE OPERATIONS REPORT\n"
            "========================================================\n\n"
            "A. CAMPUS OVERVIEW\n"
            f"DATABASE FACTS: Total recorded waste in reporting window: {tot_waste}.\n"
            f"Recyclable stream volume: {rec_waste}.\n"
            f"Active virtual bins: {len(bins)} bins across semantic campus zones.\n"
            f"Recorded collection events: {collection.get('collection_events', 0)} ({_kg(collection.get('collected_weight_kg'))} collected).\n\n"
            "B. SEGREGATION & CONTAMINATION\n"
            f"Stream composition: {comp_str}.\n"
            f"Measured cross-stream contamination: {contam_str}.\n"
            f"Zones requiring segregation attention: {aff_zones}.\n"
            "Observation: Waste mismatches occur primarily in high-traffic common areas.\n\n"
            "C. COLLECTION QUEUE & OVERFLOW RISKS\n"
            f"Bins requiring collection: {col_req_codes}.\n"
            f"Highest priority bin: {top_code} (fill: {top_bin.get('fill_pct', 0) if top_bin else 0}%, score: {top_bin.get('priority_score', 0) if top_bin else 0:.1f}).\n"
            f"Suggested operational order: {col_req_codes if req_bins else 'Routine monitoring rotation'}.\n\n"
            "D. RECYCLING & MATERIAL RECOVERY\n"
            f"Recyclable input: {_kg(recycling.get('recyclable_input_kg'))}.\n"
            f"Recovered material: {_kg(recycling.get('recovered_kg'))}.\n"
            f"Rejected material: {_kg(recycling.get('rejected_kg'))}.\n"
            f"Recovery efficiency: {rec_eff}.\n"
            f"Sorted material available for processing: {_kg(sorting.get('sorted_kg'))}.\n\n"
            "E. RECOMMENDED ACTION PLAN\n"
            f"1. Immediate (Next 2 Hours): Empty flagged bins [{col_req_codes}] to avoid overflow.\n"
            "2. Shift Plan (Next 24 Hours): Process available sorted batches through recycling recovery.\n"
            f"3. Operational Improvement: Deploy signage in {aff_zones} to reduce cross-stream contamination.\n\n"
            "F. DATA QUALITY & UNCERTAINTY\n"
            "DATABASE FACTS: Metrics are computed strictly from persisted SQLite records.\n"
            "SIMULATED RECORDS: Seeded background events are software-generated for demonstration.\n"
            "UNCERTAINTY: Overflow forecasts depend on linear extrapolation of 7-day accumulation rates."
        )

    # 4. Specific operational topic queries
    question_seg = any(
        w in text for w in ("segregat", "contamination", "mismatch")
    )
    question_recycle = any(w in text for w in ("recycl", "recovery", "recover"))
    question_collect = any(
        w in text for w in ("collect", "overflow", "schedule", "priority", "bin")
    )

    sections = []

    if question_seg:
        issue = (
            f"Recorded contamination is {contamination:.1f}%."
            if contamination is not None
            else "INSUFFICIENT DATA for campus contamination rate."
        )
        if category_top:
            issue += f" Highest category by volume: {category_top[0]} ({category_top[1]:.1f} kg)."
        aff_str = ", ".join(ctx.get("affected_zones") or []) or "None flagged"
        sections.append(
            (
                "SEGREGATION",
                _section(
                    "Source segregation review is indicated for streams with recorded mismatches.",
                    f"DATABASE FACTS: {issue} Affected zones from current bin states: {aff_str}.",
                    "Reinforce dual-stream bins in affected zones and review receptacle labeling.",
                    "Reduces cross-stream contamination before collection batches reach the sorting line.",
                    "Contamination is computed from uncollected waste deposited since the last collection.",
                ),
            )
        )

    if question_recycle:
        rec_eff_str = (
            f"{recycling.get('recycling_efficiency'):.1f}%"
            if recycling.get("recycling_efficiency") is not None
            else "INSUFFICIENT DATA (no recycling batches in window)"
        )
        sections.append(
            (
                "RECYCLING",
                _section(
                    "Recovery efficiency depends on clean sorted material inputs.",
                    f"DATABASE FACTS: Recovery efficiency: {rec_eff_str}; Recovered {_kg(recycling.get('recovered_kg'))} from {_kg(recycling.get('recyclable_input_kg'))} input; Rejected {_kg(recycling.get('rejected_kg'))}. Sorted material ready: {_kg(sorting.get('sorted_kg'))}.",
                    "Process available sorted batches and feed clean mono-streams into dedicated recycling recovery.",
                    "Improves mass recovery yield and prevents recyclable degradation in storage.",
                    "Facility throughput limits and physical reject rates constrain maximum recovery.",
                ),
            )
        )

    if question_collect:
        if req_bins:
            b_list = ", ".join(b["code"] for b in req_bins)
            top_desc = f"{top_bin['code']} ({top_bin.get('zone')}) is {top_bin.get('fill_pct', 0)}% full with priority {top_bin.get('priority_label')}."
            act = f"Dispatch collection crew to {top_bin['code']} first, followed by remaining high-priority bins ({b_list})."
        elif top_bin and (top_bin.get("fill_kg") or 0) > 0:
            top_desc = f"All bins are currently below critical thresholds. Highest fill: {top_bin['code']} at {top_bin.get('fill_pct', 0):.1f}% fill."
            act = f"Monitor {top_bin['code']}; no urgent collection dispatch required."
        else:
            top_desc = "All campus bins are currently empty or have near-zero fill."
            act = "No collections required at this time. Maintain standard monitoring rotation."

        sections.append(
            (
                "COLLECTION",
                _section(
                    "Collection routing is sequenced strictly by current uncollected fill and priority score.",
                    f"DATABASE FACTS: {top_desc} Bins requiring collection: {len(req_bins)}. Recorded collections: {collection.get('collection_events', 0)} ({_kg(collection.get('collected_weight_kg'))}).",
                    act,
                    "Prevents bin overflow while optimizing operational crew routes and vehicle trips.",
                    "Empty bins are excluded from collection; priority never exceeds LOW for an empty bin.",
                ),
            )
        )

    if not sections:
        return _section(
            "The question does not match a supported persisted waste-management signal.",
            "DATABASE FACTS: The grounded assistant can answer bin, collection, overflow, segregation, recycling, recovery, waste-volume, category, location, report, and simulation questions.",
            "Ask about a specific bin, waste total, category, location, collection queue, recycling result, or simulation scenario.",
            "Keeps recommendations tied to measurements available in the Digital Twin.",
            "No live provider is configured, so unsupported questions are not answered from general world knowledge.",
        )

    return "\n\n".join(f"{title}\n{body}" for title, body in sections)
