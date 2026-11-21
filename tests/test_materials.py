"""检查交换资料的结构和记录间引用。"""
import json
import subprocess
import sys
from pathlib import Path
import unittest

from catalog import read_table

ROOT = Path(__file__).resolve().parents[1]
TABLE_KEYS = {'contracts': ['contract', 'version'], 'receipts': ['receipt', 'revision'], 'metric_bridge': ['contract', 'before', 'after'], 'report_cycles': ['cycle'], 'audiences': ['route'], 'deliveries': ['delivery', 'attempt'], 'delivery_receipts': ['delivery', 'acknowledged'], 'progress_cases': ['case']}


class MaterialsTest(unittest.TestCase):
    def rows(self, name):
        return list(read_table(name))

    def assert_subset(self, left, reference, right, key):
        self.assertLessEqual({r[reference] for r in self.rows(left)},
                             {r[key] for r in self.rows(right)})

    def test_structure(self):
        for name, columns in TABLE_KEYS.items():
            with self.subTest(name=name):
                rows = self.rows(name)
                self.assertTrue(rows)
                keys = []
                for row in rows:
                    self.assertNotIn(None, row)
                    self.assertNotIn(None, row.values())
                    value = tuple(row[field] for field in columns)
                    self.assertTrue(all(value))
                    keys.append(value)
                self.assertEqual(len(keys), len(set(keys)))

    def test_catalog_reports_input_counts(self):
        result = subprocess.check_output([sys.executable, "catalog.py"], cwd=ROOT, text=True)
        self.assertEqual(json.loads(result), {name: len(self.rows(name)) for name in TABLE_KEYS})

    def test_business_references(self):
        self.assert_subset("deliveries", "route", "audiences", "route")
        self.assert_subset("progress_cases", "contract", "contracts", "contract")
        self.assertGreater(next(row["acknowledged"] for row in self.rows("delivery_receipts") if row["delivery"] == "D02"), next(row["acknowledged"] for row in self.rows("delivery_receipts") if row["delivery"] == "D06"))
        self.assertEqual({row["state"] for row in self.rows("receipts") if row["receipt"] == "R230"}, {"signed", "revoked"})
