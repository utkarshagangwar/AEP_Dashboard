"""Dashboard service — computes all KPI and chart data for the main dashboard.

All queries are designed to return in a single pass so the frontend only makes
one API call.  When *project_id* is supplied every metric is scoped to that
project.
"""
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.logging import get_logger

logger = get_logger(__name__)


def get_dashboard_stats(db: Session, project_id: str | None = None) -> dict:
    """Return all dashboard statistics in a single dict.

    When project_id is provided, all stats are scoped to that project.
    """
    try:
        kpis = _compute_kpis(db, project_id)
        pass_rate_by_day = _pass_rate_by_day(db, project_id)
        runs_by_project = _runs_by_project(db, project_id)
        recent_runs = _recent_runs(db, project_id)
        top_defects = _top_defects(db, project_id)

        return {
            **kpis,
            "pass_rate_by_day": pass_rate_by_day,
            "runs_by_project": runs_by_project,
            "recent_runs": recent_runs,
            "top_defects": top_defects,
        }
    except Exception as exc:
        logger.error("Failed to compute dashboard stats: %s", exc)
        raise


# ── Private helpers ───────────────────────────────────────────────────────────


def _compute_kpis(db: Session, project_id: str | None = None) -> dict:
    """Compute the six KPI cards with period-over-period change."""
    params: dict = {}
    tr_join = ""
    tr_filter = ""
    d_filter = ""

    if project_id:
        params["project_id"] = project_id
        tr_join = "JOIN test_suites _pts ON tr.test_suite_id = _pts.id"
        tr_filter = "AND _pts.project_id = :project_id"
        d_filter = "AND d.project_id = :project_id"

    # --- Total runs today vs yesterday ---
    row = db.execute(
        text(f"""
            SELECT
              COUNT(*) FILTER (WHERE tr.created_at >= date_trunc('day', NOW())) AS today,
              COUNT(*) FILTER (
                WHERE tr.created_at >= date_trunc('day', NOW()) - INTERVAL '1 day'
                  AND tr.created_at <  date_trunc('day', NOW())
              ) AS yesterday
            FROM test_runs tr
            {tr_join}
            WHERE TRUE {tr_filter}
        """),
        params,
    ).fetchone()
    total_today = int(row[0] or 0)
    total_yesterday = int(row[1] or 0)
    today_change = _pct_change(total_today, total_yesterday)

    # --- Pass rate last 7d vs previous 7d (individual test results, not runs) ---
    pr_join = (
        "JOIN test_suites _pts ON tr.test_suite_id = _pts.id"
        if project_id
        else ""
    )
    pr_filter = "AND _pts.project_id = :project_id" if project_id else ""

    row = db.execute(
        text(f"""
            SELECT
              COUNT(*) FILTER (
                WHERE tres.status = 'passed'
                  AND tr.created_at >= NOW() - INTERVAL '7 days'
              ) AS passed_7d,
              COUNT(*) FILTER (
                WHERE tr.created_at >= NOW() - INTERVAL '7 days'
              ) AS total_7d,
              COUNT(*) FILTER (
                WHERE tres.status = 'passed'
                  AND tr.created_at >= NOW() - INTERVAL '14 days'
                  AND tr.created_at <  NOW() - INTERVAL '7 days'
              ) AS passed_prev,
              COUNT(*) FILTER (
                WHERE tr.created_at >= NOW() - INTERVAL '14 days'
                  AND tr.created_at <  NOW() - INTERVAL '7 days'
              ) AS total_prev
            FROM test_results tres
            JOIN test_runs tr ON tres.test_run_id = tr.id
            {pr_join}
            WHERE tr.created_at >= NOW() - INTERVAL '14 days'
            {pr_filter}
        """),
        params,
    ).fetchone()
    pr7 = _safe_rate(int(row[0] or 0), int(row[1] or 0))
    pr_prev = _safe_rate(int(row[2] or 0), int(row[3] or 0))
    pr_change = _pct_change(pr7, pr_prev)

    # --- Open defects ---
    row = db.execute(
        text(f"""
            SELECT
              COUNT(*) FILTER (WHERE d.status IN ('open', 'in_progress')) AS open_cnt,
              COUNT(*) FILTER (
                WHERE d.status IN ('open', 'in_progress')
                  AND d.severity = 'critical'
              ) AS crit_cnt,
              COUNT(*) FILTER (
                WHERE d.status IN ('open', 'in_progress')
                  AND d.created_at >= NOW() - INTERVAL '7 days'
              ) AS open_prev_7d,
              COUNT(*) FILTER (
                WHERE d.status IN ('open', 'in_progress')
                  AND d.created_at >= NOW() - INTERVAL '14 days'
                  AND d.created_at <  NOW() - INTERVAL '7 days'
              ) AS open_prev2_7d
            FROM defects d
            WHERE TRUE {d_filter}
        """),
        params,
    ).fetchone()
    open_defects = int(row[0] or 0)
    crit_defects = int(row[1] or 0)
    open_prev = int(row[2] or 0)
    open_prev2 = int(row[3] or 0)
    defect_change = _pct_change(open_prev, open_prev2)

    # --- Critical defects (separate change vs prior period) ---
    row_crit = db.execute(
        text(f"""
            SELECT
              COUNT(*) FILTER (
                WHERE d.severity = 'critical'
                  AND d.status IN ('open', 'in_progress')
                  AND d.created_at >= NOW() - INTERVAL '7 days'
              ) AS crit_7d,
              COUNT(*) FILTER (
                WHERE d.severity = 'critical'
                  AND d.status IN ('open', 'in_progress')
                  AND d.created_at >= NOW() - INTERVAL '14 days'
                  AND d.created_at <  NOW() - INTERVAL '7 days'
              ) AS crit_prev
            FROM defects d
            WHERE TRUE {d_filter}
        """),
        params,
    ).fetchone()
    crit_change = _pct_change(int(row_crit[0] or 0), int(row_crit[1] or 0))

    # --- Active projects / Test suites (context-dependent) ---
    if project_id:
        row = db.execute(
            text(
                "SELECT COUNT(*) FROM test_suites"
                " WHERE project_id = :project_id AND is_active = true"
            ),
            params,
        ).fetchone()
        active_value = int(row[0] or 0)
        active_label = "Test Suites"
        proj_change = None
    else:
        row = db.execute(
            text("""
                SELECT
                  COUNT(*) FILTER (WHERE is_active = true) AS active,
                  COUNT(*) FILTER (
                    WHERE is_active = true
                      AND created_at >= NOW() - INTERVAL '30 days'
                  ) AS new_30d,
                  COUNT(*) FILTER (
                    WHERE is_active = true
                      AND created_at >= NOW() - INTERVAL '60 days'
                      AND created_at <  NOW() - INTERVAL '30 days'
                  ) AS prev_30d
                FROM projects
            """)
        ).fetchone()
        active_value = int(row[0] or 0)
        proj_change = _pct_change(int(row[1] or 0), int(row[2] or 0))
        active_label = "Active Projects"

    # --- Avg execution duration (properly scoped to last 7 days) ---
    row = db.execute(
        text(f"""
            SELECT
              COALESCE(AVG(CASE
                WHEN tr.started_at IS NOT NULL AND tr.ended_at IS NOT NULL
                  AND tr.created_at >= NOW() - INTERVAL '7 days'
                THEN EXTRACT(EPOCH FROM (tr.ended_at - tr.started_at)) * 1000
              END), 0) AS avg_ms_7d,
              COALESCE(AVG(CASE
                WHEN tr.started_at IS NOT NULL AND tr.ended_at IS NOT NULL
                  AND tr.created_at >= NOW() - INTERVAL '14 days'
                  AND tr.created_at <  NOW() - INTERVAL '7 days'
                THEN EXTRACT(EPOCH FROM (tr.ended_at - tr.started_at)) * 1000
              END), 0) AS avg_ms_prev
            FROM test_runs tr
            {tr_join}
            WHERE tr.created_at >= NOW() - INTERVAL '14 days'
            {tr_filter}
        """),
        params,
    ).fetchone()
    avg_ms = int(float(row[0] or 0))
    avg_prev = int(float(row[1] or 0))
    avg_change = _pct_change(avg_ms, avg_prev)

    return {
        "total_runs_today": {
            "value": total_today,
            "change_pct": today_change,
            "label": "Runs Today",
        },
        "pass_rate_7d": {
            "value": round(pr7, 1),
            "change_pct": pr_change,
            "label": "Pass Rate (7d)",
        },
        "open_defects": {
            "value": open_defects,
            "change_pct": defect_change,
            "label": "Open Defects",
        },
        "critical_defects": {
            "value": crit_defects,
            "change_pct": crit_change,
            "label": "Critical Defects",
        },
        "active_projects": {
            "value": active_value,
            "change_pct": proj_change,
            "label": active_label,
        },
        "avg_execution_duration": {
            "value": avg_ms,
            "change_pct": avg_change,
            "label": "Avg Duration",
        },
    }


def _pass_rate_by_day(db: Session, project_id: str | None = None) -> list[dict]:
    """Return pass rate % per day for the last 14 days (based on test results)."""
    params: dict = {}
    join_clause = ""
    filter_clause = ""
    if project_id:
        params["project_id"] = project_id
        join_clause = "JOIN test_suites _pts ON tr.test_suite_id = _pts.id"
        filter_clause = "AND _pts.project_id = :project_id"

    rows = db.execute(
        text(f"""
            SELECT
              d.day::date AS day,
              COALESCE(r.total, 0) AS total,
              COALESCE(r.passed, 0) AS passed
            FROM generate_series(
              date_trunc('day', NOW()) - INTERVAL '13 days',
              date_trunc('day', NOW()),
              INTERVAL '1 day'
            ) AS d(day)
            LEFT JOIN (
              SELECT
                date_trunc('day', tr.created_at)::date AS day,
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE tres.status = 'passed') AS passed
              FROM test_results tres
              JOIN test_runs tr ON tres.test_run_id = tr.id
              {join_clause}
              WHERE tr.created_at >= date_trunc('day', NOW()) - INTERVAL '13 days'
              {filter_clause}
              GROUP BY date_trunc('day', tr.created_at)::date
            ) r ON r.day = d.day::date
            ORDER BY d.day
        """),
        params,
    ).fetchall()

    return [
        {
            "date": str(r[0]),
            "total": int(r[1]),
            "passed": int(r[2]),
            "pass_rate": round(_safe_rate(int(r[2]), int(r[1])), 1),
        }
        for r in rows
    ]


def _runs_by_project(db: Session, project_id: str | None = None) -> list[dict]:
    """Return run counts per project for the last 30 days."""
    params: dict = {}
    filter_clause = ""
    if project_id:
        params["project_id"] = project_id
        filter_clause = "AND p.id = :project_id"

    rows = db.execute(
        text(f"""
            SELECT p.name AS project_name, COUNT(tr.id) AS run_count
            FROM test_runs tr
            JOIN test_suites ts ON tr.test_suite_id = ts.id
            JOIN projects p ON ts.project_id = p.id
            WHERE tr.created_at >= NOW() - INTERVAL '30 days'
            {filter_clause}
            GROUP BY p.name
            ORDER BY run_count DESC
        """),
        params,
    ).fetchall()

    return [{"project_name": r[0], "run_count": int(r[1])} for r in rows]


def _recent_runs(db: Session, project_id: str | None = None) -> list[dict]:
    """Return the last 10 test runs with project and suite info."""
    params: dict = {}
    filter_clause = ""
    if project_id:
        params["project_id"] = project_id
        filter_clause = "AND p.id = :project_id"

    rows = db.execute(
        text(f"""
            SELECT
              tr.id,
              p.name AS project_name,
              ts.name AS suite_name,
              tr.status,
              COALESCE(rs.passed, 0) AS passed,
              COALESCE(rs.failed, 0) AS failed,
              COALESCE(rs.total, 0) AS total,
              CASE
                WHEN tr.started_at IS NOT NULL AND tr.ended_at IS NOT NULL
                THEN EXTRACT(EPOCH FROM (tr.ended_at - tr.started_at)) * 1000
                ELSE NULL
              END AS duration_ms,
              tr.created_at
            FROM test_runs tr
            LEFT JOIN test_suites ts ON tr.test_suite_id = ts.id
            LEFT JOIN projects p ON ts.project_id = p.id
            LEFT JOIN (
              SELECT test_run_id,
                     COUNT(*) AS total,
                     COUNT(*) FILTER (WHERE status = 'passed') AS passed,
                     COUNT(*) FILTER (WHERE status = 'failed') AS failed
              FROM test_results
              GROUP BY test_run_id
            ) rs ON rs.test_run_id = tr.id
            WHERE TRUE {filter_clause}
            ORDER BY tr.created_at DESC
            LIMIT 10
        """),
        params,
    ).fetchall()

    return [
        {
            "id": str(r[0]),
            "project_name": r[1],
            "suite_name": r[2],
            "status": r[3].value if hasattr(r[3], "value") else r[3],
            "passed": int(r[4]),
            "failed": int(r[5]),
            "total": int(r[6]),
            "duration_ms": int(r[7]) if r[7] else None,
            "created_at": r[8].isoformat() if r[8] else "",
        }
        for r in rows
    ]


def _top_defects(db: Session, project_id: str | None = None) -> list[dict]:
    """Return top 5 open / in-progress defects sorted by severity."""
    params: dict = {}
    filter_clause = ""
    if project_id:
        params["project_id"] = project_id
        filter_clause = "AND d.project_id = :project_id"

    rows = db.execute(
        text(f"""
            SELECT
              d.id,
              d.title,
              d.severity,
              u.full_name AS assigned_to_name,
              d.created_at
            FROM defects d
            LEFT JOIN users u ON d.assigned_to = u.id
            WHERE d.status IN ('open', 'in_progress')
            {filter_clause}
            ORDER BY
              CASE d.severity
                WHEN 'critical' THEN 1
                WHEN 'high' THEN 2
                WHEN 'medium' THEN 3
                ELSE 4
              END,
              d.created_at DESC
            LIMIT 5
        """),
        params,
    ).fetchall()

    return [
        {
            "id": str(r[0]),
            "title": r[1],
            "severity": r[2].value if hasattr(r[2], "value") else r[2],
            "assigned_to_name": r[3],
            "created_at": r[4].isoformat() if r[4] else "",
        }
        for r in rows
    ]


# ── Utility helpers ───────────────────────────────────────────────────────────


def _safe_rate(numerator: int, denominator: int) -> float:
    """Return percentage rate, guarding against division by zero."""
    if denominator == 0:
        return 0.0
    return (numerator / denominator) * 100.0


def _pct_change(current: float, previous: float) -> float | None:
    """Return % change from previous to current, or None if no prior data."""
    if previous == 0:
        if current == 0:
            return 0.0
        return None
    return round(((current - previous) / previous) * 100.0, 1)
