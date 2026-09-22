"""FastAPI 接口集成测试：使用临时 SQLite 文件。"""

import json
import os
import sqlite3
import tempfile
import unittest

# 必须在导入 app.* 之前设置数据库路径（app.database 在导入时读取该变量）
_TMPDIR = tempfile.mkdtemp(prefix="beat-align-test-")
os.environ["ALIGN_DB_PATH"] = os.path.join(_TMPDIR, "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app import database  # noqa: E402
from app.main import app  # noqa: E402

REF = [0.0, 0.5, 1.0, 1.5, 2.0]
PERF = [0.0, 0.48, 1.52, 2.0]  # 漏第 3 拍(1.0)，且有轻微抢/拖


class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._cm = TestClient(app)
        cls.client = cls._cm.__enter__()
        cls.addClassCleanup(cls._cm.__exit__, None, None, None)

    def test_health(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok"})

    def test_full_flow(self):
        # 1. 创建
        r = self.client.post("/jobs", json={
            "name": "take-01",
            "reference_beats": REF,
            "performance_onsets": PERF,
            "params": {"band_width": 2},
        })
        self.assertEqual(r.status_code, 201, r.text)
        job = r.json()
        self.assertEqual(job["status"], "pending")
        job_id = job["id"]

        # 2. 对齐
        r = self.client.post(f"/jobs/{job_id}/align")
        self.assertEqual(r.status_code, 200, r.text)
        done = r.json()
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["result"]["statistics"]["missed_count"], 1)
        self.assertEqual(done["result"]["statistics"]["matched_count"], 4)

        # 3. 查询
        r = self.client.get(f"/jobs/{job_id}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "done")

        r = self.client.get(f"/jobs/{job_id}/result")
        self.assertEqual(r.status_code, 200)
        self.assertIn("matched_beats", r.json()["result"])

        r = self.client.get("/jobs")
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(len(r.json()), 1)

        # 4. 重复对齐不重算；force=true 可重算
        r = self.client.post(f"/jobs/{job_id}/align")
        self.assertEqual(r.status_code, 200)
        r = self.client.post(f"/jobs/{job_id}/align?force=true")
        self.assertEqual(r.status_code, 200)

    def test_404(self):
        r = self.client.get("/jobs/999999")
        self.assertEqual(r.status_code, 404)
        r = self.client.post("/jobs/999999/align")
        self.assertEqual(r.status_code, 404)

    def test_result_before_align_conflicts(self):
        r = self.client.post("/jobs", json={
            "reference_beats": REF, "performance_onsets": REF,
        })
        job_id = r.json()["id"]
        r = self.client.get(f"/jobs/{job_id}/result")
        self.assertEqual(r.status_code, 409)

    def test_band_too_narrow_rejected(self):
        r = self.client.post("/jobs", json={
            "reference_beats": [0.0, 0.5, 1.0, 1.5],
            "performance_onsets": [0.0],
            "params": {"band_width": 1},
        })
        self.assertEqual(r.status_code, 400)

    def test_non_monotonic_rejected(self):
        r = self.client.post("/jobs", json={
            "reference_beats": [0.0, 0.6, 0.5],
            "performance_onsets": [0.0, 0.5],
        })
        self.assertEqual(r.status_code, 422)

    # ---------- 锚点硬约束 ----------

    def test_anchor_full_flow_and_persistence(self):
        body = {
            "name": "anchored",
            "reference_beats": [0.0, 1.0, 2.0, 3.0],
            "performance_onsets": [0.0, 1.0, 2.0, 10.0],
            "params": {"band_width": 2, "skip_penalty": 0.01, "insert_penalty": 0.01},
            "anchors": [{"ref_index": 3, "perf_index": 3}],
        }
        r = self.client.post("/jobs", json=body)
        self.assertEqual(r.status_code, 201, r.text)
        job = r.json()
        self.assertEqual(job["anchors"], [{"ref_index": 3, "perf_index": 3}])
        job_id = job["id"]

        r = self.client.post(f"/jobs/{job_id}/align")
        self.assertEqual(r.status_code, 200, r.text)
        result = r.json()["result"]
        pairs = [(b["ref_index"], b["perf_index"]) for b in result["matched_beats"]]
        self.assertEqual(pairs, [(0, 0), (1, 1), (2, 2), (3, 3)])
        # result 中回显已采用锚点
        self.assertEqual(len(result["anchors"]), 1)
        self.assertEqual(result["anchors"][0]["ref_index"], 3)
        self.assertEqual(result["anchors"][0]["perf_index"], 3)

        # 任务查询（完整 / 仅结果 / 列表）均兼容
        r = self.client.get(f"/jobs/{job_id}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["anchors"], [{"ref_index": 3, "perf_index": 3}])
        self.assertEqual(r.json()["result"]["anchors"][0]["perf_index"], 3)
        r = self.client.get(f"/jobs/{job_id}/result")
        self.assertEqual(r.json()["result"]["anchors"][0]["ref_index"], 3)

    def test_job_without_anchors_defaults_empty(self):
        r = self.client.post("/jobs", json={
            "reference_beats": REF, "performance_onsets": REF,
        })
        self.assertEqual(r.status_code, 201)
        job = r.json()
        self.assertEqual(job["anchors"], [])
        r = self.client.post(f"/jobs/{job['id']}/align")
        self.assertEqual(r.json()["result"]["anchors"], [])

    def test_anchor_ref_not_increasing_400(self):
        r = self.client.post("/jobs", json={
            "reference_beats": REF, "performance_onsets": REF,
            "params": {"band_width": 2},
            "anchors": [
                {"ref_index": 1, "perf_index": 0},
                {"ref_index": 1, "perf_index": 2},
            ],
        })
        self.assertEqual(r.status_code, 400)
        self.assertIn("严格递增", r.json()["detail"])

    def test_anchor_perf_not_increasing_400(self):
        r = self.client.post("/jobs", json={
            "reference_beats": REF, "performance_onsets": REF,
            "params": {"band_width": 2},
            "anchors": [
                {"ref_index": 0, "perf_index": 2},
                {"ref_index": 2, "perf_index": 2},
            ],
        })
        self.assertEqual(r.status_code, 400)
        self.assertIn("演奏音索引必须严格递增", r.json()["detail"])

    def test_anchor_out_of_range_400(self):
        r = self.client.post("/jobs", json={
            "reference_beats": REF, "performance_onsets": REF,
            "params": {"band_width": 2},
            "anchors": [{"ref_index": 5, "perf_index": 0}],
        })
        self.assertEqual(r.status_code, 400)
        self.assertIn("越界", r.json()["detail"])

    def test_anchor_outside_band_400(self):
        r = self.client.post("/jobs", json={
            "reference_beats": REF, "performance_onsets": REF,
            "params": {"band_width": 2},
            "anchors": [{"ref_index": 0, "perf_index": 4}],
        })
        self.assertEqual(r.status_code, 400)
        self.assertIn("带宽不可达", r.json()["detail"])

    def test_anchor_negative_index_422(self):
        r = self.client.post("/jobs", json={
            "reference_beats": REF, "performance_onsets": REF,
            "anchors": [{"ref_index": -1, "perf_index": 0}],
        })
        self.assertEqual(r.status_code, 422)

    def test_legacy_db_without_anchors_column_migrates(self):
        """旧版 jobs 表（无 anchors 列）应被就地迁移且旧数据可读、可对齐。"""
        legacy_path = os.path.join(_TMPDIR, "legacy.db")
        conn = sqlite3.connect(legacy_path)
        conn.execute(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT,
                reference_beats TEXT NOT NULL, performance_onsets TEXT NOT NULL,
                params TEXT NOT NULL, status TEXT NOT NULL, error TEXT,
                result TEXT, created_at TEXT NOT NULL, aligned_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO jobs (name,reference_beats,performance_onsets,params,"
            "status,created_at) VALUES (?,?,?,?,?,?)",
            (
                "legacy",
                json.dumps(REF),
                json.dumps(REF),
                json.dumps({"band_width": 3}),
                "pending",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.commit()
        conn.close()

        old_path = database.DB_PATH
        try:
            database.DB_PATH = legacy_path
            database.init_db()  # 触发 ALTER TABLE 迁移
            r = self.client.get("/jobs/1")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["anchors"], [])
            r = self.client.post("/jobs/1/align")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["result"]["anchors"], [])
            self.assertEqual(r.json()["anchors"], [])
        finally:
            database.DB_PATH = old_path


if __name__ == "__main__":
    unittest.main()
