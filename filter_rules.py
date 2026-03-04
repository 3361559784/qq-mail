from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class FilterDecision:
    should_reply: bool
    reason: str
    confidence: float


class MailFilter:
    def __init__(self, level: str = "medium") -> None:
        if level != "medium":
            raise ValueError("Only FILTER_LEVEL=medium is supported in current version")
        self.level = level

    @staticmethod
    def _has_header(headers: dict[str, str], name: str) -> bool:
        value = headers.get(name.lower(), "")
        return bool(value and value.strip())

    @staticmethod
    def _contains_keywords(text: str, keywords: list[str]) -> bool:
        lowered = text.lower()
        return any(keyword in lowered for keyword in keywords)

    @staticmethod
    def _marketing_body_hit(body: str) -> bool:
        lowered = body.lower()
        marketing_markers = [
            "unsubscribe",
            "退订",
            "manage preferences",
            "view in browser",
            "click here",
            "limited time",
            "coupon",
            "promo",
            "discount",
            "sale",
            "deal",
            "优惠",
            "折扣",
            "立减",
            "立即购买",
            "下单",
        ]
        hits = sum(1 for marker in marketing_markers if marker in lowered)
        link_count = len(re.findall(r"https?://", lowered, re.IGNORECASE))
        tracking_link_count = len(re.findall(r"https?://[^\\s]+(utm_|ref=|trk=|mc_eid=)", lowered, re.IGNORECASE))
        if hits >= 2:
            return True
        if link_count >= 5:
            return True
        if tracking_link_count >= 2:
            return True
        return False

    def _hard_filter(
        self,
        headers: dict[str, str],
        sender: str,
        subject: str,
        body: str,
    ) -> FilterDecision | None:
        sender_lower = sender.lower().strip()
        subject_lower = subject.lower().strip()
        auto_submitted = headers.get("auto-submitted", "").strip().lower()
        precedence = headers.get("precedence", "").strip().lower()
        return_path = headers.get("return-path", "").strip().lower().replace(" ", "")
        to_header = headers.get("to", "").lower()

        if auto_submitted and auto_submitted != "no":
            return FilterDecision(False, "hard:auto-submitted", 1.0)

        if precedence in {"bulk", "list", "junk", "auto_reply"}:
            return FilterDecision(False, "hard:precedence", 1.0)

        if self._has_header(headers, "List-Unsubscribe"):
            return FilterDecision(False, "hard:list-unsubscribe", 1.0)
        if self._has_header(headers, "List-Id"):
            return FilterDecision(False, "hard:list-id", 1.0)
        if self._has_header(headers, "List-Post"):
            return FilterDecision(False, "hard:list-post", 1.0)

        if return_path == "<>":
            return FilterDecision(False, "hard:return-path-empty", 1.0)

        if "undisclosed-recipients" in to_header:
            return FilterDecision(False, "hard:undisclosed-recipients", 1.0)

        if any(flag in sender_lower for flag in ["no-reply", "noreply", "mailer-daemon", "postmaster"]):
            return FilterDecision(False, "hard:non-human-sender", 1.0)

        system_subject_keywords = [
            "验证码",
            "账单",
            "通知",
            "订阅",
            "促销",
            "newsletter",
            "notification",
            "otp",
            "verify",
            "verification",
            "receipt",
            "invoice",
            "system alert",
            "limited time",
            "discount",
            "sale",
            "coupon",
            "offer",
            "deal",
            "black friday",
            "cyber monday",
            "优惠",
            "折扣",
            "活动",
            "福利",
        ]
        if self._contains_keywords(subject_lower, system_subject_keywords):
            return FilterDecision(False, "hard:system-subject", 0.95)

        if self._marketing_body_hit(body):
            return FilterDecision(False, "hard:marketing-body", 0.95)

        return None

    def _human_signal_score(self, subject: str, body: str) -> float:
        text = f"{subject}\n{body}".strip()
        if not text:
            return 0.0

        score = 0.0
        body_strip = body.strip()

        if len(body_strip) >= 20:
            score += 0.2
        if len(body_strip) >= 60:
            score += 0.15
        if "?" in text or "？" in text:
            score += 0.25
        if re.search(r"(请问|麻烦|可以|是否|帮忙|thanks|thank you|could you|please)", text, re.IGNORECASE):
            score += 0.25
        if re.search(r"(你好|您好|hi|hello)", text, re.IGNORECASE):
            score += 0.1

        link_count = len(re.findall(r"https?://", text, re.IGNORECASE))
        if link_count >= 3:
            score -= 0.3
        if link_count >= 6:
            score -= 0.4

        if re.search(r"(请勿回复|do not reply|自动发送|system generated|unsubscribe)", text, re.IGNORECASE):
            score -= 0.4

        return max(0.0, min(1.0, score))

    def evaluate(
        self,
        headers: dict[str, str],
        sender: str,
        subject: str,
        body: str,
        denylist_hit: bool,
        allowlist_hit: bool,
        frequent_hit: bool,
    ) -> FilterDecision:
        if denylist_hit:
            return FilterDecision(False, "hard:sender-denylist", 1.0)

        normalized_headers = {key.lower(): value for key, value in headers.items()}
        hard = self._hard_filter(
            headers=normalized_headers,
            sender=sender,
            subject=subject,
            body=body,
        )
        if hard:
            return hard

        score = self._human_signal_score(subject=subject, body=body)
        if score >= 0.55:
            return FilterDecision(True, "soft:human-signal", score)

        if allowlist_hit:
            return FilterDecision(True, "soft:allowlist", max(score, 0.7))

        if frequent_hit:
            return FilterDecision(True, "soft:frequent-sender", max(score, 0.65))

        return FilterDecision(False, "soft:low-human-signal", score)
