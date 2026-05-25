"""持仓组合摘要生成器。

输入：
    holdings: List[Dict]            # 来自 src.holdings.load_holdings()
    results : List[AnalysisResult]  # 来自 pipeline

输出：
    Markdown 字符串，包含每只持仓的总盈亏% 与 今日涨跌%。

设计：
- 不重新拉取行情：AnalysisResult 已经带 current_price 与 change_pct（分析时填入）。
- 缺数据的持仓不会被丢弃，会在表里显示「—」并在末尾标注「数据缺失 N 条」。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _fmt_pct(v: Optional[float]) -> str:
    if v is None:
        return "—"
    emoji = "🟢" if v >= 0 else "🔴"
    sign = "+" if v >= 0 else ""
    return f"{emoji} {sign}{v:.2f}%"


def _fmt_money(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v:,.2f}"


def _build_index(results: Iterable[Any]) -> Dict[str, Any]:
    """code(uppercase) -> AnalysisResult。"""
    idx: Dict[str, Any] = {}
    for r in results or []:
        code = getattr(r, "code", None)
        if code:
            idx[code.upper()] = r
    return idx


def _row_for_holding(h: Dict[str, Any], result: Optional[Any]) -> Tuple[List[str], Optional[float], Optional[float]]:
    """返回 (markdown-cells, pnl_pct, change_pct)。"""
    code = h["code"]
    qty = h["qty"]
    cost = h["cost_price"]
    note = h.get("note") or ""

    current_price = getattr(result, "current_price", None) if result else None
    change_pct = getattr(result, "change_pct", None) if result else None
    name = getattr(result, "name", None) if result else None
    display_name = name or note or code

    pnl_pct: Optional[float] = None
    if current_price is not None and cost > 0:
        pnl_pct = (current_price - cost) / cost * 100.0

    cells = [
        code,
        display_name,
        f"{qty:g}",
        _fmt_money(cost),
        _fmt_money(current_price),
        _fmt_pct(pnl_pct),
        _fmt_pct(change_pct),
    ]
    return cells, pnl_pct, change_pct


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

    header = "## 💼 持仓概览"
    if date_str:
        header = f"## 💼 {date_str} 持仓概览"

    lines: List[str] = [
        header,
        "",
        f"> 共 **{len(holdings)}** 只持仓",
        "",
        "| 代码 | 名称 | 持仓 | 成本 | 现价 | 总盈亏% | 今日% |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]

    missing = 0
    for h in holdings:
        result = idx.get(h["code"].upper())
        if result is None:
            missing += 1
        cells, _, _ = _row_for_holding(h, result)
        lines.append("| " + " | ".join(cells) + " |")

    lines.append("")
    if missing:
        lines.append(f"> ⚠️ {missing} 只持仓未在本次分析结果中找到（行情/分析失败），相关单元格显示 —")
        lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)
