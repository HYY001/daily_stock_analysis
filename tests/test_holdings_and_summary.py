"""Unit tests for src.holdings + src.portfolio_summary (B 方案)。

只测纯函数 / 文件 IO 部分，不涉及 LLM / 行情拉取。
"""
import json
import os
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Ensure project root in path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.holdings import (
    holdings_to_codes,
    infer_currency,
    load_holdings,
)
from src.portfolio_summary import build_portfolio_summary


@dataclass
class FakeResult:
    code: str
    name: str = ""
    current_price: Optional[float] = None
    change_pct: Optional[float] = None


class TestInferCurrency(unittest.TestCase):
    def test_a_share(self):
        self.assertEqual(infer_currency("600519"), "CNY")
        self.assertEqual(infer_currency("000001"), "CNY")
        self.assertEqual(infer_currency("300750"), "CNY")

    def test_hk_share(self):
        self.assertEqual(infer_currency("hk00700"), "HKD")
        self.assertEqual(infer_currency("HK09988"), "HKD")

    def test_us_share(self):
        self.assertEqual(infer_currency("AAPL"), "USD")
        self.assertEqual(infer_currency("TSLA"), "USD")
        self.assertEqual(infer_currency(""), "USD")


class TestLoadHoldings(unittest.TestCase):
    def test_load_from_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({
                "holdings": [
                    {"code": "600519", "qty": 100, "cost_price": 1650.5, "note": "茅台"},
                    {"code": "AAPL", "qty": 50, "cost_price": 180.2},
                ]
            }, f)
            path = Path(f.name)
        try:
            holdings = load_holdings(path)
            self.assertEqual(len(holdings), 2)
            self.assertEqual(holdings[0]["code"], "600519")
            self.assertEqual(holdings[0]["currency"], "CNY")
            self.assertEqual(holdings[0]["note"], "茅台")
            self.assertEqual(holdings[1]["currency"], "USD")
            self.assertIsNone(holdings[1]["note"])
        finally:
            path.unlink()

    def test_drops_invalid_records(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({
                "holdings": [
                    {"code": "600519", "qty": 100, "cost_price": 1650.5},
                    {"code": "", "qty": 10, "cost_price": 100},  # missing code
                    {"code": "BAD", "qty": -5, "cost_price": 100},  # negative qty
                    {"code": "BAD2", "qty": "abc", "cost_price": 100},  # non-numeric
                ]
            }, f)
            path = Path(f.name)
        try:
            holdings = load_holdings(path)
            self.assertEqual(len(holdings), 1)
            self.assertEqual(holdings[0]["code"], "600519")
        finally:
            path.unlink()

    def test_load_from_env(self):
        os.environ["HOLDINGS_JSON"] = json.dumps({
            "holdings": [{"code": "TSLA", "qty": 10, "cost_price": 200}]
        })
        try:
            # Pass a non-existent path so env fallback kicks in
            holdings = load_holdings(Path("/tmp/nonexistent_holdings_xyz.json"))
            self.assertEqual(len(holdings), 1)
            self.assertEqual(holdings[0]["code"], "TSLA")
        finally:
            del os.environ["HOLDINGS_JSON"]

    def test_top_level_list_also_works(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump([{"code": "AAPL", "qty": 1, "cost_price": 100}], f)
            path = Path(f.name)
        try:
            holdings = load_holdings(path)
            self.assertEqual(len(holdings), 1)
        finally:
            path.unlink()

    def test_holdings_to_codes_dedup(self):
        h = [
            {"code": "600519"},
            {"code": "AAPL"},
            {"code": "600519"},  # duplicate
        ]
        self.assertEqual(holdings_to_codes(h), ["600519", "AAPL"])


class TestPortfolioSummary(unittest.TestCase):
    def test_basic_render(self):
        holdings = [
            {"code": "600519", "qty": 100, "cost_price": 1650.5, "note": "茅台", "currency": "CNY"},
            {"code": "AAPL", "qty": 50, "cost_price": 180.2, "currency": "USD"},
        ]
        results = [
            FakeResult(code="600519", name="贵州茅台", current_price=1620.0, change_pct=0.42),
            FakeResult(code="AAPL", name="Apple", current_price=195.3, change_pct=-0.21),
        ]
        md = build_portfolio_summary(holdings, results, date_str="2026-05-25")
        self.assertIn("💼 2026-05-25 持仓概览", md)
        self.assertIn("贵州茅台", md)
        self.assertIn("Apple", md)
        # P&L: (1620 - 1650.5)/1650.5 = -1.85% → 🔴
        self.assertIn("🔴", md)
        self.assertIn("-1.85%", md)
        # P&L: (195.3 - 180.2)/180.2 = +8.38% → 🟢
        self.assertIn("+8.38%", md)
        # 今日 change: 0.42 → 🟢
        self.assertIn("+0.42%", md)
        # 表格头存在
        self.assertIn("| 代码 | 名称 | 持仓 | 成本 | 现价 | 总盈亏% | 今日% |", md)

    def test_missing_result_shows_dash(self):
        holdings = [{"code": "600519", "qty": 100, "cost_price": 1650.5}]
        md = build_portfolio_summary(holdings, results=[])
        self.assertIn("—", md)  # em-dash for missing price
        self.assertIn("1 只持仓未在本次分析结果中找到", md)

    def test_empty_holdings_returns_empty_string(self):
        self.assertEqual(build_portfolio_summary([], results=[]), "")


if __name__ == "__main__":
    unittest.main()
