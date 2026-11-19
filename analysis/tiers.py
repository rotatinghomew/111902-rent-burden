"""定档：按可支持的上升幅度确定档位，并划分选项。

可支持的上升幅度 = 趋势下界估计 − 抽样半宽（支持下限）。区间上限不
证实结论，只用于标记待查选项。待定归属或分摊不只改一个片区的分数：
同一资金池内两个片区的待查选项往往建立在同一份共同凭据的相斥解释
上，不能据此同时签出两笔钱；能同时签收的选项必须在全部联合可行的
解释下都成立（即各自取最不利投影后仍过线），且预算足以同时覆盖。
"""

TIER_STATE_SIGNABLE = "可签收"
TIER_STATE_PENDING = "待查"
TIER_STATE_BUDGET = "被预算挡住"
TIER_STATE_UNSUPPORTED = "无支持"


def required_pp(standard, mult):
    """某档位所需的可支持上升幅度（百分点）。"""
    return standard["change_pp"] * mult


def assess_request(request, spec, trend):
    """单个申请的档位评估。

    返回 {request, area, floor, ceiling, precision_ok, comparable,
          robust_tier, potential_tier, tiers: {k: 状态(不含预算)}}。
    floor 为支持下限（趋势下界−半宽）；ceiling 为潜在上限（趋势上界−半宽，
    仅用于待查判定，不证实结论）。
    """
    std = {"change_pp": spec.change_pp}
    lo, hi = trend["bounds"]["lo"], trend["bounds"]["hi"]
    comparable = lo is not None and hi is not None
    precision_ok = comparable and not trend["flags"]
    floor = ceil = None
    if lo is not None and lo["half_width"] is not None:
        floor = lo["estimate"] - lo["half_width"]
    if hi is not None and hi["half_width"] is not None:
        ceil = hi["estimate"] - hi["half_width"]

    tiers = {}
    robust_tier = 0
    potential_tier = 0
    for k, mult in enumerate(request["trend_multipliers"], start=1):
        need = required_pp(std, mult)
        robust = (
            precision_ok and floor is not None and floor * 100 >= need
        )
        potential = ceil is not None and ceil * 100 >= need
        if robust:
            robust_tier = k
        if potential:
            potential_tier = k
        tiers[k] = {"need_pp": need, "robust": robust, "potential": potential}
    return {
        "request": request["request"],
        "area": request["area"],
        "floor_pp": None if floor is None else floor * 100,
        "ceiling_pp": None if ceil is None else ceil * 100,
        "precision_ok": precision_ok,
        "comparable": comparable,
        "flags": list(trend["flags"]),
        "robust_tier": robust_tier,
        "potential_tier": potential_tier,
        "tiers": tiers,
    }


def classify_options(assessments, requests, pool_available):
    """划分选项：可签收 / 待查 / 被预算挡住 / 无支持。

    assessments: {request_id: assess_request 结果}；pool_available: {pool: 余额}。
    同一申请的各档是互斥选择；某档"可签收"指：证据健壮支持，且在池内其他
    申请都按各自最高可支持档签收时预算仍够（所有片区能同时签收）。
    证据支持但预算不够的标为被预算挡住。返回 {request_id: {tier_k: 状态}}
    与池级说明。
    """
    states = {}
    notes = []
    best = {}
    for rid, a in assessments.items():
        best[rid] = (requests[rid]["tiers"][a["robust_tier"] - 1]
                     if a["robust_tier"] else 0)
    for rid, a in assessments.items():
        req = requests[rid]
        pool = req["_pool"]
        others_best = sum(b for r2, b in best.items()
                          if r2 != rid and requests[r2]["_pool"] == pool)
        states[rid] = {}
        for k, t in a["tiers"].items():
            if not t["robust"]:
                states[rid][k] = TIER_STATE_PENDING if t["potential"] \
                    else TIER_STATE_UNSUPPORTED
                continue
            amount = req["tiers"][k - 1]
            if amount + others_best <= pool_available.get(pool, 0):
                states[rid][k] = TIER_STATE_SIGNABLE
            else:
                states[rid][k] = TIER_STATE_BUDGET
                notes.append(
                    f"{rid} 第{k}档（{amount}）与池内其他申请的可支持档位"
                    f"同时落实将超出 {pool} 池余额，被预算挡住")
    # 池内待查选项的相斥说明
    by_pool = {}
    for rid, req in requests.items():
        by_pool.setdefault(req["_pool"], []).append(rid)
    for pool, rids in by_pool.items():
        pending = [rid for rid in rids
                   if any(s == TIER_STATE_PENDING for s in states[rid].values())]
        if len(pending) > 1:
            notes.append(
                f"{pool} 池内 {'、'.join(sorted(pending))} 的待查选项建立在同一批待定归属/"
                f"分摊的相斥解释上，一份共同凭据不能签出两笔钱，需待回执厘清")
    return states, notes
