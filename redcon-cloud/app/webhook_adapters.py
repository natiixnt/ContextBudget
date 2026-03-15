from __future__ import annotations

"""Platform-specific webhook adapters for Slack and PagerDuty.

Redcon webhooks normally deliver raw JSON payloads.  These adapters
translate them into the native formats expected by Slack Incoming Webhooks
and PagerDuty Events API v2.

Usage
-----
    from app.webhook_adapters import SlackAdapter, PagerDutyAdapter

    # In your webhook dispatch logic:
    adapter = SlackAdapter(webhook_url="https://hooks.slack.com/services/...")
    adapter.send_policy_violation(event_payload)

    adapter = PagerDutyAdapter(routing_key="abc123...")
    adapter.send_policy_violation(event_payload)

Environment variables
---------------------
RC_CLOUD_SLACK_WEBHOOK_URL        Slack Incoming Webhook URL for policy alerts
RC_CLOUD_PAGERDUTY_ROUTING_KEY    PagerDuty integration routing key (Events API v2)
"""

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_TIMEOUT = 5  # seconds

SLACK_WEBHOOK_URL: str = os.getenv("RC_CLOUD_SLACK_WEBHOOK_URL", "")
PAGERDUTY_ROUTING_KEY: str = os.getenv("RC_CLOUD_PAGERDUTY_ROUTING_KEY", "")


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> bool:
    """Fire-and-forget JSON POST.  Returns True on success."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
            return resp.status < 300
    except Exception as exc:
        logger.warning("Webhook adapter POST failed to %s: %s", url, exc)
        return False


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

class SlackAdapter:
    """Translates Redcon events into Slack Block Kit messages."""

    def __init__(self, webhook_url: str | None = None) -> None:
        self.webhook_url = webhook_url or SLACK_WEBHOOK_URL

    def _send(self, blocks: list[dict]) -> bool:
        if not self.webhook_url:
            logger.debug("Slack adapter: no webhook URL configured, skipping")
            return False
        return _post_json(self.webhook_url, {"blocks": blocks})

    def send_policy_violation(self, payload: dict[str, Any]) -> bool:
        run_id = payload.get("run_id", "unknown")
        violations = payload.get("violations", [])
        repo = payload.get("repository_id", "")
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": ":warning: Redcon Policy Violation", "emoji": True},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Run ID*\n`{run_id}`"},
                    {"type": "mrkdwn", "text": f"*Repository*\n{repo or '—'}"},
                    {"type": "mrkdwn", "text": f"*Endpoint*\n`{payload.get('endpoint', '')}`"},
                    {"type": "mrkdwn", "text": f"*Tokens used*\n{payload.get('tokens_used', 0):,}"},
                ],
            },
        ]
        if violations:
            violation_text = "\n".join(f"• {v}" for v in violations)
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Violations*\n{violation_text}"},
            })
        return self._send(blocks)

    def send_budget_overrun(self, payload: dict[str, Any]) -> bool:
        run_id = payload.get("run_id", "unknown")
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": ":rotating_light: Redcon Budget Overrun", "emoji": True},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Run ID*\n`{run_id}`"},
                    {"type": "mrkdwn", "text": f"*Repository*\n{payload.get('repository_id', '—')}"},
                    {"type": "mrkdwn", "text": f"*Tokens used*\n{payload.get('tokens_used', 0):,}"},
                    {"type": "mrkdwn", "text": f"*Max allowed*\n{payload.get('max_tokens', 0):,}"},
                ],
            },
        ]
        return self._send(blocks)

    def send_drift_alert(self, payload: dict[str, Any]) -> bool:
        repo = payload.get("repository_id", "unknown")
        drift_pct = payload.get("token_drift_pct", 0)
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": ":chart_with_upwards_trend: Redcon Token Drift Alert", "emoji": True},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Repository*\n{repo}"},
                    {"type": "mrkdwn", "text": f"*Drift*\n+{drift_pct:.1f}%"},
                    {"type": "mrkdwn", "text": f"*Verdict*\n{payload.get('verdict', '')}"},
                ],
            },
        ]
        return self._send(blocks)

    def send_generic(self, event_type: str, payload: dict[str, Any]) -> bool:
        blocks = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Redcon event*: `{event_type}`\n```{json.dumps(payload, indent=2)[:1500]}```"},
            }
        ]
        return self._send(blocks)


# ---------------------------------------------------------------------------
# PagerDuty
# ---------------------------------------------------------------------------

_PD_EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"

_SEVERITY_MAP = {
    "policy_violation": "warning",
    "budget_overrun": "error",
    "drift_alert": "warning",
}


class PagerDutyAdapter:
    """Translates Redcon events into PagerDuty Events API v2 payloads."""

    def __init__(self, routing_key: str | None = None) -> None:
        self.routing_key = routing_key or PAGERDUTY_ROUTING_KEY

    def _trigger(self, summary: str, source: str, severity: str, custom_details: dict) -> bool:
        if not self.routing_key:
            logger.debug("PagerDuty adapter: no routing key configured, skipping")
            return False
        payload = {
            "routing_key": self.routing_key,
            "event_action": "trigger",
            "payload": {
                "summary": summary,
                "source": source,
                "severity": severity,
                "custom_details": custom_details,
            },
        }
        return _post_json(_PD_EVENTS_URL, payload)

    def send_policy_violation(self, payload: dict[str, Any]) -> bool:
        run_id = payload.get("run_id", "unknown")
        repo = payload.get("repository_id", "unknown")
        violations = payload.get("violations", [])
        return self._trigger(
            summary=f"Redcon policy violation in {repo} (run {run_id})",
            source=repo,
            severity=_SEVERITY_MAP["policy_violation"],
            custom_details={**payload, "violations": violations},
        )

    def send_budget_overrun(self, payload: dict[str, Any]) -> bool:
        run_id = payload.get("run_id", "unknown")
        repo = payload.get("repository_id", "unknown")
        tokens_used = payload.get("tokens_used", 0)
        max_tokens = payload.get("max_tokens", 0)
        return self._trigger(
            summary=f"Redcon budget overrun in {repo}: {tokens_used:,} > {max_tokens:,} tokens",
            source=repo,
            severity=_SEVERITY_MAP["budget_overrun"],
            custom_details=payload,
        )

    def send_drift_alert(self, payload: dict[str, Any]) -> bool:
        repo = payload.get("repository_id", "unknown")
        drift_pct = payload.get("token_drift_pct", 0)
        return self._trigger(
            summary=f"Redcon token drift +{drift_pct:.1f}% in {repo}",
            source=repo,
            severity=_SEVERITY_MAP["drift_alert"],
            custom_details=payload,
        )

    def send_generic(self, event_type: str, payload: dict[str, Any]) -> bool:
        return self._trigger(
            summary=f"Redcon event: {event_type}",
            source=payload.get("repository_id", "redcon"),
            severity="info",
            custom_details=payload,
        )


# ---------------------------------------------------------------------------
# Dispatch helpers (called from the cloud service webhook delivery path)
# ---------------------------------------------------------------------------

def dispatch_to_platform_adapters(event_type: str, payload: dict[str, Any]) -> None:
    """Dispatch an event to all configured platform adapters.

    This is a best-effort, fire-and-forget operation.  Errors are logged at
    WARNING level and never raised.
    """
    slack = SlackAdapter()
    pd = PagerDutyAdapter()

    dispatch_fn = {
        "policy_violation": lambda a: a.send_policy_violation(payload),
        "budget_overrun":   lambda a: a.send_budget_overrun(payload),
        "drift_alert":      lambda a: a.send_drift_alert(payload),
    }

    send = dispatch_fn.get(event_type, lambda a: a.send_generic(event_type, payload))

    if slack.webhook_url:
        send(slack)
    if pd.routing_key:
        send(pd)
