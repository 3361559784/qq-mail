from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any, Callable

import requests
from personalization import (
    PersonalizationBundle,
    PersonalizationLoadError,
    build_personalized_prompt,
    detect_reply_language,
    load_personalization_bundle,
    needs_profile_disclosure,
    select_fixed_examples,
    select_relevant_memories,
)


@dataclass(frozen=True)
class ModelReply:
    text: str
    used_model: str
    attempted_models: list[str]


class LimitExceededError(RuntimeError):
    pass


class ModelChainClient:
    TOKEN_PROFILES = [(16000, 8000), (8000, 4000), (4000, 2000)]

    def __init__(
        self,
        token: str,
        api_url: str,
        primary: str,
        fallbacks: list[str] | None = None,
        timeout_seconds: int = 45,
        request_fn: Callable[..., Any] = requests.post,
        personalization_dir: Path | None = None,
    ) -> None:
        self.token = token
        self.api_url = api_url
        self.primary = primary
        self.fallbacks = fallbacks or []
        self.timeout_seconds = timeout_seconds
        self.request_fn = request_fn
        self.logger = logging.getLogger("qq-auto-reply")
        self.personalization_dir = personalization_dir or Path(__file__).resolve().parent / "personalization"
        self.personalization_bundle: PersonalizationBundle | None = None
        try:
            self.personalization_bundle = load_personalization_bundle(self.personalization_dir)
        except PersonalizationLoadError as exc:
            self.logger.warning("Personalization disabled due to load error: %s", exc)

    def _build_default_prompt(self, sender: str, subject: str, body: str, language: str) -> str:
        if language == "en":
            return (
                "You are an email auto-reply assistant.\n"
                "Goal: generate a professional, polite, concise email body that can be sent directly.\n"
                "Rules:\n"
                "1) Keep 2-4 sentences by default.\n"
                "2) If the question is clear, do not ask unnecessary follow-up questions.\n"
                "3) If information is missing, ask at most one clarifying question.\n"
                "4) Do not fabricate facts or reveal reasoning traces.\n"
                "5) Output body text only, no JSON/Markdown/Subject/From/To lines.\n"
                "6) Do not output placeholders like [Recipient's Name] or [Your Name].\n"
                "7) You may keep one brief polite closing line, but no personal signature.\n\n"
                f"From: {sender}\n"
                f"Subject: {subject}\n"
                "Message:\n"
                f"{body}\n"
            )

        return (
            "你是中文邮件自动回复助手。\n"
            "目标：给来信生成专业礼貌、简洁直答、可直接发送的邮件回复。\n"
            "要求：\n"
            "1) 默认输出 2-4 句，先回应结论，再补充下一步。\n"
            "2) 若对方问题明确，不要反问。\n"
            "3) 若信息不足，最多追问 1 个关键问题。\n"
            "4) 不要编造事实，不要输出解释过程。\n"
            "5) 仅输出邮件正文，不要输出 JSON、Markdown、Subject:/From:/To:。\n"
            "6) 不要输出任何占位符（如 [您的姓名]、[您的职位]、[您的公司]）。\n"
            "7) 可保留 1 行简短礼貌收尾，但不要出现姓名/职位/公司署名。\n\n"
            f"发件人: {sender}\n"
            f"邮件主题: {subject}\n"
            "邮件内容:\n"
            f"{body}\n"
        )

    def _build_prompt(self, sender: str, subject: str, body: str) -> tuple[str, str, bool, int, int]:
        resolved_language = detect_reply_language(subject=subject, body=body)
        if self.personalization_bundle is None:
            return (
                self._build_default_prompt(sender=sender, subject=subject, body=body, language=resolved_language),
                resolved_language,
                False,
                0,
                0,
            )

        memory_top_k = 3
        example_top_k = 3
        allow_disclosure = needs_profile_disclosure(subject=subject, body=body)
        relevant_projects = select_relevant_memories(
            subject=subject,
            body=body,
            projects=self.personalization_bundle.projects,
            top_k=memory_top_k,
        )
        selected_examples = select_fixed_examples(self.personalization_bundle.examples, k=example_top_k)
        prompt = build_personalized_prompt(
            sender=sender,
            subject=subject,
            body=body,
            bundle=self.personalization_bundle,
            language=resolved_language,
            style_mode="polite",
            memory_top_k=memory_top_k,
            example_top_k=example_top_k,
        )

        return (
            prompt,
            resolved_language,
            allow_disclosure,
            len(selected_examples),
            len(relevant_projects),
        )

    @staticmethod
    def _likely_limit_error(status_code: int, body_text: str) -> bool:
        if status_code not in {400, 422}:
            return False
        lowered = body_text.lower()
        keywords = (
            "token",
            "max_tokens",
            "too many tokens",
            "context length",
            "context_length_exceeded",
            "maximum context",
            "input too long",
            "request too large",
            "exceeds",
        )
        return any(keyword in lowered for keyword in keywords)

    @staticmethod
    def _truncate_body_by_input_cap(body: str, input_cap: int) -> str:
        # Rough guardrail: keep payload within a practical char budget derived from input tokens.
        # We intentionally keep a safety margin for prompt prefix and headers.
        max_body_chars = max(800, int(input_cap * 1.5))
        if len(body) <= max_body_chars:
            return body
        return body[:max_body_chars]

    def _call_model(
        self,
        model: str,
        sender: str,
        subject: str,
        body: str,
        input_cap: int,
        output_cap: int,
    ) -> str:
        bounded_body = self._truncate_body_by_input_cap(body=body, input_cap=input_cap)
        prompt, resolved_language, allow_disclosure, examples_count, memories_count = self._build_prompt(
            sender=sender,
            subject=subject,
            body=bounded_body,
        )
        self.logger.info(
            "PROMPT_PROFILE | style=polite | lang_mode=auto | lang_resolved=%s | disclosure=%s | examples=%d | memories=%d",
            resolved_language,
            "on" if allow_disclosure else "off",
            examples_count,
            memories_count,
        )
        system_content = (
            "你是资深中文商务邮件助手。请使用中文输出，保持专业、礼貌、简洁，可直接发送。"
            if resolved_language == "zh"
            else "You are an experienced email assistant. Reply in English, concise and professional, ready to send."
        )
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": system_content,
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": output_cap,
            "stream": False,
        }

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        }

        response = self.request_fn(
            self.api_url,
            headers=headers,
            json=payload,
            timeout=self.timeout_seconds,
        )
        status_code = getattr(response, "status_code", None)
        if status_code is None or status_code >= 400:
            body_text = getattr(response, "text", "")
            if status_code is not None and self._likely_limit_error(status_code=status_code, body_text=body_text):
                raise LimitExceededError(
                    f"GitHub Models token limit exceeded model={model} status={status_code} body={body_text[:400]}"
                )
            raise RuntimeError(
                f"GitHub Models request failed model={model} status={status_code} body={body_text[:400]}"
            )

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"Model response has no choices for model={model}")

        message = choices[0].get("message") or {}
        content = message.get("content", "")
        if isinstance(content, list):
            text_parts: list[str] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
            content = "\n".join(text_parts)

        cleaned = (content or "").strip()
        if not cleaned:
            raise RuntimeError(f"Model returned empty content for model={model}")
        return cleaned

    def _generate_with_budget_fallback(self, model: str, sender: str, subject: str, body: str) -> str:
        limit_errors: list[str] = []
        for input_cap, output_cap in self.TOKEN_PROFILES:
            try:
                return self._call_model(
                    model=model,
                    sender=sender,
                    subject=subject,
                    body=body,
                    input_cap=input_cap,
                    output_cap=output_cap,
                )
            except LimitExceededError as exc:
                limit_errors.append(str(exc))
                continue
        joined = " | ".join(limit_errors[-3:]) if limit_errors else "unknown limit failure"
        raise RuntimeError(f"All token profiles exceeded for model={model}. errors={joined}")

    def generate_reply(self, sender: str, subject: str, body: str) -> ModelReply:
        attempted: list[str] = []
        errors: list[str] = []
        chain = [self.primary] + self.fallbacks

        for model in chain:
            attempted.append(model)
            try:
                text = self._generate_with_budget_fallback(
                    model=model,
                    sender=sender,
                    subject=subject,
                    body=body,
                )
                return ModelReply(text=text, used_model=model, attempted_models=attempted)
            except Exception as exc:
                errors.append(f"{model}: {exc}")

        joined = " | ".join(errors[-4:]) if errors else "unknown failure"
        raise RuntimeError(f"All models failed. attempted={attempted}. errors={joined}")
