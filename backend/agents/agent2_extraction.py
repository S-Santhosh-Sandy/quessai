"""
Agent 2 — Document Extraction (Direct Mapping from Pandas JSON)

The data from Agent 1 (pandas) is already perfectly structured JSON.
No LLM needed — directly map JSON fields to our schema.

For unstructured files (PDF/email/text), falls back to Bedrock LLM.
"""

import json
import re
import os
import math
import asyncio
import boto3
from botocore.exceptions import ClientError
from datetime import datetime
from typing import Optional

# ── Bedrock config (only used for PDF/email fallback) ─────────────────────────
BEDROCK_REGION   = "us-east-1"
BEDROCK_MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"
BATCH_ROW_SIZE   = 50
MAX_CHARS        = 15000
MAX_CONCURRENT   = 10

FALLBACK_MODEL_IDS = [
    "anthropic.claude-3-haiku-20240307-v1:0",
    "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "anthropic.claude-3-sonnet-20240229-v1:0",
]

_BEDROCK_CLIENT: Optional[object] = None

def _get_bedrock_client():
    global _BEDROCK_CLIENT
    if _BEDROCK_CLIENT is None:
        kwargs = {
            "service_name":          "bedrock-runtime",
            "region_name":           BEDROCK_REGION,
            "aws_access_key_id":     os.getenv("AWS_ACCESS_KEY_ID"),
            "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY"),
        }
        if os.getenv("AWS_SESSION_TOKEN"):
            kwargs["aws_session_token"] = os.getenv("AWS_SESSION_TOKEN")
        _BEDROCK_CLIENT = boto3.client(**kwargs)
    return _BEDROCK_CLIENT


# ── Column aliases for direct mapping ─────────────────────────────────────────
# key = our field name, values = possible column names (lowercase)
COLUMN_MAP = {
    "assignment_id":      ["assignment id", "assignmentid", "assignment_id", "asgn id"],
    "quess_id":           ["quess id", "quessid", "quess_id", "employee id", "emp id"],
    "employee_name":      ["first name last name", "name", "employee name", "full name", "employee_name"],
    "client_name":        ["client name", "client_name", "clientname", "account"],
    "location":           ["location", "location name", "loc"],
    "total_working_days": ["grand total", "total", "working days", "total_working_days"],
    "wo_days":            ["wo", "week off", "weekoff", "wo days"],
    "pl_days":            ["pl", "paid leave", "pl days"],
    "missing_days":       ["missing", "missing days", "absent"],
    "timesheet_status":   ["ts status", "timesheet status", "status", "ts_status"],
}

# Date pattern for detecting daily columns (2026-02-01 etc.)
DATE_COL_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _map_columns(rec: dict) -> tuple:
    """
    Map a raw pandas record to our field schema.
    Returns (field_mapping, date_columns).
    field_mapping = {our_field: column_name_in_rec}
    date_columns  = list of column names that are daily dates
    """
    field_mapping = {}
    date_columns  = []

    for col in rec.keys():
        col_lower = str(col).strip().lower()

        # Check if it's a date column
        if DATE_COL_RE.match(str(col).strip()):
            date_columns.append(col)
            continue

        # Match to our fields
        for field, aliases in COLUMN_MAP.items():
            if col_lower in aliases and field not in field_mapping:
                field_mapping[field] = col
                break

    return field_mapping, date_columns


def _compute_day_stats(rec: dict, date_cols: list) -> dict:
    """Compute hours and day counts from daily date columns.
    Also builds entries[] per worked day for QZone per-day API push.
    Only WORKED days go into entries[] — WO, PL, ABSENT, MISSING are skipped.
    """
    total_hours   = 0.0
    working_days  = 0
    wo_days       = 0
    pl_days       = 0
    missing_days  = 0
    missing_dates = []   # exact dates that are MISSING
    period_start  = None
    period_end    = None
    entries       = []   # per-day worked entries for QZone

    for col in date_cols:
        val     = rec.get(col)
        col_str = str(col).strip()

        # Track period range
        if DATE_COL_RE.match(col_str):
            if period_start is None:
                period_start = col_str
            period_end = col_str

        if val is None:
            missing_days += 1
            missing_dates.append(col_str)
            continue

        val_str = str(val).strip().upper()

        if val_str == "WO":
            wo_days += 1          # ← week off, NOT attendance, skip entry

        elif val_str == "PL":
            pl_days += 1          # ← paid leave, NOT attendance, skip entry

        elif val_str in ("MISSING", "MISS", "ABSENT"):
            missing_days += 1     # ← absent, NOT attendance, skip entry
            missing_dates.append(col_str)

        else:
            try:
                hours = float(val_str)
                total_hours  += hours
                working_days += 1

                # ── Build per-day entry for QZone ──────────────────────────
                # Only worked days become attendance entries
                regular_hours  = min(hours, 8.0)               # first 8hrs = regular
                overtime_hours = round(max(0.0, hours - 8.0), 2)  # beyond 8hrs = overtime

                entries.append({
                    "date":           col_str,        # "2025-01-06"
                    "hours_regular":  regular_hours,  # 8.0
                    "hours_overtime": overtime_hours, # 1.5
                    "confidence":     0.96,           # per-day confidence
                })
                # ───────────────────────────────────────────────────────────

            except ValueError:
                pass

    return {
        "total_hours":        round(total_hours, 2),
        "total_working_days": working_days,
        "wo_days":            wo_days,
        "pl_days":            pl_days,
        "missing_days":       missing_days,
        "missing_dates":      missing_dates,   # e.g. ["2026-02-26", "2026-02-27"]
        "period_start":       period_start,
        "period_end":         period_end,
        "entries":            entries,         # per-day worked entries for QZone
    }


def _direct_map(sheet_data: list, filename: str, config: dict) -> list:
    """
    Directly map pandas JSON records to our schema — zero LLM calls.
    Works for any structured Excel/CSV with recognizable column names.
    """
    if not sheet_data:
        return []

    # Use first record to detect columns
    first_rec       = sheet_data[0]
    field_mapping, date_cols = _map_columns(first_rec)

    # Need at least employee_name or assignment_id
    if "assignment_id" not in field_mapping and "employee_name" not in field_mapping:
        print("[Agent2] Cannot detect columns — falling back to LLM")
        return []

    print(f"[Agent2] Direct mapping: {field_mapping}")
    print(f"[Agent2] Date columns: {len(date_cols)} ({date_cols[0] if date_cols else 'none'} → {date_cols[-1] if date_cols else 'none'})")

    cfg_start = config.get("period_start") or None
    cfg_end   = config.get("period_end") or None

    records = []
    for rec in sheet_data:
        out = {
            "source_file":      filename,
            "extracted_at":     datetime.utcnow().isoformat(),
            "extraction_model": "DIRECT_MAPPING",
        }

        # Map known fields
        for field, col in field_mapping.items():
            val = rec.get(col)
            out[field] = val if val not in (None, "", "None", "nan") else None

        # Compute from daily columns
        if date_cols:
            day_stats = _compute_day_stats(rec, date_cols)

            # Only use computed values if not already set by direct column
            out["total_hours"]        = out.get("total_hours") or day_stats["total_hours"]
            out["total_working_days"] = out.get("total_working_days") or day_stats["total_working_days"]
            out["wo_days"]            = out.get("wo_days") or day_stats["wo_days"]
            out["pl_days"]            = out.get("pl_days") or day_stats["pl_days"]
            out["missing_days"]       = out.get("missing_days") if out.get("missing_days") is not None else day_stats["missing_days"]
            out["missing_dates"]      = day_stats["missing_dates"]   # exact missing dates list
            out["period_start"]       = cfg_start or day_stats["period_start"]
            out["period_end"]         = cfg_end   or day_stats["period_end"]
            out["entries"]            = day_stats["entries"]         # ← per-day worked entries
        else:
            out["period_start"]  = cfg_start
            out["period_end"]    = cfg_end
            out["missing_dates"] = []
            out["entries"]       = []   # ← no daily columns, no entries

        # Confidence based on filled key fields
        key_fields = ["employee_name", "assignment_id", "total_hours", "period_start", "period_end"]
        filled     = sum(1 for f in key_fields if out.get(f))
        out["confidence"] = round(0.70 + (filled / len(key_fields)) * 0.29, 2)

        # Skip blank rows
        if not out.get("employee_name") and not out.get("assignment_id"):
            continue

        records.append(out)

    print(f"[Agent2] Direct mapping: {len(records)} records extracted (0 LLM calls)")
    return records


# ── Dedup ──────────────────────────────────────────────────────────────────────
def _dedup(records: list) -> list:
    seen = set()
    out  = []
    for rec in records:
        key = (
            (rec.get("employee_name") or "").strip().upper(),
            str(rec.get("assignment_id") or "").strip(),
        )
        if key not in seen:
            seen.add(key)
            out.append(rec)
    return out


# ── Empty fallback ─────────────────────────────────────────────────────────────
def _empty_record(filename: str, error_msg: str) -> dict:
    return {
        "employee_name": None, "assignment_id": None, "quess_id": None,
        "client_name": None, "location": None,
        "period_start": None, "period_end": None,
        "total_hours": None, "total_working_days": None,
        "wo_days": None, "pl_days": None, "missing_days": None,
        "timesheet_status": None, "confidence": 0.0,
        "source_file": filename,
        "extracted_at": datetime.utcnow().isoformat(),
        "extraction_error": error_msg,
    }


# ── LLM system prompt (PDF/email fallback only) ────────────────────────────────
SYSTEM_PROMPT = """You are a timesheet data extraction specialist for QuessCorp.
Extract structured employee timesheet data from the document.
Respond ONLY with a valid JSON object — no markdown, no explanation.

Extract: employee_name, assignment_id, quess_id, client_name, location,
period_start (YYYY-MM-DD), period_end (YYYY-MM-DD), total_hours,
total_working_days, wo_days, pl_days, missing_days, timesheet_status,
confidence (0.0-1.0)

Always return: {"records": [...]}"""


def _build_llm_prompt(chunk: str, filename: str, config: dict,
                      batch_num: int, total_batches: int) -> str:
    period_hint = ""
    if config.get("period_start") and config.get("period_end"):
        period_hint = f"\nPay period: {config['period_start']} to {config['period_end']}"

    return (
        f"Extract timesheet records from this document.\n"
        f"File: {filename}{period_hint}\n"
        f"Batch {batch_num}/{total_batches}\n\n"
        f"{chunk}\n\n"
        'Return ONLY the JSON with "records" list.'
    )


def _parse_llm_response(text: str, filename: str, model_id: str) -> list:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        parsed = json.loads(match.group()) if match else {}

    records = parsed.get("records", [])
    for rec in records:
        rec["source_file"]      = filename
        rec["extracted_at"]     = datetime.utcnow().isoformat()
        rec["extraction_model"] = model_id
    return records


def _invoke_sync(bedrock, prompt: str, model_id: str, max_tokens: int) -> str:
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens":        max_tokens,
        "system":            SYSTEM_PROMPT,
        "messages":          [{"role": "user", "content": prompt}],
    })
    response      = bedrock.invoke_model(
        modelId=model_id, contentType="application/json",
        accept="application/json", body=body,
    )
    return json.loads(response["body"].read())["content"][0]["text"]


async def _invoke_async(bedrock, prompt, filename, models, max_tokens):
    last_err = None
    for model_id in models:
        try:
            text    = await asyncio.to_thread(_invoke_sync, bedrock, prompt, model_id, max_tokens)
            records = _parse_llm_response(text, filename, model_id)
            return records, model_id
        except ClientError as e:
            code = e.response["Error"]["Code"]
            msg  = e.response["Error"]["Message"]
            if code in ("ResourceNotFoundException", "ValidationException"):
                last_err = f"[{code}] {msg}"
                continue
            raise RuntimeError(f"Bedrock Error [{code}]: {msg}") from e
        except Exception as e:
            last_err = str(e)
            continue
    raise RuntimeError(f"All models failed. Last: {last_err}")


async def _process_text_chunk(semaphore, bedrock, chunk, filename,
                               config, batch_num, total_batches, models):
    async with semaphore:
        prompt = _build_llm_prompt(chunk, filename, config, batch_num, total_batches)
        try:
            records, model = await _invoke_async(bedrock, prompt, filename, models, 2048)
            print(f"[Agent2] LLM chunk {batch_num}/{total_batches}: {len(records)} records via {model}")
            return records
        except RuntimeError as e:
            print(f"[Agent2] LLM chunk {batch_num} failed: {e}")
            return []


# ── Main entry point ───────────────────────────────────────────────────────────
async def run(raw_content, sheet_data, file_type, original_filename,
              api_key, config, **kwargs):
    """
    For Excel/CSV → direct column mapping (instant, zero LLM).
    For PDF/email/text → parallel Bedrock LLM calls (fallback).
    """

    # ── Direct mapping for structured Excel/CSV ────────────────────────────────
    if sheet_data and file_type in ("excel", "csv"):
        records = _direct_map(sheet_data, original_filename, config)
        if records:
            records = _dedup(records)
            print(f"[Agent2] Done: {len(records)} records via direct mapping (no LLM)")
            return {"records": records}
        print("[Agent2] Direct mapping failed — falling back to LLM")

    # ── LLM fallback for unstructured files ───────────────────────────────────
    print("[Agent2] Using Bedrock LLM...")
    bedrock   = _get_bedrock_client()
    models    = [BEDROCK_MODEL_ID] + [m for m in FALLBACK_MODEL_IDS if m != BEDROCK_MODEL_ID]
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    text_source = raw_content or json.dumps(sheet_data, ensure_ascii=False)
    chunks      = [text_source[i: i + MAX_CHARS]
                   for i in range(0, max(len(text_source), 1), MAX_CHARS)]

    tasks = [
        _process_text_chunk(semaphore, bedrock, chunk, original_filename,
                            config, bn, len(chunks), models)
        for bn, chunk in enumerate(chunks, 1)
    ]

    t0      = datetime.utcnow()
    results = await asyncio.gather(*tasks)
    elapsed = (datetime.utcnow() - t0).total_seconds()

    all_records = []
    for r in results:
        all_records.extend(r)

    print(f"[Agent2] LLM done in {elapsed:.1f}s")

    all_records = _dedup(all_records)
    print(f"[Agent2] Final: {len(all_records)} unique records")

    if not all_records:
        return {"records": [_empty_record(original_filename, "No records extracted")]}

    return {"records": all_records}