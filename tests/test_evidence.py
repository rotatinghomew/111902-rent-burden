"""证据链与调查单位组装的检查。"""

import unittest

from analysis.evidence import EvidenceSet
from analysis.sources import Sources


class EvidenceReal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = Sources()
        cls.ev = EvidenceSet(cls.src)

    def test_unit_is_household_and_dedup(self):
        # 调查单位是住户：代理重复报送不重复计户
        cls = self.ev.report_classification()
        self.assertEqual(len(cls["代理重复报送"]), 100)
        # 每户每季至多一条记录
        self.assertEqual(len(self.ev.records), len(set(self.ev.records)))
        # 共同分担住户与只缺访谈答案的住户分开统计
        self.assertEqual(len(cls["共同分担住户"]), 384)
        self.assertEqual(len(cls["只缺访谈答案"]), 60)

    def test_receipt_chain_and_joint_constraint(self):
        # E2 被 E3 撤销：有效回执为 E1、E3
        self.assertEqual(sorted(r["evidence"] for r in self.ev.receipts), ["E1", "E3"])
        # 回执把 H-0-8-家庭-0 的分摊收紧到 [0.43,0.5]，归属限为沿江
        rec = self.ev.records[("H-0-8-家庭-0", 2)]
        self.assertEqual((rec.share_lo, rec.share_hi), (0.43, 0.5))
        self.assertEqual(rec.areas, ("沿江",))
        # 同一合同共同约束：合租人被收紧到 [0.5,0.57]
        co = self.ev.records[("H-0-8-家庭-1", 2)]
        self.assertAlmostEqual(co.share_lo, 0.5)
        self.assertAlmostEqual(co.share_hi, 0.57)

    def test_no_imputation_for_refused(self):
        # 拒答不补成中点或零：负担率区间为 [0, inf)
        refused = [r for r in self.ev.records.values() if r.refused]
        self.assertTrue(refused)
        for r in refused:
            self.assertEqual(r.ratio_lo, 0.0)
            self.assertEqual(r.ratio_hi, float("inf"))

    def test_income_interval_by_bounds(self):
        # 收入区间按上下界解释：lo 用上界收入，hi 用下界收入
        rec = self.ev.records[("H-0-0-家庭-2", 1)]  # 收入 [3119,3319]，整租
        self.assertAlmostEqual(rec.ratio_lo, rec.share_lo * rec.cost / 3319)
        self.assertAlmostEqual(rec.ratio_hi, rec.share_hi * rec.cost / 3119)

    def test_as_of_filters_evidence(self):
        # 基准日前不计后到回执
        ev = EvidenceSet(self.src, as_of="2026-07-03")
        self.assertEqual(ev.receipts, [])
        rec = ev.records[("H-0-8-家庭-0", 2)]
        self.assertEqual((rec.share_lo, rec.share_hi), (0.36, 0.64))
        self.assertEqual(rec.areas, ("东湾", "沿江"))


class EvidenceConflict(unittest.TestCase):
    def setUp(self):
        self.src = Sources()
        self.clash = {"evidence": "E99", "lease": "L-2-0-8-家庭-0",
                      "household": "H-0-8-家庭-0", "received": "2026-07-07",
                      "areas": ["东湾"], "share": [0.58, 0.64], "revokes": None}

    def test_conflict_marked_with_scope(self):
        ev = EvidenceSet(self.src, extra_receipts=[self.clash])
        self.assertEqual(len(ev.conflicts), 1)
        c = ev.conflicts[0]
        # 标出不能共同成立的证据及受影响范围
        self.assertEqual(set(c["evidence"]), {"P-2-H-0-8-家庭-0", "E1", "E3", "E99"})
        self.assertEqual(set(c["areas"]), {"东湾", "沿江"})
        rec = ev.records[("H-0-8-家庭-0", 2)]
        self.assertTrue(rec.conflicted)
        self.assertEqual((rec.share_lo, rec.share_hi), (0.0, 1.0))
        # 其他片区不受影响：冲突范围不含北台、青岭
        _, areas = ev.conflict_scope()
        self.assertNotIn("北台", areas)
        self.assertNotIn("青岭", areas)

    def test_revocation_resolves(self):
        resolve = {"evidence": "E100", "lease": "L-2-0-8-家庭-0",
                   "household": "H-0-8-家庭-0", "received": "2026-07-08",
                   "areas": ["沿江"], "share": [0.43, 0.5], "revokes": "E99"}
        ev = EvidenceSet(self.src, extra_receipts=[self.clash, resolve])
        self.assertEqual(ev.conflicts, [])


if __name__ == "__main__":
    unittest.main()
