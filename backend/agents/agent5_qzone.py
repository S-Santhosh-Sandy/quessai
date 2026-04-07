

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


# ── Hours calculator ───────────────────────────────────────────────────────────

def _compute_hours(rec: dict) -> tuple:
    """
    Split total_hours into regularHours and overtimeHours.
    Logic: first 8hrs per working day = regular, remainder = overtime.
    Example: 20 working days × 8hrs = 160 regular cap.
              If total = 170 → regular=160, overtime=10
    """
    total_hours    = float(rec.get("total_hours") or 0)
    working_days   = int(rec.get("total_working_days") or 1) or 1
    regular_cap    = 8.0 * working_days
    regular_hours  = min(total_hours, regular_cap)
    overtime_hours = max(0.0, round(total_hours - regular_cap, 2))
    return round(regular_hours, 2), overtime_hours


# ── Payload builder ───────────────────────────────────────────────────────────

def _build_payload(rec: dict) -> dict:
    """Map normalized record fields to QZone API payload schema."""
    regular_hours, overtime_hours = _compute_hours(rec)

    return {
        "employeeId":    str(rec.get("quess_id") or rec.get("assignment_id") or ""),
        "clientAccount": str(rec.get("client_name") or ""),
        "projectCode":   "Default",
        "attendanceDate": str(rec.get("period_start") or ""),
        "regularHours":  regular_hours,
        "overtimeHours": overtime_hours,
        "submittedBy":   "AUTOMATION-AGENT",
        "approvalStatus": "APPROVED",
    }


# ── HTTP push with retry ──────────────────────────────────────────────────────

async def _push_batch(
    batch: list,
    endpoint: str,
    token: str,
    batch_num: int,
    logs: list,
) -> tuple:
    """
    Push one batch of records to QZone.
    Returns (success_count, fail_count).
    """
    if not HTTPX_AVAILABLE:
        logs.append(f"[WARN] Batch {batch_num}: httpx not installed — dry run mode")
        for rec in batch:
            payload = _build_payload(rec)
            logs.append(f"  [DRY-RUN] {rec.get('employee_name') or rec.get('quess_id')} → {json.dumps(payload)}")
        return len(batch), 0

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }

    pushed      = 0
    failed      = 0
    max_retries = 3

    async with httpx.AsyncClient(timeout=30.0) as client:
        for rec in batch:
            payload   = _build_payload(rec)
            rec_label = (
                f"{rec.get('employee_name') or 'Unknown'} "
                f"/ empId:{payload['employeeId']}"
            )

            for attempt in range(1, max_retries + 1):
                try:
                    response = await client.post(endpoint, headers=headers, json=payload)

                    if response.status_code in (200, 201, 202):
                        logs.append(f"  [OK] {rec_label} → HTTP {response.status_code}")
                        pushed += 1
                        break

                    elif response.status_code == 401:
                        logs.append(f"  [AUTH ERROR] {rec_label} → 401 Unauthorized. Check QZone token.")
                        failed += 1
                        break

                    elif response.status_code == 422:
                        logs.append(
                            f"  [VALIDATION] {rec_label} → 422 Unprocessable. "
                            f"Payload: {json.dumps(payload)[:300]}"
                        )
                        failed += 1
                        break

                    else:
                        wait = 2 ** attempt
                        logs.append(
                            f"  [RETRY {attempt}/{max_retries}] {rec_label} "
                            f"→ HTTP {response.status_code}. Waiting {wait}s..."
                        )
                        if attempt < max_retries:
                            await asyncio.sleep(wait)
                        else:
                            logs.append(f"  [FAIL] {rec_label} → gave up after {max_retries} retries")
                            failed += 1

                except httpx.ConnectError:
                    logs.append(f"  [CONNECT ERROR] {rec_label} → Cannot reach {endpoint}")
                    failed += 1
                    break

                except httpx.TimeoutException:
                    wait = 2 ** attempt
                    logs.append(f"  [TIMEOUT {attempt}/{max_retries}] {rec_label}. Waiting {wait}s...")
                    if attempt < max_retries:
                        await asyncio.sleep(wait)
                    else:
                        failed += 1

                except Exception as e:
                    logs.append(f"  [ERROR] {rec_label} → {e}")
                    failed += 1
                    break

    return pushed, failed


# ── Main run ──────────────────────────────────────────────────────────────────

async def run(
    approved_records: list,
    endpoint: str,
    token: str,
    batch_size: int = 50,
    **kwargs,
) -> dict:
    """
    Push all approved records to QZone in batches.
    Returns: { "status": str, "pushed": int, "failed": int, "logs": list }
    """
    logs         = []
    total_pushed = 0
    total_failed = 0

    if not approved_records:
        return {
            "status": "SKIPPED",
            "pushed": 0,
            "failed": 0,
            "logs": ["No approved records to push"],
        }

    if not endpoint:
        return {
            "status": "SKIPPED",
            "pushed": 0,
            "failed": 0,
            "logs": ["No QZone endpoint configured"],
        }

    logs.append(f"[{datetime.utcnow().isoformat()}] Starting QZone push")
    logs.append(f"  Endpoint  : {endpoint}")
    logs.append(f"  Records   : {len(approved_records)}")
    logs.append(f"  Batch size: {batch_size}")
    logs.append(f"  Schema    : employeeId | clientAccount | projectCode | "
                f"attendanceDate | regularHours | overtimeHours")

    # Log sample payload for first record
    if approved_records:
        sample = _build_payload(approved_records[0])
        logs.append(f"\n  Sample payload: {json.dumps(sample, indent=2)}")

    # Split into batches
    batches = [
        approved_records[i: i + batch_size]
        for i in range(0, len(approved_records), batch_size)
    ]

    for idx, batch in enumerate(batches, 1):
        logs.append(f"\n[Batch {idx}/{len(batches)}] Pushing {len(batch)} records...")
        pushed, failed = await _push_batch(
            batch=batch,
            endpoint=endpoint,
            token=token,
            batch_num=idx,
            logs=logs,
        )
        total_pushed += pushed
        total_failed += failed
        logs.append(f"[Batch {idx}] Done — pushed: {pushed}, failed: {failed}")

    # Overall status
    if total_failed == 0:
        status = "SUCCESS"
    elif total_pushed == 0:
        status = "FAILED"
    else:
        status = "PARTIAL"

    logs.append(
        f"\n[SUMMARY] Status: {status} | "
        f"Pushed: {total_pushed} | Failed: {total_failed}"
    )

    return {
        "status": status,
        "pushed": total_pushed,
        "failed": total_failed,
        "logs":   logs,
    }