"""
Supervisor Agent — Orchestrates the full 5-agent pipeline.
State machine: RECEIVED -> CLASSIFIED -> EXTRACTED -> NORMALIZED -> VALIDATED -> PUSHED -> COMPLETE
Supports master data upload for Quess ID / client / location / DOJ / DOS mapping.
"""

import uuid
import csv
import io
from datetime import datetime
from typing import Optional

from agents import agent1_ingestion
from agents import agent2_extraction
from agents import agent3_normalization
from agents import agent4_validation
from agents import agent5_qzone

JOBS: dict = {}
MASTER_DATA: list = []  # Global master data store


def load_master_data(filepath: str) -> list:
    """Load master data from CSV or Excel file."""
    global MASTER_DATA
    import os
    ext = os.path.splitext(filepath)[1].lower()
    rows = []
    if ext in (".xlsx", ".xls"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(filepath, data_only=True)
            ws = wb.active
            headers = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i == 0:
                    headers = [str(c).strip().lower().replace(" ", "_") if c else f"col_{j}" for j, c in enumerate(row)]
                else:
                    if all(c is None for c in row):
                        continue
                    rows.append({headers[j]: (str(v).strip() if v is not None else "") for j, v in enumerate(row)})
        except Exception as e:
            raise RuntimeError(f"Failed to load master Excel: {e}")
    else:
        with open(filepath, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append({k.strip().lower().replace(" ", "_"): v.strip() for k, v in row.items()})
    MASTER_DATA = rows
    return rows


def get_master_data() -> list:
    return MASTER_DATA


def _new_job(job_id: str, original_filename: str) -> dict:
    return {
        "job_id":            job_id,
        "filename":          original_filename,
        "source_file":       original_filename,
        "state":             "RECEIVED",
        "created_at":        datetime.utcnow().isoformat(),
        "updated_at":        datetime.utcnow().isoformat(),
        "records":           [],
        "review_queue":      [],
        "auto_approved":     [],
        "pushed":            0,
        "push_status":       "",
        "push_logs":         [],
        "error":             None,
        "logs":              [],
    }


def _log(job: dict, msg: str):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    job["logs"].append(f"[{ts}] {msg}")
    job["updated_at"] = datetime.utcnow().isoformat()


def _set_state(job: dict, state: str):
    job["state"] = state
    _log(job, f"State -> {state}")


def get_job(job_id: str) -> Optional[dict]:
    return JOBS.get(job_id)


def list_jobs() -> list:
    return list(JOBS.values())


async def run_pipeline(filepath, original_filename, api_key, qzone_endpoint, qzone_token, config):
    job_id = f"JOB-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
    job = _new_job(job_id, original_filename)
    JOBS[job_id] = job
    _log(job, f"Job created: {job_id} for '{original_filename}'")

    try:
        # Agent 1
        _set_state(job, "CLASSIFYING")
        ingestion_result = await agent1_ingestion.run(filepath=filepath, original_filename=original_filename)
        job["file_type"]   = ingestion_result.get("file_type", "unknown")
        job["raw_content"] = ingestion_result.get("raw_content", "")
        job["sheet_data"]  = ingestion_result.get("sheet_data", [])
        _set_state(job, "CLASSIFIED")
        _log(job, f"Agent 1 done. Type: {job['file_type']}, rows: {ingestion_result.get('row_count', 0)}")

        # Agent 2
        _set_state(job, "EXTRACTING")
        extraction_result = await agent2_extraction.run(
            raw_content=job["raw_content"],
            sheet_data=job["sheet_data"],
            file_type=job["file_type"],
            original_filename=original_filename,
            api_key=api_key,
            config=config,
        )
        job["extracted_records"] = extraction_result.get("records", [])
        _set_state(job, "EXTRACTED")
        _log(job, f"Agent 2 done. Extracted {len(job['extracted_records'])} record(s)")

        # Agent 3
        _set_state(job, "NORMALIZING")
        norm_result = await agent3_normalization.run(
            records=job["extracted_records"],
            config=config,
            master_data=get_master_data(),
        )
        job["normalized_records"] = norm_result.get("records", [])
        _set_state(job, "NORMALIZED")
        _log(job, f"Agent 3 done. Normalized {len(job['normalized_records'])} record(s)")

        # Agent 4
        _set_state(job, "VALIDATING")
        val_result = await agent4_validation.run(records=job["normalized_records"], config=config)
        job["records"]       = val_result.get("all_records", [])
        job["auto_approved"] = val_result.get("auto_approved", [])
        job["review_queue"]  = val_result.get("review_queue", [])
        _set_state(job, "VALIDATED")
        _log(job, f"Agent 4 done. Auto-approved: {len(job['auto_approved'])}, Review: {len(job['review_queue'])}")

        # Agent 5
        if qzone_endpoint and qzone_token and job["auto_approved"]:
            _set_state(job, "PUSHING")
            push_result = await agent5_qzone.run(
                approved_records=job["auto_approved"],
                endpoint=qzone_endpoint,
                token=qzone_token,
                batch_size=config.get("batch_size", 50),
            )
            job["pushed"]      = push_result.get("pushed", 0)
            job["push_status"] = push_result.get("status", "")
            job["push_logs"]   = push_result.get("logs", [])
            _set_state(job, "PUSHED")
            _log(job, f"Agent 5 done. Status: {job['push_status']}, pushed: {job['pushed']}")
        else:
            job["push_status"] = "SKIPPED"
            _log(job, "Agent 5 skipped (no endpoint/token or no approved records)")

        _set_state(job, "COMPLETE")
        _log(job, "Pipeline complete")

    except Exception as exc:
        job["state"] = "ERROR"
        job["error"] = str(exc)
        _log(job, f"Pipeline ERROR: {exc}")
        raise

    return job


def generate_payroll_report(job_id: str) -> str:
    """Generate payroll-ready CSV string from approved records."""
    job = get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    payroll_records = [r for r in job.get("records", []) if r.get("payroll_ready")]

    output = io.StringIO()
    fields = [
        "employee_name", "assignment_id", "quess_id", "client_name", "location",
        "period_start", "period_end", "total_hours", "overtime_hours",
        "total_working_days", "wo_days", "pl_days", "missing_days",
        "lopr", "allowances", "expenses",
        "timesheet_status", "validation_status", "confidence", "source_file"
    ]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(payroll_records)
    return output.getvalue()
