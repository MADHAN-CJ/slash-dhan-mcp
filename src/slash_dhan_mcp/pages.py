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
          --select: #f6d64d; --button: #171a17; --negative: #a43c2f; --negative-surface: #f8e3df;
          --positive: #34432e; --positive-surface: #e6efdc; --ring: #4a6640; }}
  body {{ margin: 0; background: #fff; color: var(--ink); font: 16px/1.6 "Onest", Arial, sans-serif;
         -webkit-font-smoothing: antialiased; }}
  main {{ max-width: 40rem; margin: 6vh auto; padding: 0 20px; }}
  h1 {{ font-family: "Libre Baskerville", Georgia, serif; font-weight: 500; font-size: 1.75rem;
       line-height: 1.2; letter-spacing: -.025em; margin: 1.4rem 0 .4rem; text-wrap: pretty; }}
  h2 {{ font-family: "Libre Baskerville", Georgia, serif; font-weight: 500; font-size: 1.25rem; margin: 2rem 0 .4rem; }}
  .brand {{ display: inline-flex; align-items: center; gap: 8px; color: var(--ink); text-decoration: none;
           font-weight: 500; font-size: 1.1rem; }}
  .brand svg {{ height: 26px; width: auto; }}
  .slash {{ display: inline-block; width: .424em; height: .8em; margin-right: .04em; vertical-align: -.12em;
           overflow: hidden; text-indent: 1em; background: currentColor;
           mask: url("data:image/svg+xml,{slash}") center / contain no-repeat;
           -webkit-mask: url("data:image/svg+xml,{slash}") center / contain no-repeat; }}
  .meta {{ color: var(--muted); font-size: .95rem; }}
  .err {{ background: var(--negative-surface); color: var(--negative); border-radius: 12px; padding: .7rem 1rem; }}
  .ok {{ background: var(--positive-surface); color: var(--positive); border-radius: 12px; padding: .7rem 1rem; }}
  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 18px; padding: 16px 20px; margin: 12px 0; }}
  .row {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; }}
  .chip {{ font-family: "Departure Mono", ui-monospace, Menlo, monospace; font-size: .8rem; padding: 2px 10px; border-radius: 999px;
          background: #fff; border: 1px solid var(--border); white-space: nowrap; }}
  .c-ARMED {{ background: var(--select); border-color: var(--select); }}
  .c-PAUSED {{ background: #fff; }}
  .c-ERROR {{ background: var(--negative-surface); color: var(--negative); border-color: var(--negative-surface); }}
  .c-EXPIRED, .c-CANCELLED {{ color: var(--muted); }}
  .fires {{ margin-top: 8px; font-family: "Departure Mono", ui-monospace, Menlo, monospace; font-size: .85rem; color: var(--muted); }}
  .inline {{ display: inline; margin: 0; }}
  button {{ display: inline-flex; align-items: center; justify-content: center; min-height: 40px; padding: 0 18px;
           margin: 8px 6px 0 0; font: inherit; font-size: .95rem; color: #fff; background: var(--button); border: 1px solid var(--button);
           border-radius: 999px; cursor: pointer; transition: background 160ms ease; }}
  button:hover {{ background: #343a32; }}
  button.quiet {{ background: #fff; color: var(--ink); border-color: var(--border); }}
  button.danger {{ background: #fff; color: var(--negative); border-color: var(--negative-surface); }}
  button:focus-visible {{ outline: 2px solid var(--ring); outline-offset: 4px; }}
</style></head><body><main>
<a class="brand" href="{home}">{logomark}<span><span class="slash" aria-hidden="true">/</span>markets</span></a>
{body}
</main></body></html>"""

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


def watcher_card(row: dict[str, Any], fires: list[dict[str, Any]], page_url: str) -> str:
    status = str(row["status"])
    buttons: list[tuple[str, str, str]] = []
    if status == "ARMED":
        buttons.append(("pause", "Pause", "quiet"))
    if status in ("PAUSED", "ERROR"):
        buttons.append(("resume", "Resume", ""))
    if status in ACTIVE:
        buttons.append(("cancel", "Cancel", "danger"))
    forms = "".join(
        f'<form class="inline" method="post" action="{html.escape(page_url)}/{row["id"]}/{action}"'
        + (
            " onsubmit=\"return confirm('Cancel this watcher for good?')\""
            if action == "cancel"
            else ""
        )
        + f'><button class="{cls}" type="submit">{label}</button></form>'
        for action, label, cls in buttons
    )
    bits = [f"checked {_when(row['last_checked_at'])}"]
    if row["last_value"] is not None:
        bits.append(f"value {html.escape(str(row['last_value']))}")
    if row["last_detail"]:
        bits.append(html.escape(str(row["last_detail"])))
    if row["fired_count"]:
        bits.append(f"alerted {row['fired_count']}× (last {_when(row['last_fired_at'])})")
    reason = ""
    if status in ("ERROR", "PAUSED") and (row["status_reason"] or row["last_error"]):
        reason = f'<div class="err" style="margin-top:8px">{html.escape(str(row["status_reason"] or row["last_error"]))}</div>'
    fire_lines = "".join(
        f"<div>⚡ {_when(f['fired_at'])}"
        + (f" — {html.escape(str(f['value']))}" if f["value"] is not None else "")
        + "</div>"
        for f in fires
    )
    market = " <span class=meta>· MCX hours</span>" if row["market"] == "MCX" else ""
    return (
        '<div class="card"><div class="row">'
        f"<div><b>{html.escape(str(row['symbol']))}</b> · {html.escape(str(row['label']))}{market}</div>"
        f'<span class="chip c-{status}">{status}</span></div>'
        f'<div class="meta">{" · ".join(bits)} · every {int(row["check_every_s"]) // 60} min · '
        f"expires {_when(row['expires_at'])} · alerts to {html.escape(_mask(str(row['notify_email'])))}</div>"
        f"{reason}"
        + (f'<div class="fires">{fire_lines}</div>' if fire_lines else "")
        + (f"<div>{forms}</div>" if forms else "")
        + "</div>"
    )


def watchers_page(
    cards: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    page_url: str,
    feed: dict[str, Any],
    notice: str = "",
) -> str:
    active = [c for c in cards if c[0]["status"] in ACTIVE]
    finished = [c for c in cards if c[0]["status"] not in ACTIVE][:20]
    if feed["status"] == "active":
        feed_line = f'<p class="ok">Dhan feed active · token valid until {html.escape(_when(feed["expiresAt"]))}</p>'
    else:
        feed_line = (
            '<p class="err">Dhan feed is not available right now (being refreshed). '
            "Watchers resume automatically.</p>"
        )
    body = (
        "<h1>Watchers</h1>"
        "<p class='meta'>Every watcher created through the /markets Dhan connector, by anyone. "
        "They run during trading hours (NSE/BSE 09:15–15:30, MCX 09:00–23:30 IST) and email the "
        "address given at creation when their condition becomes true. Create new ones by asking "
        "your AI assistant.</p>" + feed_line + notice
    )
    body += (
        "".join(watcher_card(r, f, page_url) for r, f in active)
        if active
        else "<p class='meta'>No active watchers. Ask your AI assistant to create one.</p>"
    )
    if finished:
        body += "<h2>Finished</h2>" + "".join(watcher_card(r, f, page_url) for r, f in finished)
    return shell("Watchers", body, page_url)


def ist_now_text(at: datetime) -> str:
    return at.astimezone(IST).strftime("%d %b %Y, %H:%M IST")
