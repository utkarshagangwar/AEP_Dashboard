"""Aggregate all v1 API routers under the /api/v1 prefix."""
from fastapi import APIRouter

from app.api.v1 import (
    ai_runs, android, audit, auth, dashboard, defects, executions, orchestrator,
    projects, reports, sow, test_results, test_suites, test_suites_list, users,
    visual_audit,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(audit.router)
api_router.include_router(dashboard.router)
api_router.include_router(users.router)
api_router.include_router(projects.router)
api_router.include_router(test_suites.router)
api_router.include_router(test_suites_list.router)
api_router.include_router(test_results.router)
api_router.include_router(executions.router)
api_router.include_router(reports.router)
api_router.include_router(defects.router)
api_router.include_router(ai_runs.router)
api_router.include_router(android.router)
api_router.include_router(visual_audit.router)
api_router.include_router(orchestrator.router)
api_router.include_router(sow.router)
