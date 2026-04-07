
# """Convert any value to a JSON-serializable type. -  _sanitize_value"""

import os
import io
import json
from datetime import datetime
from pathlib import Path


# Summary sheet keywords — prefer these over raw data sheets
SUMMARY_SHEET_KEYWORDS = ["modif", "summary", "consolidated", "processed", "final", "report"]
RAW_SHEET_KEYWORDS     = ["raw", "portal", "source", "daily", "detail"]


def _pick_best_sheet(sheet_names: list) -> str:
    """Prefer summary/modified sheets over raw/portal sheets."""
    lower = [s.lower() for s in sheet_names]

    for kw in SUMMARY_SHEET_KEYWORDS:
        for i, name in enumerate(lower):
            if kw in name:
                return sheet_names[i]

    if len(sheet_names) > 1:
        for kw in RAW_SHEET_KEYWORDS:
            for i, name in enumerate(lower):
                if kw in name:
                    others = [s for s in sheet_names if s != sheet_names[i]]
                    if others:
                        return others[0]

    return sheet_names[0]
# """Convert any value to a JSON-serializable type."""

def _sanitize_value(v):
    """Convert any value to a JSON-serializable type."""
    import pandas as pd
    import math

    # NaT / None
    if v is None:
        return None
    if isinstance(v, type(pd.NaT)) or v is pd.NaT:
        return None

    # pandas Timestamp → "YYYY-MM-DD"
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d") if not pd.isnull(v) else None

    # python datetime / date → "YYYY-MM-DD"
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")

    # numpy int / float
    try:
        import numpy as np
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating,)):
            return None if math.isnan(v) else float(v)
        if isinstance(v, np.bool_):
            return bool(v)
    except ImportError:
        pass

    # float NaN
    if isinstance(v, float) and math.isnan(v):
        return None

    # string "NaT" / "nan" / "None"
    if isinstance(v, str) and v.strip().lower() in ("nat", "nan", "none", ""):
        return None

    return v
#This is where the actual DataFrame → JSON conversion happens:

def _df_to_json_records(df) -> list:
    """
    Convert a pandas DataFrame to a clean list of JSON-serializable dicts.
    - Renames datetime column headers to YYYY-MM-DD strings
    - Converts all Timestamp / NaT / NaN / numpy types to JSON-safe values
    """
    import pandas as pd

    # Rename datetime column headers → "YYYY-MM-DD"
    new_cols = {}
    for col in df.columns:
        if isinstance(col, (datetime, pd.Timestamp)):
            new_cols[col] = col.strftime("%Y-%m-%d")
        elif hasattr(col, "strftime"):
            new_cols[col] = col.strftime("%Y-%m-%d")
    df = df.rename(columns=new_cols)

    # Convert to records and sanitize every value
    raw_records = df.to_dict(orient="records")
    cleaned = []
    for rec in raw_records:
        cleaned.append({k: _sanitize_value(v) for k, v in rec.items()})

    return cleaned


async def run(filepath: str, original_filename: str, **kwargs) -> dict:
    ext = Path(original_filename).suffix.lower()

    if ext in (".xlsx", ".xls"):
        return await _process_excel(filepath, original_filename)
    elif ext == ".csv":
        return await _process_csv(filepath, original_filename)
    elif ext == ".pdf":
        return await _process_pdf(filepath, original_filename)
    elif ext in (".eml", ".msg"):
        return await _process_email(filepath, original_filename)
    else:
        try:
            return await _process_csv(filepath, original_filename)
        except Exception:
            return await _process_text(filepath, original_filename)


# ── Excel ─────────────────────────────────────────────────────────────────────

async def _process_excel(filepath: str, filename: str) -> dict:
    import pandas as pd

    xl          = pd.ExcelFile(filepath)
    sheet_names = xl.sheet_names
    best_sheet  = _pick_best_sheet(sheet_names)

    print(f"[Agent1] Sheets: {sheet_names} → selected: '{best_sheet}'")

    df = pd.read_excel(filepath, sheet_name=best_sheet)

    # Drop completely empty rows
    df = df.dropna(how="all")

    records     = _df_to_json_records(df)
    raw_content = json.dumps(records, ensure_ascii=False)

    print(f"[Agent1] Loaded {len(records)} rows from '{best_sheet}' using pandas")

    return {
        "file_type":      "excel",
        "raw_content":    raw_content,
        "sheet_data":     records,
        "row_count":      len(records),
        "selected_sheet": best_sheet,
    }


# ── CSV ───────────────────────────────────────────────────────────────────────

async def _process_csv(filepath: str, filename: str) -> dict:
    import pandas as pd

    df      = pd.read_csv(filepath, encoding="utf-8-sig")
    df      = df.dropna(how="all")
    records = _df_to_json_records(df)

    raw_content = json.dumps(records, ensure_ascii=False)

    print(f"[Agent1] Loaded {len(records)} rows from CSV using pandas")

    return {
        "file_type":   "csv",
        "raw_content": raw_content,
        "sheet_data":  records,
        "row_count":   len(records),
    }


# ── PDF ───────────────────────────────────────────────────────────────────────

async def _process_pdf(filepath: str, filename: str) -> dict:
    try:
        import fitz
        doc        = fitz.open(filepath)
        pages_text = []
        for page_num, page in enumerate(doc, 1):
            text = page.get_text()
            if text.strip():
                pages_text.append(f"=== Page {page_num} ===\n{text}")
        doc.close()
        raw_content = "\n\n".join(pages_text)
    except ImportError:
        with open(filepath, "rb") as f:
            raw_content = f"[PDF file: {filename} — PyMuPDF not available]"

    return {
        "file_type":   "pdf",
        "raw_content": raw_content,
        "sheet_data":  [],
        "row_count":   raw_content.count("\n"),
    }


# ── Email ─────────────────────────────────────────────────────────────────────

async def _process_email(filepath: str, filename: str) -> dict:
    import email as email_lib

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        raw = f.read()

    msg   = email_lib.message_from_string(raw)
    parts = []
    parts.append(
        f"Subject: {msg.get('subject', '')}\n"
        f"From: {msg.get('from', '')}\n"
        f"Date: {msg.get('date', '')}\n"
    )
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                parts.append(payload.decode("utf-8", errors="ignore"))

    raw_content = "\n\n".join(parts)

    return {
        "file_type":   "email",
        "raw_content": raw_content,
        "sheet_data":  [],
        "row_count":   raw_content.count("\n"),
    }


# ── Plain text ────────────────────────────────────────────────────────────────

async def _process_text(filepath: str, filename: str) -> dict:
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        raw_content = f.read()

    return {
        "file_type":   "text",
        "raw_content": raw_content,
        "sheet_data":  [],
        "row_count":   raw_content.count("\n"),
    }