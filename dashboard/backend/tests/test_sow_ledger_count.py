"""The ledger reports how many facts EXIST, not how many fit on one page.

The defect: list_ledger paginated at 500 and the client counted the array it
received, so a document with 571 extracted facts displayed "500 facts". The
71 hidden rows were the tail of the document (rows come back in document
order), which read as "extraction lost the end of my file" when in fact
every fact was saved and every fact was being used for generation.

These are contract tests over the endpoint's signature and the header it
promises — they need no database.
"""
from __future__ import annotations

import inspect

from app.api.v1 import sow as sow_api


def _ledger_params():
    return inspect.signature(sow_api.list_ledger).parameters


def test_the_endpoint_takes_a_response_so_it_can_set_a_header():
    assert "response" in _ledger_params(), (
        "list_ledger must accept Response to report X-Total-Count"
    )


def test_the_default_page_size_exceeds_any_realistic_document():
    """A 138 KB SOW yields ~570 facts. The default has to sit well above
    that or the common case silently truncates again."""
    default = _ledger_params()["limit"].default
    assert default.default >= 2000


def test_the_source_still_reports_the_true_total_separately():
    """Guards the actual mechanism: the count query must not inherit the
    LIMIT, or the header would just repeat the page size."""
    src = inspect.getsource(sow_api.list_ledger)
    assert 'response.headers["X-Total-Count"]' in src
    # .count() is taken from the filtered query BEFORE .limit() is applied.
    assert src.index("X-Total-Count") < src.index(".limit(limit)")


def test_the_total_is_computed_from_the_filtered_query():
    """Filtering to ui_element must report the ui_element total, not the
    document's grand total — otherwise the badge lies in the other
    direction."""
    src = inspect.getsource(sow_api.list_ledger)
    assert src.index("if fact_type is not None") < src.index("query.order_by(None).count()")
