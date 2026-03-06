from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

import requests

from workbench.models import LlmDecision


@dataclass(frozen=True)
class LlmTriageResult:
    decision: LlmDecision | None
    parse_failed: bool


class LlmTriageClient:
    def __init__(
        self,
        token: str,
        api_url: str,
        model: str,
        timeout_seconds: int = 30,
        request_fn: Callable[..., Any] = requests.post,
    ) -> None:
        self.token = token
        self.api_url = api_url
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.request_fn = request_fn

    @staticmethod
    def _extract_json_text(text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
            stripped = re.sub(r"```$", "", stripped).strip()
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("no json object found")
        return stripped[start : end + 1]

    @staticmethod
    def _coerce_decision(data: dict[str, Any], model_name: str) -> LlmDecision:
        category = str(data.get("category", "fyi")).lower()
        if category not in {"action", "waiting", "fyi", "spamish"}:
            category = "fyi"
        priority = str(data.get("priority", "low")).lower()
        if priority not in {"high", "med", "low"}:
            priority = "low"
        needs_action = bool(data.get("needs_action", category == "action"))

        suggested_tasks_raw = data.get("suggested_tasks", [])
        suggested_tasks = (
            [str(item).strip() for item in suggested_tasks_raw if str(item).strip()] if isinstance(suggested_tasks_raw, list) else []
        )

        evidence_raw = data.get("evidence", [])
        evidence = [str(item).strip() for item in evidence_raw if str(item).strip()] if isinstance(evidence_raw, list) else []
        if not evidence:
            evidence = ["llm_without_evidence"]

        due_date_guess = data.get("due_date_guess")
        if due_date_guess is not None:
            due_date_guess = str(due_date_guess).strip() or None

        confidence = data.get("confidence", 0.6)
        try:
            confidence = float(confidence)
        except Exception:
            confidence = 0.6
        confidence = max(0.0, min(confidence, 1.0))

        return LlmDecision(
            category=category,
            priority=priority,
            needs_action=needs_action,
            suggested_tasks=suggested_tasks[:3],
            due_date_guess=due_date_guess,
            evidence=evidence[:3],
            confidence=confidence,
            model_name=model_name,
        )

    def _build_prompt(self, sender_email: str, subject: str, body_text: str) -> str:
        return (
            "You are an email triage assistant. Return strict JSON only.\n"
            "Required JSON fields: category, priority, needs_action, suggested_tasks, due_date_guess, evidence, confidence.\n"
            "category in [action, waiting, fyi, spamish]; priority in [high, med, low].\n"
            "suggested_tasks: list of 1-3 executable TODOs, may be empty.\n"
            "evidence: list of 1-3 short quoted snippets from the mail text.\n"
            "Do not output markdown or explanation.\n\n"
            f"From: {sender_email}\n"
            f"Subject: {subject}\n"
            "Body:\n"
            f"{body_text}\n"
        )

    def _call_once(self, sender_email: str, subject: str, body_text: str) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You classify and extract actionable tasks from email; output strict JSON only."},
                {"role": "user", "content": self._build_prompt(sender_email, subject, body_text)},
            ],
            "temperature": 0.1,
            "max_tokens": 500,
            "stream": False,
        }
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        }
        response = self.request_fn(self.api_url, headers=headers, json=payload, timeout=self.timeout_seconds)
        status = getattr(response, "status_code", None)
        if status is None or status >= 400:
            raise RuntimeError(f"llm request failed status={status} body={getattr(response, 'text', '')[:300]}")
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("llm choices empty")
        message = choices[0].get("message") or {}
        content = message.get("content", "")
        if isinstance(content, list):
            text_parts: list[str] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(str(part.get("text", "")))
            content = "\n".join(text_parts)
        json_text = self._extract_json_text(str(content))
        return json.loads(json_text)

    def triage(self, sender_email: str, subject: str, body_text: str) -> LlmTriageResult:
        parse_failed = False
        last_exc: Exception | None = None
        for _ in range(2):
            try:
                parsed = self._call_once(sender_email=sender_email, subject=subject, body_text=body_text)
                decision = self._coerce_decision(parsed, model_name=self.model)
                return LlmTriageResult(decision=decision, parse_failed=parse_failed)
            except (ValueError, json.JSONDecodeError) as exc:
                parse_failed = True
                last_exc = exc
                continue
            except Exception as exc:
                last_exc = exc
                break

        if last_exc:
            return LlmTriageResult(decision=None, parse_failed=parse_failed)
        return LlmTriageResult(decision=None, parse_failed=parse_failed)
