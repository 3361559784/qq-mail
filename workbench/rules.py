from __future__ import annotations

import json
import re

from workbench.models import RuleDecision


ACTION_KEYWORDS = {
    "请",
    "麻烦",
    "截止",
    "需要",
    "确认",
    "回复",
    "review",
    "面试",
    "附件",
    "合同",
    "deadline",
    "action",
    "follow up",
}

NOTIFY_DOMAINS = {
    "github.com",
    "noreply.github.com",
    "steamPowered.com".lower(),
    "qq.com",
    "patreon.com",
}

SPAM_MARKERS = {
    "newsletter",
    "unsubscribe",
    "促销",
    "优惠",
    "折扣",
    "验证码",
    "账单",
    "notification",
}


def _contains_any(text: str, keywords: set[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _sender_domain(sender_email: str) -> str:
    sender = sender_email.strip().lower()
    if "@" not in sender:
        return ""
    return sender.split("@", 1)[1]


def _is_unread(flags_json: str) -> bool:
    try:
        flags = json.loads(flags_json)
    except Exception:
        return True
    if not isinstance(flags, list):
        return True
    lowered = {str(flag).lower() for flag in flags}
    return "\\seen" not in lowered


def triage_by_rules(
    sender_email: str,
    subject: str,
    body_text: str,
    headers_json: str,
    flags_json: str,
    high_priority_senders: set[str] | None = None,
) -> RuleDecision:
    sender = sender_email.strip().lower()
    domain = _sender_domain(sender)
    merged_text = f"{subject}\n{body_text}"
    high_priority_senders = high_priority_senders or set()

    headers_text = headers_json.lower()
    if (
        "list-unsubscribe" in headers_text
        or "auto-submitted" in headers_text
        or "precedence" in headers_text
        or "return-path\": \"<>\"" in headers_text
        or any(x in sender for x in ("noreply", "no-reply", "mailer-daemon", "postmaster"))
    ):
        return RuleDecision(
            category="spamish",
            priority="low",
            needs_action=False,
            evidence=["hard header/sender rule matched"],
            is_candidate=False,
        )

    if domain in NOTIFY_DOMAINS and _contains_any(merged_text, SPAM_MARKERS):
        return RuleDecision(
            category="fyi",
            priority="low",
            needs_action=False,
            evidence=["notification sender/domain"],
            is_candidate=False,
        )

    if _contains_any(merged_text, SPAM_MARKERS):
        return RuleDecision(
            category="spamish",
            priority="low",
            needs_action=False,
            evidence=["spam/promo markers in subject/body"],
            is_candidate=False,
        )

    unread = _is_unread(flags_json)
    has_action_keyword = _contains_any(merged_text, ACTION_KEYWORDS)
    high_sender = sender in {x.lower() for x in high_priority_senders}

    if unread and (has_action_keyword or high_sender):
        return RuleDecision(
            category="action",
            priority="high" if high_sender else "med",
            needs_action=True,
            evidence=["unread + action keyword/high-priority sender"],
            is_candidate=True,
        )

    if subject.lower().startswith("re:") and _contains_any(merged_text, {"please confirm", "请确认", "follow up"}):
        return RuleDecision(
            category="waiting",
            priority="med",
            needs_action=False,
            evidence=["reply-like follow-up wording"],
            is_candidate=True,
        )

    if unread:
        return RuleDecision(
            category="fyi",
            priority="low",
            needs_action=False,
            evidence=["unread but no hard action signal"],
            is_candidate=True,
        )

    return RuleDecision(
        category="fyi",
        priority="low",
        needs_action=False,
        evidence=["default rule"],
        is_candidate=False,
    )
