# -*- coding: utf-8 -*-
"""Integration tests for analysis API endpoints."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.app import create_app
from src.config import Config


class _FakeTaskInfo:
    def __init__(self, task_id: str, stock_code: str):
        self.task_id = task_id
        self.stock_code = stock_code


class _FakeTaskQueue:
    def __init__(self) -> None:
        self.submitted = []

    def is_analyzing(self, stock_code: str) -> bool:
        return False

    def get_analyzing_task_id(self, stock_code: str):
        return None

    def submit_task(self, stock_code: str, stock_name=None, report_type="detailed", force_refresh=False):
        self.submitted.append(
            {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "report_type": report_type,
                "force_refresh": force_refresh,
            }
        )
        return _FakeTaskInfo(task_id=f"task_{stock_code}", stock_code=stock_code)


class AnalysisApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_path = Path(self.temp_dir.name) / ".env"
        self.env_path.write_text("STOCK_LIST=600519,000858\n", encoding="utf-8")
        os.environ["ENV_FILE"] = str(self.env_path)
        Config.reset_instance()

        app = create_app(static_dir=Path(self.temp_dir.name) / "empty-static")
        self.client = TestClient(app)

    def tearDown(self) -> None:
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        self.temp_dir.cleanup()

    @patch("src.services.analysis_service.AnalysisService.analyze_stocks")
    def test_sync_batch_analysis_returns_multiple_results(self, mock_analyze_stocks) -> None:
        mock_analyze_stocks.return_value = {
            "query_id": "batch_query_001",
            "total": 2,
            "success_count": 2,
            "failed_count": 0,
            "results": [
                {
                    "stock_code": "600519",
                    "stock_name": "贵州茅台",
                    "report": {
                        "meta": {
                            "query_id": "batch_query_001",
                            "stock_code": "600519",
                            "stock_name": "贵州茅台",
                            "report_type": "detailed",
                            "created_at": "2026-04-19T18:30:00",
                        },
                        "summary": {
                            "analysis_summary": "趋势偏强",
                            "operation_advice": "持有",
                            "trend_prediction": "看多",
                            "sentiment_score": 72,
                            "sentiment_label": "乐观",
                        },
                    },
                },
                {
                    "stock_code": "000858",
                    "stock_name": "五粮液",
                    "report": {
                        "meta": {
                            "query_id": "batch_query_001",
                            "stock_code": "000858",
                            "stock_name": "五粮液",
                            "report_type": "detailed",
                            "created_at": "2026-04-19T18:30:00",
                        },
                        "summary": {
                            "analysis_summary": "继续观察",
                            "operation_advice": "观望",
                            "trend_prediction": "震荡",
                            "sentiment_score": 55,
                            "sentiment_label": "中性",
                        },
                    },
                },
            ],
            "failed_stock_codes": [],
            "created_at": "2026-04-19T18:30:00",
        }

        response = self.client.post(
            "/api/v1/analysis/analyze",
            json={
                "stock_codes": ["600519", "000858"],
                "report_type": "detailed",
                "force_refresh": True,
                "async_mode": False,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["success_count"], 2)
        self.assertEqual(len(payload["results"]), 2)
        self.assertEqual(payload["results"][0]["stock_code"], "600519")
        self.assertEqual(payload["results"][1]["stock_code"], "000858")

        mock_analyze_stocks.assert_called_once()
        kwargs = mock_analyze_stocks.call_args.kwargs
        self.assertEqual(kwargs["stock_codes"], ["600519", "000858"])
        self.assertTrue(kwargs["force_refresh"])
        self.assertEqual(kwargs["report_type"], "detailed")

    def test_async_batch_analysis_returns_task_ids(self) -> None:
        fake_queue = _FakeTaskQueue()

        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=fake_queue):
            response = self.client.post(
                "/api/v1/analysis/analyze",
                json={
                    "stock_codes": ["600519", "000858"],
                    "report_type": "simple",
                    "force_refresh": True,
                    "async_mode": True,
                },
            )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["task_ids"], ["task_600519", "task_000858"])
        self.assertEqual(payload["stock_codes"], ["600519", "000858"])
        self.assertEqual(len(fake_queue.submitted), 2)
        self.assertTrue(all(item["force_refresh"] for item in fake_queue.submitted))


if __name__ == "__main__":
    unittest.main()
