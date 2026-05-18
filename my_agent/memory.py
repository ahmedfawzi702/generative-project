from __future__ import annotations

import re
from typing import Any, Optional


# ============================================================
# BASIC MESSAGE HELPERS
# ============================================================

def add_message(
    state: dict[str, Any],
    role: str,
    content: str,
    *,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Add a message to state["messages"].
    """
    messages = state.get("messages", [])

    messages.append(
        {
            "role": role,
            "content": content or "",
            "metadata": metadata or {},
        }
    )

    state["messages"] = messages[-30:]
    return state


def compact_recent_messages(
    messages: list[dict[str, Any]],
    limit: int = 8,
) -> list[dict[str, Any]]:
    """
    Return only the most recent messages.
    """
    if not messages:
        return []

    return messages[-limit:]


def messages_to_text(
    messages: list[dict[str, Any]],
    limit: int = 8,
    max_chars_per_message: int = 700,
) -> str:
    """
    Convert recent messages to compact text for prompts.
    """
    selected = compact_recent_messages(messages, limit=limit)

    lines: list[str] = []

    for msg in selected:
        role = msg.get("role", "unknown")
        content = msg.get("content", "") or ""

        if len(content) > max_chars_per_message:
            content = content[:max_chars_per_message].rstrip() + "..."

        lines.append(f"{role}: {content}")

    return "\n".join(lines)


# ============================================================
# SAFE MEMORY FILTERING
# ============================================================

SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9_\-]{20,}",
    r"sk-or-[A-Za-z0-9_\-]{20,}",
    r"pk-[A-Za-z0-9_\-]{20,}",
    r"tvly-[A-Za-z0-9_\-]{10,}",
    r"xkeysib-[A-Za-z0-9_\-]{20,}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"gh[pousr]_[A-Za-z0-9_]{20,}",
    r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+",
    r"api[_-]?key\s*[:=]\s*\S+",
    r"openrouter[_-]?api[_-]?key\s*[:=]\s*\S+",
    r"tavily[_-]?api[_-]?key\s*[:=]\s*\S+",
    r"smtp[_-]?(password|key|secret)\s*[:=]\s*\S+",
    r"brevo[_-]?(password|key|secret)\s*[:=]\s*\S+",
    r"password\s*[:=]\s*\S+",
    r"passwd\s*[:=]\s*\S+",
    r"secret\s*[:=]\s*\S+",
    r"token\s*[:=]\s*\S+",
    r"bearer\s+[A-Za-z0-9_\-.]+",
]


def redact_secrets(text: str) -> str:
    """
    Redact obvious secrets from memory summaries.
    """
    if not text:
        return ""

    redacted = text

    for pattern in SECRET_PATTERNS:
        redacted = re.sub(pattern, "[REDACTED_SECRET]", redacted, flags=re.IGNORECASE)

    return redacted


def clean_memory_text(text: str, max_chars: int = 1200) -> str:
    """
    Clean and shorten text before storing in memory.
    """
    if not text:
        return ""

    text = redact_secrets(text)
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."

    return text


def should_store_user_preference(text: str) -> bool:
    """
    Simple heuristic for whether a user preference is worth remembering in working memory.

    This does not store personal/sensitive facts. It only helps within the app flow.
    """
    if not text:
        return False

    lowered = text.lower()

    preference_markers = [
        "from now on",
        "always",
        "prefer",
        "i prefer",
        "خليك",
        "من دلوقتي",
        "دايما",
        "بحب",
        "فضل",
        "خلي الرد",
        "باللغة",
        "بالعربي",
        "بالانجليزي",
        "اختصر",
        "فصل",
        "اشرح ببساطة",
    ]

    return any(marker in lowered for marker in preference_markers)


# ============================================================
# LATEST ITEM HELPERS
# ============================================================

def summarize_artifact(artifact: Optional[dict[str, Any]]) -> str:
    if not artifact:
        return ""

    parts = [
        f"id={artifact.get('artifact_id')}",
        f"type={artifact.get('type')}",
        f"title={artifact.get('title')}",
        f"path={artifact.get('path')}",
    ]

    preview = artifact.get("content_preview")
    if preview:
        parts.append(f"preview={clean_memory_text(preview, 350)}")

    return " | ".join([p for p in parts if p and not p.endswith("=None")])


def latest_artifact_summary(state: dict[str, Any]) -> str:
    artifacts = state.get("artifacts", [])
    latest_id = state.get("latest_artifact_id")

    if latest_id:
        for artifact in reversed(artifacts):
            if artifact.get("artifact_id") == latest_id:
                return summarize_artifact(artifact)

    if artifacts:
        return summarize_artifact(artifacts[-1])

    return ""


def latest_report_summary(state: dict[str, Any]) -> str:
    latest_report_id = state.get("latest_report_id")
    report = state.get("latest_report") or state.get("report_draft")

    if latest_report_id:
        artifact_summary = latest_artifact_summary(state)
        if artifact_summary:
            return artifact_summary

    if report:
        return clean_memory_text(report, 500)

    return ""


def latest_code_summary(state: dict[str, Any]) -> str:
    code = state.get("latest_code") or state.get("latest_commands") or state.get("generated_commands")
    code_id = state.get("latest_code_id")

    if code_id:
        return f"id={code_id} | preview={clean_memory_text(code or '', 500)}"

    if code:
        return clean_memory_text(code, 500)

    return ""


def latest_image_summary(state: dict[str, Any]) -> str:
    """
    Summarize the latest image analysis for working memory.

    Updated for structured Vision Agent output:
    - topic
    - image_type
    - summary/explanation
    - visible_text preview
    - confidence
    - uncertainties count
    """
    image = state.get("latest_image_analysis")
    if not image:
        return ""

    if isinstance(image, dict):
        topic = image.get("topic") or ""
        image_type = image.get("image_type") or ""
        summary = image.get("summary") or image.get("explanation") or image.get("user_response") or image.get("raw_output") or ""
        visible_text = image.get("visible_text") or ""
        confidence = image.get("confidence")
        uncertainties = image.get("uncertainties") or []

        parts = []

        if topic:
            parts.append(f"topic={clean_memory_text(topic, 180)}")

        if image_type:
            parts.append(f"type={clean_memory_text(image_type, 80)}")

        if summary:
            parts.append(f"summary={clean_memory_text(summary, 500)}")

        if visible_text:
            parts.append(f"visible_text={clean_memory_text(visible_text, 300)}")

        if confidence is not None:
            parts.append(f"confidence={confidence}")

        if isinstance(uncertainties, list) and uncertainties:
            parts.append(f"uncertainties_count={len(uncertainties)}")

        return " | ".join(parts)

    return clean_memory_text(str(image), 500)


def latest_email_summary(state: dict[str, Any]) -> str:
    draft = state.get("email_draft")
    pending = state.get("pending_approval")

    if draft:
        return (
            f"draft_id={draft.get('draft_id')} | "
            f"to={draft.get('to')} | "
            f"subject={draft.get('subject')} | "
            f"status={draft.get('status')} | "
            f"requires_confirmation={draft.get('requires_confirmation')}"
        )

    if pending and pending.get("type") == "confirm_send_email":
        draft = pending.get("email_draft") or {}
        return (
            f"pending_email_confirmation | "
            f"to={draft.get('to')} | "
            f"subject={draft.get('subject')}"
        )

    return ""


# ============================================================
# CURRENT TOPIC / TASK INFERENCE
# ============================================================

def infer_current_topic(state: dict[str, Any]) -> Optional[str]:
    """
    Infer current topic from explicit current_topic or latest outputs.
    """
    if state.get("current_topic"):
        return clean_memory_text(str(state["current_topic"]), 220)

    image = state.get("latest_image_analysis")
    if isinstance(image, dict) and image.get("topic"):
        return clean_memory_text(str(image["topic"]), 220)

    active_task = state.get("active_task")
    if isinstance(active_task, dict) and active_task.get("topic"):
        return clean_memory_text(str(active_task["topic"]), 220)

    decision = state.get("orchestrator_decision")
    if isinstance(decision, dict):
        understanding = decision.get("understanding")
        if understanding:
            return clean_memory_text(str(understanding), 220)

    return None


def infer_active_task(state: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Infer or preserve active task.
    """
    active = state.get("active_task")
    if active:
        return active

    if state.get("latest_report_id") or state.get("report_draft"):
        return {
            "type": "report",
            "status": "draft_ready",
            "artifact_id": state.get("latest_report_id") or state.get("latest_artifact_id"),
            "available_actions": ["edit", "export_pdf", "export_docx", "export_markdown", "email"],
        }

    if state.get("latest_code_id") or state.get("generated_commands"):
        return {
            "type": "command_code",
            "status": "ready",
            "artifact_id": state.get("latest_code_id") or state.get("latest_artifact_id"),
            "available_actions": ["edit", "save", "export_pdf", "export_docx", "email"],
        }

    pending = state.get("pending_approval")
    if pending:
        return {
            "type": "approval",
            "status": "waiting_for_user",
            "approval_type": pending.get("type"),
        }

    return None


# ============================================================
# MEMORY SUMMARY
# ============================================================

def update_memory_summary(state: dict[str, Any]) -> str:
    """
    Build a compact working memory summary.

    This is deterministic and safe. It does not call an LLM.
    It intentionally stores only task-relevant context.
    """
    parts: list[str] = []

    current_topic = infer_current_topic(state)
    if current_topic:
        parts.append(f"Current topic: {current_topic}")

    active_task = infer_active_task(state)
    if active_task:
        parts.append(f"Active task: {active_task}")

    pending = state.get("pending_approval")
    if pending:
        parts.append(f"Pending approval/info: {pending}")

    image_summary = latest_image_summary(state)
    if image_summary:
        parts.append(f"Latest image analysis: {image_summary}")

    report_summary = latest_report_summary(state)
    if report_summary:
        parts.append(f"Latest report: {report_summary}")

    artifact_summary = latest_artifact_summary(state)
    if artifact_summary:
        parts.append(f"Latest artifact: {artifact_summary}")

    code_summary = latest_code_summary(state)
    if code_summary:
        parts.append(f"Latest code/commands: {code_summary}")

    email_summary = latest_email_summary(state)
    if email_summary:
        parts.append(f"Latest email draft/status: {email_summary}")

    # Store only useful style preference if clear from recent messages.
    recent_text = " ".join(
        [
            msg.get("content", "")
            for msg in state.get("messages", [])[-6:]
            if msg.get("role") == "user"
        ]
    )

    if should_store_user_preference(recent_text):
        parts.append(f"User working preference: {clean_memory_text(recent_text, 300)}")

    summary = "\n".join(parts)
    return clean_memory_text(summary, 3000)


def build_context_for_reference_resolution(state: dict[str, Any]) -> dict[str, Any]:
    """
    Build structured context specifically for resolving references.
    """
    return {
        "current_topic": infer_current_topic(state),
        "active_task": infer_active_task(state),
        "pending_approval": state.get("pending_approval"),
        "latest_answer": clean_memory_text(state.get("latest_answer") or state.get("latest_text_output") or "", 700),
        "latest_image_analysis": latest_image_summary(state),
        "latest_report": latest_report_summary(state),
        "latest_artifact": latest_artifact_summary(state),
        "latest_code": latest_code_summary(state),
        "latest_email": latest_email_summary(state),
        "recent_messages": compact_recent_messages(state.get("messages", []), limit=8),
    }


def update_state_after_response(state: dict[str, Any]) -> dict[str, Any]:
    """
    Final memory update helper after final_response is produced.
    """
    state["current_topic"] = infer_current_topic(state)
    state["active_task"] = infer_active_task(state)
    state["memory_summary"] = update_memory_summary(state)
    return state


# ============================================================
# TURN MANAGEMENT
# ============================================================

def prepare_new_turn(
    state: dict[str, Any],
    *,
    raw_input: str,
    image_path: Optional[str] = None,
) -> dict[str, Any]:
    """
    Prepare a persisted state for a new user message.

    Preserves:
    - messages
    - current_topic
    - active_task
    - pending approvals
    - artifacts
    - sources
    - latest image/report/code/email context

    Resets:
    - per-turn routing
    - critic result
    - final response
    - safety checks
    """
    state["raw_input"] = raw_input
    state["image_path"] = image_path
    state["image_paths"] = [image_path] if image_path else []

    state["language"] = "unknown"
    state["input_type"] = "unknown"
    state["input_metadata"] = {}

    state["input_safety"] = {}
    state["output_safety"] = {}
    state["risk_level"] = "safe"

    state["orchestrator_decision"] = {}
    state["context_resolution"] = {}
    state["plan"] = {}
    state["next_action"] = None

    state["step_count"] = 0
    state["retry_count"] = 0

    state["latest_agent_output"] = None
    state["critic_result"] = {}
    state["final_response"] = None

    return state


def finalize_turn(state: dict[str, Any]) -> dict[str, Any]:
    """
    Add current user/assistant messages and update memory.
    """
    raw_input = state.get("raw_input")
    final_response = state.get("final_response")

    if raw_input:
        add_message(state, "user", raw_input)

    if final_response:
        add_message(state, "assistant", final_response)

    return update_state_after_response(state)