"""Grantable feature-access permission keys.

Independent of `role` (UserRole is a descriptive label only — see
app/models/user.py). Access to each area is explicitly granted per user by
an admin at creation or edit time via app/api/v1/users.py. Admins always
have every permission implicitly (see require_permission in
app/core/dependencies.py); nobody else has any permission by default.

Users and Audit Logs are intentionally not in this list — they're the
access-control mechanism itself, and stay permanently admin-only.
"""

PERMISSION_KEYS = [
    "projects",
    "test_suites",
    "execute",
    "defects",
    "vibe_testing",
    # SOW Creation & Rewrite (see app/models/sow.py, app/api/v1/sow.py) --
    # kept distinct from vibe_testing on purpose (SOW_FEATURE_PLAN.md §11.1)
    # so authoring SOWs and running the AI test agent can be granted
    # independently.
    "sow",
    # Project Intelligence (see app/models/project_intelligence.py,
    # app/api/v1/project_intelligence.py) -- two keys, kept distinct on
    # purpose (AEP_Project_Intelligence_Consolidated_Spec v3.0 §7/§22):
    # "project_intelligence" grants read/browse access to the AI
    # Intelligence tab; "project_intelligence_review" additionally grants
    # approve/edit/reject on pending records and ledger-heal apply. A
    # reviewer needs both; a QA engineer who should only browse needs the
    # first alone.
    "project_intelligence",
    "project_intelligence_review",
]
