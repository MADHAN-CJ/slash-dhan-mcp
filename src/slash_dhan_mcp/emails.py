"""Transactional emails in the /markets brand: HTML with a plain-text twin for every message."""

from __future__ import annotations

import html
from dataclasses import dataclass

INK = "#20251b"
MUTED = "#62675f"
SURFACE = "#f7f6f2"
BORDER = "#e4e7dc"
SELECT = "#f6d64d"
BUTTON = "#171a17"
SERIF = "'Libre Baskerville', Georgia, 'Times New Roman', serif"
SANS = "Onest, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif"
MONO = "'Departure Mono', ui-monospace, Menlo, Consolas, monospace"

LOGOMARK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="62" height="32" viewBox="0 0 341 174" '
    'fill="#34432e" role="img" aria-label="/markets">{paths}</svg>'
)
READ_ONLY = "/markets is read-only; it can never place, change or cancel an order."


@dataclass(frozen=True)
class Email:
    subject: str
    html: str
    text: str


def _wordmark() -> str:
    return (
        f'<span style="font-family:{SANS};font-size:18px;font-weight:600;color:{INK};'
        'letter-spacing:-.01em">/markets</span>'
    )


def _button(label: str, href: str) -> str:
    return (
        f'<a href="{html.escape(href)}" style="display:inline-block;min-height:48px;line-height:48px;'
        f"padding:0 24px;border-radius:999px;background:{BUTTON};color:#ffffff;font-family:{SANS};"
        f'font-size:16px;text-decoration:none">{html.escape(label)}</a>'
    )


def _shell(preheader: str, headline: str, body: str, footer: str, logomark_paths: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta name="color-scheme" content="light"><title>{html.escape(headline)}</title></head>
<body style="margin:0;padding:0;background:#ffffff;color:{INK}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{html.escape(preheader)}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#ffffff">
<tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:520px">
<tr><td style="padding:0 0 28px">
  <table role="presentation" cellspacing="0" cellpadding="0"><tr>
    <td style="padding-right:10px;vertical-align:middle">{LOGOMARK_SVG.format(paths=logomark_paths)}</td>
    <td style="vertical-align:middle">{_wordmark()}</td>
  </tr></table>
</td></tr>
<tr><td style="padding:0 0 12px;font-family:{SERIF};font-size:30px;line-height:1.2;font-weight:500;letter-spacing:-.02em;color:{INK}">{headline}</td></tr>
<tr><td style="font-family:{SANS};font-size:16px;line-height:1.65;color:{INK}">{body}</td></tr>
<tr><td style="padding:32px 0 0;border-top:1px solid {BORDER};margin-top:32px;font-family:{SANS};font-size:13px;line-height:1.6;color:{MUTED}">{footer}</td></tr>
</table></td></tr></table></body></html>"""


def _p(text: str) -> str:
    return f'<p style="margin:0 0 16px">{text}</p>'


def _muted(text: str) -> str:
    return f'<p style="margin:0 0 16px;color:{MUTED}">{text}</p>'


def _table(rows: list[tuple[str, str]]) -> str:
    return (
        '<table role="presentation" cellspacing="0" cellpadding="0" style="margin:0 0 24px;font-size:16px">'
        + "".join(
            f'<tr><td style="padding:6px 12px 6px 0;color:{MUTED};white-space:nowrap;vertical-align:top">'
            f'{html.escape(k)}</td><td style="padding:6px 0;font-family:{MONO if k == "Value" else SANS}">'
            f"{html.escape(v)}</td></tr>"
            for k, v in rows
        )
        + "</table>"
    )


def watcher_alert(
    symbol: str,
    label: str,
    value: str | None,
    detail: str | None,
    when: str,
    page_url: str,
    logomark_paths: str = "",
) -> Email:
    rows = [("Condition", label)]
    if value is not None:
        rows.append(("Value", value))
    if detail:
        rows.append(("Detail", detail))
    rows.append(("Time", when))
    body = (
        _p(
            f'<span style="display:inline-block;padding:2px 8px;border-radius:6px;background:{SELECT}">'
            f"{html.escape(symbol)}</span> crossed the line you asked your AI assistant to watch."
        )
        + _table(rows)
        + _p(_button("Manage watchers", page_url))
    )
    footer = f"{READ_ONLY} This watcher alerts again only after the condition resets."
    text_rows = "\n".join(f"{k}: {v}" for k, v in rows)
    return Email(
        subject=f"⚡ {symbol}: {label}",
        html=_shell(
            f"{symbol}: {label}",
            f"{html.escape(symbol)} just triggered.",
            body,
            footer,
            logomark_paths,
        ),
        text=(
            f"Your watcher on {symbol} just triggered.\n\n{text_rows}\n\n"
            f"Manage watchers: {page_url}\n\n{footer}"
        ),
    )


def feed_expired(client_id: str | None, since: str, reason: str, logomark_paths: str = "") -> Email:
    rows = [("Account", client_id or "unset"), ("Since", since), ("Dhan said", reason)]
    body = (
        _p(
            "The Dhan access token behind the /markets feed stopped working. Every assistant "
            "query fails and all watchers are paused until a fresh token is pushed through the "
            "admin API."
        )
        + _table(rows)
        + _muted("Watchers resume by themselves once the new token is accepted.")
    )
    return Email(
        subject="/markets Dhan feed DOWN: push a fresh token",
        html=_shell(
            "The /markets Dhan feed token expired",
            "Dhan feed is down.",
            body,
            READ_ONLY,
            logomark_paths,
        ),
        text=(
            "The Dhan access token behind the /markets feed stopped working.\n\n"
            + "\n".join(f"{k}: {v}" for k, v in rows)
            + "\n\nPush a fresh token through the admin API; watchers resume by themselves."
        ),
    )


def feed_expiring(client_id: str | None, expires_at: str, logomark_paths: str = "") -> Email:
    rows = [("Account", client_id or "unset"), ("Expires", expires_at)]
    body = _p(
        "The Dhan access token behind the /markets feed expires soon. Push the next one "
        "through the admin API before then to avoid an outage."
    ) + _table(rows)
    return Email(
        subject=f"/markets Dhan feed token expires {expires_at}",
        html=_shell(
            "The /markets Dhan feed token expires soon",
            "Dhan token expires soon.",
            body,
            READ_ONLY,
            logomark_paths,
        ),
        text=(
            "The Dhan access token behind the /markets feed expires soon.\n\n"
            + "\n".join(f"{k}: {v}" for k, v in rows)
            + "\n\nPush the next one through the admin API before then."
        ),
    )
