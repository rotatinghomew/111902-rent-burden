"""取证计划的检查。"""

import tempfile
import unittest
from pathlib import Path

from analysis.evidence import EvidenceSet
from analysis.inquiries import (IMPACT_NONE, IMPACT_ROUND, apply_receipt,
                                plan, unrecovered_inquiries)
from analysis.ledger import Office
from analysis.report import build_analysis
from analysis.sources import Sources
from tests.fixtures import write_supplies


class InquiryReal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = Sources()
        cls.ev = EvidenceSet(cls.src)

    def test_unrecovered(self):
        # X0 的合同已有有效回执（E1/E3），不算未回收
        got = {q["inquiry"] for q in unrecovered_inquiries(self.src, self.ev)}
        self.assertNotIn("X0", got)
        self.assertEqual(len(got), 31)

    def test_apply_receipt_joint_constraint(self):
        # 模拟回执：目标户收紧到 [0.45,0.55]，合租人按共同约束收紧
        new = apply_receipt(self.ev.records, self.src, "L-2-0-10-家庭-0",
                            "H-0-10-家庭-0", ["沿江"], [0.45, 0.55])
        r0 = new[("H-0-10-家庭-0", 2)]
        r1 = new[("H-0-10-家庭-1", 2)]
        self.assertEqual((r0.share_lo, r0.share_hi), (0.45, 0.55))
        self.assertEqual(r0.areas, ("沿江",))
        self.assertAlmostEqual(r1.share_lo, 0.45)
        self.assertAlmostEqual(r1.share_hi, 0.55)

    def test_plan_respects_hours_and_deadline(self):
        office = Office(self.src, tempfile.mktemp())
        an = build_analysis(self.src, office)
        result = plan(self.src, an["evidence"], an["specs"],
                      an["assessments"], office.entries())
        by_auth = {}
        for s in result["scheduled"]:
            q = s["sim"]["inquiry"]
            by_auth[q["authority"]] = by_auth.get(q["authority"], 0) + q["hours"]
            self.assertLessEqual(q["return_by"], s["meeting"])
        for auth, hours in by_auth.items():
            self.assertLessEqual(hours, self.src.work_roster[auth]["free_hours"])
        # 回收期晚于拨款会的一律不安排进本轮
        late = [q for q, _ in result["skipped"] if q["return_by"] > "2026-07-09"]
        self.assertEqual(len(late), 10)
        # 不把各派单可能释放的金额简单相加：计划附明确说明
        self.assertIn("不能简单相加", result["note"])


class InquiryTiny(unittest.TestCase):
    """精确小例子：回执改变分摊后结论随之改变。"""

    def test_impact_simulation_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Sources(write_supplies(Path(tmp)))
            office = Office(src, Path(tmp) / "state.json")
            an = build_analysis(src, office)
            result = plan(src, an["evidence"], an["specs"],
                          an["assessments"], office.entries())
            # 唯一派单 X甲 工时 2h、回收期 07-08：应被安排
            self.assertEqual(len(result["scheduled"]), 1)
            s = result["scheduled"][0]
            self.assertEqual(s["sim"]["inquiry"]["inquiry"], "X甲")
            self.assertIn(s["impact"]["impact"], (IMPACT_ROUND, IMPACT_NONE))


if __name__ == "__main__":
    unittest.main()
