"""jobs 表读写。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_anchors(raw: Any) -> list[dict[str, Any]]:
    """统一锚点结构：兼容 [ref, perf] 元组形式（手工写入/旧数据）。"""
    out: list[dict[str, Any]] = []
    for a in raw or []:
        if isinstance(a, dict):
            out.append({"ref_index": a["ref_index"], "perf_index": a["perf_index"]})
        else:
            out.append({"ref_index": a[0], "perf_index": a[1]})
    return out


def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    keys = row.keys()
    return {
        "id": row["id"],
        "name": row["name"],
        "reference_beats": json.loads(row["reference_beats"]),
        "performance_onsets": json.loads(row["performance_onsets"]),
        "params": json.loads(row["params"]),
        # anchors 列为后续迁移新增：兼容旧库 / 旧行（缺失时回退为空）
        "anchors": (
            _normalize_anchors(json.loads(row["anchors"]))
            if "anchors" in keys and row["anchors"]
            else []
        ),
        "status": row["status"],
        "error": row["error"],
        "created_at": row["created_at"],
        "aligned_at": row["aligned_at"],
        "result": json.loads(row["result"]) if row["result"] else None,
    }


def create_job(
    conn: sqlite3.Connection,
    name: str | None,
    reference_beats: list[float],
    performance_onsets: list[float],
    params: dict[str, Any],
    anchors: list[Any] | None = None,
) -> dict[str, Any]:
    cur = conn.execute(
        """
        INSERT INTO jobs (name, reference_beats, performance_onsets, params, anchors,
                          status, error, result, created_at, aligned_at)
        VALUES (?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, NULL)
        """,
        (
            name,
            json.dumps(reference_beats),
            json.dumps(performance_onsets),
            json.dumps(params),
            json.dumps(anchors or []),
            _now(),
        ),
    )
    conn.commit()
    return get_job(conn, cur.lastrowid)


def get_job(conn: sqlite3.Connection, job_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def list_jobs(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM jobs ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_row_to_job(row) for row in rows]


def save_result(conn: sqlite3.Connection, job_id: int, result: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE jobs SET status='done', result=?, error=NULL, aligned_at=? WHERE id=?",
        (json.dumps(result), _now(), job_id),
    )
    conn.commit()


def save_error(conn: sqlite3.Connection, job_id: int, message: str) -> None:
    conn.execute(
        "UPDATE jobs SET status='error', error=?, aligned_at=? WHERE id=?",
        (message, _now(), job_id),
    )
    conn.commit()
