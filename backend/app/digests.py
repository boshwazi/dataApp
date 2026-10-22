"""Dashboard digests: a weekly or monthly email with a dashboard's headline numbers and a link to it.

A digest lists each published page's summary and KPI cards (the numbers and their change), already
written and checked when the dashboard was built, so no AI runs to send it. It goes to workspace members
the dashboard is shared with, by email when SMTP is set up (mail.py) and always as an in-app notification.

A dataset with row-level security roles gets digests without numbers (only the link): a digest is worked
out once, and one person's numbers mustn't reach another. Schedules are in Malaysia time.
"""

import html
import re
from datetime import UTC, datetime, timedelta, timezone

from . import model, report, security

MYT = timezone(timedelta(hours=8))
FREQUENCIES = ("off", "weekly", "monthly")
AT = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
MAX_RECIPIENTS = 50


class DigestError(ValueError):
    pass


def settings(dashboard: dict) -> dict:
    return {"frequency": "off", "day": 0, "at": "08:00", "people": [], "groups": [], "next_run": None,
            "last_sent": None, **(dashboard.get("digest") or {})}


def next_slot(config: dict, after: datetime) -> datetime | None:
    """Weekly on a weekday, or monthly on the 1st, at a time of day in Malaysia time."""
    if config["frequency"] == "off":
        return None
    hour, minute = (int(x) for x in config["at"].split(":"))
    local = after.astimezone(MYT)
    if config["frequency"] == "weekly":
        candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        while candidate <= local or candidate.weekday() != config["day"]:
            candidate += timedelta(days=1)
    else:
        candidate = local.replace(day=1, hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local:
            candidate = (candidate + timedelta(days=32)).replace(day=1)
    return candidate.astimezone(UTC)


def configure(dashboard: dict, body: dict, groups: list[dict]) -> dict:
    config = settings(dashboard)
    if body.get("frequency") not in FREQUENCIES:
        raise DigestError("Send it weekly, monthly, or not at all.")
    if not AT.match(str(body.get("at") or "08:00")):
        raise DigestError("Give the time as HH:MM, for example 08:00.")
    if int(body.get("day") or 0) not in range(7):
        raise DigestError("The day is 0 (Monday) to 6 (Sunday).")
    people = security.emails(body.get("people"))
    known = {g["id"] for g in groups}
    chosen = [g for g in body.get("groups") or [] if g in known]
    if body["frequency"] != "off" and not (people or chosen):
        raise DigestError("Choose who gets it.")
    if len(people) > MAX_RECIPIENTS:
        raise DigestError(f"Up to {MAX_RECIPIENTS} people.")
    config.update(frequency=body["frequency"], day=int(body.get("day") or 0), at=str(body.get("at") or "08:00"),
                  people=people, groups=chosen)
    slot = next_slot(config, datetime.now(UTC))
    config["next_run"] = slot.isoformat(timespec="seconds") if slot else None
    dashboard["digest"] = config
    return config


def recipients(dashboard: dict, groups: list[dict], members: list[str] | None) -> list[str]:
    """Who it goes to: the people and groups chosen, if they're members (when known) and the dashboard is shared with them."""
    config = settings(dashboard)
    wanted = list(config["people"])
    for group in groups:
        if group["id"] in config["groups"]:
            wanted += [m for m in group["members"] if m not in wanted]
    sharing = dashboard.get("sharing") or {"mode": "workspace"}
    allowed = [e for e in wanted if sharing["mode"] == "workspace" or security.in_refs(e, sharing, groups)]
    return [e for e in allowed if members is None or e in members]


def due(boards, at: datetime | None = None) -> list[str]:
    at = at or datetime.now(UTC)
    return [d["id"] for d in boards.all()
            if d.get("published") and settings(d)["next_run"] and datetime.fromisoformat(settings(d)["next_run"]) <= at]


def scheduled(dashboard: dict) -> None:
    """After sending: the next slot."""
    config = settings(dashboard)
    config["last_sent"] = datetime.now(UTC).isoformat(timespec="seconds")
    slot = next_slot(config, datetime.now(UTC))
    config["next_run"] = slot.isoformat(timespec="seconds") if slot else None
    dashboard["digest"] = config


# What it says


def short(value, fmt: dict) -> str:
    if value is None:
        return "no data"
    kind = (fmt or {}).get("type", "number")
    if kind == "percent":
        return f"{value * 100:.1f}%" if abs(value) <= 1.5 else f"{value:.1f}%"
    size = abs(value)
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if size >= limit:
            text = f"{value / limit:.1f}{suffix}"
            break
    else:
        text = f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"
    return f"RM {text}" if kind == "currency" else text


def compose(con, project: dict, dashboard: dict, dataset: dict, link: str) -> dict:
    """The digest's subject, plain text and HTML."""
    restricted = bool(security.roles(dataset))
    formats = {m["name"]: m["format"] for m in model.state(project)["measures"]}
    sections = []
    for page in (dashboard.get("published") or {}).get("pages") or []:
        kpis = []
        if not restricted:
            for visual in page["visuals"]:
                if visual["type"] != "kpi":
                    continue
                try:
                    data = report.visual_data(con, project, visual)
                except (report.ReportError, model.ModelError):
                    continue
                change = f" ({data['delta_pct'] * 100:+.1f}% {data.get('comparison') or 'vs before'})" \
                    if data.get("delta_pct") is not None else ""
                kpis.append(f"{visual['title']}: {short(data.get('value'), formats.get(visual['measures'][0]))}{change}")
        sections.append({"title": page["title"], "summary": "" if restricted else page.get("summary") or "", "kpis": kpis})
    subject = f"{dashboard['name']}: your {settings(dashboard)['frequency']} update"
    lines = [dashboard["name"], ""]
    for s in sections:
        lines += [s["title"]] + ([s["summary"]] if s["summary"] else []) + [f"  - {k}" for k in s["kpis"]] + [""]
    if restricted:
        lines += ["Open the dashboard to see your numbers.", ""]
    lines.append(f"Open it: {link}")
    body = "".join(
        f"<h3 style='margin:18px 0 4px'>{html.escape(s['title'])}</h3>"
        + (f"<p style='margin:0 0 6px;color:#444'>{html.escape(s['summary'])}</p>" if s["summary"] else "")
        + ("<ul>" + "".join(f"<li>{html.escape(k)}</li>" for k in s["kpis"]) + "</ul>" if s["kpis"] else "")
        for s in sections)
    note = "<p>Open the dashboard to see your numbers.</p>" if restricted else ""
    page = (f"<div style='font-family:Segoe UI,Arial,sans-serif;max-width:600px;color:#16202e'>"
            f"<h2 style='margin:0'>{html.escape(dashboard['name'])}</h2>{body}{note}"
            f"<p style='margin-top:20px'><a href='{html.escape(link)}' style='background:#1b5fd1;color:#fff;padding:8px 14px;"
            f"border-radius:6px;text-decoration:none'>Open the dashboard</a></p></div>")
    return {"subject": subject, "text": "\n".join(lines), "html": page, "restricted": restricted}
