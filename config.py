from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    qq_email: str
    qq_auth_code: str
    github_token: str
    github_api_url: str
    github_model_primary: str
    github_model_fallbacks: list[str]
    model_request_timeout_seconds: int
    model_signature_template: str
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    poll_seconds: int
    max_input_chars: int
    imap_fetch_days: int
    processed_state_file: Path
    allow_senders_file: Path
    deny_senders_file: Path
    frequent_sender_file: Path
    frequent_window_days: int
    frequent_min_count: int
    frequent_max_events: int
    reply_signature: str
    filter_level: str
    timer_schedule: str
    storage_backend: str
    table_connection_string: str
    processed_table_name: str
    frequent_table_name: str


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and (value is None or value == ""):
        raise ValueError(f"Missing required environment variable: {name}")
    return value or ""


def _to_int(name: str, default: str) -> int:
    value = _env(name, default)
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be integer, got: {value}") from exc


def _parse_fallbacks(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def load_settings() -> Settings:
    load_dotenv()

    legacy_model = _env("GITHUB_MODEL", "").strip()
    primary = _env("GITHUB_MODEL_PRIMARY", "").strip() or legacy_model or "openai/gpt-4.1"
    fallbacks = _parse_fallbacks(_env("GITHUB_MODEL_FALLBACKS", ""))
    storage_backend = (_env("STORAGE_BACKEND", "auto").strip().lower() or "auto")
    if storage_backend not in {"auto", "table", "file"}:
        raise ValueError(f"STORAGE_BACKEND must be one of auto/table/file, got: {storage_backend}")

    return Settings(
        qq_email=_env("QQ_EMAIL", required=True),
        qq_auth_code=_env("QQ_AUTH_CODE", required=True),
        github_token=_env("GITHUB_TOKEN", required=True),
        github_api_url=_env(
            "GITHUB_API_URL",
            "https://models.github.ai/inference/chat/completions",
        ),
        github_model_primary=primary,
        github_model_fallbacks=fallbacks,
        model_request_timeout_seconds=_to_int("MODEL_REQUEST_TIMEOUT_SECONDS", "45"),
        model_signature_template=_env(
            "MODEL_SIGNATURE_TEMPLATE",
            "--\n使用 {model} 模型自动生成回复",
        ).replace("\\n", "\n"),
        imap_host=_env("IMAP_HOST", "imap.qq.com"),
        imap_port=_to_int("IMAP_PORT", "993"),
        smtp_host=_env("SMTP_HOST", "smtp.qq.com"),
        smtp_port=_to_int("SMTP_PORT", "465"),
        poll_seconds=_to_int("POLL_SECONDS", "60"),
        max_input_chars=_to_int("MAX_INPUT_CHARS", "4000"),
        imap_fetch_days=_to_int("IMAP_FETCH_DAYS", "1"),
        processed_state_file=Path(_env("STATE_FILE", "data/processed_messages.json")),
        allow_senders_file=Path(_env("ALLOW_SENDERS_FILE", "data/allow_senders.txt")),
        deny_senders_file=Path(_env("DENY_SENDERS_FILE", "data/deny_senders.txt")),
        frequent_sender_file=Path(_env("FREQUENT_SENDER_FILE", "data/frequent_senders.json")),
        frequent_window_days=_to_int("FREQUENT_WINDOW_DAYS", "30"),
        frequent_min_count=_to_int("FREQUENT_MIN_COUNT", "3"),
        frequent_max_events=_to_int("FREQUENT_MAX_EVENTS", "20"),
        reply_signature=_env("REPLY_SIGNATURE", "--\n这是一封自动回复邮件。").replace("\\n", "\n"),
        filter_level=_env("FILTER_LEVEL", "medium").strip().lower() or "medium",
        timer_schedule=_env("TIMER_SCHEDULE", "0 */5 * * * *"),
        storage_backend=storage_backend,
        table_connection_string=_env("TABLE_CONNECTION_STRING", ""),
        processed_table_name=_env("PROCESSED_TABLE_NAME", "processedstate"),
        frequent_table_name=_env("FREQUENT_TABLE_NAME", "frequentsenderstate"),
    )
