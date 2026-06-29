"""
hitl_manager.py
────────────────────────────────────────────────────────────────────────────────
Human-in-the-Loop (HITL) Change Manager — CLI tool.

Commands:
  python libs/hitl_manager.py list     → show all suggestion files with status
  python libs/hitl_manager.py review   → print pending suggestions for human review
  python libs/hitl_manager.py apply    → apply approved suggestions to page objects
                                          and append audit trail to resources/locators.resource

Approval workflow:
  1. Run tests. AI suggestions appear in:
       test-artifacts/ai_suggestions/{test-name}/suggestions.json
  2. Open a suggestions.json file. For each suggestion, set:
       "approved": true   ← apply this locator
       "approved": false  ← reject (skip)
       "approved": null   ← not yet reviewed (pending)
  3. Run:  python libs/hitl_manager.py apply
  4. Approved locators are:
       a. Written into the relevant pages/*.py class attribute (replaces old locator)
       b. Appended to resources/locators.resource as an RF audit trail variable
       c. Marked "applied": true in the suggestions JSON

apply is idempotent: re-running it skips already-applied suggestions.
────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
REPO_ROOT        = Path(__file__).resolve().parents[1]
ARTIFACTS_ROOT   = REPO_ROOT / "test-artifacts" / "ai_suggestions"
PAGES_DIR        = REPO_ROOT / "pages"
LOCATORS_RESOURCE = REPO_ROOT / "resources" / "locators.resource"

# ANSI colours (disabled on Windows if not supported)
try:
    import os
    _colour = os.name != "nt" or "ANSICON" in os.environ or "WT_SESSION" in os.environ
except Exception:
    _colour = False

_G = "\033[92m" if _colour else ""   # green
_Y = "\033[93m" if _colour else ""   # yellow
_R = "\033[91m" if _colour else ""   # red
_B = "\033[94m" if _colour else ""   # blue
_D = "\033[0m"  if _colour else ""   # reset


# ── Helpers ────────────────────────────────────────────────────────────────────

def _all_suggestion_files() -> list[Path]:
    """Return all {suite}/{test_case}.json files under ai_suggestions/."""
    if not ARTIFACTS_ROOT.exists():
        return []
    return sorted(ARTIFACTS_ROOT.glob("*/*.json"))


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"{_R}[ERROR] Cannot read {path}: {exc}{_D}")
        return {}


def _save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _status_badge(data: dict) -> str:
    status = data.get("status", "unknown")
    colours = {
        "pending_review": _Y,
        "applied":        _G,
        "skipped":        _B,
        "ai_error":       _R,
    }
    c = colours.get(status, "")
    return f"{c}[{status}]{_D}"


# ── Commands ───────────────────────────────────────────────────────────────────

def cmd_list() -> None:
    """List all suggestion files with one-line status summary."""
    files = _all_suggestion_files()
    if not files:
        print(f"{_Y}No suggestion files found in {ARTIFACTS_ROOT}{_D}")
        return

    print(f"\n{'Suite / Test Case':<60} {'Status':<20} {'Suggestions':>11}")
    print("─" * 95)
    for f in files:
        data = _load(f)
        if not data:
            continue
        suite = data.get("suite_name", f.parent.name)
        test  = data.get("test_name", f.stem)
        label = f"{suite} / {test}"[:59]
        badge = data.get("status", "unknown")
        count = len(data.get("suggestions", []))
        approved = sum(1 for s in data.get("suggestions", []) if s.get("approved") is True)
        print(f"{label:<60} {badge:<20} {count:>5} total / {approved:>3} approved")
    print()


def cmd_review() -> None:
    """Pretty-print all pending suggestions for human review."""
    files = _all_suggestion_files()
    if not files:
        print(f"{_Y}No suggestion files found.{_D}")
        return

    pending = [
        f for f in files
        if _load(f).get("status") == "pending_review"
    ]

    if not pending:
        print(f"{_G}No pending suggestions. All reviewed.{_D}")
        return

    print(f"\n{_B}══ PENDING AI LOCATOR SUGGESTIONS ══{_D}  ({len(pending)} file(s))\n")

    for f in pending:
        data = _load(f)
        if not data:
            continue

        test = data.get("test_name", "unknown")
        locator = data.get("failed_locator", "(unknown)")
        analysis = data.get("analysis", "")
        suggestions = data.get("suggestions", [])
        timestamp = data.get("timestamp", "")

        print(f"{_B}┌─ Test: {test}{_D}")
        print(f"│  Timestamp:      {timestamp}")
        print(f"│  Failed locator: {_R}{locator}{_D}")
        print(f"│  Analysis:       {analysis}")
        print(f"│  File:           {f}")
        print(f"│")

        for i, s in enumerate(suggestions, 1):
            approved = s.get("approved")
            status_icon = (
                f"{_G}✓ approved{_D}"  if approved is True  else
                f"{_R}✗ rejected{_D}" if approved is False else
                f"{_Y}? pending{_D}"
            )
            print(f"│  [{i}] {s.get('locator', '')}  ({s.get('confidence','?')} confidence)")
            print(f"│      {s.get('explanation','')}")
            print(f"│      Status: {status_icon}")
            print(f"│")

        print(f"└─ To approve: open the file above and set \"approved\": true on a suggestion.")
        print()

    print(
        f"{_Y}After editing, run:{_D}  python libs/hitl_manager.py apply\n"
    )


def _find_page_object(failed_locator: str) -> tuple[Path | None, str | None]:
    """
    Search pages/*.py for the failed locator string.
    Returns (file_path, attribute_name) or (None, None) if not found.
    """
    if not failed_locator:
        return None, None

    for py_file in sorted(PAGES_DIR.glob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        if failed_locator in source:
            # Try to find the attribute name on the line containing the locator
            for line in source.splitlines():
                if failed_locator in line:
                    m = re.match(r"\s+(\w+)\s*=", line)
                    if m:
                        return py_file, m.group(1)
            return py_file, None   # found in file but couldn't extract name

    return None, None


def _patch_page_object(py_file: Path, old_locator: str, new_locator: str) -> bool:
    """
    Replace old_locator string with new_locator in the Python page object file.
    Handles both single-string and concatenated-string locator definitions.
    Returns True if the file was modified.
    """
    source = py_file.read_text(encoding="utf-8")
    if old_locator not in source:
        print(f"{_Y}  Locator not found in {py_file.name} — may have already been patched.{_D}")
        return False

    new_source = source.replace(old_locator, new_locator, 1)
    py_file.write_text(new_source, encoding="utf-8")
    print(f"{_G}  Patched {py_file.name}{_D}")
    return True


def _append_locators_resource(
    test_name: str,
    attribute_name: str | None,
    old_locator: str,
    new_locator: str,
    py_file: Path | None,
) -> None:
    """
    Append the approved locator change to resources/locators.resource
    as an RF variable (audit trail).
    """
    LOCATORS_RESOURCE.parent.mkdir(parents=True, exist_ok=True)

    # Bootstrap the file if it does not yet exist
    if not LOCATORS_RESOURCE.exists():
        LOCATORS_RESOURCE.write_text(
            "*** Settings ***\n"
            "Documentation    AI-approved locator overrides.\n"
            "...              Applied by: python libs/hitl_manager.py apply\n"
            "...              Human-reviewed before every write.\n\n"
            "*** Variables ***\n"
            "# Format: ${<PAGE>_<ATTR>}    <new_locator>\n\n",
            encoding="utf-8",
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    page_name = py_file.stem.upper() if py_file else "UNKNOWN_PAGE"
    attr_name  = attribute_name.upper() if attribute_name else "UNKNOWN_ATTR"
    var_name   = f"{page_name}_{attr_name}"

    block = (
        f"# ── {test_name} — applied {timestamp} ────────────────────────────────\n"
        f"# Old: {old_locator}\n"
        f"${{LOCATOR_{var_name}}}    {new_locator}\n\n"
    )

    with LOCATORS_RESOURCE.open("a", encoding="utf-8") as fh:
        fh.write(block)

    print(f"{_G}  Appended to resources/locators.resource{_D}")


def cmd_apply() -> None:
    """
    Apply all approved (approved: true) suggestions that haven't been applied yet.
    For each:
      1. Patch the relevant pages/*.py file.
      2. Append to resources/locators.resource.
      3. Mark suggestion applied: true in JSON.
    """
    files = _all_suggestion_files()
    applied_count = 0
    skipped_count = 0

    for f in files:
        data = _load(f)
        if not data:
            continue

        suggestions = data.get("suggestions", [])
        file_dirty = False

        for s in suggestions:
            # Only process approved, not-yet-applied suggestions
            if s.get("approved") is not True:
                skipped_count += 1
                continue
            if s.get("applied") is True:
                print(f"{_B}[SKIP] Already applied: {s.get('locator','')[:80]}{_D}")
                skipped_count += 1
                continue

            new_locator   = s.get("locator", "")
            old_locator   = data.get("failed_locator", "")
            test_name     = data.get("test_name", "unknown")

            if not new_locator:
                print(f"{_R}[ERROR] Suggestion has no locator field — skipping.{_D}")
                continue

            print(f"\n{_B}Applying suggestion for: {test_name}{_D}")
            print(f"  Old: {_R}{old_locator}{_D}")
            print(f"  New: {_G}{new_locator}{_D}")

            # Find and patch the page object
            py_file, attr_name = _find_page_object(old_locator)
            if py_file:
                patched = _patch_page_object(py_file, old_locator, new_locator)
            else:
                print(
                    f"{_Y}  No page object found containing: {old_locator[:80]}{_D}\n"
                    f"  Manual update required."
                )
                patched = False

            # Always write to locators.resource as audit trail
            _append_locators_resource(
                test_name=test_name,
                attribute_name=attr_name,
                old_locator=old_locator,
                new_locator=new_locator,
                py_file=py_file,
            )

            # Mark as applied
            s["applied"] = True
            s["applied_at"] = datetime.now(timezone.utc).isoformat()
            file_dirty = True
            applied_count += 1

        # Update status field on the suggestions file
        if file_dirty:
            all_applied = all(
                s.get("applied") is True
                for s in suggestions
                if s.get("approved") is True
            )
            if all_applied:
                data["status"] = "applied"
            _save(f, data)

    print(f"\n{_G}Done.{_D}  Applied: {applied_count}  |  Skipped/pending: {skipped_count}\n")

    if applied_count > 0:
        print(
            f"{_Y}Next steps:{_D}\n"
            f"  1. Review pages/*.py diffs before committing.\n"
            f"  2. Run the affected tests to verify the new locators work.\n"
            f"  3. Commit both the patched page object and resources/locators.resource.\n"
        )


# ── Entry point ────────────────────────────────────────────────────────────────

COMMANDS = {
    "list":   cmd_list,
    "review": cmd_review,
    "apply":  cmd_apply,
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(
            f"\nUsage: python libs/hitl_manager.py <command>\n\n"
            f"Commands:\n"
            f"  list    — show all suggestion files with status\n"
            f"  review  — print pending suggestions for human review\n"
            f"  apply   — apply approved suggestions to page objects\n"
        )
        sys.exit(1)

    COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    main()
