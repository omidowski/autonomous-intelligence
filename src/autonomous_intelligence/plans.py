"""Per-plan monthly usage caps.

`api.py` calls `monthly_limit()` before running a metered operation
(`/research`, `/daily-content/run`, `/custom-content/run`) and returns HTTP
402 once a company is over its plan's cap for the calendar month, counted
from the same `usage_log` rows that feed the `/usage` dashboard.

Every limit here is `None` by default, which means *unlimited*: the gating
path ships wired-up but inert, so switching Stripe billing on does not
retroactively lock existing free-tier companies out mid-cycle. Set an
integer (here, or per-deployment by editing this map at startup) to start
enforcing that resource for that plan.

Resource keys: ``"research"`` (one per `/research` call) and
``"content_runs"`` (one per `/daily-content/run` or `/custom-content/run`,
regardless of how many trend bundles it produced).
"""

from __future__ import annotations

RESOURCES = ("research", "content_runs")

PLAN_LIMITS: dict[str, dict[str, int | None]] = {
    "free": {"research": None, "content_runs": None},
    "starter": {"research": None, "content_runs": None},
    "pro": {"research": None, "content_runs": None},
}


def monthly_limit(plan: str, resource: str) -> int | None:
    """The calendar-month cap for `resource` on `plan`, or `None` for
    unlimited. Unknown plans fall back to the `free` row."""
    if resource not in RESOURCES:
        raise ValueError(f"Unknown metered resource: {resource!r}.")
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"]).get(resource)


__all__ = ["PLAN_LIMITS", "RESOURCES", "monthly_limit"]
