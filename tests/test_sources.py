"""检查业务录入资料能否对账；不计算业务结果。"""

import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SourceContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = json.loads((ROOT / "data" / "supplies.json").read_text(encoding="utf-8"))
        cls.schema = json.loads((ROOT / "data" / "fields.json").read_text(encoding="utf-8"))

    def rows(self, table):
        return self.source["tables"][table]

    def references(self, table, field, reference_table, reference_field):
        values = {row[reference_field] for row in self.rows(reference_table)}
        for row in self.rows(table):
            if row[field] is not None:
                self.assertIn(row[field], values, (table, row))

    def test_fields(self):
        self.assertTrue(self.source["synthetic"])
        self.assertEqual(set(self.schema), set(self.source["tables"]))
        for table, fields in self.schema.items():
            self.assertTrue(self.rows(table))
            for row in self.rows(table):
                self.assertEqual(set(fields), set(row), table)
                for field, accepted in fields.items():
                    self.assertIn(type(row[field]).__name__, accepted, (table, field))

    def test_source_relationships(self):
        self.references('frame', 'area', 'areas', 'area')
        self.references('contracts', 'lease', 'frame', 'lease')
        self.references('interviews', 'household', 'frame', 'household')
        self.references('claims', 'lease', 'contracts', 'lease')
        self.references('receipts', 'lease', 'contracts', 'lease')
        self.assertGreater(len(self.rows('frame')), 1000)
        self.assertTrue(any(row['income_low'] is None for row in self.rows('interviews')))
        self.assertTrue(any(len(row['possible_areas']) > 1 for row in self.rows('claims')))
