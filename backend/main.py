"""
QuessCorp Timesheet Automation — FastAPI Backend
Multi-Agent AI pipeline with Anthropic Claude Sonnet
"""

import os
import uuid
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from agents import supervisor

load_dotenv()

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "outputs"))
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

app = FastAPI(
    title="QuessCorp Timesheet Automation API",
    description="Multi-Agent AI pipeline — Anthropic Claude Sonnet",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if (FRONTEND_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR / "static")), name="static")

_config_store: dict = {}


# ── Models ────────────────────────────────────────────────────────────────────
class ReviewDecision(BaseModel):
    job_id: str
    assignment_id: str
    decision: str
    employee_name: Optional[str] = None
    total_hours: Optional[float] = None
    timesheet_status: Optional[str] = None
    lopr: Optional[float] = None
    allowances: Optional[float] = None
    expenses: Optional[float] = None


class QZonePushRequest(BaseModel):
    job_id: str
    qzone_endpoint: str
    qzone_token: str
    batch_size: int = 50


# ── Frontend ──────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>QuessCorp Timesheet Automation API</h1><p>Visit /docs for API docs.</p>")


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "QuessCorp Timesheet Automation", "time": datetime.utcnow().isoformat()}


# ── Master Data Upload ────────────────────────────────────────────────────────
@app.post("/api/master-data/upload")
async def upload_master_data(file: UploadFile = File(...)):
    """Upload monthly master data file (Excel or CSV) with Quess IDs, client, location, DOJ, DOS."""
    safe_name = f"master_{uuid.uuid4().hex}_{file.filename}"
    save_path = UPLOAD_DIR / safe_name
    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        rows = supervisor.load_master_data(str(save_path))
        return {"status": "ok", "rows_loaded": len(rows), "filename": file.filename}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/master-data")
async def get_master_data():
    data = supervisor.get_master_data()
    return {"rows": len(data), "sample": data[:3]}


# ── Process File ──────────────────────────────────────────────────────────────
@app.post("/api/process")
async def process_file(
    file: UploadFile = File(...),
    api_key: str = "",
    qzone_endpoint: str = "",
    qzone_token: str = "",
    confidence_threshold: float = 0.90,
    max_daily_hours: float = 24.0,
    max_weekly_hours: float = 60.0,
    batch_size: int = 50,
    period_start: str = "",
    period_end: str = "",
):
    effective_key = api_key or _config_store.get("api_key", os.getenv("ANTHROPIC_API_KEY", ""))
    if not effective_key:
        raise HTTPException(status_code=400, detail="Anthropic API key is required.")

    safe_name = f"{uuid.uuid4().hex}_{file.filename}"
    save_path = UPLOAD_DIR / safe_name
    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    config = {
        "confidence_threshold": confidence_threshold,
        "max_daily_hours":      max_daily_hours,
        "max_weekly_hours":     max_weekly_hours,
        "batch_size":           batch_size,
        "period_start":         period_start,
        "period_end":           period_end,
    }

    try:
        result = await supervisor.run_pipeline(
            filepath=str(save_path),
            original_filename=file.filename,
            api_key=effective_key,
            qzone_endpoint=qzone_endpoint or _config_store.get("qzone_endpoint", os.getenv("QZONE_API_ENDPOINT", "")),
            qzone_token=qzone_token or _config_store.get("qzone_token", os.getenv("QZONE_API_TOKEN", "")),
            config=config,
        )
        return JSONResponse(content=result)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ── Jobs ──────────────────────────────────────────────────────────────────────
@app.get("/api/jobs")
async def get_jobs():
    return {"jobs": supervisor.list_jobs()}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = supervisor.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job


@app.get("/api/jobs/{job_id}/records")
async def get_records(job_id: str):
    job = supervisor.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"records": job.get("records", [])}


@app.get("/api/jobs/{job_id}/review")
async def get_review_queue(job_id: str):
    job = supervisor.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    pending = [r for r in job.get("review_queue", []) if r.get("validation_status") == "review"]
    return {"review_queue": pending}


# ── Human Review ──────────────────────────────────────────────────────────────
@app.post("/api/review/decide")
async def review_decide(decision: ReviewDecision):
    job = supervisor.get_job(decision.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    updated = False
    for rec in job.get("review_queue", []):
        if rec.get("assignment_id") == decision.assignment_id:
            rec["validation_status"] = decision.decision
            if decision.employee_name:
                rec["employee_name"] = decision.employee_name
            if decision.total_hours is not None:
                rec["total_hours"] = decision.total_hours
            if decision.timesheet_status:
                rec["timesheet_status"] = decision.timesheet_status
            if decision.lopr is not None:
                rec["lopr"] = decision.lopr
            if decision.allowances is not None:
                rec["allowances"] = decision.allowances
            if decision.expenses is not None:
                rec["expenses"] = decision.expenses
            rec["reviewed_at"] = datetime.utcnow().isoformat()
            rec["reviewed_by"] = "HR_STAFF"

            for r in job.get("records", []):
                if r.get("assignment_id") == decision.assignment_id:
                    r.update(rec)
                    break

            if decision.decision == "approved":
                job.setdefault("auto_approved", [])
                existing_ids = [x.get("assignment_id") for x in job["auto_approved"]]
                if decision.assignment_id not in existing_ids:
                    job["auto_approved"].append(rec)

            updated = True
            break

    if not updated:
        raise HTTPException(status_code=404, detail="Record not found in review queue")

    return {"status": "ok", "decision": decision.decision, "assignment_id": decision.assignment_id}


# ── QZone Push ────────────────────────────────────────────────────────────────
@app.post("/api/qzone/push")
async def push_qzone(req: QZonePushRequest):
    from agents import agent5_qzone
    job = supervisor.get_job(req.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    approved = [r for r in job.get("records", []) if r.get("validation_status") == "approved"]
    result = await agent5_qzone.run(
        approved_records=approved,
        endpoint=req.qzone_endpoint,
        token=req.qzone_token,
        batch_size=req.batch_size,
    )
    job["pushed"]      = result.get("pushed", 0)
    job["push_status"] = result.get("status", "")
    job["push_logs"]   = result.get("logs", [])
    return result


# ── Payroll Export ────────────────────────────────────────────────────────────
@app.get("/api/jobs/{job_id}/export/payroll")
async def export_payroll(job_id: str):
    """Export payroll-ready records as CSV (includes LOPR, allowances, expenses, overtime)."""
    try:
        csv_content = supervisor.generate_payroll_report(job_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=payroll_{job_id}.csv"}
    )


@app.get("/api/jobs/{job_id}/export/csv")
async def export_csv(job_id: str):
    import csv, io
    job = supervisor.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    records = job.get("records", [])
    output = io.StringIO()
    fields = [
        "employee_name", "assignment_id", "quess_id", "client_name",
        "location", "period_start", "period_end", "total_hours", "overtime_hours",
        "total_working_days", "wo_days", "pl_days", "missing_days",
        "lopr", "allowances", "expenses",
        "timesheet_status", "validation_status", "confidence", "source_file"
    ]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(records)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=timesheet_{job_id}.csv"}
    )


# ── Settings ──────────────────────────────────────────────────────────────────
@app.post("/api/settings")
async def save_settings(settings: dict):
    _config_store.update(settings)
    return {"status": "saved"}


@app.get("/api/settings")
async def get_settings():
    return {
        "api_key":              "***" if _config_store.get("api_key") else "",
        "qzone_endpoint":       _config_store.get("qzone_endpoint", os.getenv("QZONE_API_ENDPOINT", "")),
        "confidence_threshold": _config_store.get("confidence_threshold", 0.90),
        "max_daily_hours":      _config_store.get("max_daily_hours", 24),
        "max_weekly_hours":     _config_store.get("max_weekly_hours", 60),
        "batch_size":           _config_store.get("batch_size", 50),
    }


@app.post("/api/settings/apikey")
async def save_api_key(body: dict):
    key = body.get("api_key", "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="API key cannot be empty")
    _config_store["api_key"] = key
    return {"status": "saved"}


# ── Stats ─────────────────────────────────────────────────────────────────────
@app.get("/api/stats")
async def stats():
    jobs = supervisor.list_jobs()
    total_records  = sum(len(j.get("records", [])) for j in jobs)
    total_review   = sum(sum(1 for r in j.get("review_queue", []) if r.get("validation_status") == "review") for j in jobs)
    total_pushed   = sum(j.get("pushed", 0) for j in jobs)
    total_payroll  = sum(sum(1 for r in j.get("records", []) if r.get("payroll_ready")) for j in jobs)
    return {
        "total_jobs":    len(jobs),
        "total_records": total_records,
        "total_review":  total_review,
        "total_pushed":  total_pushed,
        "total_payroll": total_payroll,
    }


@app.get("/api/records/all")
async def all_records():
    all_recs = []
    for job in supervisor.list_jobs():
        all_recs.extend(job.get("records", []))
    return {"records": all_recs, "total": len(all_recs)}


@app.get("/api/records/review")
async def all_review():
    pending = []
    for job in supervisor.list_jobs():
        for r in job.get("review_queue", []):
            if r.get("validation_status") == "review":
                pending.append(r)
    return {"review_queue": pending, "total": len(pending)}
