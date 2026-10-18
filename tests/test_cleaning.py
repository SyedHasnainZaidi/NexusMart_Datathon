"""Unit tests for parsing and cleaning rules.

Run:  python -m pytest -q        (or: python -m unittest discover -s tests)
"""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nexusmart_etl import clean  # noqa: E402
from nexusmart_etl.mappings import (CATEGORY, CHANNEL, COURIER, ORDER_STATUS, PAYMENT_METHOD,  # noqa: E402
                                    RETURN_STATUS, map_label)
from nexusmart_etl.utils import DQLog, parse_dates, parse_money, parse_quantity  # noqa: E402


class TestParsing(unittest.TestCase):
    def test_dates_mixed_formats_day_first(self):
        s = pd.Series(["2024-03-05", "05/03/2024", "Mar 5 2024", "2024-02-30", "00/00/2024", "2024-13-05", ""])
        out = parse_dates(s)
        self.assertTrue((out.iloc[:3] == pd.Timestamp("2024-03-05")).all())   # slashes are day/month/year
        self.assertTrue(out.iloc[3:].isna().all())                            # impossible dates -> NaT

    def test_money(self):
        out = parse_money(pd.Series(["Rs. 2,290", "5850", "-120", "abc", ""]))
        self.assertEqual(out.iloc[0], 2290)
        self.assertEqual(out.iloc[1], 5850)
        self.assertEqual(out.iloc[2], -120)
        self.assertTrue(np.isnan(out.iloc[3]) and np.isnan(out.iloc[4]))

    def test_quantity_words(self):
        out = parse_quantity(pd.Series(["one", "2.0", "three", "4"]))
        self.assertEqual(out.tolist(), [1, 2, 3, 4])


class TestMappings(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(map_label("FASHION ", CATEGORY), "Fashion")
        self.assertEqual(map_label("Home&Kitchen", CATEGORY), "Home & Kitchen")
        self.assertEqual(map_label("Elec.", CATEGORY), "Electronics")
        self.assertEqual(map_label("DELIVERD", ORDER_STATUS), "Delivered")
        self.assertEqual(map_label("In-Transit", ORDER_STATUS), "In Transit")
        self.assertEqual(map_label("Cancelled ", ORDER_STATUS), "Cancelled")
        self.assertEqual(map_label("MWeb", CHANNEL), "Mobile Web")
        self.assertEqual(map_label("Cash On Delivery", PAYMENT_METHOD), "COD")
        self.assertEqual(map_label("T.C.S", COURIER), "TCS")
        self.assertEqual(map_label("Declined", RETURN_STATUS), "Rejected")
        self.assertIsNone(map_label("Something new", CATEGORY))   # unmapped -> caller logs it
        self.assertEqual(map_label("", CATEGORY), "Unknown")


class TestRules(unittest.TestCase):
    def test_exact_duplicates_are_quarantined_not_dropped(self):
        dq = DQLog()
        df = pd.DataFrame({"order_id": ["O1", "O1", "O2"], "x": ["a", "a", "b"]})
        out = clean._dedupe_exact(df, "orders", dq, "order_id")
        self.assertEqual(len(out), 2)
        rej = dq.rejects_frame()
        self.assertEqual(len(rej), 1)
        self.assertEqual(rej.reason_code.iloc[0], "EXACT_DUPLICATE")

    def test_price_index_fit(self):
        days = pd.Series(np.arange(0, 500, 5), dtype=float)
        lines = pd.DataFrame({"days": days, "ratio": 1 + 0.0001 * days})
        a, b = clean._fit_price_index(lines)
        self.assertAlmostEqual(b, 0.0001, places=6)

    def test_phone_normalisation(self):
        out = clean._norm_phone(pd.Series(["0343-4328042", "+923244534611", "330 2812162", "123"]))
        self.assertEqual(out.iloc[0], "03434328042")
        self.assertEqual(out.iloc[1], "03244534611")
        self.assertEqual(out.iloc[2], "03302812162")
        self.assertTrue(pd.isna(out.iloc[3]))


class TestEndToEndSample(unittest.TestCase):
    """Runs the full cleaning on the real raw files (if present) and checks invariants."""

    @classmethod
    def setUpClass(cls):
        from nexusmart_etl import config, extract
        if not (config.RAW_DIR / "orders.csv").exists():
            raise unittest.SkipTest("raw data not present")
        raw = extract.extract()
        extract.validate_raw(raw)
        cls.dq = DQLog()
        cls.out, _ = clean.clean_all(raw, cls.dq)

    def test_no_duplicate_keys(self):
        for t, k in [("orders", "order_id"), ("order_items", "order_item_id"), ("customers", "customer_id"),
                     ("products", "product_id"), ("payments", "payment_id")]:
            self.assertFalse(self.out[t][k].duplicated().any(), t)

    def test_amount_integrity(self):
        oi = self.out["order_items"]
        self.assertTrue((oi.quantity > 0).all())
        self.assertTrue((oi.unit_price > 0).all())
        self.assertTrue((oi.discount_amount <= oi.gross_amount).all())
        self.assertTrue(np.allclose(oi.net_amount, oi.gross_amount - oi.discount_amount))

    def test_dates_in_valid_window(self):
        od = self.out["orders"].order_date
        self.assertTrue(od.between("2024-01-01", "2025-06-30").all())

    def test_every_row_accounted_for(self):
        counts = self.dq.counts_frame().set_index("table")
        rej = self.dq.rejects_frame()
        for t in ["orders", "payments", "returns", "order_items", "shipping"]:
            lost = counts.loc[t, "raw"] - counts.loc[t, "clean"]
            self.assertEqual(lost, (rej.source_table == t).sum(), t)

    def test_refund_capped(self):
        r = self.out["returns"].merge(self.out["order_items"][["order_item_id", "net_amount"]], on="order_item_id")
        self.assertTrue((r.refund_amount <= r.net_amount + 0.01).all())


if __name__ == "__main__":
    unittest.main()
