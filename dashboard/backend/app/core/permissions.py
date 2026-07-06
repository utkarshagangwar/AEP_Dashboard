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
    "test_runs",
    "execute",
    "defects",
    "reports",
    "vibe_testing",
]
