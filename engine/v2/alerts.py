"""
engine.v2.alerts — tell someone when the book changes.
=====================================================

The terminal only spoke when you opened it: a new pass, an audit regression, a
drawdown breach or a decayed strategy were all silent until the next manual
look. This module adds a tiny, dependency-free notifier with three channels,
each enabled purely by environment variable so no secret ever lives in the repo:

* ``ZERO_ALERT_WEBHOOK``            HTTPS endpoint receiving ``{"text": ...}``
  (Slack/Discord/Teams-compatible shape);
* ``ZERO_ALERT_TELEGRAM_TOKEN`` + ``ZERO_ALERT_TELEGRAM_CHAT``;
* ``ZERO_ALERT_SMTP_HOST`` (+ ``_PORT``, ``_USER``, ``_PASSWORD``,
  ``_FROM``, ``_TO``).

Every call is appended to ``db/v2/alerts.jsonl`` regardless of delivery, so the
audit trail exists even when nothing is configured. Delivery failures are
logged and swallowed: an alert must never break the job that raised it.
"""
from __future__ import annotations

import json
import os
import smtplib
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from typing import Any, Dict, Iterable, List, Optional

ALERTS_DIR = os.path.join("db", "v2")
ALERTS_PATH = os.path.join(ALERTS_DIR, "alerts.jsonl")
DEDUPE_WINDOW_SECONDS = int(os.getenv("ZERO_ALERT_DEDUPE_SECONDS", "3600"))
TIMEOUT = float(os.getenv("ZERO_ALERT_TIMEOUT", "8"))

LEVELS = ("INFO", "WARN", "CRITICAL")

_recent: Dict[str, float] = {}


def _append_log(row: Dict[str, Any]) -> None:
    try:
        os.makedirs(ALERTS_DIR, exist_ok=True)
        with open(ALERTS_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, sort_keys=True) + "\n")
    except Exception:
        pass


def channels() -> List[str]:
    out = []
    if os.getenv("ZERO_ALERT_WEBHOOK"):
        out.append("webhook")
    if os.getenv("ZERO_ALERT_TELEGRAM_TOKEN") and os.getenv("ZERO_ALERT_TELEGRAM_CHAT"):
        out.append("telegram")
    if os.getenv("ZERO_ALERT_SMTP_HOST") and os.getenv("ZERO_ALERT_SMTP_TO"):
        out.append("smtp")
    return out


def _post_json(url: str, payload: Dict[str, Any]) -> bool:
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _send_webhook(text: str) -> bool:
    url = os.getenv("ZERO_ALERT_WEBHOOK")
    return _post_json(url, {"text": text}) if url else False


def _send_telegram(text: str) -> bool:
    token = os.getenv("ZERO_ALERT_TELEGRAM_TOKEN")
    chat = os.getenv("ZERO_ALERT_TELEGRAM_CHAT")
    if not token or not chat:
        return False
    return _post_json(
        f"https://api.telegram.org/bot{token}/sendMessage",
        {"chat_id": chat, "text": text},
    )


def _send_smtp(subject: str, text: str) -> bool:
    host = os.getenv("ZERO_ALERT_SMTP_HOST")
    to = os.getenv("ZERO_ALERT_SMTP_TO")
    if not host or not to:
        return False
    try:
        port = int(os.getenv("ZERO_ALERT_SMTP_PORT", "587"))
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = os.getenv("ZERO_ALERT_SMTP_FROM", os.getenv("ZERO_ALERT_SMTP_USER", "zero@localhost"))
        msg["To"] = to
        msg.set_content(text)
        with smtplib.SMTP(host, port, timeout=TIMEOUT) as s:
            s.ehlo()
            if port != 25:
                s.starttls()
            user = os.getenv("ZERO_ALERT_SMTP_USER")
            pwd = os.getenv("ZERO_ALERT_SMTP_PASSWORD")
            if user and pwd:
                s.login(user, pwd)
            s.send_message(msg)
        return True
    except Exception:
        return False


def notify(level: str, title: str, body: str = "",
           payload: Optional[Dict[str, Any]] = None,
           dedupe_key: Optional[str] = None) -> Dict[str, Any]:
    """
    Send one alert. Returns what happened (also written to the audit log).

    ``dedupe_key`` suppresses repeats inside ``ZERO_ALERT_DEDUPE_SECONDS`` so a
    per-tick drawdown check does not spam the channel.
    """
    level = level.upper() if level.upper() in LEVELS else "INFO"
    key = dedupe_key or title
    now = time.time()
    suppressed = False
    last = _recent.get(key)
    if last and now - last < DEDUPE_WINDOW_SECONDS:
        suppressed = True
    else:
        _recent[key] = now

    text = f"[ZERO {level}] {title}" + (f"\n{body}" if body else "")
    result: Dict[str, Any] = {
        "ts": now,
        "level": level,
        "title": title,
        "body": body,
        "payload": payload or {},
        "suppressed": suppressed,
        "delivered": [],
        "configured": channels(),
    }
    if not suppressed:
        if "webhook" in result["configured"] and _send_webhook(text):
            result["delivered"].append("webhook")
        if "telegram" in result["configured"] and _send_telegram(text):
            result["delivered"].append("telegram")
        if "smtp" in result["configured"] and _send_smtp(f"ZERO {level}: {title}", text):
            result["delivered"].append("smtp")
    _append_log(result)
    return result


# ── The events worth waking someone for ────────────────────────────────────

def alert_new_passes(names: Iterable[str]) -> Dict[str, Any]:
    names = list(names)
    return notify("INFO", f"{len(names)} strategy pass(es) produced",
                  ", ".join(names[:10]), {"strategies": names},
                  dedupe_key="new_passes:" + ",".join(sorted(names))[:120])


def alert_audit_regression(health_pct: float, failed: int) -> Dict[str, Any]:
    return notify("CRITICAL" if health_pct < 90 else "WARN",
                  f"Integrity audit at {health_pct}% ({failed} failing)",
                  dedupe_key=f"audit:{round(health_pct)}:{failed}")


def alert_drawdown(book: str, drawdown_pct: float, limit_pct: float) -> Dict[str, Any]:
    return notify("CRITICAL" if drawdown_pct > limit_pct else "WARN",
                  f"{book} drawdown {drawdown_pct:.1f}% (limit {limit_pct:.0f}%)",
                  dedupe_key=f"dd:{book}:{round(drawdown_pct)}")


def alert_decay(decayed: Iterable[str]) -> Dict[str, Any]:
    decayed = list(decayed)
    return notify("WARN", f"{len(decayed)} strategy(ies) decayed forward",
                  ", ".join(decayed[:10]), dedupe_key="decay:" + ",".join(sorted(decayed))[:120])


def alert_provenance_rejected(reason: str, symbol: str = "") -> Dict[str, Any]:
    return notify("WARN", f"Promotion blocked: {reason}",
                  f"symbol={symbol}", dedupe_key=f"prov:{symbol}:{reason}")


if __name__ == "__main__":
    print(json.dumps(notify("INFO", "ZERO alert channel test",
                            "If you can read this, the channel works."), indent=2))
