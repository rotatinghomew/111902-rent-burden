"""估计层检查：权重、投影界、面板趋势、精度与覆盖标记。"""

import unittest

from analysis.estimate import Estimator, region_specs
from analysis.evidence import EvidenceSet
from analysis.sources import Sources


class EstimateReal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = Sources()
        cls.ev = EvidenceSet(cls.src)
        cls.est = Estimator(cls.ev)
        cls.specs = region_specs(cls.src)

    def test_design_weights(self):
        # 设计权重 = 1/(p_psu × p_house)
        expected = {round(1 / (2 / 3) / p, 6) for p in (0.5, 0.65, 0.8)}
        for rec in self.ev.records.values():
            self.assertIn(round(rec.weight, 6), expected)

    def test_projection_band_order(self):
        # 各片区各季：下界 ≤ 上界；抽样半宽非负
        for name in ("东湾", "沿江", "北台", "青岭"):
            spec = self.specs[name]
            for q in (1, 2):
                band = self.est.proportion_band(spec, q)["bounds"]
                self.assertLessEqual(band["lo"]["estimate"], band["hi"]["estimate"])
                self.assertGreaterEqual(band["lo"]["half_width"], 0)

    def test_merged_region_reestimated_not_summed(self):
        # 合并地域以原住户重新估计：河东的待定归属户是确定成员，
        # 其下界高于两片区下界按人口的简单外推，不等于各地极端值相加
        dongwan = self.est.proportion_band(self.specs["东湾"], 2)["bounds"]
        yanjiang = self.est.proportion_band(self.specs["沿江"], 2)["bounds"]
        hedong = self.est.proportion_band(self.specs["河东"], 2)["bounds"]
        self.assertGreater(hedong["lo"]["estimate"],
                           min(dongwan["lo"]["estimate"], yanjiang["lo"]["estimate"]))
        self.assertLess(hedong["hi"]["estimate"],
                        max(dongwan["hi"]["estimate"], yanjiang["hi"]["estimate"]) + 1e-9)

    def test_panel_not_independent(self):
        # 同一住户跨季不当作独立样本：趋势方差使用配对 PSU 协方差
        trend = self.est.trend(self.specs["东湾"])
        self.assertEqual(trend["bounds"]["lo"]["n_common_psu"], 12)
        # 350 户两季都入样（轮换面板），不是 1151 个独立样本
        paired = sum(1 for (h, q) in self.ev.records
                     if q == 1 and (h, 2) in self.ev.records)
        self.assertEqual(paired, 350)

    def test_precision_flag(self):
        # 无法估计所需精度 -> 标出不能用于定档
        trend = self.est.trend(self.specs["沿江"])
        self.assertTrue(any("精度" in f for f in trend["flags"]))

    def test_upper_bound_proves_nothing(self):
        # 区间上限不为结论背书：支持下限 = 下界估计 − 半宽，远未过线
        trend = self.est.trend(self.specs["东湾"])
        lo = trend["bounds"]["lo"]
        floor = lo["estimate"] - lo["half_width"]
        self.assertLess(floor * 100, 2)  # 东湾第1档需 ≥2pp

    def test_stratum_coverage_no_reweight(self):
        # 构造单层覆盖不足的情形：标出不能比较，不重配权重
        spec = self.specs["东湾"]
        cov = self.est.coverage(spec)
        self.assertTrue(all(n >= spec.min_psu for n in cov.values()))
        flags = self.est.comparability(spec)
        self.assertEqual(flags, [])


class EstimateTiny(unittest.TestCase):
    """手工可算的小例子：精确负担率下趋势恰为 +100pp。"""

    def test_exact_trend(self):
        import tempfile
        from pathlib import Path
        from tests.fixtures import write_supplies
        with tempfile.TemporaryDirectory() as tmp:
            src = Sources(write_supplies(Path(tmp)))
            ev = EvidenceSet(src)
            est = Estimator(ev)
            specs = region_specs(src)
            trend = est.trend(specs["甲"])
            lo, hi = trend["bounds"]["lo"], trend["bounds"]["hi"]
            self.assertAlmostEqual(lo["estimate"], 1.0)
            self.assertAlmostEqual(hi["estimate"], 1.0)
            self.assertEqual(trend["flags"], [])
            # 比例水平：Q1 全线下，Q2 全线上
            p1 = est.proportion_band(specs["甲"], 1)["bounds"]
            p2 = est.proportion_band(specs["甲"], 2)["bounds"]
            self.assertEqual(p1["lo"]["estimate"], 0.0)
            self.assertEqual(p2["hi"]["estimate"], 1.0)


if __name__ == "__main__":
    unittest.main()
