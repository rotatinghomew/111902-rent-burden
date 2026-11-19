"""证据链与调查单位组装。

调查单位是住户（household）。每户每季的有效证据 = 该季申报（claims）
与截至基准日仍有效的回执（receipts，未被后续回执撤销）的交集：
回执收紧分摊比例区间、限定片区归属；申报的 replaces 与回执的 revokes
构成修订链。同一合同各户的分摊比例共同受合同总额约束（合计为 1），
据此互相收紧。并存有效证据若共同不可行（区间交空、合同合计不可达），
标出不能共同成立的证据及受影响范围；该范围内住户的相关属性按完全未知
处理，不影响其他仍有充分证据的片区。

报送（reports）只用于辨别：代理重复报送（同一住户被代理与承租人重复
申报）、真正分担月租的不同住户（同一合同下的不同住户）、只缺访谈答案
的住户（访谈两端皆缺）。重复报送不重复计户。
"""

from collections import defaultdict
from dataclasses import dataclass

from .conventions import monthly_cost, ratio_bounds


@dataclass
class HouseholdQuarter:
    """一户一季的调查记录（分析基本单元）。"""

    household: str
    quarter: int
    frame_area: str          # 抽样片区（不一定是最后承担援助的片区）
    stratum: str
    psu: str
    housing: str
    lease: str
    weight: float            # 设计权重 = 1 / (p_psu × p_house_given_psu)
    share_lo: float
    share_hi: float
    areas: tuple             # 可能的归属片区（联合约束下的候选）
    income_low: object       # None 表示该端无收入证据
    income_high: object
    cost: float              # 合同月实际居住成本
    ratio_lo: float
    ratio_hi: float
    reports: tuple = ()      # 该户当季报送编号（去重后）
    evidence: tuple = ()     # 采用的证据编号（申报 + 有效回执）
    conflicted: bool = False # 处于证据冲突范围：相关属性按未知处理

    @property
    def refused(self):
        """只缺访谈答案：收入两端皆无证据。"""
        return self.income_low is None and self.income_high is None

    def over_flags(self, line):
        """超负担线指示的上下界：i_lo=必然超线，i_hi=可能超线。"""
        return (
            1.0 if self.ratio_lo > line else 0.0,
            1.0 if self.ratio_hi > line else 0.0,
        )


def _apply_as_of(rows, as_of):
    if as_of is None:
        return list(rows)
    return [r for r in rows if r.get("received") is None or r["received"] <= as_of]


def valid_receipts(receipts, as_of=None):
    """截至基准日仍有效的回执：被后续有效回执撤销的除外。"""
    rows = _apply_as_of(receipts, as_of)
    revoked = {r["revokes"] for r in rows if r.get("revokes")}
    return [r for r in rows if r["evidence"] not in revoked]


def current_claims(claims, as_of=None):
    """截至基准日的有效申报（replaces 链取最新）。"""
    rows = _apply_as_of(claims, as_of)
    replaced = {r["replaces"] for r in rows if r.get("replaces")}
    return [r for r in rows if r["claim"] not in replaced]


def current_interviews(interviews, as_of=None):
    rows = _apply_as_of(interviews, as_of)
    replaced = {r["replaces"] for r in rows if r.get("replaces")}
    return [r for r in rows if r["response"] not in replaced]


class EvidenceSet:
    """某一基准日下的全部有效证据与组装好的调查记录。"""

    def __init__(self, sources, as_of=None, extra_receipts=()):
        self.sources = sources
        self.as_of = as_of
        receipts = list(sources.receipts) + [dict(r) for r in extra_receipts]
        # 全部回执（含被撤销的）用于审计；有效回执用于估计
        self.all_receipts = _apply_as_of(receipts, as_of)
        self.receipts = valid_receipts(receipts, as_of)
        self.claims = current_claims(sources.claims, as_of)
        self.interviews = current_interviews(sources.interviews, as_of)
        self.conflicts = self._find_conflicts()
        self.records = self._assemble()

    # ---- 证据链 ----
    def _evidence_by_household_lease(self):
        """(household, lease) -> (claim, [有效回执...])，按收到时间排序。"""
        claims = {(c["household"], c["lease"]): c for c in self.claims}
        recs = defaultdict(list)
        for r in sorted(self.receipts, key=lambda r: r["received"]):
            recs[(r["household"], r["lease"])].append(r)
        return claims, recs

    def _find_conflicts(self):
        """并存有效证据共同不可行的范围。

        两类冲突：(1) 同一住户同一合同的有效证据（申报+回执）交集为空；
        (2) 同一合同各户分摊区间合计无法达到 1（共同约束不可行）。
        返回 [{lease, households, evidence, areas, kind}]，areas 为受影响
        片区（相关住户的全部候选归属）。
        """
        claims, recs = self._evidence_by_household_lease()
        conflicts = []
        # 住户级：证据交集
        per_lease = defaultdict(list)
        for (hh, lease), claim in claims.items():
            lo, hi = claim["rent_share"]
            areas = set(claim["possible_areas"])
            ev = [claim["claim"]]
            for r in recs.get((hh, lease), []):
                lo, hi = max(lo, r["share"][0]), min(hi, r["share"][1])
                areas &= set(r["areas"])
                ev.append(r["evidence"])
            per_lease[lease].append((hh, lo, hi, areas, ev))
            if lo > hi or not areas:
                conflicts.append({
                    "kind": "证据互斥",
                    "lease": lease,
                    "households": [hh],
                    "evidence": ev,
                    "areas": sorted(claim["possible_areas"]),
                })
        # 合同级：共同约束（合计为 1）可行性
        for lease, rows in per_lease.items():
            if len(rows) < 2:
                continue
            lo_sum = sum(r[1] for r in rows)
            hi_sum = sum(r[2] for r in rows)
            if lo_sum > 1 + 1e-9 or hi_sum < 1 - 1e-9:
                ev = [e for r in rows for e in r[4]]
                areas = sorted({a for r in rows for a in r[3]})
                conflicts.append({
                    "kind": "合同共同约束不可行",
                    "lease": lease,
                    "households": [r[0] for r in rows],
                    "evidence": ev,
                    "areas": areas,
                })
        # 合并受影响范围相同的冲突
        return conflicts

    def conflict_scope(self):
        """冲突影响的住户集合与片区集合。"""
        households, areas = set(), set()
        for c in self.conflicts:
            households.update(c["households"])
            areas.update(c["areas"])
        return households, areas

    # ---- 记录组装 ----
    def _assemble(self):
        src = self.sources
        claims, recs = self._evidence_by_household_lease()
        interviews = {}
        for iv in self.interviews:
            interviews[(iv["household"], iv["quarter"])] = iv
        reports = defaultdict(list)
        for r in src.reports:
            reports[r["household"]].append(r)
        conflict_households, _ = self.conflict_scope()

        # 先按证据交集得到每户分摊与归属，再按合同共同约束互相收紧
        raw = {}
        for f in src.frame:
            if not f["sampled"]:
                continue
            hh, q = f["household"], f["quarter"]
            claim = claims.get((hh, f["lease"]))
            if claim is None:
                continue
            lo, hi = claim["rent_share"]
            areas = set(claim["possible_areas"])
            ev = [claim["claim"]]
            for r in recs.get((hh, f["lease"]), []):
                lo, hi = max(lo, r["share"][0]), min(hi, r["share"][1])
                areas &= set(r["areas"])
                ev.append(r["evidence"])
            raw[(hh, q)] = [lo, hi, areas, ev, f]

        # 合同共同约束：share_i ∈ [lo_i, hi_i] 且 Σ share = 1，迭代收紧
        by_lease = defaultdict(list)
        for (hh, q), v in raw.items():
            by_lease[(q, v[4]["lease"])].append((hh, q))
        for (q, lease), keys in by_lease.items():
            for _ in range(len(keys)):  # 至多收敛于户数轮
                changed = False
                los = {k: raw[k][0] for k in keys}
                his = {k: raw[k][1] for k in keys}
                for k in keys:
                    others_lo = sum(los[o] for o in keys if o != k)
                    others_hi = sum(his[o] for o in keys if o != k)
                    new_lo = max(raw[k][0], 1.0 - others_hi)
                    new_hi = min(raw[k][1], 1.0 - others_lo)
                    if new_lo > raw[k][0] + 1e-12 or new_hi < raw[k][1] - 1e-12:
                        raw[k][0], raw[k][1] = new_lo, new_hi
                        changed = True
                if not changed:
                    break

        records = {}
        for (hh, q), (lo, hi, areas, ev, f) in raw.items():
            iv = interviews.get((hh, q))
            income_low = iv["income_low"] if iv else None
            income_high = iv["income_high"] if iv else None
            conflicted = hh in conflict_households
            if conflicted:
                # 冲突范围内：分摊按完全未知处理，归属仍限于其申报候选
                lo, hi = 0.0, 1.0
                areas = set(claim["possible_areas"])
            cost = monthly_cost(src.contracts[f["lease"]])
            ratio_lo, ratio_hi = ratio_bounds(lo, hi, cost, income_low, income_high)
            records[(hh, q)] = HouseholdQuarter(
                household=hh, quarter=q, frame_area=f["area"], stratum=f["stratum"],
                psu=f["psu"], housing=f["housing"], lease=f["lease"],
                weight=1.0 / (f["p_psu"] * f["p_house_given_psu"]),
                share_lo=lo, share_hi=hi, areas=tuple(sorted(areas)),
                income_low=income_low, income_high=income_high, cost=cost,
                ratio_lo=ratio_lo, ratio_hi=ratio_hi,
                reports=tuple(r["report"] for r in reports.get(hh, [])),
                evidence=tuple(ev), conflicted=conflicted,
            )
        return records

    # ---- 报送辨别 ----
    def report_classification(self):
        """区分代理重复报送、真正分担月租的不同住户、只缺访谈答案的住户。"""
        src = self.sources
        by_household = defaultdict(set)
        for r in src.reports:
            by_household[r["household"]].add(r["sender"])
        agent_dup = sorted(
            h for h, senders in by_household.items()
            if "代理" in senders and "承租人" in senders
        )
        lease_members = defaultdict(set)
        for (hh, q), rec in self.records.items():
            lease_members[(q, rec.lease)].add(hh)
        sharing = sorted({hh for members in lease_members.values() if len(members) > 1
                          for hh in members})
        interview_missing = sorted({rec.household for rec in self.records.values()
                                    if rec.refused})
        return {
            "代理重复报送": agent_dup,
            "共同分担住户": sharing,
            "只缺访谈答案": interview_missing,
        }

    def quarters(self):
        return sorted({q for (_, q) in self.records})
