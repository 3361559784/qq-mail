from __future__ import annotations

from dataclasses import dataclass
import json
import re
from pathlib import Path


@dataclass(frozen=True)
class PersonalizationBundle:
    persona_text: str
    profile: dict[str, object]
    projects: list[dict[str, object]]
    preferences: dict[str, object]
    examples: list[dict[str, str]]
    notes_text: str


class PersonalizationLoadError(RuntimeError):
    pass


def _resolve_personalization_dir(base_dir: Path) -> Path:
    if (base_dir / "persona.md").exists():
        return base_dir
    return base_dir / "personalization"


def _read_text(path: Path) -> str:
    if not path.exists():
        raise PersonalizationLoadError(f"Missing personalization file: {path}")
    return path.read_text(encoding="utf-8").strip()


def _read_json(path: Path) -> object:
    if not path.exists():
        raise PersonalizationLoadError(f"Missing personalization file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PersonalizationLoadError(f"Invalid JSON file: {path}: {exc}") from exc


def load_personalization_bundle(base_dir: Path) -> PersonalizationBundle:
    root = _resolve_personalization_dir(base_dir)

    persona_text = _read_text(root / "persona.md")
    notes_text = _read_text(root / "notes.md")

    profile = _read_json(root / "profile.json")
    projects = _read_json(root / "projects.json")
    preferences = _read_json(root / "preferences.json")
    examples = _read_json(root / "qa_examples.json")

    if not isinstance(profile, dict):
        raise PersonalizationLoadError("profile.json must be an object")
    if not isinstance(projects, list):
        raise PersonalizationLoadError("projects.json must be a list")
    if not isinstance(preferences, dict):
        raise PersonalizationLoadError("preferences.json must be an object")
    if not isinstance(examples, list):
        raise PersonalizationLoadError("qa_examples.json must be a list")

    normalized_projects: list[dict[str, object]] = []
    for item in projects:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        stack = item.get("stack")
        architecture = item.get("architecture")
        keywords = item.get("keywords", [])
        if not isinstance(name, str) or not isinstance(stack, list) or not isinstance(architecture, list):
            continue
        if not all(isinstance(entry, str) for entry in stack):
            continue
        if not all(isinstance(entry, str) for entry in architecture):
            continue
        if not isinstance(keywords, list) or not all(isinstance(entry, str) for entry in keywords):
            keywords = []
        normalized_projects.append(
            {
                "name": name,
                "stack": stack,
                "architecture": architecture,
                "keywords": keywords,
            }
        )

    normalized_examples: list[dict[str, str]] = []
    for item in examples:
        if not isinstance(item, dict):
            continue
        question = item.get("question")
        answer = item.get("answer")
        if isinstance(question, str) and isinstance(answer, str):
            normalized_examples.append({"question": question, "answer": answer})

    if not normalized_projects:
        raise PersonalizationLoadError("projects.json has no valid project entries")
    if not normalized_examples:
        raise PersonalizationLoadError("qa_examples.json has no valid question/answer entries")

    return PersonalizationBundle(
        persona_text=persona_text,
        profile=profile,
        projects=normalized_projects,
        preferences=preferences,
        examples=normalized_examples,
        notes_text=notes_text,
    )


def needs_profile_disclosure(subject: str, body: str) -> bool:
    text = f"{subject}\n{body}".lower()
    triggers = (
        "你是谁",
        "你的背景",
        "背景",
        "你的经历",
        "经历",
        "学校",
        "工作",
        "公司",
        "介绍一下你自己",
        "介绍你自己",
        "who are you",
        "your background",
        "your experience",
        "introduce yourself",
    )
    return any(keyword in text for keyword in triggers)


def _tokenize(text: str) -> set[str]:
    lowered = text.lower()
    tokens = re.findall(r"[a-z0-9][a-z0-9_./+-]*|[\u4e00-\u9fff]{2,}", lowered)
    return {token for token in tokens if token}


def _project_tokens(project: dict[str, object]) -> set[str]:
    fields: list[str] = []
    for key in ("name",):
        value = project.get(key)
        if isinstance(value, str):
            fields.append(value)

    for key in ("stack", "architecture", "keywords"):
        value = project.get(key)
        if isinstance(value, list):
            fields.extend([entry for entry in value if isinstance(entry, str)])

    all_tokens: set[str] = set()
    for field in fields:
        all_tokens |= _tokenize(field)
    return all_tokens


def select_relevant_memories(
    subject: str,
    body: str,
    projects: list[dict[str, object]],
    top_k: int = 3,
) -> list[dict[str, object]]:
    query_tokens = _tokenize(f"{subject} {body}")
    scored: list[tuple[int, dict[str, object]]] = []

    for project in projects:
        project_tokens = _project_tokens(project)
        score = len(query_tokens & project_tokens)
        if score > 0:
            scored.append((score, project))

    scored.sort(key=lambda item: (-item[0], str(item[1].get("name", ""))))
    return [project for _, project in scored[: max(top_k, 0)]]


def select_fixed_examples(examples: list[dict[str, str]], k: int = 3) -> list[dict[str, str]]:
    return examples[: max(k, 0)]


def _format_json_like(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _format_style_rules(preferences: dict[str, object], notes_text: str) -> str:
    lines: list[str] = []

    for key in ("tone", "response_flow", "avoid", "prefer"):
        value = preferences.get(key)
        if isinstance(value, list) and value:
            lines.append(f"{key}:")
            for item in value:
                if isinstance(item, str):
                    lines.append(f"- {item}")
            lines.append("")

    if notes_text:
        lines.append("engineering_notes:")
        for line in notes_text.splitlines():
            stripped = line.strip()
            if stripped:
                lines.append(f"- {stripped}")

    return "\n".join(lines).strip()


def _format_projects(projects: list[dict[str, object]]) -> str:
    if not projects:
        return "- (no directly matched experience)"

    lines: list[str] = []
    for project in projects:
        name = str(project.get("name", "Unnamed project"))
        lines.append(f"- project: {name}")

        stack = project.get("stack")
        if isinstance(stack, list) and stack:
            lines.append("  stack: " + ", ".join(str(item) for item in stack))

        architecture = project.get("architecture")
        if isinstance(architecture, list) and architecture:
            lines.append("  architecture: " + ", ".join(str(item) for item in architecture))
    return "\n".join(lines)


def _format_examples(examples: list[dict[str, str]]) -> str:
    if not examples:
        return "- (no examples)"
    lines: list[str] = []
    for idx, example in enumerate(examples, start=1):
        lines.append(f"Example {idx} Q: {example['question']}")
        lines.append(f"Example {idx} A: {example['answer']}")
    return "\n".join(lines)


def build_personalized_prompt(
    sender: str,
    subject: str,
    body: str,
    bundle: PersonalizationBundle,
    memory_top_k: int = 3,
    example_top_k: int = 3,
) -> str:
    relevant_projects = select_relevant_memories(
        subject=subject,
        body=body,
        projects=bundle.projects,
        top_k=memory_top_k,
    )
    selected_examples = select_fixed_examples(bundle.examples, k=example_top_k)
    allow_disclosure = needs_profile_disclosure(subject=subject, body=body)

    disclosure_lines = []
    if allow_disclosure:
        disclosure_lines.append(
            "Sender explicitly asked about background; you may mention background in 1-2 concise sentences."
        )
        disclosure_lines.append("Allowed profile context:")
        disclosure_lines.append(_format_json_like(bundle.profile))
    else:
        disclosure_lines.append("If sender does not explicitly ask about background, do NOT mention personal background or resume.")

    return (
        "You are replying to an email as Liu Ziheng.\n\n"
        "Persona\n"
        "-------\n"
        f"{bundle.persona_text}\n\n"
        "Style Rules\n"
        "-------\n"
        f"{_format_style_rules(bundle.preferences, bundle.notes_text)}\n\n"
        "Relevant Experience\n"
        "-------\n"
        f"{_format_projects(relevant_projects)}\n\n"
        "Examples\n"
        "-------\n"
        f"{_format_examples(selected_examples)}\n\n"
        "Disclosure Rule\n"
        "-------\n"
        f"{' '.join(disclosure_lines)}\n\n"
        "Email\n"
        "-------\n"
        f"From: {sender}\n"
        f"Subject: {subject}\n"
        "Body:\n"
        f"{body}\n"
    )
