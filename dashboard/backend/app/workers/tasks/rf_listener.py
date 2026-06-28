"""Robot Framework listener that inserts test results into the DB as each test completes."""
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class LiveResultListener:
    """RF Listener v3 — called after each test ends, inserts result into DB immediately."""

    ROBOT_LISTENER_API_VERSION = 3

    def __init__(self, run_id):
        self.run_id = run_id
        database_url = os.environ.get("AEP_DATABASE_URL", "")
        if not database_url:
            raise RuntimeError("AEP_DATABASE_URL environment variable not set")
        self._engine = create_engine(database_url, pool_pre_ping=True, future=True)
        self._Session = sessionmaker(bind=self._engine, autocommit=False, autoflush=False, future=True)

    def end_test(self, data, result):
        raw_status = result.status.upper() if hasattr(result, "status") else "FAIL"
        if raw_status == "PASS":
            status = "passed"
        elif raw_status == "FAIL":
            status = "failed"
        else:
            status = "failed"

        elapsed_ms = int(result.elapsedtime) if hasattr(result, "elapsedtime") and result.elapsedtime else 0

        error_msg = None
        if status == "failed" and hasattr(result, "message") and result.message:
            error_msg = str(result.message)[:2000]

        source_suite = None
        try:
            if hasattr(data, "parent") and data.parent and hasattr(data.parent, "name"):
                source_suite = str(data.parent.name)[:500]
        except Exception:
            pass

        tags_str = None
        try:
            raw_tags = data.tags if hasattr(data, "tags") else None
            if raw_tags:
                tags_str = ", ".join(str(t) for t in raw_tags)[:1000]
        except Exception:
            pass

        session = self._Session()
        try:
            from sqlalchemy import text
            session.execute(
                text(
                    "INSERT INTO test_results (id, test_run_id, test_name, status, duration_ms, error_message, source_suite, tags, created_at, updated_at) "
                    "VALUES (:id, :run_id, :test_name, :status::test_status, :duration_ms, :error_msg, :source_suite, :tags, :created_at, :created_at)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "run_id": self.run_id,
                    "test_name": result.name if hasattr(result, "name") else data.name,
                    "status": status,
                    "duration_ms": elapsed_ms,
                    "error_msg": error_msg,
                    "source_suite": source_suite,
                    "tags": tags_str,
                    "created_at": datetime.now(timezone.utc),
                },
            )
            session.commit()
        except Exception as exc:
            session.rollback()
            print(f"[LiveResultListener] Failed to insert result: {exc}")
        finally:
            session.close()
