from mozaiksai.core.workflow.messages.utils import normalize_to_strict_ag2


def test_normalize_to_strict_ag2_uses_agent_name_for_assistant_resume_messages() -> None:
    raw_messages = [
        {
            "role": "assistant",
            "agent_name": "ResumeRouterAgent",
            "content": "[SYSTEM_RESUME_SIGNAL] Continue workflow execution after UI tool response.",
        }
    ]

    normalized = normalize_to_strict_ag2(raw_messages)

    assert normalized == [
        {
            "role": "assistant",
            "name": "ResumeRouterAgent",
            "content": "[SYSTEM_RESUME_SIGNAL] Continue workflow execution after UI tool response.",
        }
    ]


def test_normalize_to_strict_ag2_preserves_persisted_agent_turn_order() -> None:
    raw_messages = [
        {"role": "user", "agent_name": "user", "content": "continue"},
        {"role": "assistant", "agent_name": "PlannerAgent", "content": "{\"workflows\": []}"},
        {
            "role": "assistant",
            "agent_name": "ResumeRouterAgent",
            "content": "[SYSTEM_RESUME_SIGNAL] Continue workflow execution after UI tool response.",
        },
    ]

    normalized = normalize_to_strict_ag2(raw_messages)

    assert [message["name"] for message in normalized] == ["user", "PlannerAgent", "ResumeRouterAgent"]