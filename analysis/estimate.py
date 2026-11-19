"""设计基础上的估计：片区与合并地域的比例、中位数与跨季趋势。

要点
----
* 设计权重 = 1 / (p_psu × p_house_given_psu)；方差按 PSU 末级群线性化，
  同一住房内的合住户自然落在同一 PSU 内，相关已被群方差覆盖。
* 片区与合并地域一律从原住户重新估计：先定每户归属候选与分摊区间，
  再在地域层面投影出上下界，不把各地各自的极端值相加。待定归属户在
  合并地域内是确定成员（其候选片区都在同一资金池/地域内），合并地域
  的区间因此可能窄于各片区之和。
* 固定住户构成：比例与中位数按 standards 的 family_share 标准化，
  两季用同一构成，比较不受构成变化影响。某层覆盖不足（PSU 数低于
  min_psu）时标出该项不能比较，不重配该层权重。
* 趋势：同一住户跨季是同一户（轮换面板），其分摊比例视为同一个未知
  安排（两季申报一致、合租人未变），收入按各季访谈区间。配对户的
  超线指示差在联合可行集上取界；未配对户按单季界。抽样不确定性与
  事实未确认的上下界分开报告：每个界有自己的估计值与抽样半宽。
* 区间上限不证实任何结论：定档只看下界（支持下限），上限仅用于
  标记待查选项。
"""

from collections import defaultdict
from statistics import NormalDist

from .conventions import DEFAULT_BURDEN_LINE

STRATA = ("家庭", "单人")


class RegionSpec:
    def __init__(self, name, areas, standard):
        self.name = name
        self.areas = set(areas)
        s = standard or {}
        self.family_share = s.get("family_share", 0.5)
        self.min_psu = s.get("min_psu", 0)
        self.confidence = s.get("confidence", 0.9)
        self.max_trend_halfwidth_pp = s.get("max_trend_halfwidth_pp", 100)
        self.change_pp = s.get("change_pp", 0)
        self.burden_line = s.get("burden_line", DEFAULT_BURDEN_LINE)

    @property
    def z(self):
        # 双侧置信区间的半宽系数（confidence=0.9 -> 1.6449）
        return NormalDist().inv_cdf(1 - (1 - self.confidence) / 2)


def region_specs(sources):
    """片区 + 合并地域（资金池、全区）的估计规格。"""
    specs = {}
    pools = defaultdict(list)
    for area, row in sources.areas.items():
        specs[area] = RegionSpec(area, [area], sources.standards.get(area))
        pools[row["pool"]].append(area)
    for pool, areas in pools.items():
        specs[pool] = RegionSpec(pool, sorted(areas), sources.standards.get(pool))
    specs["全区"] = RegionSpec("全区", sorted(sources.areas), sources.standards.get("全区"))
    return specs


# ---------------------------------------------------------------- 工具

def _lin_var(u):
    """末级群方差：n/(n-1)·Σ(u−ū)²。u 为 PSU 级线性化值。"""
    n = len(u)
    if n < 2:
        return None
    m = sum(u.values()) / n
    return sum((x - m) ** 2 for x in u.values()) * n / (n - 1)


def _lin_cov(u1, u2):
    """跨季重复入样的 PSU 配对协方差（仅在两季都入样的 PSU 上）。"""
    common = sorted(set(u1) & set(u2))
    n = len(common)
    if n < 2:
        return 0.0, 0
    m1 = sum(u1[k] for k in common) / n
    m2 = sum(u2[k] for k in common) / n
    cov = sum((u1[k] - m1) * (u2[k] - m2) for k in common) * n / (n - 1)
    return cov, n


def _std_combo(parts, family_share):
    """按固定构成合并两层：parts[stratum] = (u_dict, estimate)。"""
    est, us = 0.0, {}
    for stratum, share in (("家庭", family_share), ("单人", 1 - family_share)):
        u, e = parts[stratum]
        est += share * e
        for k, v in u.items():
            us[k] = us.get(k, 0.0) + share * v
    return us, est


class Estimator:
    def __init__(self, evidence):
        # 接受 EvidenceSet 或直接的 {(户, 季): HouseholdQuarter} 字典
        self.records = evidence.records if hasattr(evidence, "records") else evidence

    # ---- 成员关系 ----
    @staticmethod
    def _membership(rec, spec):
        """certain=确定成员（候选都在地域内）；possible=可能成员；否则 None。"""
        areas = set(rec.areas)
        if areas <= spec.areas:
            return "certain"
        if areas & spec.areas:
            return "possible"
        return None

    def _domain(self, spec, quarter, stratum, bound):
        """投影域与超线取值。

        bound='lo'：下界（压低比例）——确定成员取 i_lo，可能成员仅当
        i_lo=0 时纳入（稀释）；bound='hi' 对称。返回 [(rec, value)]。
        """
        line = spec.burden_line
        out = []
        for rec in self.records.values():
            if rec.quarter != quarter or rec.stratum != stratum:
                continue
            mem = self._membership(rec, spec)
            if mem is None:
                continue
            i_lo, i_hi = rec.over_flags(line)
            if bound == "lo":
                if mem == "certain":
                    out.append((rec, i_lo))
                elif i_lo == 0.0:
                    out.append((rec, 0.0))
            else:
                if mem == "certain":
                    out.append((rec, i_hi))
                elif i_hi == 1.0:
                    out.append((rec, 1.0))
        return out

    @staticmethod
    def _ratio_lin(domain):
        """比例 R=Σwy/Σw 的估计值与 PSU 线性化变量。"""
        t = defaultdict(lambda: [0.0, 0.0])
        for rec, y in domain:
            t[rec.psu][0] += rec.weight * y
            t[rec.psu][1] += rec.weight
        O = sum(v[0] for v in t.values())
        W = sum(v[1] for v in t.values())
        if W <= 0:
            return None, {}, 0
        R = O / W
        u = {p: (v[0] - R * v[1]) / W for p, v in t.items()}
        return R, u, len(u)

    def _std_estimate(self, spec, quarter, bound):
        """固定构成的标准化比例：返回 (estimate, u, 各层 PSU 数)。"""
        parts, coverage = {}, {}
        for stratum in STRATA:
            domain = self._domain(spec, quarter, stratum, bound)
            R, u, n_psu = self._ratio_lin(domain)
            coverage[stratum] = n_psu
            if R is None:
                return None, {}, coverage
            parts[stratum] = (u, R)
        u, est = _std_combo(parts, spec.family_share)
        return est, u, coverage

    # ---- 水平：超负担线比例 ----
    def proportion_band(self, spec, quarter):
        """某地域某季超负担线比例的上下界（固定构成），含抽样半宽。"""
        out = {"quarter": quarter, "region": spec.name, "bounds": {}, "coverage": {}}
        for bound in ("lo", "hi"):
            est, u, coverage = self._std_estimate(spec, quarter, bound)
            out["coverage"][bound] = coverage
            if est is None:
                out["bounds"][bound] = None
                continue
            var = _lin_var(u)
            se = var ** 0.5 if var is not None else None
            out["bounds"][bound] = {
                "estimate": est,
                "se": se,
                "half_width": spec.z * se if se is not None else None,
                "n_psu": len(u),
            }
        return out

    # ---- 水平：负担率分布 ----
    def _cdf_lin(self, spec, quarter, x, which):
        """标准化 ECDF 在 x 处的取值与 PSU 线性化变量（只含确定成员）。

        which='hi' 取负担率下界（CDF 上界），'lo' 取负担率上界（CDF 下界）。
        """
        key = "ratio_lo" if which == "hi" else "ratio_hi"
        F, u = 0.0, {}
        for stratum, share in (("家庭", spec.family_share),
                               ("单人", 1 - spec.family_share)):
            rows = [r for r in self.records.values()
                    if r.quarter == quarter and r.stratum == stratum
                    and self._membership(r, spec) == "certain"]
            W = sum(r.weight for r in rows)
            if W <= 0:
                return None, {}
            t = defaultdict(lambda: [0.0, 0.0])
            for r in rows:
                y = 1.0 if getattr(r, key) <= x else 0.0
                t[r.psu][0] += r.weight * y
                t[r.psu][1] += r.weight
            O = sum(v[0] for v in t.values())
            R = O / W
            F += share * R
            for p, v in t.items():
                u[p] = u.get(p, 0.0) + share * (v[0] - R * v[1]) / W
        return F, u

    def distribution(self, spec, quarter):
        """实际负担分布：标准化分位数区间（事实上下界）+ 中位数抽样参考。"""
        rows = [r for r in self.records.values()
                if r.quarter == quarter and self._membership(r, spec) == "certain"]
        if not rows:
            return {"quarter": quarter, "region": spec.name, "bands": None,
                    "note": "无确定成员，不能比较"}
        # 每层每侧一次排序，分位数直接查表
        from bisect import bisect_right
        ecdf = {}
        for stratum in STRATA:
            srows = [r for r in rows if r.stratum == stratum]
            W = sum(r.weight for r in srows)
            for which, key in (("hi", "ratio_lo"), ("lo", "ratio_hi")):
                vals = sorted((getattr(r, key), r.weight) for r in srows)
                xs = [v for v, _ in vals]
                cw, acc = [], 0.0
                for _, w in vals:
                    acc += w
                    cw.append(acc / W if W else 0.0)
                ecdf[(stratum, which)] = (xs, cw)

        def F(x, which):
            total = 0.0
            for stratum, share in (("家庭", spec.family_share),
                                   ("单人", 1 - spec.family_share)):
                xs, cw = ecdf[(stratum, which)]
                i = bisect_right(xs, x)
                total += share * (cw[i - 1] if i else 0.0)
            return total

        def quantile(q, which):
            xs = sorted({x for s in STRATA for x in ecdf[(s, which)][0]
                         if x != float("inf")})
            for x in xs:
                if F(x, which) >= q:
                    return x
            return float("inf")

        bands = {}
        for q in (0.25, 0.5, 0.75):
            bands[q] = (quantile(q, "hi"), quantile(q, "lo"))
        # 中位数的抽样参考（Woodruff 式）：CDF 在中位数区间端点处的标准误
        m_lo, m_hi = bands[0.5]
        se = {}
        for label, x, which in (("lo", m_lo, "hi"), ("hi", m_hi, "lo")):
            if x in (0.0, float("inf")):
                se[label] = None
                continue
            _, u = self._cdf_lin(spec, quarter, x, which)
            var = _lin_var(u)
            se[label] = var ** 0.5 if var is not None else None
        return {"quarter": quarter, "region": spec.name, "bands": bands,
                "median_band": (m_lo, m_hi), "median_cdf_se": se}

    def median_change(self, spec):
        """中位数变化：事实区间 + 在 Q1 中位数端点处 ΔF 的配对抽样参考。"""
        d1 = self.distribution(spec, 1)
        d2 = self.distribution(spec, 2)
        if not d1.get("median_band") or not d2.get("median_band"):
            return {"change_band": None, "note": "有一季不能比较"}
        m1, m2 = d1["median_band"], d2["median_band"]
        change = (m2[0] - m1[1], m2[1] - m1[0])
        paired = {}
        for label, x, which in (("lo", m1[0], "hi"), ("hi", m1[1], "lo")):
            if x in (0.0, float("inf")):
                paired[label] = None
                continue
            _, u1 = self._cdf_lin(spec, 1, x, which)
            _, u2 = self._cdf_lin(spec, 2, x, which)
            v1, v2 = _lin_var(u1), _lin_var(u2)
            if v1 is None or v2 is None:
                paired[label] = None
                continue
            cov, _ = _lin_cov(u1, u2)
            paired[label] = max(v1 + v2 - 2 * cov, 0.0) ** 0.5
        return {"change_band": change, "delta_cdf_se": paired}

    # ---- 趋势：跨季变化 ----
    @staticmethod
    def _feasible_combos(r1, r2, line):
        """配对户 (i1, i2) 的联合可行集：分摊是同一个未知安排。"""
        s_lo = max(r1.share_lo, r2.share_lo)
        s_hi = min(r1.share_hi, r2.share_hi)
        if s_lo > s_hi + 1e-12:
            return None  # 跨季证据不交：关联不成立，按未配对处理
        s_lo, s_hi = min(s_lo, s_hi), max(s_lo, s_hi)

        def thr(rec):
            ys = [y for y in (rec.income_low, rec.income_high) if y]
            if not ys:
                return 0.0, float("inf")
            return (min(line * y / rec.cost for y in ys),
                    max(line * y / rec.cost for y in ys))

        t1_lo, t1_hi = thr(r1)
        t2_lo, t2_hi = thr(r2)
        i1_opts, i2_opts = set(), set()
        if s_lo <= t1_hi + 1e-12:
            i1_opts.add(0)
        if s_hi > t1_lo + 1e-12:
            i1_opts.add(1)
        if s_lo <= t2_hi + 1e-12:
            i2_opts.add(0)
        if s_hi > t2_lo + 1e-12:
            i2_opts.add(1)
        combos = set()
        for a in i1_opts:
            for b in i2_opts:
                if a == 1 and b == 0:
                    ok = t1_lo < t2_hi and s_hi > t1_lo + 1e-12 and s_lo <= t2_hi + 1e-12
                elif a == 0 and b == 1:
                    ok = t2_lo <= t1_hi and s_hi > t2_lo + 1e-12 and s_lo <= t1_hi + 1e-12
                else:
                    ok = True
                if ok:
                    combos.add((a, b))
        return combos or {(0, 0), (0, 1), (1, 0), (1, 1)}

    def _trend_bound_stratum(self, spec, stratum, bound):
        """某地域某层趋势（p2−p1）的一个界：返回 (估计, u1, u2, W1, W2)。"""
        line = spec.burden_line
        dom1 = self._domain(spec, 1, stratum, "hi" if bound == "lo" else "lo")
        dom2 = self._domain(spec, 2, stratum, bound)
        W1 = sum(r.weight for r, _ in dom1)
        W2 = sum(r.weight for r, _ in dom2)
        if W1 <= 0 or W2 <= 0:
            return None
        v1 = {r.household: v for r, v in dom1}
        v2 = {r.household: v for r, v in dom2}
        rec_by_hq = self.records
        o1 = defaultdict(float)
        # 配对户：联合可行集上取界，覆盖单季独立界
        for h, r1v in v1.items():
            r1 = rec_by_hq[(h, 1)]
            if h in v2:
                r2 = rec_by_hq[(h, 2)]
                combos = self._feasible_combos(r1, r2, line)
                if combos is not None:
                    vals = [b / W2 - a / W1 for a, b in combos]
                    d = min(vals) if bound == "lo" else max(vals)
                    base = v2[h] / W2 - r1v / W1
                    # 差额相对未配对基准的调整，记到 Q1 侧（同一 PSU）
                    o1[r1.psu] += r1.weight * (d - base)
        # 未配对部分按单季界
        est = sum(r.weight * v for r, v in dom2) / W2 - \
              sum(r.weight * v for r, v in dom1) / W1 + sum(o1.values())
        p1 = sum(r.weight * v for r, v in dom1) / W1
        p2 = sum(r.weight * v for r, v in dom2) / W2
        u1 = defaultdict(float)
        u2 = defaultdict(float)
        for r, v in dom1:
            u1[r.psu] += r.weight * (v - p1) / W1
        for r, v in dom2:
            u2[r.psu] += r.weight * (v - p2) / W2
        for psu, delta in o1.items():
            u1[psu] += delta
        return est, dict(u1), dict(u2), W1, W2

    def trend(self, spec):
        """跨季趋势区间（固定构成）：事实上下界 + 各自抽样半宽 + 精度标记。"""
        out = {"region": spec.name, "bounds": {}, "flags": []}
        for bound in ("lo", "hi"):
            parts, us1, us2 = {}, defaultdict(float), defaultdict(float)
            ok = True
            for stratum, share in (("家庭", spec.family_share), ("单人", 1 - spec.family_share)):
                res = self._trend_bound_stratum(spec, stratum, bound)
                if res is None:
                    ok = False
                    break
                est, u1, u2, _, _ = res
                parts[stratum] = (est, share)
                for k, v in u1.items():
                    us1[k] += share * v
                for k, v in u2.items():
                    us2[k] += share * v
            if not ok:
                out["bounds"][bound] = None
                out["flags"].append(f"{bound}界：层内无样本，不能比较")
                continue
            est = sum(e * s for e, s in parts.values())
            v1, v2 = _lin_var(us1), _lin_var(us2)
            cov, n_common = _lin_cov(us1, us2)
            if v1 is None or v2 is None:
                se = None
            else:
                se = max(v1 + v2 - 2 * cov, 0.0) ** 0.5
            hw = spec.z * se if se is not None else None
            out["bounds"][bound] = {
                "estimate": est, "se": se, "half_width": hw,
                "n_psu_q1": len(us1), "n_psu_q2": len(us2),
                "n_common_psu": n_common,
            }
            if hw is None:
                out["flags"].append(f"{bound}界：PSU 不足，无法估计精度")
            elif hw * 100 > spec.max_trend_halfwidth_pp:
                out["flags"].append(
                    f"{bound}界：抽样半宽 {hw*100:.1f}pp 超过所需精度 "
                    f"{spec.max_trend_halfwidth_pp}pp，趋势不能用于定档")
        return out

    # ---- 覆盖与可比性 ----
    def coverage(self, spec):
        """各季各层入样 PSU 数；低于 min_psu 的层标出（不重配权重）。"""
        out = {}
        for quarter in (1, 2):
            for stratum in STRATA:
                psus = {r.psu for r in self.records.values()
                        if r.quarter == quarter and r.stratum == stratum
                        and self._membership(r, spec) is not None}
                out[(quarter, stratum)] = len(psus)
        return out

    def comparability(self, spec):
        flags = []
        cov = self.coverage(spec)
        for (quarter, stratum), n in sorted(cov.items()):
            if n < spec.min_psu:
                flags.append(f"Q{quarter}{stratum}层覆盖不足（入样PSU {n} < "
                             f"{spec.min_psu}），该层不能比较，不重配权重")
        return flags
