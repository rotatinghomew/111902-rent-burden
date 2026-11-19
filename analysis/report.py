"""汇总季度调查分析：负担分布、趋势、定档、预算与取证计划。"""

from .estimate import Estimator, region_specs
from .evidence import EvidenceSet
from .tiers import assess_request, classify_options


def build_analysis(sources, office=None, as_of=None, extra_receipts=()):
    """完整分析。office 提供台账状态（影响余额与选项划分）。"""
    if office is not None:
        extra_receipts = list(extra_receipts) + list(office.state["extra_receipts"])
    ev = EvidenceSet(sources, as_of=as_of, extra_receipts=extra_receipts)
    est = Estimator(ev)
    specs = region_specs(sources)

    regions = {}
    for name, spec in specs.items():
        regions[name] = {
            "proportion": {q: est.proportion_band(spec, q) for q in (1, 2)},
            "distribution": {q: est.distribution(spec, q) for q in (1, 2)},
            "median_change": est.median_change(spec),
            "trend": est.trend(spec),
            "comparability": est.comparability(spec),
        }

    requests = {rid: {**r, "_pool": sources.pool_of(r["area"])}
                for rid, r in sources.requests.items()}
    assessments = {}
    for rid, req in requests.items():
        spec = specs[req["area"]]
        assessments[rid] = assess_request(req, spec, regions[req["area"]]["trend"])

    pool_available = office.pool_available() if office else {
        p: b["total"] for p, b in sources.budgets.items()}
    option_states, pool_notes = classify_options(assessments, requests, pool_available)

    cls = ev.report_classification()
    scope = {
        "as_of": as_of or "全部有效证据",
        "sampled_households": {q: sum(1 for r in ev.records.values() if r.quarter == q)
                               for q in ev.quarters()},
        "sampled_psus": {q: len({r.psu for r in ev.records.values() if r.quarter == q})
                         for q in ev.quarters()},
        "代理重复报送": len(cls["代理重复报送"]),
        "共同分担住户": len(cls["共同分担住户"]),
        "只缺访谈答案": len(cls["只缺访谈答案"]),
        "有效回执": [r["evidence"] for r in ev.receipts],
        "冲突": ev.conflicts,
    }
    return {
        "evidence": ev,
        "specs": specs,
        "regions": regions,
        "requests": requests,
        "assessments": assessments,
        "option_states": option_states,
        "pool_notes": pool_notes,
        "pool_available": pool_available,
        "ledger": list(office.entries().values()) if office else [],
        "scope": scope,
    }


def retrospective_snapshot(sources, office, entry):
    """为源台账条目重建当时的调查范围与理由（按基准日截取证据）。"""
    analysis = build_analysis(sources, office, as_of=entry["basis"])
    a = analysis["assessments"].get(entry["request"])
    rationale = None
    if a is not None:
        tier = None
        req = analysis["requests"][entry["request"]]
        for k, amount in enumerate(req["tiers"], start=1):
            if amount == entry["amount"]:
                tier = k
        rationale = {
            "支持下限pp": a["floor_pp"],
            "潜在上限pp": a["ceiling_pp"],
            "档位": tier,
            "该档需pp": None if tier is None else a["tiers"][tier]["need_pp"],
            "精度可定档": a["precision_ok"],
        }
    return {
        "at": entry["basis"],
        "as_of": entry["basis"],
        "request": entry["request"],
        "重建说明": "源台账条目，按基准日证据重建",
        "调查范围": {
            "抽样住户": analysis["scope"]["sampled_households"],
            "入样PSU": analysis["scope"]["sampled_psus"],
            "代理重复报送": analysis["scope"]["代理重复报送"],
            "共同分担住户": analysis["scope"]["共同分担住户"],
            "只缺访谈答案": analysis["scope"]["只缺访谈答案"],
            "有效回执": analysis["scope"]["有效回执"],
            "冲突": analysis["scope"]["冲突"],
        },
        "理由": rationale,
        "evidence_ids": analysis["scope"]["有效回执"],
    }


def snapshot_for(office, analysis, request_id, tier, at):
    """签收/预留时留存的调查范围与理由。"""
    a = analysis["assessments"][request_id]
    req = analysis["requests"][request_id]
    pool = req["_pool"]
    return {
        "at": at,
        "as_of": analysis["scope"]["as_of"],
        "request": request_id,
        "area": req["area"],
        "tier": tier,
        "amount": req["tiers"][tier - 1],
        "调查范围": {
            "抽样住户": analysis["scope"]["sampled_households"],
            "入样PSU": analysis["scope"]["sampled_psus"],
            "代理重复报送": analysis["scope"]["代理重复报送"],
            "共同分担住户": analysis["scope"]["共同分担住户"],
            "只缺访谈答案": analysis["scope"]["只缺访谈答案"],
            "有效回执": analysis["scope"]["有效回执"],
            "冲突": analysis["scope"]["冲突"],
        },
        "理由": {
            "支持下限pp": a["floor_pp"],
            "潜在上限pp": a["ceiling_pp"],
            "该档需pp": a["tiers"][tier]["need_pp"],
            "精度可定档": a["precision_ok"],
            "资金池": pool,
            "签收前池余额": office.pool_available().get(pool) if office else None,
        },
        "evidence_ids": analysis["scope"]["有效回执"],
    }
