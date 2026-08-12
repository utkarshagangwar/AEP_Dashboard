"""Robot Framework listener that inserts test results into the DB as each test completes."""
import json
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class LiveResultListener:
    """RF Listener v3 — called after each test ends, inserts result into DB immediately.

    Project Intelligence capture (start_keyword/end_keyword/close, below) is
    entirely additive to this class's original job and is deliberately kept
    dependency-light: it never imports anything from `app.*`. This listener
    runs inside the `robot`/`pabot` subprocess itself, launched with a
    project-specific --pythonpath (see workers/tasks/execution.py) that has
    no guarantee of matching the main app's import graph — pulling in
    app.services/app.models here risks failing to import at all in that
    subprocess and aborting every single test run via the --listener flag,
    which is a far worse failure than Project Intelligence just not
    capturing anything. So this listener does the absolute minimum: buffer
    plain dicts in memory and, at the very end of the run, write them to a
    JSON sidecar file next to the suite's own output.xml. A separate Celery
    task in the main app process (workers/tasks/pi_ingest.py), which is
    already allowed to depend on the full app, reads that sidecar after the
    subprocess has exited and does the real DB work — see
    workers/tasks/execution.py's call to ingest_rf_capture.delay(...).
    """

    ROBOT_LISTENER_API_VERSION = 3

    def __init__(self, run_id):
        self.run_id = run_id
        database_url = os.environ.get("AEP_DATABASE_URL", "")
        if not database_url:
            raise RuntimeError("AEP_DATABASE_URL environment variable not set")
        self._engine = create_engine(database_url, pool_pre_ping=True, future=True)
        self._Session = sessionmaker(bind=self._engine, autocommit=False, autoflush=False, future=True)

        # Project Intelligence capture state — see class docstring. Always
        # initialised (even when the flag is off) so every method below can
        # unconditionally append to self._pi_events without a None check;
        # the flag alone controls whether anything is ever collected.
        self._pi_capture_enabled = self._pi_flag_enabled()
        self._pi_events = []

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

    # ── Project Intelligence capture ─────────────────────────────────────
    # Everything below is best-effort and additive — see the class
    # docstring for why it stays free of app.* imports. Every method is
    # wrapped so nothing it does can ever propagate an exception back into
    # Robot Framework's listener call, which would abort the run.

    _ACTION_KEYWORDS = {
        "click element": "button",
        "click button": "button",
        "click link": "link",
        "click image": "other",
        "input text": "input",
        "input password": "input",
        "choose file": "input",
        "select checkbox": "checkbox",
        "unselect checkbox": "checkbox",
        "select radio button": "radio",
        "select from list by label": "select",
        "select from list by value": "select",
        "select from list by index": "select",
        "submit form": "button",
        "press keys": "input",
    }

    @staticmethod
    def _pi_flag_enabled() -> bool:
        """Mirrors app.services.pi_ingest.pi_enabled()/rf_capture_enabled()'s
        opt-out convention, reimplemented against bare os.environ (see class
        docstring on why this can't import that module directly)."""
        try:
            master = os.environ.get("PI_ENABLED", "").strip().lower() in ("1", "true", "yes")
            if not master:
                return False
            raw = os.environ.get("PI_CAPTURE_RF", "").strip()
            if not raw:
                return True
            return raw.lower() not in ("0", "false", "no", "off")
        except Exception:
            return False

    @staticmethod
    def _classify_locator(locator) -> str:
        """Best-effort strategy label for a SeleniumLibrary locator string,
        in the vocabulary app.services.pi_ingest.classify_identity_tier
        expects. Never definitive — worst case this under-classifies a
        stable locator into a lower-trust tier, which only makes downstream
        drift detection more conservative, never wrongly confident."""
        if not locator:
            return "text"
        text = str(locator)
        lower = text.lower()
        if "data-testid" in lower:
            return "data-testid"
        if lower.startswith("id:") or lower.startswith("id="):
            return "id"
        if lower.startswith("name:") or lower.startswith("name="):
            return "name"
        if "aria-label" in lower:
            return "aria-label"
        if lower.startswith("css:") or lower.startswith("css="):
            return "css"
        if lower.startswith("xpath:") or lower.startswith("xpath=") or text.strip().startswith("//"):
            return "xpath"
        return "text"

    def _current_page_url(self):
        """Best-effort current URL via whatever browser library the suite
        loaded. Returns None (never raises) if no such library is active —
        e.g. a non-browser keyword, or a suite that hasn't opened a browser
        yet."""
        try:
            from robot.libraries.BuiltIn import BuiltIn

            builtin = BuiltIn()
            for lib_name in ("SeleniumLibrary", "Browser"):
                try:
                    lib = builtin.get_library_instance(lib_name)
                except Exception:
                    continue
                if lib is None:
                    continue
                driver = getattr(lib, "driver", None)
                if driver is not None:
                    return getattr(driver, "current_url", None)
            return None
        except Exception:
            return None

    def end_keyword(self, data, result):
        """Buffer one observed UI action, if this keyword call is one we
        recognise as a user-facing action (see _ACTION_KEYWORDS) — assertion
        and wait keywords are deliberately not captured, since they don't
        represent something a user did. Never raises."""
        if not self._pi_capture_enabled:
            return
        try:
            keyword_name = str(getattr(data, "name", "") or "")
            component_type = self._ACTION_KEYWORDS.get(keyword_name.strip().lower())
            if component_type is None:
                return

            args = list(getattr(data, "args", None) or [])
            locator = args[0] if args else None
            status = getattr(result, "status", None)

            self._pi_events.append({
                "page_url": self._current_page_url(),
                "component_type": component_type,
                "keyword_name": keyword_name,
                "locator": locator,
                "locator_strategy": self._classify_locator(locator),
                "status": status,
            })
        except Exception:
            pass

    def close(self):
        """Called once, after the entire run (all suites) has finished.
        Writes the buffered events to a JSON sidecar next to this run's own
        output.xml, where workers/tasks/execution.py's finalization phase
        expects to find it (see its call to
        workers.tasks.pi_ingest.ingest_rf_capture.delay(...)). A missing or
        unwritable sidecar is simply "nothing captured" downstream — never
        an error surfaced here."""
        if not self._pi_capture_enabled or not self._pi_events:
            return
        try:
            from robot.libraries.BuiltIn import BuiltIn

            output_dir = BuiltIn().get_variable_value("${OUTPUT_DIR}")
            if not output_dir:
                return
            sidecar_path = os.path.join(str(output_dir), "pi_capture.json")
            with open(sidecar_path, "w", encoding="utf-8") as fh:
                json.dump({"run_id": self.run_id, "raw_events": self._pi_events}, fh)
        except Exception:
            pass
