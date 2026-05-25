"""持仓数据加载与校验。

支持两种来源（按优先级）：
1. data/holdings.json 文件
2. HOLDINGS_JSON 环境变量（GitHub Actions 友好，整段 JSON 作为单个 secret）

字段：
    code (str):       股票代码，例如 "600519"、"AAPL"、"hk00700"
    qty (int|float):  持仓数量（股）
    cost_price (float): 平均成本价
    note (str, 可选): 自由备注

公开 API:
    load_holdings(env_path=None) -> List[Dict]      # 已校验
    infer_currency(code: str) -> str                # "CNY" / "HKD" / "USD"
    holdings_to_codes(holdings) -> List[str]
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_HOLDINGS_PATH = _PROJECT_ROOT / "data" / "holdings.json"


def infer_currency(code: str) -> str:
    """按股票代码前缀推断货币。

    A股（6/0/3 开头的 6 位数字） -> CNY
    港股（以 hk 开头，不区分大小写）-> HKD
    其余视为美股 -> USD
    """
    if not code:
        return "USD"
    c = code.strip()
    cl = c.lower()
    if cl.startswith("hk"):
        return "HKD"
    if c.isdigit() and len(c) == 6 and c[0] in {"0", "3", "6"}:
        return "CNY"
    return "USD"


def _validate_one(idx: int, item: Any) -> Optional[Dict[str, Any]]:
    """校验单条持仓记录。无效返回 None 并打 warning。"""
    if not isinstance(item, dict):
        logger.warning(f"holdings[{idx}] 不是对象，已跳过: {item!r}")
        return None
    code = str(item.get("code", "")).strip()
    if not code:
        logger.warning(f"holdings[{idx}] 缺少 code，已跳过")
        return None
    try:
        qty = float(item.get("qty", 0))
        cost = float(item.get("cost_price", 0))
    except (TypeError, ValueError):
        logger.warning(f"holdings[{idx}] qty 或 cost_price 非数字，已跳过: {item!r}")
        return None
    if qty <= 0 or cost <= 0:
        logger.warning(f"holdings[{idx}] qty/cost_price 必须为正数，已跳过: {item!r}")
        return None
    return {
        "code": code,
        "qty": qty,
        "cost_price": cost,
        "note": str(item.get("note", "")).strip() or None,
        "currency": infer_currency(code),
    }


def _parse_payload(raw: Any) -> List[Dict[str, Any]]:
    """接受 {'holdings': [...]} 或顶层列表两种格式。"""
    if isinstance(raw, dict):
        items = raw.get("holdings", [])
    elif isinstance(raw, list):
        items = raw
    else:
        logger.error(f"holdings 数据结构无法识别（顶层既不是 dict 也不是 list）: {type(raw)}")
        return []
    out: List[Dict[str, Any]] = []
    for i, item in enumerate(items):
        v = _validate_one(i, item)
        if v:
            out.append(v)
    return out


def load_holdings(holdings_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """加载持仓。文件优先，环境变量兜底；都没有则返回空列表。"""
    path = holdings_path or _DEFAULT_HOLDINGS_PATH
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            holdings = _parse_payload(data)
            if holdings:
                logger.info(f"从 {path} 加载了 {len(holdings)} 条持仓")
            return holdings
        except json.JSONDecodeError as e:
            logger.error(f"holdings.json 解析失败: {e}")
            return []
        except Exception as e:
            logger.error(f"读取 holdings.json 失败: {e}")
            return []

    env_payload = os.getenv("HOLDINGS_JSON", "").strip()
    if env_payload:
        try:
            data = json.loads(env_payload)
            holdings = _parse_payload(data)
            if holdings:
                logger.info(f"从环境变量 HOLDINGS_JSON 加载了 {len(holdings)} 条持仓")
            return holdings
        except json.JSONDecodeError as e:
            logger.error(f"HOLDINGS_JSON 环境变量 JSON 解析失败: {e}")
            return []

    logger.debug("未配置持仓数据（缺少 data/holdings.json 与 HOLDINGS_JSON 环境变量）")
    return []


def holdings_to_codes(holdings: List[Dict[str, Any]]) -> List[str]:
    """从持仓列表抽取股票代码（保持顺序、去重）。"""
    seen = set()
    out: List[str] = []
    for h in holdings:
        code = h.get("code")
        if code and code not in seen:
            seen.add(code)
            out.append(code)
    return out
