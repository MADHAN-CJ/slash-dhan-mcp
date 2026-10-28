"""Create (or refresh) the slash-dhan-mcp starter dashboard in PostHog.

Needs a PostHog PERSONAL API key (phx_..., Settings → Personal API keys, scopes
insight:write + dashboard:write) in POSTHOG_PERSONAL_API_KEY and the project id in
POSTHOG_PROJECT_ID; POSTHOG_HOST defaults to https://us.posthog.com. Idempotent: insights are
matched by name inside the dashboard. Run: uv run python scripts/posthog_dashboard.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DASHBOARD = "slash-dhan-mcp usage"


def trends(
    series: list[dict[str, Any]],
    *,
    breakdown: str | None = None,
    interval: str = "day",
    display: str = "ActionsLineGraph",
) -> dict[str, Any]:
    source: dict[str, Any] = {
        "kind": "TrendsQuery",
        "series": series,
        "interval": interval,
        "dateRange": {"date_from": "-30d"},
        "trendsFilter": {"display": display},
    }
    if breakdown:
        source["breakdownFilter"] = {"breakdown": breakdown, "breakdown_type": "event"}
    return {"kind": "InsightVizNode", "source": source}


def event(name: str, math: str = "total", **props: Any) -> dict[str, Any]:
    node: dict[str, Any] = {"kind": "EventsNode", "event": name, "name": name, "math": math}
    if props:
        node["properties"] = [
            {"key": k, "value": v, "operator": "exact", "type": "event"} for k, v in props.items()
        ]
    return node


INSIGHTS: list[tuple[str, dict[str, Any]]] = [
    ("Tool calls per day by host", trends([event("run_python")], breakdown="host")),
    (
        "run_python outcomes",
        trends(
            [event("run_python", ok=True), event("run_python", ok=False)],
            display="ActionsBar",
        ),
    ),
    (
        "run_python p95 duration (ms)",
        trends([{**event("run_python"), "math": "p95", "math_property": "duration_ms"}]),
    ),
    (
        "Dhan calls per script (avg)",
        trends([{**event("run_python"), "math": "avg", "math_property": "dhan_call_count"}]),
    ),
    (
        "Top slashdhan functions",
        trends([event("run_python")], breakdown="library_calls", display="ActionsBarValue"),
    ),
    (
        "Top Dhan endpoints",
        trends([event("run_python")], breakdown="dhan_calls", display="ActionsBarValue"),
    ),
    (
        "Watchers: created vs fired vs errored",
        trends(
            [
                event("create_watcher", created=True),
                event("create_watcher", created=False),
                event("watcher_fired"),
                event("watcher_error"),
            ]
        ),
    ),
    (
        "Feed: token pushes and expiries",
        trends([event("feed_token_set"), event("feed_expired"), event("feed_token_rejected")]),
    ),
    (
        "Page and skill downloads",
        trends([event("watchers_page_viewed"), event("skill_downloaded")], breakdown="format"),
    ),
]


def main() -> int:
    key = os.environ.get("POSTHOG_PERSONAL_API_KEY", "").strip()
    project = os.environ.get("POSTHOG_PROJECT_ID", "").strip()
    host = (os.environ.get("POSTHOG_HOST_API") or "https://us.posthog.com").rstrip("/")
    if not key.startswith("phx_") or not project:
        print("set POSTHOG_PERSONAL_API_KEY (phx_...) and POSTHOG_PROJECT_ID", file=sys.stderr)
        return 2
    api = httpx.Client(
        base_url=f"{host}/api/projects/{project}",
        headers={"Authorization": f"Bearer {key}"},
        timeout=30,
    )
    found = api.get("/dashboards/", params={"search": DASHBOARD, "limit": 50}).raise_for_status()
    dashboards = [d for d in found.json()["results"] if d["name"] == DASHBOARD]
    if dashboards:
        dash = dashboards[0]
    else:
        dash = (
            api.post(
                "/dashboards/",
                json={"name": DASHBOARD, "description": "Starter metrics for the Dhan MCP"},
            )
            .raise_for_status()
            .json()
        )
    dash_id = int(dash["id"])
    existing = {
        i["name"]: i
        for i in api.get("/insights/", params={"dashboards": dash_id, "limit": 100})
        .raise_for_status()
        .json()["results"]
    }
    for name, query in INSIGHTS:
        body = {"name": name, "query": query, "dashboards": [dash_id]}
        if name in existing:
            api.patch(f"/insights/{existing[name]['id']}/", json=body).raise_for_status()
            print(f"updated  {name}")
        else:
            api.post("/insights/", json=body).raise_for_status()
            print(f"created  {name}")
    print(f"dashboard: {host}/project/{project}/dashboard/{dash_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
