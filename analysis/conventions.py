"""分析口径约定。

负担率口径（全流程唯一出处，改动只在此处理）：
    户月负担 = 分摊比例 × (合同月租 × (月天数 − 免租日) / 月天数 + 水电)
    负担率   = 户月负担 / 月收入
收入区间按其上下界解释：上界缺失则负担率下界为 0，下界缺失则负担率上界
为正无穷；两端都缺（拒答）不补中点或零，该户负担状态完全未知。
"""

DAYS_IN_MONTH = 30

# 负担线缺省值；地区标准（standards 表）携带各自的 burden_line，以其为准。
DEFAULT_BURDEN_LINE = 0.35


def monthly_cost(contract):
    """合同月实际居住成本：月租按免租日折算后加水电。"""
    rent = contract["monthly_total"] * (DAYS_IN_MONTH - contract["free_days"]) / DAYS_IN_MONTH
    return rent + contract["utilities"]


def ratio_bounds(share_lo, share_hi, cost, income_low, income_high):
    """按上下界解释收入区间，返回负担率区间 [lo, hi]。

    收入上界缺失 -> 负担率下界为 0；收入下界缺失 -> 负担率上界为正无穷。
    拒答（两端皆缺）-> [0, inf]，不补任何点值。
    """
    lo = share_lo * cost / income_high if income_high else 0.0
    hi = share_hi * cost / income_low if income_low else float("inf")
    return lo, hi
