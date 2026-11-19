"""派单影响分辨与复核幂等性的检查。"""

import tempfile
import unittest
from pathlib import Path

from analysis.inquiries import (IMPACT_NONE, IMPACT_RECONSIDER, IMPACT_ROUND,
                                classify_impact)
from analysis.ledger import Office
from analysis.report import build_analysis
from analysis.sources import Sources
from tests.fixtures import write_supplies


def _assess(robust_tiers):
    """构造 classify_impact 需要的评估结构。"""
    out = {}
    for rid, tiers in robust_tiers.items():
        out[rid] = {"robust_tier": max(tiers) if tiers else 0,
                    "floor_pp": 0.0,
                    "tiers": {k: {"robust": k in tiers, "need_pp": k} for k in (1, 2, 3)}}
    return out


class ImpactClassification(unittest.TestCase):
    def _sim(self, baseline, outcomes):
        return {"inquiry": {"inquiry": "X", "lease": "L", "authority": "甲",
                            "hours": 1, "return_by": "2026-07-06", "state": "待安排",
                            "possible_share_bands": []},
                "pool": "P", "baseline": _assess(baseline),
                "outcomes": [{"assess": _assess(o)} for o in outcomes]}

    def _sources(self, tmp):
        return Sources(write_supplies(Path(tmp)))

    def test_round_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = self._sources(tmp)
            sim = self._sim({"R甲": set(), "R乙": set()},
                            [{"R甲": {1}, "R乙": set()}, {"R甲": set(), "R乙": set()}])
            impact = classify_impact(src, sim, {})
            self.assertEqual(impact["impact"], IMPACT_ROUND)

    def test_reconsider_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = self._sources(tmp)
            # 已签收 R甲 第1档；某回执结论使其失去/恢复支持，但可签收档不变
            entries = {"L-1": {"entry": "L-1", "request": "R甲", "amount": 1000,
                               "state": "已签收", "basis": "2026-07-01",
                               "reason": None, "reconsider": None}}
            sim = self._sim({"R甲": set(), "R乙": set()},
                            [{"R甲": set(), "R乙": set()}, {"R甲": set(), "R乙": set()}])
            # 手工让某个结论下第1档 robust 翻转
            sim["outcomes"][1]["assess"]["R甲"]["tiers"][1]["robust"] = True
            impact = classify_impact(src, sim, entries)
            self.assertEqual(impact["impact"], IMPACT_RECONSIDER)

    def test_no_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = self._sources(tmp)
            sim = self._sim({"R甲": set()}, [{"R甲": set()}, {"R甲": set()}])
            impact = classify_impact(src, sim, {})
            self.assertEqual(impact["impact"], IMPACT_NONE)


class ReviewIdempotency(unittest.TestCase):
    def test_second_review_is_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Sources()  # 真实资料：预留会被暂停、签收会被立复议
            state = Path(tmp) / "state.json"
            office = Office(src, state)
            an = build_analysis(src, office)
            first = office.review(an["assessments"], an["evidence"], "2026-07-09")
            self.assertTrue(any(a["action"] == "暂停" for a in first))
            self.assertTrue(any(a["action"] == "复议" for a in first))
            office2 = Office(src, state)
            an2 = build_analysis(src, office2)
            second = office2.review(an2["assessments"], an2["evidence"], "2026-07-10")
            # 第二次复核：不重复暂停、不重复立复议、不重复登记更正
            self.assertFalse(any(a["action"] in ("暂停", "复议") for a in second))
            self.assertFalse(any(a["action"] == "更正登记" for a in second))
            # 暂停仍占款
            self.assertEqual(office2.pool_available()["河东"], 22000)


if __name__ == "__main__":
    unittest.main()
