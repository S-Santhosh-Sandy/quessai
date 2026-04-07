"""
Agent 3 — Field Normalization
Standardizes extracted fields to match the QZone data model exactly.
- ISO 8601 dates
- Decimal hours
- QZone legend codes: WO / PL / MISSING
- Canonical IDs and location codes
"""

import re
from datetime import datetime
from typing import Optional, Any


# ── Date normalization ────────────────────────────────────────────────────────

DATE_FORMATS = [
    "%Y-%m-%d",       # 2026-02-01
    "%d-%m-%Y",       # 01-02-2026
    "%d/%m/%Y",       # 01/02/2026
    "%m/%d/%Y",       # 02/01/2026
    "%d-%b-%Y",       # 01-Feb-2026
    "%d %b %Y",       # 01 Feb 2026
    "%b %d, %Y",      # Feb 01, 2026
    "%B %d, %Y",      # February 01, 2026
    "%d-%m-%y",       # 01-02-26
    "%d/%m/%y",       # 01/02/26
    "%Y/%m/%d",       # 2026/02/01
    "%d.%m.%Y",       # 01.02.2026
]


def _normalize_date(value: Any) -> Optional[str]:
    """Convert any date string to ISO 8601 YYYY-MM-DD format."""
    if value is None:
        return None

    s = str(value).strip()
    if not s or s.lower() in ("none", "null", "n/a", "-"):
        return None

    # Already ISO format
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s

    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    # Try partial — e.g., "Feb 2026" → first of month
    try:
        dt = datetime.strptime(s, "%b %Y")
        return dt.strftime("%Y-%m-01")
    except ValueError:
        pass

    return s  # Return as-is if can't parse


# ── Hours normalization ───────────────────────────────────────────────────────

def _normalize_hours(value: Any) -> Optional[float]:
    """Convert hours to decimal float. Handles: 8, 8.5, '8 hrs 30 mins', '8:30'."""
    if value is None:
        return None

    s = str(value).strip().lower()
    if not s or s in ("none", "null", "n/a", "-", ""):
        return None

    # Direct numeric
    try:
        return round(float(s), 2)
    except ValueError:
        pass

    # HH:MM format
    hhmm = re.match(r"^(\d+):(\d{2})$", s)
    if hhmm:
        hours = int(hhmm.group(1))
        minutes = int(hhmm.group(2))
        return round(hours + minutes / 60, 2)

    # "8 hrs 30 mins" / "8 hours 30 minutes"
    hrs_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:hrs?|hours?)", s)
    min_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:mins?|minutes?)", s)
    if hrs_match:
        h = float(hrs_match.group(1))
        m = float(min_match.group(1)) if min_match else 0
        return round(h + m / 60, 2)

    # Extract first numeric
    num = re.search(r"(\d+(?:\.\d+)?)", s)
    if num:
        return round(float(num.group(1)), 2)

    return None


# ── Integer normalization ─────────────────────────────────────────────────────

def _normalize_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return None


# ── Status normalization ──────────────────────────────────────────────────────

STATUS_MAP = {
    "approved":   "APPROVED",
    "approve":    "APPROVED",
    "aprvd":      "APPROVED",
    "pending":    "PENDING",
    "submitted":  "SUBMITTED",
    "submit":     "SUBMITTED",
    "rejected":   "REJECTED",
    "reject":     "REJECTED",
    "processing": "PROCESSING",
}


def _normalize_status(value: Any) -> str:
    if value is None:
        return "PENDING"
    s = str(value).strip().lower()
    return STATUS_MAP.get(s, str(value).strip().upper() or "PENDING")


# ── Name normalization ────────────────────────────────────────────────────────

def _normalize_name(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("none", "null", "n/a"):
        return None
    return s.upper()


# ── ID normalization ──────────────────────────────────────────────────────────

def _normalize_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("none", "null", "n/a"):
        return None
    return s


# ── Main run ──────────────────────────────────────────────────────────────────

async def run(records: list, config: dict, **kwargs) -> dict:
    """
    Normalize all extracted records to the QZone data model.
    Returns: { "records": [...] }
    """
    normalized = []

    for rec in records:
        norm = dict(rec)  # copy all fields (preserve source_file, extracted_at, etc.)

        norm["employee_name"]      = _normalize_name(rec.get("employee_name"))
        norm["assignment_id"]      = _normalize_id(rec.get("assignment_id"))
        norm["quess_id"]           = _normalize_id(rec.get("quess_id"))
        norm["client_name"]        = rec.get("client_name") or None
        norm["location"]           = rec.get("location") or None
        norm["period_start"]       = _normalize_date(rec.get("period_start"))
        norm["period_end"]         = _normalize_date(rec.get("period_end"))
        norm["total_hours"]        = _normalize_hours(rec.get("total_hours"))
        norm["total_working_days"] = _normalize_int(rec.get("total_working_days"))
        norm["wo_days"]            = _normalize_int(rec.get("wo_days"))
        norm["pl_days"]            = _normalize_int(rec.get("pl_days"))
        norm["missing_days"]       = _normalize_int(rec.get("missing_days"))
        norm["timesheet_status"]   = _normalize_status(rec.get("timesheet_status"))
        norm["confidence"]         = float(rec.get("confidence") or 0.0)

        # Derived: set missing_days to 0 if not provided
        if norm["missing_days"] is None:
            norm["missing_days"] = 0

        norm["normalized_at"] = datetime.utcnow().isoformat()
        normalized.append(norm)

    return {"records": normalized}