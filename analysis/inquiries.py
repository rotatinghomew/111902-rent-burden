"""取证计划：未回收派单的影响评估与安排。

一次回执可能只限制一个分摊比例，也可能撤销旧凭据；评估时按派单给出
的可能分摊区间逐一枚举（并遵循同一合同的共同约束与归属候选），看再
查一次会改变本轮仍可办理的金额，还是只关系已经签收款的复议。共用
证据由有权的街道核查，别的街道能加入，不重复占用调查工时。可查方向
按按时回收、可用工时、证据可能给出的结论与受影响款项排序；各派单
可能释放的金额建立在同一批待定事实的相斥解释上，不能简单相加。
"""

from .estimate import Estimator
from .tiers import assess_request

IMPACT_ROUND = "改变本轮可办理金额"
IMPACT_RECONSIDER = "只关系已签收款的复议"
IMPACT_NONE = "不改变本轮办理"


def unrecovered_inquiries(sources, evidence):
    """未回收的派单：状态未回收且其合同尚无有效回执。"""
    recovered_leases = {r["lease"] for r in evidence.receipts}
    out = []
    for q in sources.inquiries:
        if q["state"] == "已回收":
            continue
        if q["lease"] in recovered_leases:
            continue
        out.append(q)
    return out


def lease_households(evidence, lease):
    """合同在调查记录中的住户（当季）。"""
    return sorted({r.household: r for r in evidence.records.values()
                   if r.lease == lease}.values(), key=lambda r: r.household)


def apply_receipt(records, sources, lease, household, areas, band):
    """把一份模拟回执应用到记录上：收紧该户分摊与归属，并按同一合同
    共同约束收紧同合同其他户。返回新的 records 字典（浅拷贝+替换）。"""
    from .conventions import ratio_bounds
    from .evidence import HouseholdQuarter

    new = dict(records)
    members = [r for r in new.values() if r.lease == lease]
    shares = {}
    for r in members:
        if r.household == household:
            lo = max(r.share_lo, band[0])
            hi = min(r.share_hi, band[1])
            shares[r.household] = (lo, hi, tuple(sorted(set(r.areas) & set(areas))))
        else:
            shares[r.household] = (r.share_lo, r.share_hi, r.areas)
    # 共同约束收紧（Σ share = 1）
    for _ in range(len(members)):
        changed = False
        for h in shares:
            lo, hi, _ = shares[h]
            others_lo = sum(shares[o][0] for o in shares if o != h)
            others_hi = sum(shares[o][1] for o in shares if o != h)
            nlo, nhi = max(lo, 1 - others_hi), min(hi, 1 - others_lo)
            if (nlo, nhi) != (lo, hi):
                shares[h] = (nlo, nhi, shares[h][2])
                changed = True
        if not changed:
            break
    for r in members:
        lo, hi, areas_t = shares[r.household]
        r_lo, r_hi = ratio_bounds(lo, hi, r.cost, r.income_low, r.income_high)
        new[(r.household, r.quarter)] = HouseholdQuarter(
            household=r.household, quarter=r.quarter, frame_area=r.frame_area,
            stratum=r.stratum, psu=r.psu, housing=r.housing, lease=r.lease,
            weight=r.weight, share_lo=lo, share_hi=hi, areas=areas_t,
            income_low=r.income_low, income_high=r.income_high, cost=r.cost,
            ratio_lo=r_lo, ratio_hi=r_hi, reports=r.reports,
            evidence=r.evidence, conflicted=r.conflicted)
    return new


def simulate_inquiry(sources, evidence, specs, request_by_area, inquiry,
                     baseline_assess):
    """枚举派单可能的回执结论，评估对所在资金池各申请的影响。"""
    members = lease_households(evidence, inquiry["lease"])
    if not members:
        return None
    target = members[0]  # 与已见回执一致：回执指名合同内一户
    candidate_areas = list(target.areas)
    pool = sources.pool_of(target.frame_area)
    pool_requests = [r for r in sources.requests.values()
                     if sources.pool_of(r["area"]) == pool]
    outcomes = []
    for band in inquiry["possible_share_bands"]:
        for area in candidate_areas:
            new_records = apply_receipt(evidence.records, sources, inquiry["lease"],
                                        target.household, [area], band)
            est = Estimator(new_records)
            assess = {}
            for req in pool_requests:
                spec = specs[req["area"]]
                trend = est.trend(spec)
                assess[req["request"]] = assess_request(req, spec, trend)
            outcomes.append({"band": band, "area": area, "assess": assess})
    return {"inquiry": inquiry, "pool": pool, "target": target.household,
            "candidate_areas": candidate_areas, "outcomes": outcomes,
            "baseline": baseline_assess}


def classify_impact(sources, sim, entries):
    """分辨再查一次改变的是本轮可办理金额，还是只关系已签收款的复议。"""
    inquiry = sim["inquiry"]
    pool_requests = {r["request"]: r for r in sources.requests.values()
                     if sources.pool_of(r["area"]) == sim["pool"]}

    def signable_amounts(assess):
        return {rid: assess[rid]["robust_tier"] for rid in pool_requests if rid in assess}

    base = signable_amounts(sim["baseline"])
    changed_round = False
    changed_reconsider = False
    amounts = []
    for oc in sim["outcomes"]:
        cur = signable_amounts(oc["assess"])
        if cur != base:
            changed_round = True
        amounts.append(sum(pool_requests[rid]["tiers"][t - 1]
                           for rid, t in cur.items() if t > 0))
        # 已签收款的复议评估是否变化
        for e in entries.values():
            if e["state"] != "已签收" or e["request"] not in oc["assess"]:
                continue
            req = sources.requests[e["request"]]
            tier = req["tiers"].index(e["amount"]) + 1 if e["amount"] in req["tiers"] else None
            if tier is None:
                continue
            now_robust = oc["assess"][e["request"]]["tiers"][tier]["robust"]
            was_robust = sim["baseline"][e["request"]]["tiers"][tier]["robust"]
            if now_robust != was_robust:
                changed_reconsider = True
    if changed_round:
        impact = IMPACT_ROUND
    elif changed_reconsider:
        impact = IMPACT_RECONSIDER
    else:
        impact = IMPACT_NONE
    # 证据可能给出的结论：各申请支持下限在不同回执结论下的范围
    floor_range = {}
    for rid in pool_requests:
        vals = [oc["assess"][rid]["floor_pp"] for oc in sim["outcomes"]
                if rid in oc["assess"] and oc["assess"][rid]["floor_pp"] is not None]
        base_floor = sim["baseline"].get(rid, {}).get("floor_pp")
        if vals:
            floor_range[rid] = {
                "基线pp": base_floor,
                "回执结论下范围pp": (min(vals), max(vals)),
            }
    return {
        "impact": impact,
        "signable_amount_range": (min(amounts), max(amounts)),
        "affected_requests": sorted(pool_requests),
        "floor_range": floor_range,
    }


def plan(sources, evidence, specs, assessments, entries):
    """生成取证计划：影响评估 + 工时与回收期限安排。"""
    request_by_area = {r["area"]: r for r in sources.requests.values()}
    sims = []
    for inquiry in unrecovered_inquiries(sources, evidence):
        sim = simulate_inquiry(sources, evidence, specs, request_by_area,
                               inquiry, assessments)
        if sim is None:
            continue
        impact = classify_impact(sources, sim, entries)
        meeting = sources.meeting_date(sim["pool"])
        on_time = inquiry["return_by"] <= meeting
        sims.append({"sim": sim, "impact": impact, "on_time": on_time,
                     "meeting": meeting})
    # 排序：先能改变本轮办理的，再只关系复议的；同级按受影响款项、回收期、工时
    rank = {IMPACT_ROUND: 0, IMPACT_RECONSIDER: 1, IMPACT_NONE: 2}
    sims.sort(key=lambda s: (
        rank[s["impact"]["impact"]],
        not s["on_time"],
        -s["impact"]["signable_amount_range"][1],
        s["sim"]["inquiry"]["return_by"],
        s["sim"]["inquiry"]["hours"],
    ))
    # 安排：共用证据由有权街道核查，工时不重复占用
    free = {a: dict(r) for a, r in sources.work_roster.items()}
    scheduled, skipped = [], []
    for s in sims:
        q = s["sim"]["inquiry"]
        auth = q["authority"]
        if not s["on_time"]:
            skipped.append((q, f"回收期 {q['return_by']} 晚于拨款会 {s['meeting']}，"
                               f"本轮来不及"))
            continue
        if free.get(auth, {}).get("free_hours", 0) >= q["hours"]:
            free[auth]["free_hours"] -= q["hours"]
            scheduled.append(s)
        else:
            skipped.append((q, f"{auth} 可用工时不足（需 {q['hours']}h）"))
    return {"scheduled": scheduled, "skipped": skipped,
            "note": "各派单可能释放的金额建立在同一批待定事实的相斥解释上，"
                    "不能简单相加；共用证据由有权街道核查，其他街道可加入，"
                    "不重复占用调查工时。"}
