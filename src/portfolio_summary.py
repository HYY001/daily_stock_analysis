"""持仓组合摘要生成器（B+ 增强版）。

输入：
    holdings: List[Dict]            # 来自 src.holdings.load_holdings()
    results : List[AnalysisResult]  # 来自 pipeline

输出：
    Markdown 字符串，含以下分块：
        1. 按货币聚合的总览（B+1）
            - 总市值、总成本、总盈亏（金额 + %）
        2. 今日组合动态（B+2）
            - 加权今日变动%（按市值加权，仅在同币种内有意义）
            - Top 涨幅 / Top 跌幅
        3. 持仓明细（B+1 + B+3 合并表格）
            - 数量、成本、现价、市值、盈亏金额、总盈亏%、仓位%（同币种内）、今日%

设计原则：
- 不重新拉取行情：AnalysisResult 已经带 current_price 与 change_pct
- 不跨币种汇总（避免假设汇率），同币种内才能算权重和组合变动
- 数据缺失（current_price=None）的持仓在表格中显示「—」，并在底部统计

公开 API：
    build_portfolio_summary(holdings, results, *, date_str=None) -> str
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 货币符号
_CURRENCY_SYMBOL = {"CNY": "¥", "USD": "$", "HKD": "HK$"}


# ---------- 工具函数 ----------

def _fmt_pct(v: Optional[float]) -> str:
    if v is None:
        return "—"
    emoji = "🟢" if v >= 0 else "🔴"
    sign = "+" if v >= 0 else ""
    return f"{emoji} {sign}{v:.2f}%"


def _fmt_pct_plain(v: Optional[float], *, with_emoji: bool = True) -> str:
    """格式化 % 数字，无 emoji 版（用于总览段）。"""
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    out = f"{sign}{v:.2f}%"
    if with_emoji:
        return ("🟢 " if v >= 0 else "🔴 ") + out
    return out


def _fmt_money(v: Optional[float], symbol: str = "") -> str:
    if v is None:
        return "—"
    return f"{symbol}{v:,.2f}"


def _fmt_money_signed(v: Optional[float], symbol: str = "") -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else "-"
    return f"{sign}{symbol}{abs(v):,.2f}"


def _build_index(results: Iterable[Any]) -> Dict[str, Any]:
    """code(uppercase) -> AnalysisResult。"""
    idx: Dict[str, Any] = {}
    for r in results or []:
        code = getattr(r, "code", None)
        if code:
            idx[code.upper()] = r
    return idx


# ---------- 聚合 ----------

class _HoldingRow:
    """每只持仓的计算结果。"""
    __slots__ = (
        "code", "name", "qty", "cost_price", "current_price", "change_pct",
        "currency", "cost_value", "market_value", "pnl_amount", "pnl_pct",
    )

    def __init__(self, holding: Dict[str, Any], result: Optional[Any]):
        self.code: str = holding["code"]
        self.qty: float = float(holding["qty"])
        self.cost_price: float = float(holding["cost_price"])
        self.currency: str = holding.get("currency", "USD")
        note = holding.get("note") or ""

        self.current_price: Optional[float] = getattr(result, "current_price", None) if result else None
        self.change_pct: Optional[float] = getattr(result, "change_pct", None) if result else None
        result_name = getattr(result, "name", None) if result else None
        self.name: str = result_name or note or self.code

        self.cost_value: float = self.qty * self.cost_price
        if self.current_price is not None:
            self.market_value: Optional[float] = self.qty * self.current_price
            self.pnl_amount: Optional[float] = self.market_value - self.cost_value
            self.pnl_pct: Optional[float] = (self.current_price - self.cost_price) / self.cost_price * 100.0
        else:
            self.market_value = None
            self.pnl_amount = None
            self.pnl_pct = None


def _aggregate_by_currency(rows: List[_HoldingRow]) -> Dict[str, Dict[str, Any]]:
    """每币种聚合：cost_total / market_total / pnl_amount / pnl_pct / weighted_today_pct。"""
    agg: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        cur = r.currency
        bucket = agg.setdefault(cur, {
            "cost_total": 0.0,
            "market_total": 0.0,
            "pnl_amount": 0.0,
            "today_weighted_num": 0.0,    # 分子: sum(market_value * change_pct)
            "today_weighted_den": 0.0,    # 分母: sum(market_value)
            "has_data": False,
            "missing_count": 0,
            "total_count": 0,
        })
        bucket["total_count"] += 1
        bucket["cost_total"] += r.cost_value
        if r.market_value is not None:
            bucket["market_total"] += r.market_value
            bucket["pnl_amount"] += (r.pnl_amount or 0.0)
            bucket["has_data"] = True
            if r.change_pct is not None:
                bucket["today_weighted_num"] += r.market_value * r.change_pct
                bucket["today_weighted_den"] += r.market_value
        else:
            bucket["missing_count"] += 1

    # 计算派生比例
    for cur, b in agg.items():
        if b["cost_total"] > 0 and b["has_data"]:
            b["pnl_pct"] = b["pnl_amount"] / b["cost_total"] * 100.0
        else:
            b["pnl_pct"] = None
        if b["today_weighted_den"] > 0:
            b["today_pct"] = b["today_weighted_num"] / b["today_weighted_den"]
        else:
            b["today_pct"] = None
    return agg


# ---------- Markdown 生成 ----------

def _build_overview_block(agg: Dict[str, Dict[str, Any]]) -> List[str]:
    """B+1: 按货币聚合的总览。"""
    if not agg:
        return []
    lines = ["### 💰 资金总览", ""]
    # 表头随币种数量自适应
    for cur in sorted(agg.keys()):
        sym = _CURRENCY_SYMBOL.get(cur, cur + " ")
        b = agg[cur]
        if not b["has_data"]:
            lines.append(
                f"- **{cur}**: 成本 {_fmt_money(b['cost_total'], sym)} | 实时数据缺失"
            )
            continue
        pnl_signed = _fmt_money_signed(b["pnl_amount"], sym)
        pnl_pct_str = _fmt_pct_plain(b["pnl_pct"])
        lines.append(
            f"- **{cur}** · {b['total_count']} 只 · "
            f"市值 {_fmt_money(b['market_total'], sym)} / 成本 {_fmt_money(b['cost_total'], sym)} · "
            f"**总盈亏 {pnl_signed}**（{pnl_pct_str}）"
        )
    lines.append("")
    return lines


def _build_today_block(agg: Dict[str, Dict[str, Any]], rows: List[_HoldingRow]) -> List[str]:
    """B+2: 今日组合动态 + Top movers。"""
    lines = ["### 📈 今日组合动态", ""]

    # 加权今日% by currency
    parts = []
    for cur in sorted(agg.keys()):
        v = agg[cur].get("today_pct")
        if v is not None:
            parts.append(f"{cur} {_fmt_pct_plain(v)}")
    if parts:
        lines.append("- 按市值加权今日变动：" + " · ".join(parts))

    # Top movers: 按今日% 排序；同一只股票分散在多账户时只算一次（按 code 去重）
    seen_codes: set = set()
    movers = []
    for r in rows:
        if r.change_pct is None or r.code in seen_codes:
            continue
        seen_codes.add(r.code)
        movers.append(r)
    if movers:
        movers_sorted = sorted(movers, key=lambda x: x.change_pct, reverse=True)
        n_each = min(3, max(1, len(movers_sorted) // 2)) if len(movers_sorted) >= 4 else 1
        top_winners = [r for r in movers_sorted[:n_each] if r.change_pct >= 0]
        top_losers = [r for r in reversed(movers_sorted[-n_each:]) if r.change_pct < 0]

        if top_winners:
            ws = " · ".join(f"{r.name}({r.code}) {_fmt_pct_plain(r.change_pct)}" for r in top_winners)
            lines.append(f"- 🟢 涨幅榜：{ws}")
        if top_losers:
            ls = " · ".join(f"{r.name}({r.code}) {_fmt_pct_plain(r.change_pct)}" for r in top_losers)
            lines.append(f"- 🔴 跌幅榜：{ls}")

    lines.append("")
    return lines


def _build_table(rows: List[_HoldingRow], agg: Dict[str, Dict[str, Any]]) -> List[str]:
    """B+1 + B+3: 持仓明细表，含市值、盈亏金额、仓位%（同币种内权重）。"""
    lines = [
        "### 📋 持仓明细",
        "",
        "| 代码 | 名称 | 持仓 | 成本 | 现价 | 市值 | 盈亏 | 总盈亏% | 仓位% | 今日% |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        sym = _CURRENCY_SYMBOL.get(r.currency, "")
        # 仓位%：同币种内的市值占比；缺数据则用成本占比兜底
        bucket = agg.get(r.currency, {})
        if r.market_value is not None and bucket.get("market_total", 0) > 0:
            weight = r.market_value / bucket["market_total"] * 100.0
        elif bucket.get("cost_total", 0) > 0:
            weight = r.cost_value / bucket["cost_total"] * 100.0
        else:
            weight = None
        weight_str = "—" if weight is None else f"{weight:.1f}%"

        cells = [
            r.code,
            r.name,
            f"{r.qty:g}",
            _fmt_money(r.cost_price, sym),
            _fmt_money(r.current_price, sym),
            _fmt_money(r.market_value, sym),
            _fmt_money_signed(r.pnl_amount, sym) if r.pnl_amount is not None else "—",
            _fmt_pct(r.pnl_pct),
            weight_str,
            _fmt_pct(r.change_pct),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return lines


def build_portfolio_summary(
    holdings: List[Dict[str, Any]],
    results: Iterable[Any],
    *,
    date_str: Optional[str] = None,
) -> str:
    """生成组合摘要 Markdown 片段。

    Returns:
        Markdown 字符串；如果 holdings 为空，返回空串（调用方自行判断是否插入）。
    """
    if not holdings:
        return ""

    idx = _build_index(results)
    rows = [_HoldingRow(h, idx.get(h["code"].upper())) for h in holdings]
    agg = _aggregate_by_currency(rows)

    header = "## 💼 持仓概览"
    if date_str:
        header = f"## 💼 {date_str} 持仓概览"

    lines: List[str] = [
        header,
        "",
        f"> 共 **{len(holdings)}** 只持仓 · 涵盖 {len(agg)} 个币种",
        "",
    ]

    lines.extend(_build_overview_block(agg))
    lines.extend(_build_today_block(agg, rows))
    lines.extend(_build_table(rows, agg))

    missing = sum(1 for r in rows if r.current_price is None)
    if missing:
        lines.append(
            f"> ⚠️ {missing} 只持仓未在本次分析结果中找到（行情/分析失败），"
            f"相关单元格显示 —；总盈亏%、仓位%、组合加权变动均已排除这些持仓。"
        )
        lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)
