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
    def _two_holdings(self):
        holdings = [
            {"code": "600519", "qty": 100, "cost_price": 1650.5, "note": "茅台", "currency": "CNY"},
            {"code": "AAPL", "qty": 50, "cost_price": 180.2, "currency": "USD"},
        ]
        results = [
            FakeResult(code="600519", name="贵州茅台", current_price=1620.0, change_pct=0.42),
            FakeResult(code="AAPL", name="Apple", current_price=195.3, change_pct=-0.21),
        ]
        return holdings, results

    def test_basic_render(self):
        holdings, results = self._two_holdings()
        md = build_portfolio_summary(holdings, results, date_str="2026-05-25")
        self.assertIn("💼 2026-05-25 持仓概览", md)
        self.assertIn("贵州茅台", md)
        self.assertIn("Apple", md)
        # P&L%: (1620-1650.5)/1650.5 = -1.85%
        self.assertIn("-1.85%", md)
        # P&L%: (195.3-180.2)/180.2 = +8.38%
        self.assertIn("+8.38%", md)
        # 今日%
        self.assertIn("+0.42%", md)
        self.assertIn("-0.21%", md)
        # 新表头：含市值/盈亏/仓位%
        self.assertIn("| 代码 | 名称 | 持仓 | 成本 | 现价 | 市值 | 盈亏 | 总盈亏% | 仓位% | 今日% |", md)

    def test_b1_currency_overview(self):
        """B+1: 按货币聚合总览。"""
        holdings, results = self._two_holdings()
        md = build_portfolio_summary(holdings, results)
        # 资金总览段存在
        self.assertIn("💰 资金总览", md)
        # CNY 部分：市值 = 100 * 1620 = 162000；盈亏金额 = -3050
        self.assertIn("¥162,000", md)
        self.assertIn("¥3,050", md)  # 金额（带正负号 -）
        # USD 部分：市值 = 50 * 195.3 = 9765；盈亏金额 = +755
        self.assertIn("$9,765", md)
        self.assertIn("$755", md)
        # 涵盖 2 个币种
        self.assertIn("2 个币种", md)

    def test_b1_position_weight(self):
        """B+1: 同币种内仓位权重%。"""
        # 两只 A 股，仓位约 60/40
        holdings = [
            {"code": "600519", "qty": 100, "cost_price": 1000, "currency": "CNY"},  # 市值 = 120000
            {"code": "000001", "qty": 100, "cost_price": 1000, "currency": "CNY"},  # 市值 = 80000
        ]
        results = [
            FakeResult(code="600519", name="A", current_price=1200, change_pct=0),
            FakeResult(code="000001", name="B", current_price=800, change_pct=0),
        ]
        md = build_portfolio_summary(holdings, results)
        # 仓位%：60.0% 与 40.0%
        self.assertIn("60.0%", md)
        self.assertIn("40.0%", md)

    def test_b2_weighted_today_pct(self):
        """B+2: 按市值加权的今日%。"""
        holdings = [
            {"code": "A1", "qty": 100, "cost_price": 100, "currency": "CNY"},  # 市值 = 12000，今日 +5
            {"code": "A2", "qty": 100, "cost_price": 100, "currency": "CNY"},  # 市值 = 8000，今日 -2.5
        ]
        results = [
            FakeResult(code="A1", name="多头", current_price=120, change_pct=5.0),
            FakeResult(code="A2", name="空头", current_price=80, change_pct=-2.5),
        ]
        md = build_portfolio_summary(holdings, results)
        # 加权: (12000*5 + 8000*-2.5)/(12000+8000) = (60000-20000)/20000 = 2.0%
        self.assertIn("CNY 🟢 +2.00%", md)

    def test_b2_top_movers(self):
        """B+2: Top 涨跌榜。"""
        holdings, results = self._two_holdings()
        md = build_portfolio_summary(holdings, results)
        self.assertIn("📈 今日组合动态", md)
        # 涨幅榜应包含茅台（+0.42%），跌幅榜应包含 Apple（-0.21%）
        self.assertIn("🟢 涨幅榜", md)
        self.assertIn("🔴 跌幅榜", md)

    def test_missing_result_shows_dash(self):
        holdings = [{"code": "600519", "qty": 100, "cost_price": 1650.5, "currency": "CNY"}]
        md = build_portfolio_summary(holdings, results=[])
        self.assertIn("—", md)
        self.assertIn("1 只持仓未在本次分析结果中找到", md)

    def test_partial_data_still_aggregates(self):
        """部分股票缺数据时，总盈亏只统计有数据的部分。"""
        holdings = [
            {"code": "OK", "qty": 100, "cost_price": 100, "currency": "CNY"},
            {"code": "MISSING", "qty": 100, "cost_price": 100, "currency": "CNY"},
        ]
        results = [FakeResult(code="OK", name="A", current_price=110, change_pct=1.0)]
        md = build_portfolio_summary(holdings, results)
        # 应只对 OK 计算盈亏，pnl=1000，pct=10% (基于成本 10000，只看有数据的)
        self.assertIn("¥1,000", md)
        # 缺失统计
        self.assertIn("1 只持仓未在本次分析结果中找到", md)

    def test_empty_holdings_returns_empty_string(self):
        self.assertEqual(build_portfolio_summary([], results=[]), "")


class TestPromptInjection(unittest.TestCase):
    """B+7: 验证 analyzer 的 prompt 在 context['holding'] 存在时注入持仓段落。"""

    def test_format_prompt_with_holding(self):
        try:
            from src.analyzer import GeminiAnalyzer
        except Exception as exc:
            self.skipTest(f"analyzer import failed (env deps): {exc}")
            return

        analyzer = GeminiAnalyzer.__new__(GeminiAnalyzer)
        context = {
            'code': '600519',
            'stock_name': '贵州茅台',
            'date': '2026-05-25',
            'today': {'close': 1620.0, 'open': 1615.0, 'high': 1630.0, 'low': 1610.0,
                      'pct_chg': 0.42, 'volume': 1000000, 'amount': 1620000000,
                      'ma5': 1625, 'ma10': 1630, 'ma20': 1640},
            'realtime': {'price': 1620.0},
            'holding': {
                'qty': 100,
                'cost_price': 1650.50,
                'currency': 'CNY',
                'note': '茅台',
                'pnl_pct': -1.85,
            },
        }
        prompt = analyzer._format_prompt(context, '贵州茅台', news_context='')
        self.assertIn('用户持仓状态', prompt)
        self.assertIn('1650.50', prompt)
        self.assertIn('100', prompt)  # qty
        self.assertIn('-1.85%', prompt)
        # 浮亏 -1.85% 落入「微利/微亏」桶
        self.assertIn('盈亏平衡区', prompt)

    def test_format_prompt_without_holding_unchanged(self):
        try:
            from src.analyzer import GeminiAnalyzer
        except Exception as exc:
            self.skipTest(f"analyzer import failed (env deps): {exc}")
            return
        analyzer = GeminiAnalyzer.__new__(GeminiAnalyzer)
        context = {
            'code': '600519',
            'stock_name': '贵州茅台',
            'date': '2026-05-25',
            'today': {'close': 1620.0, 'open': 1615.0, 'high': 1630.0, 'low': 1610.0,
                      'pct_chg': 0.42, 'volume': 1000000, 'amount': 1620000000,
                      'ma5': 1625, 'ma10': 1630, 'ma20': 1640},
        }
        prompt = analyzer._format_prompt(context, '贵州茅台', news_context='')
        self.assertNotIn('用户持仓状态', prompt)


if __name__ == "__main__":
    unittest.main()
