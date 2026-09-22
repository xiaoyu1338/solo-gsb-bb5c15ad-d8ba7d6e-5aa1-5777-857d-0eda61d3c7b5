"""多轨录音节拍对齐 API。

启动：uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import RedirectResponse

from . import crud
from .alignment import AlignmentError, AlignmentParams, align_beats
from .database import get_db, init_db
from .schemas import JobCreate, JobOut, JobSummary


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="多轨录音节拍对齐 API",
    version="1.0.0",
    description=(
        "提交参考节拍网格与演奏轨音符起始时间，服务使用带宽约束的动态时间规整"
        "（Sakoe-Chiba DTW）处理漏拍、抢拍与插入拍，回溯生成一一对应映射及"
        "连续偏差区间。"
    ),
    lifespan=lifespan,
)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


@app.post("/jobs", response_model=JobOut, status_code=status.HTTP_201_CREATED, tags=["jobs"])
def create_job(payload: JobCreate, db: sqlite3.Connection = Depends(get_db)):
    """创建对齐任务（创建后状态为 pending，需调用 /align 执行对齐）。"""
    if abs(len(payload.reference_beats) - len(payload.performance_onsets)) > (
        payload.params.band_width
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"序列长度差 {abs(len(payload.reference_beats) - len(payload.performance_onsets))} "
                f"超过带宽 {payload.params.band_width}，请增大 params.band_width"
            ),
        )
    return crud.create_job(
        db,
        name=payload.name,
        reference_beats=payload.reference_beats,
        performance_onsets=payload.performance_onsets,
        params=payload.params.model_dump(),
    )


@app.post("/jobs/{job_id}/align", response_model=JobOut, tags=["jobs"])
def align_job(
    job_id: int,
    force: bool = Query(default=False, description="已完成时是否强制重新对齐"),
    db: sqlite3.Connection = Depends(get_db),
):
    """执行 DTW 对齐并持久化结果。"""
    job = crud.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    if job["status"] == "done" and not force:
        return job

    params = AlignmentParams(
        band_width=job["params"].get("band_width", 3),
        skip_penalty=job["params"].get("skip_penalty"),
        insert_penalty=job["params"].get("insert_penalty"),
        on_time_threshold=job["params"].get("on_time_threshold", 0.03),
    )
    try:
        result = align_beats(job["reference_beats"], job["performance_onsets"], params)
    except AlignmentError as exc:
        crud.save_error(db, job_id, str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    crud.save_result(db, job_id, result)
    return crud.get_job(db, job_id)


@app.get("/jobs", response_model=list[JobSummary], tags=["jobs"])
def list_jobs(
    limit: int = Query(default=100, ge=1, le=500),
    db: sqlite3.Connection = Depends(get_db),
):
    """列出任务摘要。"""
    jobs = crud.list_jobs(db, limit)
    return [
        {
            "id": j["id"],
            "name": j["name"],
            "status": j["status"],
            "created_at": j["created_at"],
            "aligned_at": j["aligned_at"],
            "ref_beat_count": len(j["reference_beats"]),
            "perf_onset_count": len(j["performance_onsets"]),
        }
        for j in jobs
    ]


@app.get("/jobs/{job_id}", response_model=JobOut, tags=["jobs"])
def get_job(job_id: int, db: sqlite3.Connection = Depends(get_db)):
    """查询单个任务（含完整对齐结果）。"""
    job = crud.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return job


@app.get("/jobs/{job_id}/result", tags=["jobs"])
def get_job_result(job_id: int, db: sqlite3.Connection = Depends(get_db)):
    """只查询对齐结果；未对齐或失败时返回 409/400 提示。"""
    job = crud.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    if job["status"] == "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="任务尚未对齐")
    if job["status"] == "error":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"上次对齐失败：{job['error']}",
        )
    return {"job_id": job_id, "result": job["result"]}
