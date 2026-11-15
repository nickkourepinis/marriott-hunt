"""Build the HTML report: one self-contained file, no network needed to open it."""
from __future__ import annotations

from . import audit
from .server import inline_page, json_for_script


def render(bundle, live=None, plans=None):
    """Return the report page as HTML text.

    live:  set by the survey server; adds the QR code panel and auto-refresh.
    plans: floor plan pictures to embed, for a saved report.
    """
    payload = {"bundle": bundle, "live": live, "plans": plans or {}}
    return inline_page("report.html", {"/*DATA*/": json_for_script(payload)})


def write(datadir, name="report.html"):
    """Rebuild the bundle from saved data and write the report next to it. Returns (path, bundle)."""
    survey = datadir.load_json("survey.json", {}) or {}
    plans = {}
    for floor, plan in (survey.get("plans") or {}).items():
        if plan.get("image"):
            plans[floor] = {"image": plan["image"], "width_m": plan.get("width_m") or 12}
    bundle = audit.build_bundle(datadir, survey_data=survey)
    for plan in bundle["survey"]["plans"].values():
        plan.pop("image", None)
    path = datadir.path(name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(render(bundle, plans=plans))
    return path, bundle
