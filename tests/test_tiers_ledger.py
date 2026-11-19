"""定档、台账与资金池的检查。"""

import tempfile
import unittest
from pathlib import Path

from analysis.ledger import Office
from analysis.report import build_analysis, snapshot_for
from analysis.sources import Sources
from analysis.tiers import (TIER_STATE_BUDGET, TIER_STATE_PENDING,
                            TIER_STATE_SIGNABLE, assess_request, classify_options)
from tests.fixtures import write_supplies


def _trend(lo_est, lo_hw, hi_est, hi_hw, flags=()):
    return {"bounds": {
        "lo": {"estimate": lo_est, "half_width": lo_hw, "se": None},
        "hi": {"estimate": hi_est, "half_width": hi_hw, "se": None}},
        "flags": list(flags)}


class TierLogic(unittest.TestCase):
    def setUp(self):
        self.request = {"request": "R", "area": "甲", "tiers": [1000, 2000, 3000],
                        "trend_multipliers": [1, 1.5, 2], "_pool": "P"}
        self.spec = type("S", (), {"change_pp": 2})()

    def test_robust_and_pending(self):
        # 支持下限 3.5pp：第1档(≥2)、第2档(≥3)健壮；第3档(≥4)上限 4.5pp 仅待查
        a = assess_request(self.request, self.spec, _trend(0.05, 0.015, 0.06, 0.015))
        self.assertEqual(a["robust_tier"], 2)
        self.assertEqual(a["potential_tier"], 3)
        self.assertTrue(a["tiers"][3]["potential"])
        self.assertFalse(a["tiers"][3]["robust"])

    def test_upper_bound_not_proof(self):
        # 上限再高，下限未过线就无一档可签收
        a = assess_request(self.request, self.spec, _trend(-0.5, 0.1, 0.9, 0.05))
        self.assertEqual(a["robust_tier"], 0)
        self.assertEqual(a["potential_tier"], 3)

    def test_precision_blocks_robust(self):
        # 精度不足时趋势不能用于定档
        a = assess_request(self.request, self.spec,
                           _trend(0.07, 0.02, 0.06, 0.015, flags=["精度不足"]))
        self.assertEqual(a["robust_tier"], 0)
        self.assertFalse(a["precision_ok"])

    def test_classify_options_budget(self):
        a1 = assess_request(self.request, self.spec, _trend(0.05, 0.015, 0.06, 0.015))
        req2 = dict(self.request, request="R2")
        a2 = assess_request(req2, self.spec, _trend(0.05, 0.015, 0.06, 0.015))
        requests = {"R": self.request, "R2": req2}
        states, notes = classify_options({"R": a1, "R2": a2}, requests,
                                         {"P": 3000})
        # 双方最高可支持档都是第2档(2000)：第1档(1000)能与对方最佳同时落实，
        # 第2档与对方最佳(2000)同时落实需 4000 > 3000，被预算挡住
        self.assertEqual(states["R"][1], TIER_STATE_SIGNABLE)
        self.assertEqual(states["R2"][1], TIER_STATE_SIGNABLE)
        self.assertEqual(states["R"][2], TIER_STATE_BUDGET)
        self.assertEqual(states["R2"][2], TIER_STATE_BUDGET)
        self.assertEqual(states["R"][3], TIER_STATE_PENDING)
        self.assertTrue(any("被预算挡住" in n for n in notes))


class LedgerTiny(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = Sources(write_supplies(self.root))
        self.state = self.root / "state.json"
        self.office = Office(self.src, self.state)

    def _analysis(self):
        return build_analysis(self.src, self.office)

    def test_exact_data_makes_tiers_signable(self):
        an = self._analysis()
        # 精确资料：趋势 +100pp，三档都健壮
        self.assertEqual(an["assessments"]["R甲"]["robust_tier"], 3)
        self.assertEqual(an["option_states"]["R甲"][3], TIER_STATE_SIGNABLE)

    def test_sign_persists_and_blocks_double_sign(self):
        an = self._analysis()
        snap = snapshot_for(self.office, an, "R甲", 1, "2026-07-09")
        entry = self.office.sign("R甲", 1, 1000, snap, "2026-07-09")
        # 余额：10000 − 1000(预留L-乙-0) − 1000(新签收)
        self.assertEqual(self.office.pool_available()["P"], 8000)
        # 重新载入状态文件：办理结果仍在
        office2 = Office(self.src, self.state)
        self.assertEqual(office2.entries()[entry]["state"], "已签收")
        self.assertEqual(office2.pool_available()["P"], 8000)

    def test_suspend_occupies_until_release(self):
        an = self._analysis()
        self.office.suspend("L-乙-0", "复核：证据不足", "2026-07-09")
        # 暂停仍占款
        self.assertEqual(self.office.pool_available()["P"], 9000)
        self.office.release("L-乙-0", "拨款会解除", "2026-07-10")
        self.assertEqual(self.office.pool_available()["P"], 10000)

    def test_signed_amount_never_returns_to_balance(self):
        an = self._analysis()
        snap = snapshot_for(self.office, an, "R甲", 1, "2026-07-09")
        entry = self.office.sign("R甲", 1, 1000, snap, "2026-07-09")
        self.office.reconsider(entry, "复核：待复议", "2026-07-10")
        # 立复议后仍占款，不回到余额
        self.assertEqual(self.office.pool_available()["P"], 8000)
        self.assertEqual(self.office.entries()[entry]["state"], "已签收")
        self.assertIsNotNone(self.office.entries()[entry]["reconsider"])

    def test_review_supported_reservation_stays(self):
        an = self._analysis()
        actions = self.office.review(an["assessments"], an["evidence"], "2026-07-09")
        # 精确资料支持 L-乙-0 的第1档：预留不暂停
        self.assertEqual(actions, [])
        self.assertEqual(self.office.entries()["L-乙-0"]["state"], "预留")

    def test_query_shows_snapshot_and_corrections(self):
        an = self._analysis()
        snap = snapshot_for(self.office, an, "R甲", 1, "2026-07-09")
        entry = self.office.sign("R甲", 1, 1000, snap, "2026-07-09")
        # 后到回执影响该范围 -> 单列更正
        self.office.receive_receipt(
            {"evidence": "E新", "lease": "L-2-H-甲-0-家庭",
             "household": "H-甲-0-家庭", "areas": ["甲"], "share": [0.9, 1.0],
             "revokes": None}, "2026-07-10")
        an2 = self._analysis()
        self.office.review(an2["assessments"], an2["evidence"], "2026-07-10")
        q = self.office.query(entry)
        self.assertEqual(q["snapshot"]["理由"]["该档需pp"], 1)
        self.assertEqual([c["evidence"] for c in q["corrections"]], ["E新"])


if __name__ == "__main__":
    unittest.main()
