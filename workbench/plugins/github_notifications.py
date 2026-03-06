from __future__ import annotations

import re

from workbench.models import GithubEntity, TaskDraft


REPO_RE = re.compile(r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")
ITEM_RE = re.compile(r"(?:#|/)(\d{1,7})")
URL_RE = re.compile(r"https?://[^\s>]+")


def match_github_notification(sender_email: str, subject: str, headers_json: str) -> bool:
    sender = sender_email.lower()
    subject_l = subject.lower()
    headers_l = headers_json.lower()
    if "github.com" in sender or "noreply.github.com" in sender:
        return True
    if any(key in subject_l for key in ("pull request", "issue", "review requested", "github")):
        return True
    return "x-github" in headers_l


def extract_github_entities(subject: str, body_text: str) -> GithubEntity | None:
    text = f"{subject}\n{body_text}"
    repo_match = REPO_RE.search(text)
    repo = repo_match.group(1) if repo_match else ""

    lower = text.lower()
    if "pull request" in lower or "/pull/" in lower:
        item_type = "pr"
    elif "issue" in lower or "/issues/" in lower:
        item_type = "issue"
    else:
        item_type = "item"

    num_match = ITEM_RE.search(text)
    item_number = num_match.group(1) if num_match else ""

    action = "review"
    for token in ("review requested", "assigned", "commented", "closed", "opened"):
        if token in lower:
            action = token
            break

    url_match = URL_RE.search(text)
    url = url_match.group(0) if url_match else ""

    if not repo and not item_number and not url:
        return None

    return GithubEntity(
        repo=repo or "unknown/unknown",
        item_type=item_type,
        item_number=item_number or "?",
        action=action,
        url=url,
    )


def create_github_tasks(entity: GithubEntity) -> list[TaskDraft]:
    title = f"[{entity.repo}] {entity.item_type.upper()} #{entity.item_number} needs {entity.action}"
    return [
        TaskDraft(
            title=title,
            priority="med",
            due_at_utc=None,
            evidence=entity.url or f"{entity.repo} #{entity.item_number}",
            source="github_plugin",
        )
    ]
