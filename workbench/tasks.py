from __future__ import annotations

from workbench.models import FinalDecision, LlmDecision, RuleDecision, TaskDraft


def merge_decision(rule: RuleDecision, llm: LlmDecision | None, llm_parse_failed: bool = False) -> FinalDecision:
    if llm is None:
        evidence = list(rule.evidence)
        if llm_parse_failed:
            evidence.append("llm_parse_failed")
        return FinalDecision(
            category=rule.category,
            priority=rule.priority,
            needs_action=rule.needs_action,
            evidence=evidence[:3],
            confidence=0.55,
            strategy="rules_only",
            model_name="rules",
            suggested_tasks=[],
            due_date_guess=None,
        )

    return FinalDecision(
        category=llm.category,
        priority=llm.priority,
        needs_action=llm.needs_action,
        evidence=llm.evidence[:3] if llm.evidence else rule.evidence[:3],
        confidence=llm.confidence,
        strategy="rules_plus_llm",
        model_name=llm.model_name,
        suggested_tasks=llm.suggested_tasks[:3],
        due_date_guess=llm.due_date_guess,
    )


def make_task_drafts(
    subject: str,
    final_decision: FinalDecision,
) -> list[TaskDraft]:
    if not final_decision.needs_action and final_decision.category != "action":
        return []

    tasks: list[TaskDraft] = []
    if final_decision.suggested_tasks:
        for item in final_decision.suggested_tasks[:3]:
            tasks.append(
                TaskDraft(
                    title=item,
                    priority=final_decision.priority,
                    due_at_utc=final_decision.due_date_guess,
                    evidence=(final_decision.evidence[0] if final_decision.evidence else "llm_suggested"),
                    source="llm",
                )
            )
        return tasks

    tasks.append(
        TaskDraft(
            title=f"处理邮件: {subject[:80]}",
            priority=final_decision.priority,
            due_at_utc=final_decision.due_date_guess,
            evidence=(final_decision.evidence[0] if final_decision.evidence else "rule_detected_action"),
            source="rule",
        )
    )
    return tasks
