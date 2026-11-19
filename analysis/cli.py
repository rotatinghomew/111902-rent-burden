"""办理入口：report / review / sign / reserve / suspend / release /
reconsider / query / plan / receive。

所有办理操作写入状态文件（默认 state/office_state.json），影响之后的
查询与办理；分析口径见 analysis.conventions 与各模块 docstring。
"""

import argparse
import json
import sys
from datetime import date

from .inquiries import plan as build_plan
from .ledger import Office
from .report import build_analysis, snapshot_for
from .sources import Sources
from .tiers import TIER_STATE_SIGNABLE


def _today():
    return date.today().isoformat()


def _print(obj, as_json):
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
    else:
        print(_render(obj))


def _render(obj, indent=0):
    pad = "  " * indent
    if isinstance(obj, dict):
        lines = []
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{pad}{k}:")
                lines.append(_render(v, indent + 1))
            else:
                lines.append(f"{pad}{k}: {v}")
        return "\n".join(lines)
    if isinstance(obj, list):
        if not obj:
            return f"{pad}（无）"
        return "\n".join(f"{pad}- " + _render(x, 0).lstrip("\n ") if isinstance(x, (dict, list))
                         else f"{pad}- {x}" for x in obj)
    return f"{pad}{obj}"


def _pp(x):
    return None if x is None else round(x, 2)


def summarize_analysis(analysis):
    """把分析结果整理成可读报告结构。"""
    out = {"调查范围": analysis["scope"], "地域": {}, "定档": {}, "台账": [],
           "资金池": {}}
    for name, reg in analysis["regions"].items():
        trend = reg["trend"]
        lo, hi = trend["bounds"]["lo"], trend["bounds"]["hi"]
        dist = {}
        for q in (1, 2):
            d = reg["distribution"][q]
            if d.get("bands"):
                dist[f"Q{q}"] = {
                    "分位数区间": {f"p{int(k*100)}": [round(v, 3) for v in vv]
                                for k, vv in d["bands"].items()},
                    "中位数CDF标准误": d["median_cdf_se"],
                }
            else:
                dist[f"Q{q}"] = d.get("note")
        mc = reg["median_change"]
        out["地域"][name] = {
            "超线比例%": {
                f"Q{q}": {b: None if reg["proportion"][q]["bounds"][b] is None else {
                    "估计": _pp(reg["proportion"][q]["bounds"][b]["estimate"] * 100),
                    "抽样半宽pp": _pp(None if reg["proportion"][q]["bounds"][b]["half_width"] is None
                                     else reg["proportion"][q]["bounds"][b]["half_width"] * 100),
                } for b in ("lo", "hi")}
                for q in (1, 2)
            },
            "负担率分布": dist,
            "中位数变化": mc,
            "趋势pp": {
                "下界": None if lo is None else {"估计": _pp(lo["estimate"] * 100),
                                              "抽样半宽": _pp(None if lo["half_width"] is None else lo["half_width"] * 100)},
                "上界": None if hi is None else {"估计": _pp(hi["estimate"] * 100),
                                              "抽样半宽": _pp(None if hi["half_width"] is None else hi["half_width"] * 100)},
                "标记": trend["flags"],
            },
            "可比性": reg["comparability"],
        }
    for rid, a in analysis["assessments"].items():
        out["定档"][rid] = {
            "片区": a["area"],
            "支持下限pp": _pp(a["floor_pp"]),
            "潜在上限pp": _pp(a["ceiling_pp"]),
            "精度可定档": a["precision_ok"],
            "标记": a["flags"],
            "档位": {f"第{k}档(需≥{t['need_pp']:g}pp)": analysis["option_states"][rid][k]
                    for k, t in a["tiers"].items()},
        }
    for e in analysis["ledger"]:
        row = f"{e['entry']} {e['request']} {e['amount']} {e['state']}"
        if e.get("reason"):
            row += f"（{e['reason']}）"
        if e.get("reconsider"):
            row += f"［复议：{e['reconsider']}］"
        out["台账"].append(row)
    out["资金池"] = dict(analysis["pool_available"])
    if analysis["pool_notes"]:
        out["池级说明"] = analysis["pool_notes"]
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(prog="analysis", description=__doc__)
    parser.add_argument("--source", help="supplies.json 路径")
    parser.add_argument("--state", help="办理状态文件路径")
    parser.add_argument("--as-of", help="证据基准日 YYYY-MM-DD（只用该日前收到的证据）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("report", help="季度调查分析报告")
    sub.add_parser("review", help="按当前证据复核台账")
    sub.add_parser("plan", help="取证计划")

    p = sub.add_parser("sign", help="签收一档援助")
    p.add_argument("request"); p.add_argument("tier", type=int)
    p = sub.add_parser("reserve", help="预留一档援助")
    p.add_argument("request"); p.add_argument("tier", type=int)
    p = sub.add_parser("suspend", help="暂停预留（标注理由，仍占款）")
    p.add_argument("entry"); p.add_argument("--reason", required=True)
    p = sub.add_parser("release", help="拨款会解除预留/暂停")
    p.add_argument("entry"); p.add_argument("--reason", default="")
    p = sub.add_parser("reconsider", help="已签收款立复议")
    p.add_argument("entry"); p.add_argument("--note", required=True)
    p = sub.add_parser("query", help="按签收编号回查")
    p.add_argument("entry")
    p = sub.add_parser("receive", help="登记一份新回执")
    p.add_argument("evidence"); p.add_argument("lease"); p.add_argument("household")
    p.add_argument("--areas", nargs="+", required=True)
    p.add_argument("--share", nargs=2, type=float, required=True)
    p.add_argument("--revokes", default=None)

    args = parser.parse_args(argv)
    sources = Sources(args.source)
    office = Office(sources, args.state)
    now = _today()

    if args.cmd == "report":
        analysis = build_analysis(sources, office, as_of=args.as_of)
        _print(summarize_analysis(analysis), args.json)
    elif args.cmd == "review":
        analysis = build_analysis(sources, office, as_of=args.as_of)
        actions = office.review(analysis["assessments"], analysis["evidence"], now)
        _print({"复核动作": actions, "资金池余额": office.pool_available()}, args.json)
    elif args.cmd == "plan":
        analysis = build_analysis(sources, office, as_of=args.as_of)
        entries = office.entries()
        result = build_plan(sources, analysis["evidence"], analysis["specs"],
                            analysis["assessments"], entries)
        _print(_render_plan(result), args.json)
    elif args.cmd in ("sign", "reserve"):
        analysis = build_analysis(sources, office, as_of=args.as_of)
        req = analysis["requests"].get(args.request)
        if req is None:
            sys.exit(f"未知申请：{args.request}")
        if args.tier not in analysis["option_states"][args.request]:
            sys.exit(f"未知档位：{args.tier}")
        state = analysis["option_states"][args.request][args.tier]
        if args.cmd == "sign" and state != TIER_STATE_SIGNABLE:
            sys.exit(f"当前证据下该档不可签收（状态：{state}）")
        amount = req["tiers"][args.tier - 1]
        if args.cmd == "sign" and amount > office.pool_available()[req["_pool"]]:
            sys.exit(f"资金池 {req['_pool']} 余额不足")
        snap = snapshot_for(office, analysis, args.request, args.tier, now)
        if args.cmd == "sign":
            entry = office.sign(args.request, args.tier, amount, snap, now)
        else:
            entry = office.reserve(args.request, args.tier, amount, snap, now)
        _print({"台账编号": entry, "金额": amount, "状态": "已签收" if args.cmd == "sign" else "预留",
                "资金池余额": office.pool_available()}, args.json)
    elif args.cmd == "suspend":
        office.suspend(args.entry, args.reason, now)
        _print({"已暂停": args.entry, "理由": args.reason,
                "说明": "暂停仍占款，待拨款会解除"}, args.json)
    elif args.cmd == "release":
        office.release(args.entry, args.reason, now)
        _print({"已解除": args.entry, "资金池余额": office.pool_available()}, args.json)
    elif args.cmd == "reconsider":
        office.reconsider(args.entry, args.note, now)
        _print({"已立复议": args.entry, "说明": "已签收款不回到余额"}, args.json)
    elif args.cmd == "query":
        result = office.query(args.entry)
        if result["snapshot"] is None:
            from .report import retrospective_snapshot
            result["snapshot"] = retrospective_snapshot(
                sources, office, result["entry"])
        _print(result, args.json)
    elif args.cmd == "receive":
        receipt = {"evidence": args.evidence, "lease": args.lease,
                   "household": args.household, "areas": args.areas,
                   "share": args.share, "revokes": args.revokes}
        office.receive_receipt(receipt, now)
        _print({"已登记回执": receipt}, args.json)


def _render_plan(result):
    out = {"已安排": [], "未安排": [], "说明": result["note"]}
    for s in result["scheduled"]:
        q = s["sim"]["inquiry"]
        out["已安排"].append({
            "派单": q["inquiry"], "合同": q["lease"], "街道": q["authority"],
            "工时": q["hours"], "回收期": q["return_by"],
            "影响": s["impact"]["impact"],
            "可签收金额区间": s["impact"]["signable_amount_range"],
            "受影响申请": s["impact"]["affected_requests"],
            "支持下限范围": s["impact"]["floor_range"],
        })
    for q, why in result["skipped"]:
        out["未安排"].append({"派单": q["inquiry"], "合同": q["lease"], "原因": why})
    return out


if __name__ == "__main__":
    main()
