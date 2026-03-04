from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import requests


@dataclass(frozen=True)
class ModelReply:
    text: str
    used_model: str
    attempted_models: list[str]


class ModelChainClient:
    def __init__(
        self,
        token: str,
        api_url: str,
        primary: str,
        fallbacks: list[str] | None = None,
        timeout_seconds: int = 45,
        request_fn: Callable[..., Any] = requests.post,
    ) -> None:
        self.token = token
        self.api_url = api_url
        self.primary = primary
        self.fallbacks = fallbacks or []
        self.timeout_seconds = timeout_seconds
        self.request_fn = request_fn

    def _build_prompt(self, sender: str, subject: str, body: str) -> str:
        return (
            "你是中文邮件自动回复助手。\n"
            "目标：给来信生成一封简洁、礼貌、可直接发送的邮件回复。\n"
            "要求：\n"
            "1) 语气专业友好。\n"
            "2) 如对方提出明确问题，先给直接回答，再补充下一步。\n"
            "3) 如果信息不足，礼貌地提出 1-2 个澄清问题。\n"
            "4) 不要编造事实，不要输出解释过程。\n"
            "5) 仅输出邮件正文，不要输出 JSON 或 Markdown。\n"
            "6) 不要输出任何占位符（如 [您的姓名]、[您的职位]、[您的公司]）。\n"
            "7) 不要输出落款签名（系统会自动追加签名）。\n\n"
            f"发件人: {sender}\n"
            f"邮件主题: {subject}\n"
            "邮件内容:\n"
            f"{body}\n"
        )

    def _call_model(self, model: str, sender: str, subject: str, body: str) -> str:
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是资深中文商务邮件助手，输出要简洁并可直接发送。",
                },
                {"role": "user", "content": self._build_prompt(sender, subject, body)},
            ],
            "temperature": 0.3,
            "max_tokens": 500,
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

    def generate_reply(self, sender: str, subject: str, body: str) -> ModelReply:
        attempted: list[str] = []
        errors: list[str] = []
        chain = [self.primary] + self.fallbacks

        for model in chain:
            attempted.append(model)
            try:
                text = self._call_model(model=model, sender=sender, subject=subject, body=body)
                return ModelReply(text=text, used_model=model, attempted_models=attempted)
            except Exception as exc:
                errors.append(f"{model}: {exc}")

        joined = " | ".join(errors[-4:]) if errors else "unknown failure"
        raise RuntimeError(f"All models failed. attempted={attempted}. errors={joined}")
