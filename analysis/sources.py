"""读取业务资料（sources 层只做载入与索引，不做分析判断）。"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data" / "supplies.json"


def load_tables(path=None):
    """载入 supplies.json，返回 {表名: [行...]}。"""
    path = Path(path) if path else DEFAULT_SOURCE
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["tables"]


class Sources:
    """各业务表的只读索引视图。"""

    def __init__(self, path=None):
        self.tables = load_tables(path)
        self.areas = {r["area"]: r for r in self.tables["areas"]}
        self.standards = {r["region"]: r for r in self.tables["standards"]}
        self.contracts = {r["lease"]: r for r in self.tables["contracts"]}
        self.budgets = {r["pool"]: r for r in self.tables["budgets"]}
        self.requests = {r["request"]: r for r in self.tables["requests"]}
        self.work_roster = {r["authority"]: r for r in self.tables["work_roster"]}
        self.frame = self.tables["frame"]
        self.interviews = self.tables["interviews"]
        self.reports = self.tables["reports"]
        self.claims = self.tables["claims"]
        self.receipts = self.tables["receipts"]
        self.ledger = self.tables["ledger"]
        self.inquiries = self.tables["inquiries"]

    def pool_of(self, area):
        return self.areas[area]["pool"]

    def pool_areas(self, pool):
        return [a for a, r in self.areas.items() if r["pool"] == pool]

    def meeting_date(self, pool):
        return self.budgets[pool]["meeting"]
