"""拨款台账与资金池：签收、预留、暂停、解除、复议，以及回查。

规则
----
* 预算先扣已签收款和有效预留；暂停的预留在拨款会解除前仍占款。
* 已签收款只能立复议，不回到余额。
* 复核导致预留不能签收时：暂停并标注理由，仍占款，直到解除。
* 经办人的签收与解除预留写入办理日志，影响之后的查询与办理，
  不是一次性导出的建议。
* 回查（query）按签收编号给出当时使用的调查范围与理由（快照），
  以及后来单列的更正和影响。
"""

import json
from pathlib import Path

ACTIVE_STATES = ("已签收", "预留", "暂停")  # 仍占用资金的状态
DEFAULT_STATE = Path(__file__).resolve().parents[1] / "state" / "office_state.json"


class Office:
    def __init__(self, sources, state_path=None):
        self.sources = sources
        self.state_path = Path(state_path) if state_path else DEFAULT_STATE
        self.state = self._load()

    # ---- 持久化 ----
    def _load(self):
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {"journal": [], "snapshots": {}, "corrections": [], "extra_receipts": []}

    def _save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _log(self, op, **fields):
        entry = {"op": op, **fields}
        self.state["journal"].append(entry)
        self._save()
        return entry

    # ---- 台账视图 ----
    def entries(self):
        """源台账 + 办理日志重放出的当前台账。"""
        entries = {}
        for row in self.sources.ledger:
            entries[row["entry"]] = {
                "entry": row["entry"], "request": row["request"],
                "amount": row["amount"], "state": row["state"],
                "basis": row["basis"], "reason": None, "reconsider": None,
            }
        for j in self.state["journal"]:
            op = j["op"]
            if op in ("sign", "reserve"):
                entries[j["entry"]] = {
                    "entry": j["entry"], "request": j["request"],
                    "amount": j["amount"],
                    "state": "已签收" if op == "sign" else "预留",
                    "basis": j["at"], "reason": None, "reconsider": None,
                }
            elif op == "suspend":
                entries[j["entry"]]["state"] = "暂停"
                entries[j["entry"]]["reason"] = j["reason"]
            elif op == "release":
                entries[j["entry"]]["state"] = "解除"
                entries[j["entry"]]["reason"] = j.get("reason")
            elif op == "reconsider":
                entries[j["entry"]]["reconsider"] = j["note"]
        return entries

    def pool_holds(self, entries=None):
        """各资金池的占用明细（已签收 + 预留 + 暂停仍占款）。"""
        entries = entries or self.entries()
        holds = {}
        for e in entries.values():
            if e["state"] not in ACTIVE_STATES:
                continue
            pool = self.sources.pool_of(self.sources.requests[e["request"]]["area"])
            holds.setdefault(pool, []).append(e)
        return holds

    def pool_available(self, entries=None):
        holds = self.pool_holds(entries)
        out = {}
        for pool, b in self.sources.budgets.items():
            used = sum(e["amount"] for e in holds.get(pool, []))
            out[pool] = b["total"] - used
        return out

    # ---- 办理操作 ----
    def _new_entry_id(self, request):
        n = 1
        existing = self.entries()
        while f"L-{request}-{n}" in existing:
            n += 1
        return f"L-{request}-{n}"

    def sign(self, request, tier, amount, snapshot, at):
        """签收一档援助：写入台账并留存当时范围与理由。"""
        entry_id = self._new_entry_id(request)
        self.state["snapshots"][entry_id] = snapshot
        self._log("sign", entry=entry_id, request=request, tier=tier,
                  amount=amount, at=at)
        return entry_id

    def reserve(self, request, tier, amount, snapshot, at):
        entry_id = self._new_entry_id(request)
        self.state["snapshots"][entry_id] = snapshot
        self._log("reserve", entry=entry_id, request=request, tier=tier,
                  amount=amount, at=at)
        return entry_id

    def suspend(self, entry, reason, at):
        """暂停预留：标注理由，仍占款，待拨款会解除。"""
        self._log("suspend", entry=entry, reason=reason, at=at)

    def release(self, entry, reason, at):
        """拨款会解除预留/暂停：资金回到余额。"""
        self._log("release", entry=entry, reason=reason, at=at)

    def reconsider(self, entry, note, at):
        """已签收款立复议：只标记，不回到余额。"""
        self._log("reconsider", entry=entry, note=note, at=at)

    def receive_receipt(self, receipt, at):
        """登记一份新回执（进入证据叠加层，影响之后的复核与办理）。"""
        receipt = dict(receipt)
        receipt.setdefault("received", at)
        self.state["extra_receipts"].append(receipt)
        self._log("receive", evidence=receipt.get("evidence"), at=at,
                  lease=receipt.get("lease"), household=receipt.get("household"))
        return receipt

    # ---- 复核 ----
    def review(self, assessments, evidence, at):
        """按当前证据复核台账。

        assessments: {request_id: tiers.assess_request 结果}。
        预留不能签收的 -> 暂停并标注理由；已签收失去支持的 -> 立复议。
        同时把基准日之后到达、且影响相关范围的证据单列为更正。
        返回本次复核的动作列表。
        """
        actions = []
        entries = self.entries()
        for e in entries.values():
            a = assessments.get(e["request"])
            if a is None:
                continue
            tier = _tier_of(e, self.sources.requests[e["request"]])
            robust = tier is not None and a["tiers"].get(tier, {}).get("robust") \
                and a["precision_ok"]
            if e["state"] == "预留" and not robust:
                reason = _review_reason(a, tier)
                self.suspend(e["entry"], reason, at)
                actions.append({"action": "暂停", "entry": e["entry"], "reason": reason})
            elif e["state"] == "已签收" and not robust and not e["reconsider"]:
                note = _review_reason(a, tier)
                self.reconsider(e["entry"], note, at)
                actions.append({"action": "复议", "entry": e["entry"], "note": note})
        # 更正：基准日之后到达的证据
        corrections = self._collect_corrections(entries, evidence)
        if corrections:
            self.state["corrections"].extend(corrections)
            self._save()
            actions.append({"action": "更正登记", "count": len(corrections)})
        return actions

    def _collect_corrections(self, entries, evidence):
        """基准日后到达且影响 entry 范围的证据，单列为更正。"""
        out = []
        seen = {(c["entry"], c["evidence"]) for c in self.state["corrections"]}
        for rec in evidence.all_receipts:
            # 该回执涉及的合同在基准日时的调查范围（按申报候选归属）
            claim_areas = set()
            for c in evidence.claims:
                if c["lease"] == rec["lease"]:
                    claim_areas.update(c["possible_areas"])
            claim_areas.update(rec.get("areas", []))
            for e in entries.values():
                if e["state"] not in ACTIVE_STATES:
                    continue
                area = self.sources.requests[e["request"]]["area"]
                if area not in claim_areas:
                    continue
                basis = e["basis"]
                snap = self.state["snapshots"].get(e["entry"], {})
                known = set(snap.get("evidence_ids", []))
                if rec["received"] <= basis or rec["evidence"] in known:
                    continue
                if (e["entry"], rec["evidence"]) in seen:
                    continue
                note = f"回执涉及 {rec['lease']}（{rec['household']}），" \
                       f"归属/分摊影响 {area} 的调查范围"
                if rec.get("revokes"):
                    note += f"；该回执撤销了 {rec['revokes']}"
                out.append({
                    "entry": e["entry"],
                    "evidence": rec["evidence"],
                    "received": rec["received"],
                    "note": note,
                    "revokes": rec.get("revokes"),
                })
        return out

    # ---- 回查 ----
    def query(self, entry_id):
        """按签收编号回查：当时的调查范围与理由 + 后来单列的更正和影响。"""
        entries = self.entries()
        if entry_id not in entries:
            raise KeyError(f"无此台账编号：{entry_id}")
        e = entries[entry_id]
        corrections = [c for c in self.state["corrections"] if c["entry"] == entry_id]
        return {
            "entry": e,
            "snapshot": self.state["snapshots"].get(entry_id),
            "corrections": corrections,
            "journal": [j for j in self.state["journal"]
                        if j.get("entry") == entry_id],
        }


def _tier_of(entry, request):
    """台账金额对应的档位（按金额匹配 tiers）。"""
    for k, amount in enumerate(request["tiers"], start=1):
        if amount == entry["amount"]:
            return k
    return None


def _review_reason(assessment, tier):
    parts = []
    if tier is None:
        parts.append("台账金额不在申请档位内")
    else:
        need = assessment["tiers"][tier]["need_pp"]
        floor = assessment["floor_pp"]
        floor_txt = "不可估计" if floor is None else f"{floor:+.1f}pp"
        parts.append(f"第{tier}档需可支持上升≥{need:.1f}pp，当前支持下限 {floor_txt}")
    if not assessment["precision_ok"]:
        parts.extend(assessment["flags"] or ["精度不足"])
    return "；".join(parts)
