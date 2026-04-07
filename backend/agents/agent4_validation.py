"""
Agent 4 — Validation + Human Review Routing

Business Rules:
  Rule 1 — Daily hours cap       : avg daily hours must not exceed 24
  Rule 2 — Weekly hours cap      : total hours must not exceed 60 per week
  Rule 3 — Pay period check      : work dates must fall within active pay period
  Rule 4 — Employee ID check     : quess_id must exist in master register
  Rule 5 — Client + Project combo: client_name must be a valid active combination
  Rule 6 — Required fields       : employee_name, assignment_id, period_start, period_end
  Rule 7 — Missing days          : any MISSING days flagged for review

Routing:
  confidence >= 0.90 AND all rules pass → auto_approved
  confidence <  0.90 OR  any violation → review_queue

Returns:
  {
    "all_records":   [...],   # every record with validation metadata
    "auto_approved": [...],   # passed all rules + high confidence
    "review_queue":  [...],   # needs human review
    "summary": {
      "total": int,
      "auto_approved": int,
      "review_queue": int,
      "rules_triggered": { rule_name: count }
    }
  }
"""
#check_all_rules()

from datetime import datetime, timedelta
from typing import Optional


# ── Rule 1: Daily hours cap ───────────────────────────────────────────────────

def _rule_daily_hours(rec: dict, config: dict) -> Optional[str]:
    """Average daily hours must not exceed max_daily_hours (default 24)."""
    max_daily   = float(config.get("max_daily_hours", 24.0))
    total_hours = rec.get("total_hours")
    working_days = rec.get("total_working_days") or 1

    if total_hours is None:
        return "total_hours is missing"

    total_hours = float(total_hours)
    if working_days > 0:
        avg_daily = total_hours / working_days
        if avg_daily > max_daily:
            return (
                f"Avg daily hours {avg_daily:.1f}h exceeds max {max_daily}h "
                f"({total_hours}h ÷ {working_days} days)"
            )
    return None


# ── Rule 2: Weekly hours cap ──────────────────────────────────────────────────

def _rule_weekly_hours(rec: dict, config: dict) -> Optional[str]:
    """
    Total hours for the period must not imply more than max_weekly_hours per week.
    Monthly cap = max_weekly_hours × 4.33 weeks.
    """
    max_weekly  = float(config.get("max_weekly_hours", 60.0))
    total_hours = rec.get("total_hours")

    if total_hours is None:
        return None  # already caught by Rule 1

    total_hours = float(total_hours)
    monthly_cap = max_weekly * 4.33  # ~4.33 weeks per month

    if total_hours > monthly_cap:
        return (
            f"Total hours {total_hours}h exceeds monthly cap "
            f"{monthly_cap:.0f}h (based on {max_weekly}h/week)"
        )
    return None


# ── Rule 3: Pay period check ──────────────────────────────────────────────────

def _rule_pay_period(rec: dict, config: dict) -> Optional[str]:
    """Work dates must fall within the configured active pay period."""
    cfg_start = config.get("period_start", "")
    cfg_end   = config.get("period_end", "")
    rec_start = rec.get("period_start")
    rec_end   = rec.get("period_end")

    if not (cfg_start and cfg_end and rec_start and rec_end):
        return None  # can't validate without both sides

    try:
        if str(rec_start) < str(cfg_start) or str(rec_end) > str(cfg_end):
            return (
                f"Record period {rec_start}→{rec_end} is outside "
                f"configured pay period {cfg_start}→{cfg_end}"
            )
    except Exception:
        return f"Cannot compare periods: rec={rec_start}→{rec_end}, cfg={cfg_start}→{cfg_end}"

    return None


# ── Rule 4: Employee ID in master register ────────────────────────────────────

def _rule_employee_id(rec: dict, master_data: list) -> Optional[str]:
    """quess_id must exist in the master register."""
    if not master_data:
        return None  # no master data loaded — skip check

    quess_id    = str(rec.get("quess_id") or "").strip()
    assignment_id = str(rec.get("assignment_id") or "").strip()

    if not quess_id and not assignment_id:
        return "Employee ID (quess_id) and Assignment ID are both missing"

    # Build lookup set from master data
    master_quess_ids      = set()
    master_assignment_ids = set()

    for row in master_data:
        qid = str(row.get("quess_id") or row.get("quess id") or row.get("emp_id") or "").strip()
        aid = str(row.get("assignment_id") or row.get("assignment id") or "").strip()
        if qid:
            master_quess_ids.add(qid)
        if aid:
            master_assignment_ids.add(aid)

    # Check quess_id first, then assignment_id
    if quess_id and master_quess_ids and quess_id not in master_quess_ids:
        return f"Quess ID '{quess_id}' not found in master register"

    if assignment_id and master_assignment_ids and assignment_id not in master_assignment_ids:
        return f"Assignment ID '{assignment_id}' not found in master register"

    return None


# ── Rule 5: Client + Project Code valid combination ───────────────────────────

def _rule_client_project(rec: dict, config: dict, master_data: list) -> Optional[str]:
    """client_name must be a valid active client."""
    client_name = str(rec.get("client_name") or "").strip()

    if not client_name:
        return "client_name is missing"

    # If master data has client info, validate against it
    if master_data:
        valid_clients = set()
        for row in master_data:
            cn = str(
                row.get("client_name") or
                row.get("client name") or
                row.get("account") or ""
            ).strip().upper()
            if cn:
                valid_clients.add(cn)

        if valid_clients and client_name.upper() not in valid_clients:
            return f"Client '{client_name}' is not a valid active client"

    return None


# ── Rule 6: Required fields ───────────────────────────────────────────────────

def _rule_required_fields(rec: dict) -> list:
    """All required fields must be present and non-empty."""
    required = {
        "employee_name":  "Employee Name",
        "assignment_id":  "Assignment ID",
        "period_start":   "Period Start",
        "period_end":     "Period End",
    }
    violations = []
    for field, label in required.items():
        val = rec.get(field)
        if val is None or str(val).strip() in ("", "None", "null"):
            violations.append(f"Missing required field: {label}")
    return violations


# ── Rule 7: Missing days ──────────────────────────────────────────────────────

def _rule_missing_days(rec: dict) -> Optional[str]:
    """
    Flag records with any missing days for human review.
    Shows exact missing dates if available.
    """
    missing = rec.get("missing_days", 0) or 0
    try:
        missing = int(float(missing))
    except (ValueError, TypeError):
        missing = 0

    if missing > 0:
        missing_dates = rec.get("missing_dates") or []
        if missing_dates:
            dates_str = ", ".join(sorted(missing_dates))
            msg = str(missing) + " missing day(s) - verify employee status. Missing dates: " + dates_str
            return msg
        return str(missing) + " missing day(s) in timesheet - verify employee status"
    return None


# ── Run all rules ─────────────────────────────────────────────────────────────

def _check_all_rules(rec: dict, config: dict, master_data: list) -> tuple:
    """
    Run all 7 business rules.
    Returns (violations: list, rules_triggered: dict)
    """
    violations     = []
    rules_triggered = {}

    def _add(rule_name: str, result):
        if isinstance(result, list):
            for v in result:
                violations.append(v)
            if result:
                rules_triggered[rule_name] = len(result)
        elif result:
            violations.append(result)
            rules_triggered[rule_name] = 1

    _add("R1_daily_hours",    _rule_daily_hours(rec, config))
    _add("R2_weekly_hours",   _rule_weekly_hours(rec, config))
    _add("R3_pay_period",     _rule_pay_period(rec, config))
    _add("R4_employee_id",    _rule_employee_id(rec, master_data))
    _add("R5_client_project", _rule_client_project(rec, config, master_data))
    _add("R6_required_fields",_rule_required_fields(rec))
    _add("R7_missing_days",   _rule_missing_days(rec))

    return violations, rules_triggered


# ── Main run ──────────────────────────────────────────────────────────────────

async def run(records: list, config: dict, master_data: list = None, **kwargs) -> dict:
    """
    Validate all records and route to auto_approved or review_queue.

    Returns:
    {
      "all_records":   [...],
      "auto_approved": [...],
      "review_queue":  [...],
      "summary": { total, auto_approved, review_queue, rules_triggered }
    }
    """
    threshold   = float(config.get("confidence_threshold", 0.90))
    master_data = master_data or []

    auto_approved   = []
    review_queue    = []
    all_records     = []
    global_rules    = {}

    for rec in records:
        rec_out    = dict(rec)
        confidence = float(rec_out.get("confidence", 0.0))

        violations, rules_triggered = _check_all_rules(rec_out, config, master_data)

        # Merge rule counts into global summary
        for rule, count in rules_triggered.items():
            global_rules[rule] = global_rules.get(rule, 0) + count

        # Stamp validation metadata
        rec_out["validation_issues"] = violations
        rec_out["validated_at"]      = datetime.utcnow().isoformat()
        rec_out["rules_triggered"]   = rules_triggered

        # Route: auto-approve or review
        if confidence >= threshold and len(violations) == 0:
            rec_out["validation_status"] = "approved"
            rec_out["review_reason"]     = None
            rec_out["payroll_ready"]     = True
            auto_approved.append(rec_out)
        else:
            rec_out["validation_status"] = "review"
            rec_out["payroll_ready"]     = False

            reasons = []
            if violations:
                reasons.extend(violations)
            if confidence < threshold:
                reasons.append(f"Low confidence ({confidence:.2f} < {threshold})")

            rec_out["review_reason"] = "; ".join(reasons)
            review_queue.append(rec_out)

        all_records.append(rec_out)

    summary = {
        "total":           len(all_records),
        "auto_approved":   len(auto_approved),
        "review_queue":    len(review_queue),
        "rules_triggered": global_rules,
    }

    print(
        f"[Agent4] Validation done — "
        f"Total: {summary['total']} | "
        f"Auto-approved: {summary['auto_approved']} | "
        f"Review: {summary['review_queue']}"
    )
    if global_rules:
        print(f"[Agent4] Rules triggered: {global_rules}")

    return {
        "all_records":   all_records,
        "auto_approved": auto_approved,
        "review_queue":  review_queue,
        "summary":       summary,
    }