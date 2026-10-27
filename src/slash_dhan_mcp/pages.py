from __future__ import annotations

import html
from datetime import datetime
from typing import Any
from urllib.parse import quote

from slashdhan.config import IST

from .db import parse_iso

_SHELL = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>{title} · /markets</title>
<style>
  :root {{ color-scheme: light; --ink: #20251b; --muted: #62675f; --surface: #f7f6f2; --border: #e4e7dc;
          --select: #f6d64d; --select-pale: #fbefbd; --button: #171a17; --negative: #a43c2f; --negative-surface: #f8e3df;
          --positive: #34432e; --positive-surface: #e6efdc; --ring: #4a6640; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #fff; color: var(--ink); font: 16px/1.55 "Onest", -apple-system, "Segoe UI", Arial, sans-serif;
         -webkit-font-smoothing: antialiased; }}
  a {{ color: inherit; }}
  header, main {{ max-width: 1040px; margin: 0 auto; padding: 0 clamp(16px, 3.5vw, 48px); }}
  header {{ padding-top: 20px; padding-bottom: 8px; }}
  main {{ padding-bottom: 96px; }}
  h1 {{ font-family: "Libre Baskerville", Georgia, serif; font-weight: 500; font-size: 30px; line-height: 1.2;
       letter-spacing: -.025em; margin: 24px 0 6px; }}
  h2 {{ font-family: "Libre Baskerville", Georgia, serif; font-weight: 500; font-size: 22px; line-height: 1.25;
       letter-spacing: -.02em; margin: 0 0 4px; }}
  .finding {{ margin: 0 0 40px; font-size: 16px; }}
  .finding.down {{ color: var(--negative); }}
  .brand {{ display: inline-flex; align-items: center; gap: 8px; color: var(--ink); text-decoration: none;
           font-weight: 500; font-size: 1.1rem; }}
  .brand svg {{ height: 26px; width: auto; }}
  .slash {{ display: inline-block; width: .424em; height: .8em; margin-right: .04em; vertical-align: -.12em;
           overflow: hidden; text-indent: 1em; background: currentColor;
           mask: url("data:image/svg+xml,{slash}") center / contain no-repeat;
           -webkit-mask: url("data:image/svg+xml,{slash}") center / contain no-repeat; }}
  .muted {{ color: var(--muted); }}
  .small {{ font-size: 14px; }}
  .err {{ background: var(--negative-surface); color: var(--negative); border-radius: 12px; padding: 12px 16px; margin: 0 0 24px; }}

  .setup {{ display: grid; grid-template-columns: minmax(220px, 340px) 1fr; gap: 32px; align-items: start; margin: 0 0 56px; }}
  .setup .lead p {{ margin: 0; }}
  .panel {{ background: var(--surface); border-radius: 18px; padding: 20px; display: grid; gap: 20px; }}
  .field label {{ display: block; font-size: 14px; font-weight: 500; margin: 0 0 8px; }}
  .joined {{ display: flex; }}
  .joined input {{ flex: 1; min-width: 0; height: 44px; padding: 0 16px; font: inherit; font-size: 15px; color: var(--ink);
                  background: #fff; border: 1px solid var(--border); border-right: 0; border-radius: 999px 0 0 999px; }}
  .joined button {{ margin: 0; height: 44px; border-radius: 0 999px 999px 0; }}
  .actions {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }}
  .btn {{ display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 44px; padding: 0 20px;
         font: inherit; font-size: 16px; color: #fff; background: var(--button); border: 1px solid var(--button);
         border-radius: 999px; cursor: pointer; text-decoration: none; white-space: nowrap; transition: background 160ms ease; }}
  .btn:hover {{ background: #343a32; }}
  .btn.secondary {{ background: #fff; color: var(--ink); border-color: var(--border); }}
  .btn.secondary:hover {{ background: var(--surface); }}
  .btn.compact {{ min-height: 32px; padding: 0 14px; font-size: 14px; }}
  .btn.negative {{ background: #fff; color: var(--negative); border-color: var(--border); }}
  .btn.negative:hover {{ background: var(--negative-surface); }}
  .btn svg {{ width: 18px; height: 18px; flex: none; }}
  .btn:focus-visible, .joined input:focus-visible {{ outline: 2px solid var(--ring); outline-offset: 3px; }}

  .section-head {{ display: flex; justify-content: space-between; align-items: baseline; gap: 8px 16px; flex-wrap: wrap; margin: 0 0 12px; }}
  .rows {{ display: grid; gap: 8px; margin: 0 0 48px; }}
  .row {{ display: grid; grid-template-columns: 1fr auto; gap: 8px 20px; align-items: start; background: var(--surface);
         border-radius: 14px; padding: 14px 18px; }}
  .row .title {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
  .row .meta {{ color: var(--muted); font-size: 14px; margin-top: 2px; }}
  .row .meta b {{ color: var(--ink); font-weight: 500; }}
  .row .reason {{ color: var(--negative); font-size: 14px; margin-top: 6px; }}
  .row .alerts {{ color: var(--muted); font-size: 14px; margin-top: 6px; }}
  .row .ctl {{ display: flex; gap: 8px; align-items: center; }}
  .row .ctl form {{ margin: 0; }}
  .pill {{ flex: none; white-space: nowrap; font-size: 13px; font-weight: 500; line-height: 22px; padding: 0 10px; border-radius: 999px;
          background: #fff; border: 1px solid var(--border); color: var(--muted); }}
  .pill.armed {{ background: var(--select-pale); border-color: #f1dc84; color: var(--ink); }}
  .pill.error {{ background: var(--negative-surface); border-color: var(--negative-surface); color: var(--negative); }}
  .pill.paused {{ color: var(--ink); }}
  .finished .row {{ background: #fff; border: 1px solid var(--border); }}
  .empty {{ background: var(--surface); border-radius: 14px; padding: 20px; margin: 0 0 48px; }}
  .empty p {{ margin: 0; }}
  @media (max-width: 680px) {{
    .setup {{ grid-template-columns: 1fr; gap: 16px; margin-bottom: 40px; }}
    .row {{ grid-template-columns: 1fr; }}
    h1 {{ font-size: 26px; }}
  }}
</style></head><body>
<header><a class="brand" href="{home}">{logomark}<span><span class="slash" aria-hidden="true">/</span>markets</span></a></header>
<main>
{body}
</main>
<script>
for (const b of document.querySelectorAll("[data-copy]")) {{
  b.addEventListener("click", async () => {{
    const input = document.getElementById(b.dataset.copy);
    input.select();
    try {{ await navigator.clipboard.writeText(input.value); }} catch (e) {{ document.execCommand("copy"); }}
    const label = b.querySelector("span");
    const was = label.textContent;
    label.textContent = "Copied";
    setTimeout(() => {{ label.textContent = was; }}, 1600);
  }});
}}
</script></body></html>"""

_LOGOMARK = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 341 174" fill="#34432e" aria-hidden="true">'
    "{paths}</svg>"
)


LOGOMARK_PATHS = '<path d="M321.7 103.317V95.0774H299.058V63.705H288.6V53.5602H278.915V43.7356H258.948V35.0202H201.585V43.4187H183.203V53.5602H173.376V63.2264H156.227V56.0956H136.299V27.5724H129.009V17.7478H120.452V8.55696H106.191V0H55.4829V8.55696H37.5767V17.7478H28.5602V28.0478H0.338053C-0.422566 27.2872 0.338053 51.9756 0.338053 51.9756H19.6704V86.2035H27.5936V102.684H36.4674V112.508H45.9752V121.382H55.4829V128.671H67.843V155.927H75.9246V164.801H83.3723V173.358H129.009V164.484H139.468V154.659H149.926V136.404H134.08V144.835H120.136V155.61H92.0116V147.37H83.3723V130.89H91.6123V121.382H99.2185V112.508H107.554V102.684H116.016V93.3343H122.671V85.8897H129.957V78.1219H146.757V85.6932H155.631V127.486H165.931V144.835H174.647V155.61H184.947V164.801H193.979V173.358H266.555V165.2H277.013V154.659H287.155V144.518H296.979V122.967H308.23V128.671H340.398V103.317H321.7ZM119.343 75.428H110.945V84.6188H101.754V93.8097H92.2462V103.317H83.3723V112.825H61.8214V107.247H53.5814V97.318H44.0736V83.6681H37.5767V33.9109H46.926V25.354H56.7506V16.797H98.7431V26.3047H109.994V34.8617H119.343V75.428ZM281.767 130.89H274.164V140.081H264.97V149.905H253.779V159.571H204.913V149.905H193.098V140.081H182.887V124.551H174.013V77.6465H183.203V69.0895H193.028V59.7403H202.853V52.1341H249.124V61.4834H262.752V71.308H272.735V81.7665H281.767V130.89Z"/><path d="M201.582 81.9258H229.696V90.6412H241.85V115.995H236.761V124.552H220.281V115.995H210.773V90.9581H201.582V81.9258Z"/><path d="M53.4961 41.8828H81.6073V50.5982H93.7614V75.9522H88.6747V84.5092H72.1946V75.9522H62.6869V50.9152H53.4961V41.8828Z"/>'
_SLASH_DATA = quote(
    '<svg width="60" height="113" viewBox="0 0 60 113" fill="none" xmlns="http://www.w3.org/2000/svg">\n<path d="M39.5479 80.2607H19.7744V112.829H0V80.2598H19.7734V32.5703H39.5479V80.2607ZM59.3213 32.5693H39.5469V0H59.3213V32.5693Z" fill="black"/>\n</svg>',
    safe="",
)

ACTIVE = ("ARMED", "PAUSED", "ERROR")
_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{}</svg>'
ICON_DOWNLOAD = _ICON.format(
    '<path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/><path d="M7 11l5 5l5-5"/><path d="M12 4v12"/>'
)
ICON_EYE = _ICON.format(
    '<path d="M10 12a2 2 0 1 0 4 0a2 2 0 0 0-4 0"/><path d="M21 12c-2.4 4-5.4 6-9 6s-6.6-2-9-6c2.4-4 5.4-6 9-6s6.6 2 9 6"/>'
)
ICON_COPY = _ICON.format(
    '<path d="M7 9.667A2.667 2.667 0 0 1 9.667 7h8.666A2.667 2.667 0 0 1 21 9.667v8.666A2.667 2.667 0 0 1 18.333 21H9.667A2.667 2.667 0 0 1 7 18.333z"/><path d="M4.012 16.737A2.005 2.005 0 0 1 3 15V5c0-1.1.9-2 2-2h10c.75 0 1.158.385 1.5 1"/>'
)


def shell(title: str, body: str, home: str) -> str:
    return _SHELL.format(
        title=html.escape(title),
        body=body,
        home=html.escape(home),
        logomark=_LOGOMARK.format(paths=LOGOMARK_PATHS),
        slash=_SLASH_DATA,
    )


def error_box(message: str) -> str:
    return f'<p class="err">{html.escape(message)}</p>'


def _when(value: Any) -> str:
    parsed = parse_iso(value)
    return parsed.astimezone(IST).strftime("%d %b, %H:%M IST") if parsed else "—"


def _mask(email: str) -> str:
    name, _, domain = email.partition("@")
    shown = name[:2] + "…" if len(name) > 2 else name
    return f"{shown}@{domain}" if domain else email


def _button(label: str, icon: str = "", cls: str = "", **attrs: str) -> str:
    extra = "".join(f' {k}="{html.escape(v)}"' for k, v in attrs.items())
    return f'<button type="button" class="btn {cls}"{extra}>{icon}<span>{html.escape(label)}</span></button>'


def _pill(status: str) -> str:
    cls = {"ARMED": "armed", "ERROR": "error", "PAUSED": "paused"}.get(status, "")
    label = status.capitalize()
    return f'<span class="pill {cls}">{label}</span>'


def watcher_row(row: dict[str, Any], fires: list[dict[str, Any]], page_url: str) -> str:
    status = str(row["status"])
    controls: list[tuple[str, str, str]] = []
    if status == "ARMED":
        controls.append(("pause", "Pause", "secondary"))
    if status in ("PAUSED", "ERROR"):
        controls.append(("resume", "Resume", ""))
    if status in ACTIVE:
        controls.append(("cancel", "Cancel", "negative"))
    forms = "".join(
        f'<form method="post" action="{html.escape(page_url)}/{row["id"]}/{action}"'
        + (
            " onsubmit=\"return confirm('Cancel this watcher for good?')\""
            if action == "cancel"
            else ""
        )
        + f'><button type="submit" class="btn compact {cls}">{label}</button></form>'
        for action, label, cls in controls
    )
    meta: list[str] = []
    if row["last_value"] is not None:
        value = f"<b>{html.escape(str(row['last_value']))}</b>"
        if row["last_detail"]:
            value += f" {html.escape(str(row['last_detail']))}"
        meta.append(value)
    elif row["last_detail"]:
        meta.append(html.escape(str(row["last_detail"])))
    meta.append(
        f"every {int(row['check_every_s']) // 60} min"
        + (" in MCX hours" if row["market"] == "MCX" else "")
    )
    meta.append(f"checked {_when(row['last_checked_at'])}")
    meta.append(f"expires {_when(row['expires_at'])}")
    meta.append(f"alerts {html.escape(_mask(str(row['notify_email'])))}")
    reason = ""
    if status in ("ERROR", "PAUSED") and (row["status_reason"] or row["last_error"]):
        reason = f'<div class="reason">{html.escape(str(row["status_reason"] or row["last_error"]))}</div>'
    alerts = ""
    if row["fired_count"]:
        recent = ", ".join(
            _when(f["fired_at"])
            + (f" at {html.escape(str(f['value']))}" if f["value"] is not None else "")
            for f in fires
        )
        alerts = (
            f'<div class="alerts">Alerted {row["fired_count"]}×'
            + (f": {recent}" if recent else "")
            + "</div>"
        )
    return (
        '<div class="row"><div>'
        f'<div class="title"><strong>{html.escape(str(row["symbol"]))}</strong>'
        f"<span>{html.escape(str(row['label']))}</span>{_pill(status)}</div>"
        f'<div class="meta">{" · ".join(meta)}</div>{reason}{alerts}</div>'
        + (f'<div class="ctl">{forms}</div>' if forms else "")
        + "</div>"
    )


def _setup(mcp_url: str, skill_url: str, skill_md_url: str) -> str:
    if not (mcp_url or skill_url):
        return ""
    fields = ""
    if mcp_url:
        fields += (
            '<div class="field"><label for="mcp-url">Connector URL</label><div class="joined">'
            f'<input id="mcp-url" type="text" readonly value="{html.escape(mcp_url)}">'
            + _button("Copy", ICON_COPY, "", **{"data-copy": "mcp-url"})
            + "</div></div>"
        )
    if skill_url:
        fields += (
            '<div class="field"><label>Skill for the assistant</label><div class="actions">'
            f'<a class="btn" href="{html.escape(skill_url)}">{ICON_DOWNLOAD}<span>Download skill (.zip)</span></a>'
            + (
                f'<a class="btn secondary" href="{html.escape(skill_md_url)}">{ICON_EYE}<span>View SKILL.md</span></a>'
                if skill_md_url
                else ""
            )
            + "</div></div>"
        )
    return (
        '<section class="setup"><div class="lead"><h2>Connect your AI assistant</h2>'
        '<p class="muted">Add the connector URL in claude.ai or ChatGPT with authentication set to none, then give '
        "the assistant the skill: upload the zip in claude.ai, or unzip it into the .claude/skills folder "
        "for Claude Code.</p></div>"
        f'<div class="panel">{fields}</div></section>'
    )


def watchers_page(
    cards: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    page_url: str,
    feed: dict[str, Any],
    notice: str = "",
    skill_url: str = "",
    skill_md_url: str = "",
    mcp_url: str = "",
) -> str:
    active = [c for c in cards if c[0]["status"] in ACTIVE]
    finished = [c for c in cards if c[0]["status"] not in ACTIVE][:20]
    armed = sum(1 for r, _ in active if r["status"] == "ARMED")
    if feed["status"] == "active":
        finding = (
            f'<p class="finding">Dhan feed active, token valid until {html.escape(_when(feed["expiresAt"]))} · '
            f"{armed} armed watcher{'s' if armed != 1 else ''}</p>"
        )
    else:
        finding = (
            '<p class="finding down">Dhan feed is down while the operator refreshes the token; '
            "armed watchers are paused and resume by themselves.</p>"
        )
    body = "<h1>Watchers</h1>" + finding + notice + _setup(mcp_url, skill_url, skill_md_url)
    body += (
        '<div class="section-head"><h2>Active</h2>'
        f'<span class="muted small">{len(active)} watcher{"s" if len(active) != 1 else ""} · '
        "checked NSE/BSE 09:15–15:30, MCX 09:00–23:30 IST</span></div>"
    )
    if active:
        body += (
            '<div class="rows">'
            + "".join(watcher_row(r, f, page_url) for r, f in active)
            + "</div>"
        )
    else:
        body += (
            '<div class="empty"><p>No active watchers.</p><p class="muted">Ask your AI assistant for one, for example '
            "“email me when NIFTY closes a 15-minute bar below 22,500”. The first check is a baseline; "
            "you get one email each time the condition becomes true.</p></div>"
        )
    if finished:
        body += (
            '<div class="section-head"><h2>Finished</h2>'
            f'<span class="muted small">{len(finished)} most recent</span></div>'
            '<div class="rows finished">'
            + "".join(watcher_row(r, f, page_url) for r, f in finished)
            + "</div>"
        )
    return shell("Watchers", body, page_url)


def ist_now_text(at: datetime) -> str:
    return at.astimezone(IST).strftime("%d %b %Y, %H:%M IST")
