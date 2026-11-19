"""构造手工可算的小型合成资料，供分析流程测试。"""

import json


def tiny_supplies():
    """两片一区一池，负担率精确可知（收入点值、整租、无水电免租）。

    每片 2 个 PSU，每 PSU 每层 1 户，共 8 户；全部入样，权重=4。
    Q1 租金 300（负担率 0.30，线下），Q2 租金 400（0.40，线上），
    收入恒为 1000：每户超线指示 0->1，趋势恰为 +100pp（事实无不确定）。
    """
    areas = [{"area": "甲", "pool": "P"}, {"area": "乙", "pool": "P"}]
    standards = []
    for region, share in (("甲", 0.5), ("乙", 0.5), ("P", 0.5), ("全区", 0.5)):
        standards.append({"region": region, "family_share": share, "min_psu": 2,
                          "confidence": 0.9, "max_trend_halfwidth_pp": 50,
                          "change_pp": 1, "burden_line": 0.35})
    frame, contracts, interviews, claims, reports = [], [], [], [], []
    for ai, area in enumerate(("甲", "乙")):
        for pi in range(2):
            psu = f"{area}-{pi}"
            for si, stratum in enumerate(("家庭", "单人")):
                hh = f"H-{area}-{pi}-{stratum}"
                for q, rent in ((1, 300), (2, 400)):
                    lease = f"L-{q}-{hh}"
                    frame.append({"household": hh, "quarter": q, "area": area,
                                  "stratum": stratum, "psu": psu,
                                  "housing": f"房-{hh}", "lease": lease,
                                  "p_psu": 0.5, "p_house_given_psu": 0.5,
                                  "sampled": True})
                    contracts.append({"lease": lease, "monthly_total": rent,
                                      "free_days": 0, "utilities": 0})
                    interviews.append({"response": f"I-{q}-{hh}", "household": hh,
                                       "quarter": q, "received": "2026-06-01",
                                       "income_low": 1000, "income_high": 1000,
                                       "replaces": None})
                    claims.append({"claim": f"C-{q}-{hh}", "lease": lease,
                                   "household": hh, "possible_areas": [area],
                                   "rent_share": [1, 1],
                                   "received": "2026-06-02", "replaces": None})
                    reports.append({"report": f"D-{q}-{hh}", "lease": lease,
                                    "sender": "承租人", "household": hh})
    # 一户代理重复报送
    reports.append({"report": "D-代理-1", "lease": "L-1-H-甲-0-家庭",
                    "sender": "代理", "household": "H-甲-0-家庭"})
    return {
        "synthetic": True,
        "tables": {
            "areas": areas, "standards": standards, "frame": frame,
            "interviews": interviews, "contracts": contracts,
            "reports": reports, "claims": claims, "receipts": [],
            "budgets": [{"pool": "P", "total": 10000, "meeting": "2026-07-09"}],
            "requests": [
                {"request": "R甲", "area": "甲", "tiers": [1000, 2000, 3000],
                 "trend_multipliers": [1, 1.5, 2]},
                {"request": "R乙", "area": "乙", "tiers": [1000, 2000, 3000],
                 "trend_multipliers": [1, 1.5, 2]},
            ],
            "ledger": [
                {"entry": "L-乙-0", "request": "R乙", "amount": 1000,
                 "state": "预留", "basis": "2026-07-03"},
            ],
            "inquiries": [
                {"inquiry": "X甲", "lease": "L-2-H-甲-0-家庭", "authority": "甲",
                 "hours": 2, "return_by": "2026-07-08", "state": "待安排",
                 "possible_share_bands": [[0.9, 1.0]]},
            ],
            "work_roster": [{"authority": "甲", "free_hours": 2},
                            {"authority": "乙", "free_hours": 2}],
        },
    }


def write_supplies(tmp_path, data=None):
    path = tmp_path / "supplies.json"
    path.write_text(json.dumps(data or tiny_supplies(), ensure_ascii=False),
                    encoding="utf-8")
    return path
