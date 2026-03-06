from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class FilterDecision:
    should_reply: bool
    reason: str
    confidence: float


class MailFilter:
    MARKETING_KEYWORDS = [
        "limited time",
        "act now",
        "last chance",
        "exclusive offer",
        "special offer",
        "promo code",
        "discount code",
        "coupon",
        "save up to",
        "black friday",
        "cyber monday",
        "flash sale",
        "free shipping",
        "unsubscribe",
        "manage preferences",
        "view in browser",
        "shop now",
        "buy now",
        "order now",
        "claim now",
        "click here",
        "优惠码",
        "折扣码",
        "优惠券",
        "限时",
        "限时抢购",
        "抢购",
        "特惠",
        "立减",
        "满减",
        "包邮",
        "立即购买",
        "立即下单",
        "马上抢",
        "促销",
    ]

    CTA_KEYWORDS = [
        "click here",
        "learn more",
        "shop now",
        "buy now",
        "order now",
        "claim now",
        "立即购买",
        "立即下单",
        "点击购买",
        "点击查看",
        "了解更多",
    ]

    SHORT_HUMAN_TOKENS = [
        "在吗",
        "在么",
        "方便吗",
        "有空吗",
        "收到",
        "辛苦了",
        "麻烦",
        "请回",
        "请回复",
        "ok",
        "okay",
        "ping",
        "test",
        "测试",
    ]

    SHORT_HUMAN_BLOCK_TOKENS = [
        "discount",
        "sale",
        "coupon",
        "offer",
        "promo",
        "shop now",
        "buy now",
        "order now",
        "unsubscribe",
        "优惠",
        "折扣",
        "优惠券",
        "优惠码",
        "限时",
        "抢购",
        "立减",
        "促销",
    ]

    MOBILE_SIGNATURE_PATTERNS = [
        r"^\s*发自我的\s*iphone\s*$",
        r"^\s*发自我的\s*ipad\s*$",
        r"^\s*sent from my iphone\s*$",
        r"^\s*sent from my ipad\s*$",
        r"^\s*sent from outlook for ios\s*$",
        r"^\s*sent from outlook for android\s*$",
    ]

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
    def _count_keyword_hits(text: str, keywords: list[str]) -> int:
        lowered = text.lower()
        return sum(1 for marker in keywords if marker in lowered)

    def _normalize_human_text(self, subject: str, body: str) -> tuple[str, str]:
        body_lines = body.splitlines()
        cleaned_lines: list[str] = []
        for line in body_lines:
            lowered = line.strip().lower()
            if any(re.match(pattern, lowered, re.IGNORECASE) for pattern in self.MOBILE_SIGNATURE_PATTERNS):
                continue
            cleaned_lines.append(line)
        cleaned_body = "\n".join(cleaned_lines).strip()
        cleaned_subject = subject.strip()
        return cleaned_subject, cleaned_body

    def _marketing_content_hit(self, subject: str, body: str) -> bool:
        merged = f"{subject}\n{body}".lower()
        keyword_hits = self._count_keyword_hits(merged, self.MARKETING_KEYWORDS)
        cta_hits = self._count_keyword_hits(merged, self.CTA_KEYWORDS)
        link_count = len(re.findall(r"https?://", merged, re.IGNORECASE))
        tracking_link_count = len(
            re.findall(
                r"https?://[^\s]+(utm_|ref=|trk=|mc_eid=|fbclid=|gclid=|yclid=)",
                merged,
                re.IGNORECASE,
            )
        )
        money_or_discount_hit = bool(
            re.search(
                r"(\$\s?\d+|¥\s?\d+|￥\s?\d+|\d+\s?%(\s?off)?|save\s?\d+\s?%)",
                merged,
                re.IGNORECASE,
            )
        )
        if keyword_hits >= 2:
            return True
        if cta_hits >= 1 and (link_count >= 1 or money_or_discount_hit):
            return True
        if tracking_link_count >= 1 and keyword_hits >= 1:
            return True
        if link_count >= 5:
            return True
        return False

    def _is_short_human_message(self, subject: str, body: str) -> bool:
        norm_subject, norm_body = self._normalize_human_text(subject, body)
        body_strip = norm_body.strip()
        if not body_strip:
            return False
        if len(body_strip) > 40:
            return False
        merged = f"{norm_subject}\n{norm_body}".lower()
        if re.search(r"https?://|www\.", merged, re.IGNORECASE):
            return False
        if self._contains_keywords(merged, self.SHORT_HUMAN_BLOCK_TOKENS):
            return False
        if self._contains_keywords(merged, self.SHORT_HUMAN_TOKENS):
            return True
        if ("?" in merged or "？" in merged) and len(body_strip) <= 16:
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
        body_lower = body.lower().strip()
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

        auth_subject_keywords = [
            "验证码",
            "otp",
            "verification code",
            "two-step verification",
            "2fa",
            "password reset",
            "reset your password",
        ]
        if self._contains_keywords(subject_lower, auth_subject_keywords):
            return FilterDecision(False, "hard:system-subject", 0.95)

        bulk_subject_keywords = [
            "newsletter",
            "system alert",
            "自动通知",
            "订阅更新",
            "subscription update",
        ]
        if self._contains_keywords(subject_lower, bulk_subject_keywords):
            link_count = len(re.findall(r"https?://", body_lower, re.IGNORECASE))
            has_bulk_markers = "unsubscribe" in body_lower or "manage preferences" in body_lower
            if link_count >= 2 or has_bulk_markers:
                return FilterDecision(False, "hard:system-subject", 0.9)

        if self._marketing_content_hit(subject=subject, body=body):
            return FilterDecision(False, "hard:marketing-body", 0.95)

        return None

    def _human_signal_score(self, subject: str, body: str) -> float:
        norm_subject, norm_body = self._normalize_human_text(subject, body)
        text = f"{norm_subject}\n{norm_body}".strip()
        if not text:
            return 0.0

        score = 0.0
        body_strip = norm_body.strip()

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

        if self._is_short_human_message(subject=subject, body=body):
            return FilterDecision(True, "soft:short-human-signal", 0.62)

        score = self._human_signal_score(subject=subject, body=body)
        if score >= 0.55:
            return FilterDecision(True, "soft:human-signal", score)

        if allowlist_hit:
            return FilterDecision(True, "soft:allowlist", max(score, 0.7))

        if frequent_hit:
            return FilterDecision(True, "soft:frequent-sender", max(score, 0.65))

        return FilterDecision(False, "soft:low-human-signal", score)
