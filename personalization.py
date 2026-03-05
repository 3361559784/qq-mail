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


def _language_score(text: str) -> tuple[int, int]:
    zh_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    zh_punc = len(re.findall(r"[，。！？；：（）【】《》“”‘’、]", text))
    en_chars = len(re.findall(r"[A-Za-z]", text))
    zh_score = zh_chars + (zh_punc * 2)
    en_score = en_chars
    return zh_score, en_score


def detect_reply_language(subject: str, body: str) -> str:
    sub_zh, sub_en = _language_score(subject)
    if sub_zh > sub_en and sub_zh > 0:
        return "zh"
    if sub_en > sub_zh and sub_en > 0:
        return "en"

    body_zh, body_en = _language_score(body)
    if body_zh > body_en and body_zh > 0:
        return "zh"
    if body_en > body_zh and body_en > 0:
        return "en"
    return "en"


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


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _format_reasoning_flow(preferences: dict[str, object]) -> str:
    configured = _as_str_list(preferences.get("response_flow"))
    if configured:
        return "\n".join(f"{idx}. {item}" for idx, item in enumerate(configured, start=1))

    default_flow = [
        "先识别核心工程问题",
        "再给系统/架构视角",
        "再给最小可执行实现",
        "再说明风险/权衡",
        "最后给可选增强项",
    ]
    return "\n".join(f"{idx}. {item}" for idx, item in enumerate(default_flow, start=1))


def _format_engineering_preferences(preferences: dict[str, object], notes_text: str) -> str:
    prefer_lines = _as_str_list(preferences.get("prefer"))
    notes_lines = [line.strip() for line in notes_text.splitlines() if line.strip()]
    lines = prefer_lines + notes_lines
    if not lines:
        lines = [
            "prefer minimal architecture",
            "avoid premature abstraction",
            "prefer observable systems",
            "prioritize reliability over cleverness",
        ]
    return "\n".join(f"- {line}" for line in lines)


def _format_tone_constraints(preferences: dict[str, object]) -> str:
    tone_lines = _as_str_list(preferences.get("tone"))
    avoid_lines = _as_str_list(preferences.get("avoid"))
    lines: list[str] = []
    for item in tone_lines:
        lines.append(f"- {item}")
    for item in avoid_lines:
        lines.append(f"- avoid: {item}")
    lines.extend(
        [
            "- answer like an engineer reasoning through the problem",
            "- avoid documentation-writer voice and generic preamble",
        ]
    )
    return "\n".join(lines)


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


def build_output_contract(language: str, style_mode: str = "polite") -> str:
    normalized_language = "zh" if language == "zh" else "en"
    polite_line = (
        "- 可以保留最多 1 行简短礼貌收尾，但不要出现姓名/职位/公司署名。"
        if normalized_language == "zh"
        else "- You may keep at most one brief polite closing line, but no personal signature."
    )
    if style_mode != "polite":
        polite_line = (
            "- 默认不要写礼貌收尾。"
            if normalized_language == "zh"
            else "- Do not add polite closing lines by default."
        )

    if normalized_language == "zh":
        return "\n".join(
            [
                "- 仅输出邮件正文，不要输出 Subject:/From:/To:。",
                "- 禁止占位符：[Recipient's Name]、[Your Name]、[Your Position]、[Your Company]、[您的姓名]、[您的职位]、[您的公司]。",
                "- 不要输出 Markdown 列表、代码块或 JSON。",
                "- 最多允许 1 个澄清问题。",
                polite_line,
            ]
        )

    return "\n".join(
        [
            "- Output body text only; do not output Subject:/From:/To: lines.",
            "- Do not use placeholders: [Recipient's Name], [Your Name], [Your Position], [Your Company], [您的姓名], [您的职位], [您的公司].",
            "- Do not output markdown list, code block, or JSON.",
            "- Ask at most one clarifying question.",
            polite_line,
        ]
    )


def build_personalized_prompt(
    sender: str,
    subject: str,
    body: str,
    bundle: PersonalizationBundle,
    language: str = "en",
    style_mode: str = "polite",
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

    resolved_language = "zh" if language == "zh" else "en"
    disclosure_lines: list[str] = []
    if allow_disclosure:
        if resolved_language == "zh":
            disclosure_lines.append("发件人明确询问了背景信息，可用 1-2 句简短说明。")
            disclosure_lines.append("允许引用的背景信息：")
        else:
            disclosure_lines.append(
                "Sender explicitly asked about background; you may mention background in 1-2 concise sentences."
            )
            disclosure_lines.append("Allowed profile context:")
        disclosure_lines.append(_format_json_like(bundle.profile))
    else:
        if resolved_language == "zh":
            disclosure_lines.append("若发件人未明确询问背景，不要提及个人履历、学校或工作经历。")
        else:
            disclosure_lines.append(
                "If sender does not explicitly ask about background, do NOT mention personal background or resume."
            )

    return (
        (
            "你正在以刘梓恒的身份回复邮件，保持工程化思考，不要写模板腔。\n\n"
            if resolved_language == "zh"
            else "You are replying to an email as Liu Ziheng. Think step-by-step like an engineer.\n\n"
        )
        +
        "Persona\n"
        "-------\n"
        f"{bundle.persona_text}\n\n"
        "Reasoning Flow\n"
        "-------\n"
        f"{_format_reasoning_flow(bundle.preferences)}\n\n"
        "Engineering Preferences\n"
        "-------\n"
        f"{_format_engineering_preferences(bundle.preferences, bundle.notes_text)}\n\n"
        "Tone Constraints\n"
        "-------\n"
        f"{_format_tone_constraints(bundle.preferences)}\n\n"
        "Relevant Experience\n"
        "-------\n"
        f"{_format_projects(relevant_projects)}\n\n"
        "Fixed Examples\n"
        "-------\n"
        f"{_format_examples(selected_examples)}\n\n"
        "Disclosure Rule\n"
        "-------\n"
        f"{' '.join(disclosure_lines)}\n\n"
        "Output Contract\n"
        "-------\n"
        f"{build_output_contract(language=resolved_language, style_mode=style_mode)}\n\n"
        "Incoming Email\n"
        "-------\n"
        f"From: {sender}\n"
        f"Subject: {subject}\n"
        "Message:\n"
        f"{body}\n"
    )
