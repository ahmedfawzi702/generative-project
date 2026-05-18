

from __future__ import annotations

import json
import re
from typing import Any, Optional
import hashlib
from pathlib import Path
from langchain_core.messages import HumanMessage, SystemMessage

from my_agent.models import (
    command_llm,
    critic_llm,
    general_llm,
    orchestrator_llm,
    report_llm,
    vision_llm,
)
from my_agent.prompts import (
    ARTIFACT_REQUEST_RESOLVER_PROMPT,
    COMMAND_AGENT_PROMPT,
    CONTEXT_BUILDER_PROMPT,
    CONTEXT_RESOLVER_PROMPT,
    CRITIC_PROMPT,
    EMAIL_AGENT_PROMPT,
    EXPLANATION_AGENT_PROMPT,
    GLOBAL_POLICY,
    INPUT_NORMALIZER_PROMPT,
    ORCHESTRATOR_PROMPT,
    OUTPUT_SAFETY_PROMPT,
    RAG_QUERY_REWRITER_PROMPT,
    RAG_ANSWER_AGENT_PROMPT,
    REPORT_AGENT_PROMPT,
    RESPONSE_COMPOSER_PROMPT,
    SAFETY_GATE_PROMPT,
    TASK_PLANNER_PROMPT,
    VISION_AGENT_PROMPT,
    WEB_RESEARCH_AGENT_PROMPT,
)
from my_agent.state import AgentState
from my_agent.tools import (
    export_pdf_from_markdown,
    export_docx_from_markdown,
    build_export_filename,
    find_artifact_by_id,
    find_email_in_text,
    format_artifacts_for_prompt,
    format_recent_messages,
    format_sources_for_prompt,
    format_state_summary_for_prompt,
    image_to_data_url,
    is_yes_confirmation,
    latest_artifact,
    read_artifact_content,
    register_sources_from_web,
    retrieve_from_documents,
    safe_filename,
    save_commands_artifact,
    save_markdown,
    save_report_artifact,
    save_text_file,
    send_email_via_smtp,
    create_email_draft,
    short_preview,
    tavily_search,
)
from my_agent.memory import update_memory_summary


# ============================================================
# JSON / LLM HELPERS
# ============================================================

def extract_json(text: str, fallback: dict[str, Any]) -> dict[str, Any]:
    """
    Extract a JSON object from model output.

    Models sometimes wrap JSON in markdown or add text. This keeps the graph alive.
    """
    if not text:
        return fallback

    try:
        return json.loads(text)
    except Exception:
        pass

    # Remove common markdown fences.
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()

    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # Last resort: first {...} block.
    try:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if match:
            return json.loads(match.group(0))
    except Exception:
        pass

    return fallback


def llm_json(
    llm,
    prompt: str,
    payload: dict[str, Any],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    """
    Call an LLM and parse JSON with fallback.
    """
    response = llm.invoke(
        [
            SystemMessage(content=f"{GLOBAL_POLICY}\n\n{prompt}"),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
        ]
    )
    return extract_json(response.content, fallback)


def llm_text(llm, prompt: str, content: str) -> str:
    """
    Call an LLM for a normal text response.
    """
    response = llm.invoke(
        [
            SystemMessage(content=f"{GLOBAL_POLICY}\n\n{prompt}"),
            HumanMessage(content=content),
        ]
    )
    return response.content


def append_debug(state: AgentState, node: str, data: dict[str, Any]) -> AgentState:
    debug_log = state.get("debug_log", [])
    debug_log.append({"node": node, "data": data})
    return {**state, "debug_log": debug_log[-50:]}


def add_error(state: AgentState, node: str, error: Exception | str) -> AgentState:
    errors = state.get("errors", [])
    errors.append({"node": node, "error": str(error)})
    return {**state, "errors": errors[-20:]}


def detect_language_simple(text: str) -> str:
    arabic = any("\u0600" <= c <= "\u06FF" for c in text or "")
    english = any("a" <= c.lower() <= "z" for c in text or "")

    if arabic and english:
        return "mixed"
    if arabic:
        return "ar"
    if english:
        return "en"
    return "unknown"



def detect_explicit_language_instruction(text: str) -> str:
    """
    Detect explicit user preference for the current response language.
    Returns: ar|en|unknown
    """
    lowered = (text or "").strip().lower()

    english_markers = [
        "reply in english",
        "respond in english",
        "answer in english",
        "speak english",
        "in english",
        "بالانجليزي",
        "بالإنجليزي",
        "انجليزي بس",
        "إنجليزي بس",
        "رد انجليزي",
        "رد إنجليزي",
    ]

    arabic_markers = [
        "reply in arabic",
        "respond in arabic",
        "answer in arabic",
        "speak arabic",
        "in arabic",
        "بالعربي",
        "عربي بس",
        "رد عربي",
        "كلمني عربي",
        "اتكلم عربي",
    ]

    if any(marker in lowered for marker in english_markers):
        return "en"

    if any(marker in lowered for marker in arabic_markers):
        return "ar"

    return "unknown"


def preferred_response_language(state: AgentState) -> str:
    """
    One source of truth for user-facing language.
    """
    raw = state.get("raw_input", "") or ""

    explicit = detect_explicit_language_instruction(raw)
    if explicit in ["ar", "en"]:
        return explicit

    detected = detect_language_simple(raw)

    if detected == "en":
        return "en"
    if detected == "ar":
        return "ar"
    if detected == "mixed":
        arabic_count = len(re.findall(r"[\u0600-\u06FF]", raw))
        english_count = len(re.findall(r"[A-Za-z]", raw))
        return "ar" if arabic_count > english_count else "en"

    lang = state.get("language") or "unknown"
    if lang in ["ar", "en"]:
        return lang

    return "en"


def localized(state: AgentState, en: str, ar: str) -> str:
    return ar if preferred_response_language(state) == "ar" else en


def user_wants_docx(text: str) -> bool:
    lowered = (text or "").lower()
    return any(token in lowered for token in ["docx", ".docx", "word", "ms word", "وورد", "ورد", "ملف وورد"])


def detect_reference_phrases(text: str) -> list[str]:
    lowered = (text or "").lower()
    phrases = [
        "ده",
        "دي",
        "دا",
        "دول",
        "الحاجات دي",
        "الموضوع ده",
        "الكلام اللي فات",
        "الصورة دي",
        "التقرير ده",
        "ابعته",
        "عدله",
        "احفظه",
        "اشرحه",
        "it",
        "this",
        "that",
        "them",
        "these",
        "those",
        "send it",
        "send them",
        "edit it",
        "save it",
        "save them",
        "export it",
        "export them",
        "make it",
        "make them",
        "explain it",
        "previous",
    ]
    return [p for p in phrases if p.lower() in lowered]


def user_wants_report(text: str) -> bool:
    lowered = (text or "").lower()
    return any(token in lowered for token in ["report", "تقرير", "ريبورت", "write-up", "writeup"])


def user_wants_pdf(text: str) -> bool:
    lowered = (text or "").lower()
    return any(token in lowered for token in ["pdf", "بي دي اف", "بى دى اف"])


def user_wants_markdown(text: str) -> bool:
    lowered = (text or "").lower()
    return any(token in lowered for token in ["markdown", ".md", "ماركداون"])


def user_is_asking_new_content_about_topic(text: str) -> bool:
    """
    Detect requests that need NEW content generation before export.
    This prevents "make pdf about X" from exporting a previous clarification.
    """
    lowered = (text or "").strip().lower()
    if not lowered:
        return False

    wants_output = user_wants_pdf(lowered) or user_wants_docx(lowered) or user_wants_markdown(lowered) or user_wants_report(lowered)
    creation_words = ["make", "create", "write", "generate", "prepare", "build", "اعمل", "اكتب", "جهز", "حضر", "حضّر"]
    topic_markers = ["about", "on ", "regarding", "concerning", "for ", "risk of", "risks of", "security of", "analysis of", "عن", "حول", "بخصوص", "مخاطر", "تحليل"]
    reference_markers = ["make it", "make this", "make them", "export it", "export this", "save it", "save this", "convert it", "convert this", "previous", "last answer", "الكلام ده", "اللي فات", "الرد ده"]

    return wants_output and any(w in lowered for w in creation_words) and any(m in lowered for m in topic_markers) and not any(m in lowered for m in reference_markers)


def infer_topic_from_generation_request(text: str) -> str:
    """Extract a readable topic from requests like 'make pdf about X'."""
    raw = (text or "").strip()
    lowered = raw.lower()

    patterns = [
        r"(?:make|create|write|generate|prepare)\s+(?:a\s+)?(?:pdf|report|docx|word|markdown)?\s*(?:about|on|regarding|concerning|for)\s+(.+)$",
        r"(?:what is|explain|analyze|analysis of)\s+(.+)$",
        r"(?:اعمل|اكتب|جهز|حضّر|حضر)\s+(?:pdf|تقرير|وورد|ملف)?\s*(?:عن|حول|بخصوص)\s+(.+)$",
        r"(?:مخاطر|تحليل)\s+(.+)$",
    ]

    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            topic = match.group(1).strip(" .؟?؛؛")
            if topic:
                return topic

    cleaned = re.sub(r"\b(make|create|write|generate|prepare|pdf|report|docx|word|markdown|about|on|regarding|concerning|for)\b", " ", lowered, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .؟?")
    return cleaned or raw


def user_wants_email(text: str) -> bool:
    lowered = (text or "").lower()
    return any(token in lowered for token in ["email", "e-mail", "mail", "ايميل", "إيميل", "ابعته", "ابعت", "send it", "send"])



def user_wants_email_followup(text: str) -> bool:
    """General email/send intent. Does not depend on any specific recipient."""
    lowered = (text or "").strip().lower()
    tokens = [
        "email", "e-mail", "mail", "gmail",
        "send it", "send this", "send them", "send the", "send as email",
        "send this pdf", "send this file", "send this as email", "email it", "email this", "send to", "send it to",
        "ايميل", "إيميل", "ابعته", "ابعت", "ارسله", "أرسله", "ابعته على", "ابعته ل", "ابعته ايميل",
    ]
    return any(token in lowered for token in tokens)


def build_compact_payload(state: AgentState) -> dict[str, Any]:
    return {
        "raw_input": state.get("raw_input", ""),
        "input_type": state.get("input_type", "unknown"),
        "language": state.get("language", "unknown"),
        "memory_summary": state.get("memory_summary", ""),
        "current_topic": state.get("current_topic"),
        "active_task": state.get("active_task"),
        "pending_approval": state.get("pending_approval"),
        "latest_answer": state.get("latest_answer"),
        "latest_image_analysis": state.get("latest_image_analysis"),
        "latest_report": state.get("latest_report"),
        "report_draft": state.get("report_draft"),
        "latest_report_id": state.get("latest_report_id"),
        "latest_artifact_id": state.get("latest_artifact_id"),
        "latest_code": state.get("latest_code"),
        "latest_commands": state.get("latest_commands"),
        "generated_commands": state.get("generated_commands"),
        "latest_code_id": state.get("latest_code_id"),
        "latest_email_draft_id": state.get("latest_email_draft_id"),
        "email_draft": state.get("email_draft"),
        "web_findings": short_preview(state.get("web_findings") or "", 1200),
        "generated_files": state.get("generated_files", [])[-6:],
        "recent_messages": state.get("messages", [])[-12:],
        "artifacts": state.get("artifacts", [])[-10:],
        "critic_result": state.get("critic_result"),
        "step_count": state.get("step_count", 0),
        "retry_count": state.get("retry_count", 0),
    }


# ============================================================
# CORE PIPELINE NODES
# ============================================================

def input_normalizer(state: AgentState) -> AgentState:
    raw_input = state.get("raw_input", "") or ""
    image_path = state.get("image_path")
    has_image = bool(image_path)
    has_text = bool(raw_input.strip())

    fallback_input_type = "text_image" if has_text and has_image else "image" if has_image else "text"
    fallback_language = detect_language_simple(raw_input)
    references = detect_reference_phrases(raw_input)

    fallback = {
        "language": fallback_language,
        "input_type": fallback_input_type,
        "clean_user_text": raw_input.strip(),
        "has_reference": bool(references),
        "reference_phrases": references,
        "has_external_action": user_wants_email(raw_input),
        "external_action_type": "send_email" if user_wants_email(raw_input) else "none",
        "wants_artifact": user_wants_report(raw_input) or user_wants_pdf(raw_input) or user_wants_docx(raw_input) or user_wants_markdown(raw_input),
        "artifact_type": "report" if user_wants_report(raw_input) else "pdf" if user_wants_pdf(raw_input) else "docx" if user_wants_docx(raw_input) else "markdown" if user_wants_markdown(raw_input) else "none",
        "likely_user_intent": "vision_analysis" if has_image else "unknown",
        "notes_for_orchestrator": "",
    }

    try:
        parsed = llm_json(
            general_llm,
            INPUT_NORMALIZER_PROMPT,
            {
                "raw_input": raw_input,
                "has_image": has_image,
                "image_path": image_path,
                "fallback": fallback,
            },
            fallback,
        )
    except Exception as exc:
        parsed = fallback
        state = add_error(state, "input_normalizer", exc)

    language = parsed.get("language") or fallback_language
    input_type = parsed.get("input_type") or fallback_input_type

    new_state: AgentState = {
        **state,
        "language": language,
        "input_type": input_type,
        "input_metadata": parsed,
        "risk_level": state.get("risk_level", "safe"),
        "step_count": state.get("step_count", 0),
        "retry_count": state.get("retry_count", 0),
        "max_steps": state.get("max_steps", 8),
        "max_retries": state.get("max_retries", 2),
        "messages": state.get("messages", []),
        "artifacts": state.get("artifacts", []),
        "sources": state.get("sources", []),
        "retrieved_docs": state.get("retrieved_docs", []),
        "web_sources": state.get("web_sources", []),
        "generated_files": state.get("generated_files", []),
        "critic_history": state.get("critic_history", []),
        "errors": state.get("errors", []),
        "debug_log": state.get("debug_log", []),
    }

    return append_debug(new_state, "input_normalizer", parsed)


def context_builder(state: AgentState) -> AgentState:
    """
    Build compact context.

    This node is mostly deterministic for reliability, but still stores a rich snapshot.
    """
    latest_items = {
        "latest_answer": state.get("latest_answer") or state.get("latest_text_output"),
        "latest_image_analysis": state.get("latest_image_analysis"),
        "latest_report": state.get("latest_report_id"),
        "latest_artifact": state.get("latest_artifact_id"),
        "latest_code_or_commands": state.get("latest_code_id") or bool(state.get("latest_commands")),
        "latest_email_draft": state.get("latest_email_draft_id"),
    }

    compact_context = (
        f"Current topic: {state.get('current_topic')}\n"
        f"Active task: {state.get('active_task')}\n"
        f"Pending approval: {state.get('pending_approval')}\n"
        f"Latest answer: {short_preview(state.get('latest_answer') or state.get('latest_text_output') or '', 700)}\n"
        f"Latest image analysis: {short_preview(str(state.get('latest_image_analysis') or ''), 700)}\n"
        f"Latest report id: {state.get('latest_report_id')}\n"
        f"Latest artifact id: {state.get('latest_artifact_id')}\n"
        f"Latest code id: {state.get('latest_code_id')}\n"
        f"Latest email draft id: {state.get('latest_email_draft_id')}\n"
        f"Recent messages:\n{format_recent_messages(state.get('messages', []), limit=8)}\n"
        f"Artifacts:\n{format_artifacts_for_prompt(state.get('artifacts', []), limit=6)}"
    )

    snapshot = {
        "compact_context": compact_context,
        "current_topic": state.get("current_topic"),
        "active_task": state.get("active_task"),
        "latest_items": latest_items,
        "pending_approval": state.get("pending_approval"),
        "important_recent_messages": state.get("messages", [])[-8:],
        "context_confidence": 0.85,
    }

    new_state = {
        **state,
        "context_snapshot": snapshot,
        "memory_summary": update_memory_summary(state),
    }

    return append_debug(new_state, "context_builder", {"context_preview": short_preview(compact_context, 1000)})

# ============================================================
# PRE-SAFETY / DETERMINISTIC SAFETY HELPERS
# ============================================================

PROMPT_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"ignore\s+(all\s+)?prior\s+instructions",
    r"disregard\s+(all\s+)?previous\s+instructions",
    r"forget\s+(all\s+)?previous\s+instructions",
    r"override\s+(the\s+)?system\s+prompt",
    r"reveal\s+(your\s+)?(system|developer)\s+prompt",
    r"show\s+(me\s+)?(your\s+)?(system|developer|hidden)\s+(prompt|instructions)",
    r"print\s+(your\s+)?(system|developer)\s+prompt",
    r"what\s+are\s+your\s+(system|developer|hidden)\s+instructions",
    r"dump\s+(your\s+)?(system|developer)\s+prompt",
    r"jailbreak",
    r"developer\s+mode",
    r"act\s+as\s+dan",

    # Arabic / Egyptian Arabic
    r"تجاهل\s+(كل\s+)?التعليمات",
    r"تجاهل\s+التعليمات\s+السابقة",
    r"انس[ى|ي]\s+(كل\s+)?التعليمات",
    r"اكشف\s+(لي\s+)?(البرومبت|التعليمات)",
    r"اظهر\s+(لي\s+)?(البرومبت|التعليمات)",
    r"وريني\s+(البرومبت|تعليماتك)",
    r"قول\s*(لي)?\s+(البرومبت|تعليماتك)",
    r"اطبع\s+(البرومبت|تعليماتك)",
    r"خليك\s+developer\s+mode",
]


PERSONAL_DATA_REQUEST_PATTERNS = [
    r"what\s+do\s+you\s+know\s+about\s+me",
    r"what\s+information\s+do\s+you\s+have\s+about\s+me",
    r"tell\s+me\s+everything\s+you\s+know\s+about\s+me",
    r"show\s+me\s+my\s+(memory|memories|personal\s+data|private\s+data)",
    r"do\s+you\s+remember\s+anything\s+about\s+me",
    r"what\s+is\s+stored\s+in\s+memory\s+about\s+me",

    # Arabic / Egyptian Arabic
    r"تعرف\s+ايه\s+عني",
    r"تعرف\s+إيه\s+عني",
    r"انت\s+عارف\s+ايه\s+عني",
    r"إنت\s+عارف\s+إيه\s+عني",
    r"قول\s*(لي)?\s+كل\s+اللي\s+تعرفه\s+عني",
    r"ايه\s+المعلومات\s+اللي\s+عندك\s+عني",
    r"إيه\s+المعلومات\s+اللي\s+عندك\s+عني",
    r"بياناتي\s+الشخصية",
    r"معلوماتي\s+الشخصية",
    r"الميموري\s+بتاعتك\s+عني",
    r"الذاكرة\s+بتاعتك\s+عني",
    r"ذاكرتك\s+عني",
]


CLEARLY_DANGEROUS_COMMAND_PATTERNS = [
    # destructive system commands
    r"\brm\s+-rf\s+/",
    r"\brm\s+-rf\s+\*",
    r"\bdel\s+/f\s+/s\s+/q\s+[a-zA-Z]:\\",
    r"\bformat\s+[a-zA-Z]:",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r":\(\)\s*\{\s*:\|:&\s*\};:",

    # pipe-to-shell remote execution
    r"\bcurl\b.*\|\s*(bash|sh|zsh|python|perl)",
    r"\bwget\b.*\|\s*(bash|sh|zsh|python|perl)",

    # suspicious reverse shell patterns
    r"\bnc\b.*\s-e\s",
    r"\bnetcat\b.*\s-e\s",
    r"/dev/tcp/",
    r"\bbash\s+-i\b",

    # credential/token dumping wording
    r"dump\s+(passwords|tokens|cookies|sessions|credentials)",
    r"steal\s+(passwords|tokens|cookies|sessions|credentials)",
    r"extract\s+(passwords|tokens|cookies|sessions|credentials)",
    r"سرقة\s+(باسورد|كلمات\s+السر|توكن|كوكيز|سيشن)",
    r"اسحب\s+(باسورد|كلمات\s+السر|توكن|كوكيز|سيشن)",
]


AMBIGUOUS_CYBER_OR_DEVICE_PATTERNS = [
    r"\bextract\b.*\b(data|files|messages|photos|contacts)\b",
    r"\bpull\b.*\b(data|files|messages|photos|contacts)\b",
    r"\bdump\b.*\b(data|files|messages|photos|contacts)\b",
    r"\bphone\b",
    r"\bandroid\b",
    r"\biphone\b",
    r"\bwhatsapp\b",
    r"\btelegram\b",
    r"\baccount\b",
    r"\bdevice\b",
    r"\bcredentials\b",
    r"\bcookies\b",
    r"\bsessions\b",

    # Arabic / Egyptian Arabic
    r"اطلع\s+(داتا|بيانات|صور|ملفات|رسايل|رسائل)",
    r"اسحب\s+(داتا|بيانات|صور|ملفات|رسايل|رسائل)",
    r"استخرج\s+(داتا|بيانات|صور|ملفات|رسايل|رسائل)",
    r"موبايل",
    r"تليفون",
    r"جهاز",
    r"حساب",
    r"واتساب",
    r"تليجرام",
]


AUTHORIZATION_HINT_PATTERNS = [
    r"\bmy\b",
    r"\bmine\b",
    r"\bowned\b",
    r"\bauthorized\b",
    r"\bpermission\b",
    r"\bconsent\b",
    r"\bcompany-approved\b",
    r"\blab\b",
    r"\bctf\b",
    r"\btest environment\b",
    r"\bsandbox\b",

    # Arabic / Egyptian Arabic
    r"جهازي",
    r"بتاعي",
    r"ملكي",
    r"عندي\s+تصريح",
    r"مصرح",
    r"بإذن",
    r"باذن",
    r"لاب",
    r"معمل",
    r"تجربة",
    r"اختبار",
    r"ctf",
]


def _matches_any_pattern(text: str, patterns: list[str]) -> bool:
    text = text or ""
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def has_prompt_injection(text: str) -> bool:
    return _matches_any_pattern(text, PROMPT_INJECTION_PATTERNS)


def is_personal_data_request(text: str) -> bool:
    return _matches_any_pattern(text, PERSONAL_DATA_REQUEST_PATTERNS)


def has_clearly_dangerous_command_request(text: str) -> bool:
    return _matches_any_pattern(text, CLEARLY_DANGEROUS_COMMAND_PATTERNS)


def is_ambiguous_cyber_or_device_request(text: str) -> bool:
    return _matches_any_pattern(text, AMBIGUOUS_CYBER_OR_DEVICE_PATTERNS)


def has_authorization_hint(text: str) -> bool:
    return _matches_any_pattern(text, AUTHORIZATION_HINT_PATTERNS)


def make_safety_state(
    state: AgentState,
    *,
    status: str,
    risk_level: str,
    reason: str,
    next_action: str,
    message: str,
    blocked_parts: list[str] | None = None,
    requires_user_clarification: bool = False,
    clarifying_question: str = "",
    allowed_scope: str = "",
) -> AgentState:
    parsed = {
        "status": status,
        "risk_level": risk_level,
        "reason": reason,
        "allowed_scope": allowed_scope,
        "blocked_parts": blocked_parts or [],
        "requires_user_clarification": requires_user_clarification,
        "clarifying_question": clarifying_question,
        "safe_alternative": message if status == "blocked" else "",
    }

    return append_debug(
        {
            **state,
            "input_safety": parsed,
            "risk_level": status,
            "next_action": next_action,
            "latest_text_output": message,
            "latest_answer": message,
        },
        "safety_gate",
        parsed,
    )


def pre_safety_gate(state: AgentState) -> AgentState:
    """
    Deterministic safety gate that runs BEFORE any LLM node.

    Purpose:
    - Block obvious prompt injection before the text reaches any LLM.
    - Block broad requests to reveal stored/private personal data.
    - Block obviously destructive or credential-stealing command requests.
    - Ask clarification for sensitive device/account/data requests without authorization context.

    This is not a replacement for the LLM-based safety_gate.
    It is a first hard filter.
    """
    raw = state.get("raw_input", "") or ""

    if has_prompt_injection(raw):
        text = (
            "مش هقدر أتبع أوامر بتطلب تجاهل التعليمات أو كشف تعليمات داخلية. "
            "أقدر أساعدك في طلب آمن وواضح."
        )

        return make_safety_state(
            state,
            status="blocked",
            risk_level="blocked",
            reason="Prompt injection attempt detected before LLM processing.",
            next_action="refuse",
            message=text,
            blocked_parts=["prompt_injection"],
            allowed_scope="Safe user requests only.",
        )

    if is_personal_data_request(raw):
        text = (
            "مش هقدر أعرض أو أستنتج معلومات شخصية مخزنة عنك. "
            "أقدر أساعدك في حاجة محددة تقولها في الرسالة الحالية، "
            "أو أشرحلك إزاي تتحكم في الذاكرة والخصوصية."
        )

        return make_safety_state(
            state,
            status="blocked",
            risk_level="medium",
            reason="Request to reveal stored/private personal information.",
            next_action="refuse",
            message=text,
            blocked_parts=["personal_data_disclosure"],
            allowed_scope="Do not reveal stored/private personal data.",
        )

    if has_clearly_dangerous_command_request(raw):
        text = (
            "مش هقدر أساعد في أوامر ممكن تضر جهاز، تمسح بيانات، تسرّب أسرار، "
            "أو تجمع credentials/tokens/cookies. "
            "أقدر أساعدك ببديل آمن زي backup، hardening، أو تحليل دفاعي مصرح."
        )

        return make_safety_state(
            state,
            status="blocked",
            risk_level="blocked",
            reason="Clearly dangerous command or credential-extraction request detected.",
            next_action="refuse",
            message=text,
            blocked_parts=["dangerous_commands", "credential_or_data_extraction"],
            allowed_scope="Safe defensive or authorized workflows only.",
        )

    if is_ambiguous_cyber_or_device_request(raw) and not has_authorization_hint(raw):
        question = (
            "محتاج أتأكد الأول: هل ده جهازك/حسابك أو عندك تصريح واضح للتعامل معاه؟ "
            "وقولي الهدف الآمن بالظبط: backup، troubleshooting، forensic analysis مصرح، ولا حاجة تانية؟"
        )

        return make_safety_state(
            state,
            status="needs_clarification",
            risk_level="medium",
            reason="Sensitive device/account/data request without clear authorization context.",
            next_action="ask_user",
            message=question,
            blocked_parts=[],
            requires_user_clarification=True,
            clarifying_question=question,
            allowed_scope="Only owned-device, authorized, defensive, educational, or lab workflows.",
        )

    return {
        **state,
        "risk_level": state.get("risk_level", "safe"),
    }
def safety_gate(state: AgentState) -> AgentState:
    """
    LLM-based input safety gate.

    Runs AFTER:
    - pre_safety_gate
    - input_normalizer
    - context_builder

    Purpose:
    - Deeper intent classification.
    - Catch ambiguous unsafe requests.
    - Ask clarification when authorization/scope is unclear.
    - Block clearly unsafe requests.
    """
    payload = {
        **build_compact_payload(state),
        "input_metadata": state.get("input_metadata"),
    }

    # Fail-safe fallback:
    # If safety classifier fails, do NOT silently mark request as safe.
    fallback = {
        "status": "needs_clarification",
        "risk_level": "medium",
        "reason": "Safety classifier failed or could not verify the request.",
        "allowed_scope": "Only safe, authorized, non-sensitive help is allowed.",
        "blocked_parts": [],
        "requires_user_clarification": True,
        "clarifying_question": "محتاج توضيح بسيط قبل ما أكمل: هل الطلب ده آمن ومصرّح بيه؟",
        "safe_alternative": "",
    }

    raw = state.get("raw_input", "") or ""

    pending = state.get("pending_approval") or {}
    if pending.get("type") == "confirm_send_email" and is_yes_confirmation(raw):
        parsed = {
            "status": "safe",
            "risk_level": "low",
            "reason": "User explicitly confirmed pending email sending.",
            "allowed_scope": "Send the already prepared email draft only.",
            "blocked_parts": [],
            "requires_user_clarification": False,
            "clarifying_question": "",
            "safe_alternative": "",
        }
        return append_debug(
            {
                **state,
                "input_safety": parsed,
                "risk_level": "safe",
                "next_action": "email_send",
            },
            "safety_gate",
            parsed,
        )

    # Simple greetings are safe and should not trigger clarification or tools.
    if is_casual_greeting(raw):
        parsed = {
            "status": "safe",
            "risk_level": "low",
            "reason": "Simple casual greeting.",
            "allowed_scope": "Normal conversation.",
            "blocked_parts": [],
            "requires_user_clarification": False,
            "clarifying_question": "",
            "safe_alternative": "",
        }
        return append_debug(
            {
                **state,
                "input_safety": parsed,
                "risk_level": "safe",
                "next_action": state.get("next_action"),
            },
            "safety_gate",
            parsed,
        )

    # Deterministic checks repeated here as defense-in-depth.
    # This protects you if the graph is changed later and pre_safety_gate is bypassed.
    if has_prompt_injection(raw):
        text = (
            "مش هقدر أتبع أوامر بتطلب تجاهل التعليمات أو كشف تعليمات داخلية. "
            "أقدر أساعدك في طلب آمن وواضح."
        )

        return make_safety_state(
            state,
            status="blocked",
            risk_level="blocked",
            reason="Prompt injection attempt detected.",
            next_action="refuse",
            message=text,
            blocked_parts=["prompt_injection"],
            allowed_scope="Safe user requests only.",
        )

    if is_personal_data_request(raw):
        text = (
            "مش هقدر أعرض أو أستنتج معلومات شخصية مخزنة عنك. "
            "أقدر أساعدك في حاجة محددة تقولها في الرسالة الحالية، "
            "أو أشرحلك إعدادات الخصوصية والذاكرة."
        )

        return make_safety_state(
            state,
            status="blocked",
            risk_level="medium",
            reason="Request to reveal stored/private personal information.",
            next_action="refuse",
            message=text,
            blocked_parts=["personal_data_disclosure"],
            allowed_scope="Do not reveal stored/private personal data.",
        )

    if has_clearly_dangerous_command_request(raw):
        text = (
            "مش هقدر أساعد في أوامر ممكن تضر جهاز، تمسح بيانات، تسرّب أسرار، "
            "أو تجمع credentials/tokens/cookies. "
            "أقدر أساعدك ببديل آمن زي backup، hardening، أو تحليل دفاعي مصرح."
        )

        return make_safety_state(
            state,
            status="blocked",
            risk_level="blocked",
            reason="Clearly dangerous command or credential-extraction request detected.",
            next_action="refuse",
            message=text,
            blocked_parts=["dangerous_commands", "credential_or_data_extraction"],
            allowed_scope="Safe defensive or authorized workflows only.",
        )

    if is_ambiguous_cyber_or_device_request(raw) and not has_authorization_hint(raw):
        question = (
            "محتاج أتأكد الأول: هل ده جهازك/حسابك أو عندك تصريح واضح للتعامل معاه؟ "
            "وقولي الهدف الآمن بالظبط: backup، troubleshooting، forensic analysis مصرح، ولا حاجة تانية؟"
        )

        return make_safety_state(
            state,
            status="needs_clarification",
            risk_level="medium",
            reason="Sensitive device/account/data request without clear authorization context.",
            next_action="ask_user",
            message=question,
            blocked_parts=[],
            requires_user_clarification=True,
            clarifying_question=question,
            allowed_scope="Only owned-device, authorized, defensive, educational, or lab workflows.",
        )

    try:
        parsed = llm_json(general_llm, SAFETY_GATE_PROMPT, payload, fallback)

        if not isinstance(parsed, dict):
            parsed = fallback

    except Exception as exc:
        parsed = fallback
        state = add_error(state, "safety_gate", exc)

    status = parsed.get("status") or "needs_clarification"

    # Normalize unknown statuses safely.
    if status not in ["safe", "caution", "needs_clarification", "blocked"]:
        status = "needs_clarification"
        parsed["status"] = status
        parsed["risk_level"] = "medium"
        parsed["reason"] = "Safety classifier returned an unknown status."
        parsed["requires_user_clarification"] = True
        parsed["clarifying_question"] = (
            parsed.get("clarifying_question")
            or "محتاج توضيح بسيط قبل ما أكمل: هل الطلب ده آمن ومصرّح بيه؟"
        )

    if status == "blocked":
        next_action = "refuse"
        latest_text = (
            parsed.get("safe_alternative")
            or "مش هقدر أساعد في الطلب ده، لكن أقدر أساعدك بطريقة آمنة ومصرح بيها."
        )

    elif status == "needs_clarification":
        next_action = "ask_user"
        latest_text = (
            parsed.get("clarifying_question")
            or "محتاج أتأكد الأول: هل الطلب ده ضمن حاجة تملكها أو عندك تصريح واضح ليها؟"
        )

    else:
        next_action = state.get("next_action")
        latest_text = state.get("latest_text_output")

    new_state = {
        **state,
        "input_safety": parsed,
        "risk_level": status,
        "next_action": next_action,
        "latest_text_output": latest_text,
        "latest_answer": latest_text if status in ["blocked", "needs_clarification"] else state.get("latest_answer"),
    }

    return append_debug(new_state, "safety_gate", parsed)
def is_no_confirmation(text: str) -> bool:
    """
    Detect user cancellation / refusal in Arabic or English.
    Used mainly for pending email sending.
    """
    lowered = (text or "").strip().lower()

    exact_no_phrases = {
        "no",
        "n",
        "cancel",
        "stop",
        "don't send",
        "do not send",
        "dont send",
        "لا",
        "لأ",
        "لاء",
        "متبعتش",
        "ما تبعتش",
        "ماتبعتش",
        "بلاش",
        "الغيه",
        "إلغيه",
        "الغي",
        "إلغي",
        "cancel it",
    }

    if lowered in exact_no_phrases:
        return True

    contains_no_phrases = [
        "don't send",
        "do not send",
        "dont send",
        "cancel",
        "متبعتش",
        "ما تبعتش",
        "ماتبعتش",
        "مش عايز ابعته",
        "مش عايزة ابعته",
        "الغى الارسال",
        "إلغاء الإرسال",
        "cancel sending",
    ]

    return any(phrase in lowered for phrase in contains_no_phrases)




def user_explicitly_customizes_email(text: str) -> bool:
    """Only ask/use custom subject/body when the user explicitly requests it."""
    lowered = (text or "").strip().lower()
    markers = [
        "custom subject", "use subject", "subject should be", "body should be",
        "write this body", "email body", "message body", "use this body",
        "غير العنوان", "اكتب العنوان", "خلي العنوان", "نص الإيميل", "نص الايميل",
        "اكتب body", "اكتب subject", "خلي نص الإيميل", "خلي نص الايميل",
    ]
    return any(m in lowered for m in markers)

def user_wants_email_preview(text: str) -> bool:
    """
    Detect if user explicitly asks to preview/review the email before sending.
    We do NOT show preview by default.
    """
    lowered = (text or "").strip().lower()

    preview_phrases = [
        "preview",
        "show preview",
        "show me the email",
        "let me review",
        "review it first",
        "before sending",
        "قبل ما تبعت",
        "قبل ما تبعته",
        "وريني الايميل",
        "وريني الإيميل",
        "اعرض الايميل",
        "اعرض الإيميل",
        "عايز اشوف الايميل",
        "عايز أشوف الإيميل",
        "خليني اراجع",
        "خليني أراجع",
        "راجع الايميل",
        "راجع الإيميل",
        "بص على الايميل",
        "بص على الإيميل",
        "معاينة",
        "اعمل preview",
    ]

    return any(phrase in lowered for phrase in preview_phrases)


def format_email_preview(draft: dict[str, Any], lang: str = "ar") -> str:
    """
    User-facing email preview.
    Only call this if the user explicitly asked for preview/review.
    """
    body = draft.get("body") or ""
    body_preview = short_preview(body, 900)

    attachment_path = draft.get("attachment_path")
    attachment_text = attachment_path if attachment_path else "No attachment"

    if lang == "en":
        return (
            "Email preview:\n\n"
            f"To: {draft.get('to')}\n"
            f"Subject: {draft.get('subject')}\n"
            f"Attachment: {attachment_text}\n\n"
            f"Body preview:\n{body_preview}\n\n"
            "If this looks good, say: send it. To cancel, say: cancel."
        )

    return (
        "دي معاينة الإيميل:\n\n"
        f"To: {draft.get('to')}\n"
        f"Subject: {draft.get('subject')}\n"
        f"Attachment: {attachment_text}\n\n"
        f"Body preview:\n{body_preview}\n\n"
        "لو تمام قول: ابعته. ولو مش عايزه يتبعت قول: الغيه."
    )

# ============================================================
# FAST-PATH / FOLLOW-UP HELPERS
# ============================================================

def detect_simple_language(text: str) -> str:
    """
    Very small deterministic language detector for fast replies.
    Returns: ar|en|mixed|unknown
    """
    text = text or ""
    has_arabic = bool(re.search(r"[\u0600-\u06FF]", text))
    has_english = bool(re.search(r"[A-Za-z]", text))

    if has_arabic and has_english:
        return "mixed"
    if has_arabic:
        return "ar"
    if has_english:
        return "en"
    return "unknown"


def is_casual_greeting(text: str) -> bool:
    """
    Detect simple greetings / small talk that should not trigger tools or specialist agents.
    Strict by design so real tasks still go through the normal pipeline.
    """
    lowered = (text or "").strip().lower()
    if not lowered:
        return False

    greeting_phrases = {
        "hi",
        "hello",
        "hey",
        "yo",
        "good morning",
        "good afternoon",
        "good evening",
        "how are you",
        "how r u",
        "what's up",
        "whats up",
        "thanks",
        "thank you",
        "ازيك",
        "إزيك",
        "ازايك",
        "إزايك",
        "عامل ايه",
        "عامل إيه",
        "عامله ايه",
        "عاملة إيه",
        "السلام عليكم",
        "صباح الخير",
        "مساء الخير",
        "اهلا",
        "أهلا",
        "اهلين",
        "هاي",
        "هلا",
        "شكرا",
        "تسلم",
    }

    if lowered in greeting_phrases:
        return True

    cleaned = re.sub(r"[!?؟\.\s]+", " ", lowered).strip()
    return cleaned in greeting_phrases


def casual_greeting_reply(text: str) -> str:
    lang = detect_simple_language(text)

    lowered = (text or "").strip().lower()
    if lowered in {"thanks", "thank you", "شكرا", "تسلم"}:
        if lang == "en":
            return "You're welcome!"
        return "العفو يا أحمد."

    if lang == "en":
        return "Hello! How can I help you?"
    if lang == "ar":
        return "أهلًا يا أحمد، أقدر أساعدك في إيه؟"
    if lang == "mixed":
        return "أهلًا! How can I help you?"
    return "Hello! How can I help you?"


def latest_assistant_message_from_history(state: AgentState) -> str:
    """
    Get latest assistant message from chat history.
    Useful when API reconstructs state from frontend chat_history.
    """
    for msg in reversed(state.get("messages", [])):
        if msg.get("role") == "assistant" and msg.get("content"):
            return str(msg.get("content") or "")
    return ""


def user_wants_direct_artifact_export(text: str) -> bool:
    lowered = (text or "").lower()

    # Do not treat requests like "make pdf about risk of api" as exporting
    # previous content. They need content generation/reporting first.
    if user_is_asking_new_content_about_topic(lowered):
        return False

    if user_wants_pdf(lowered) or user_wants_docx(lowered) or user_wants_markdown(lowered):
        return True

    export_phrases = [
        "export it",
        "export them",
        "save it",
        "save them",
        "make it",
        "make them",
        "convert it",
        "convert them",
        "download it",
        "download them",
        "طلع",
        "طلعه",
        "طلعها",
        "صدر",
        "صدّر",
        "صدره",
        "صدرها",
        "احفظه",
        "احفظها",
        "حوله",
        "حوّله",
        "ملف",
    ]

    return any(p in lowered for p in export_phrases)


def is_general_question_request(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    question_markers = [
        "what is", "what are", "how does", "how do", "why", "explain", "tell me about",
        "risk of", "risks of", "security risk", "api risk", "api risks",
        "ما هو", "ما هي", "ايه", "إيه", "اشرح", "وضح", "يعني ايه", "مخاطر",
    ]
    action_markers = ["pdf", "report", "email", "send", "docx", "word", "save", "export", "ابعته", "ابعت"]
    return any(m in lowered for m in question_markers) and not any(a in lowered for a in action_markers)


def build_deterministic_decision(fallback: dict[str, Any], *, raw: str, goal: str, action: str, reason: str, depends: bool = False, resolved_to: str = "none", needs_user_input: bool = False, missing_info: Optional[list[str]] = None, needs_approval: bool = False, approval_type: str = "none", stop_condition: str = "respond") -> dict[str, Any]:
    return {
        **fallback,
        "understanding": reason,
        "resolved_goal": goal,
        "next_action": action,
        "reason": reason,
        "depends_on_previous_context": depends,
        "target_reference": {"phrase": raw, "resolved_to": resolved_to, "confidence": 1.0 if resolved_to != "unknown" else 0.5},
        "needs_user_input": needs_user_input,
        "missing_info": missing_info or [],
        "needs_approval": needs_approval,
        "approval_type": approval_type,
        "stop_condition": stop_condition,
    }

def orchestrator(state: AgentState) -> AgentState:
    if state.get("risk_level") == "blocked":
        return {
            **state,
            "next_action": "refuse",
            "orchestrator_decision": {
                "understanding": "Request blocked by safety gate.",
                "resolved_goal": "refuse",
                "next_action": "refuse",
                "reason": state.get("input_safety", {}).get("reason", "blocked"),
                "depends_on_previous_context": False,
                "target_reference": {
                    "phrase": "",
                    "resolved_to": "none",
                    "confidence": 1.0,
                },
                "needs_user_input": False,
                "missing_info": [],
                "needs_approval": False,
                "approval_type": "none",
                "stop_condition": "safe_refusal",
            },
        }

    payload = {
        **build_compact_payload(state),
        "context_snapshot": state.get("context_snapshot"),
        "input_safety": state.get("input_safety"),
        "output_safety": state.get("output_safety"),
    }

    fallback = {
        "understanding": "Fallback decision.",
        "resolved_goal": "final_response",
        "next_action": "final_response",
        "reason": "Could not parse orchestrator output.",
        "depends_on_previous_context": False,
        "target_reference": {
            "phrase": "",
            "resolved_to": "unknown",
            "confidence": 0.0,
        },
        "needs_user_input": False,
        "missing_info": [],
        "needs_approval": False,
        "approval_type": "none",
        "stop_condition": "respond",
    }

    allowed_next_actions = {
        "vision_analysis",
        "rag_answer",
        "web_research",
        "report_generation",
        "command_code_generation",
        "email_draft",
        "email_send",
        "artifact_export",
        "explain_more",
        "ask_user",
        "final_response",
        "refuse",
    }

    raw = (state.get("raw_input") or "").strip()
    pending = state.get("pending_approval") or {}

    # ============================================================
    # Deterministic overrides for fast/critical multi-turn cases
    # ============================================================

    # 0a. Simple greetings / small talk should be answered directly.
    if is_casual_greeting(raw):
        reply = casual_greeting_reply(raw)
        parsed = {
            **fallback,
            "understanding": "User sent a simple greeting or thanks.",
            "resolved_goal": "casual_greeting",
            "next_action": "final_response",
            "reason": "Simple message does not require specialist agents or tools.",
            "depends_on_previous_context": False,
            "target_reference": {
                "phrase": raw,
                "resolved_to": "none",
                "confidence": 1.0,
            },
            "needs_user_input": False,
            "missing_info": [],
            "needs_approval": False,
            "approval_type": "none",
            "stop_condition": "respond",
        }
        new_state = {
            **state,
            "orchestrator_decision": parsed,
            "next_action": "final_response",
            "latest_text_output": reply,
            "latest_answer": state.get("latest_answer") or reply,
            "latest_smalltalk_answer": reply,
            "step_count": state.get("step_count", 0) + 1,
        }
        return append_debug(new_state, "orchestrator", parsed)

    previous_content_available = bool(
        state.get("latest_answer")
        or state.get("latest_text_output")
        or state.get("latest_report")
        or state.get("report_draft")
        or state.get("generated_commands")
        or state.get("latest_artifact_id")
        or state.get("artifacts")
        or latest_assistant_message_from_history(state)
    )

    detected_email = find_email_in_text(raw)

    # 0b. New content/report request with target format.
    # Example: "make pdf about risk of api" means generate a report first,
    # then export it, not export the previous assistant message.
    if user_is_asking_new_content_about_topic(raw):
        topic = infer_topic_from_generation_request(raw)
        parsed = {
            **fallback,
            "understanding": f"User wants new content/report about: {topic}",
            "resolved_goal": "report_generation",
            "next_action": "report_generation",
            "reason": "Request contains a new topic plus an output format; generate content before exporting.",
            "depends_on_previous_context": False,
            "target_reference": {
                "phrase": raw,
                "resolved_to": "new_topic",
                "confidence": 0.95,
            },
            "needs_user_input": False,
            "missing_info": [],
            "needs_approval": False,
            "approval_type": "none",
            "stop_condition": "report_or_artifact_ready",
        }
        state = {
            **state,
            "current_topic": topic,
            "desired_output_format": (
                "pdf" if user_wants_pdf(raw) else
                "docx" if user_wants_docx(raw) else
                "markdown" if user_wants_markdown(raw) else
                "report"
            ),
            "task_queue": ([{"action": "email_draft", "to": detected_email}] if detected_email and user_wants_email_followup(raw) else []),
        }

    # 0c. Generic email follow-up with detected recipient.
    # This must run BEFORE artifact export, because phrases like
    # "send this PDF to email x@y.com" contain the word PDF but are email tasks.
    elif detected_email and user_wants_email_followup(raw) and previous_content_available:
        parsed = {
            **fallback,
            "understanding": "User wants to email existing content/artifact to the detected recipient.",
            "resolved_goal": "draft_email",
            "next_action": "email_draft",
            "reason": "Detected recipient email and previous content/artifact exist; auto-generate subject/body and attach latest file.",
            "depends_on_previous_context": True,
            "target_reference": {
                "phrase": raw,
                "resolved_to": "latest_relevant_content_or_artifact",
                "confidence": 1.0,
            },
            "needs_user_input": False,
            "missing_info": [],
            "needs_approval": True,
            "approval_type": "send_email",
            "stop_condition": "email_draft_ready",
        }

    # 0c. Direct artifact/export follow-up.
    # Examples: make them PDF, export it as PDF, طلعه PDF, احفظه markdown.
    elif user_wants_direct_artifact_export(raw) and not user_wants_email_followup(raw) and previous_content_available:
        parsed = {
            **fallback,
            "understanding": "User wants to export the previous content as an artifact.",
            "resolved_goal": "artifact_export",
            "next_action": "artifact_export",
            "reason": "Export request refers to previous assistant content.",
            "depends_on_previous_context": True,
            "target_reference": {
                "phrase": raw,
                "resolved_to": "latest_answer",
                "confidence": 0.95,
            },
            "needs_user_input": False,
            "missing_info": [],
            "needs_approval": False,
            "approval_type": "none",
            "stop_condition": "artifact_ready",
        }

    # 0c. User supplied a new recipient while a draft is pending.
    # This means: reuse the same latest content/draft, but create a new draft for the new recipient.
    elif pending.get("type") == "confirm_send_email" and find_email_in_text(raw):
        parsed = {
            **fallback,
            "understanding": "User provided a new recipient email for the pending/previous content.",
            "resolved_goal": "draft_email",
            "next_action": "email_draft",
            "reason": "An email draft exists and the user supplied another recipient address.",
            "depends_on_previous_context": True,
            "target_reference": {
                "phrase": raw,
                "resolved_to": "pending_email_or_latest_content",
                "confidence": 1.0,
            },
            "needs_user_input": False,
            "missing_info": [],
            "needs_approval": True,
            "approval_type": "send_email",
            "stop_condition": "email_draft_ready",
        }

    # 1. User confirmed pending email send.
    elif pending.get("type") == "confirm_send_email" and is_yes_confirmation(raw):
        parsed = {
            **fallback,
            "understanding": "User confirmed pending email sending.",
            "resolved_goal": "send_email",
            "next_action": "email_send",
            "reason": "Pending email approval exists and user explicitly confirmed sending.",
            "depends_on_previous_context": True,
            "target_reference": {
                "phrase": raw,
                "resolved_to": "pending_approval",
                "confidence": 1.0,
            },
            "needs_user_input": False,
            "missing_info": [],
            "needs_approval": False,
            "approval_type": "send_email",
            "stop_condition": "email_sent_or_error",
        }

    # 2. User cancelled pending email send.
    elif pending.get("type") == "confirm_send_email" and is_no_confirmation(raw):
        parsed = {
            **fallback,
            "understanding": "User cancelled pending email sending.",
            "resolved_goal": "cancel_email_send",
            "next_action": "email_draft",
            "reason": "Pending email approval exists and user declined or cancelled sending.",
            "depends_on_previous_context": True,
            "target_reference": {
                "phrase": raw,
                "resolved_to": "pending_approval",
                "confidence": 1.0,
            },
            "needs_user_input": False,
            "missing_info": [],
            "needs_approval": False,
            "approval_type": "none",
            "stop_condition": "email_send_cancelled",
        }

    # 3. User explicitly asked to preview/review the pending email draft.
    elif pending.get("type") == "confirm_send_email" and user_wants_email_preview(raw):
        parsed = {
            **fallback,
            "understanding": "User asked to preview the pending email draft.",
            "resolved_goal": "preview_email_draft",
            "next_action": "email_draft",
            "reason": "Pending email draft exists and user requested preview/review before sending.",
            "depends_on_previous_context": True,
            "target_reference": {
                "phrase": raw,
                "resolved_to": "pending_approval",
                "confidence": 1.0,
            },
            "needs_user_input": False,
            "missing_info": [],
            "needs_approval": True,
            "approval_type": "send_email",
            "stop_condition": "email_preview_shown",
        }

    # 4. System was waiting for recipient email, and user provided only/mostly an email.
    elif pending.get("type") == "need_email_recipient" and find_email_in_text(raw):
        parsed = {
            **fallback,
            "understanding": "User provided recipient email for pending email draft.",
            "resolved_goal": "draft_email",
            "next_action": "email_draft",
            "reason": "Pending email recipient was requested and user provided an email address.",
            "depends_on_previous_context": True,
            "target_reference": {
                "phrase": raw,
                "resolved_to": "pending_approval",
                "confidence": 1.0,
            },
            "needs_user_input": False,
            "missing_info": [],
            "needs_approval": True,
            "approval_type": "send_email",
            "stop_condition": "email_draft_ready",
        }

    # 4b. User wants to email existing content/report/artifact.
    # If recipient is missing, email_agent will ask only for the recipient email.
    elif user_wants_email_followup(raw) and previous_content_available:
        parsed = {
            **fallback,
            "understanding": "User wants to email the previous generated content.",
            "resolved_goal": "draft_email",
            "next_action": "email_draft",
            "reason": "Email request refers to previous report/answer/artifact.",
            "depends_on_previous_context": True,
            "target_reference": {
                "phrase": raw,
                "resolved_to": "latest_artifact_or_answer",
                "confidence": 0.95,
            },
            "needs_user_input": not bool(find_email_in_text(raw)),
            "missing_info": [] if find_email_in_text(raw) else ["recipient_email"],
            "needs_approval": True,
            "approval_type": "send_email",
            "stop_condition": "email_draft_ready_or_waiting_for_recipient",
        }

    # 4c. General knowledge / explanation question.
    elif is_general_question_request(raw):
        parsed = build_deterministic_decision(
            fallback,
            raw=raw,
            goal="answer_question",
            action="explain_more",
            reason="User asked a direct question; answer it instead of asking for report details.",
            depends=False,
            resolved_to="raw_input",
            stop_condition="respond",
        )

    # 5. Safety gate asked for clarification.
    elif state.get("risk_level") == "needs_clarification" or state.get("next_action") == "ask_user":
        question = (
            state.get("latest_text_output")
            or state.get("input_safety", {}).get("clarifying_question")
            or "محتاج توضيح بسيط قبل ما أكمل."
        )

        parsed = {
            **fallback,
            "understanding": "Request needs clarification before continuing.",
            "resolved_goal": "ask_user",
            "next_action": "ask_user",
            "reason": state.get("input_safety", {}).get("reason", "Clarification required."),
            "depends_on_previous_context": False,
            "target_reference": {
                "phrase": raw,
                "resolved_to": "unknown",
                "confidence": 0.0,
            },
            "needs_user_input": True,
            "missing_info": [question],
            "needs_approval": False,
            "approval_type": "none",
            "stop_condition": "waiting_for_user_clarification",
        }

    # 6. Normal path: let the LLM orchestrator decide.
    else:
        try:
            parsed = llm_json(
                orchestrator_llm,
                ORCHESTRATOR_PROMPT,
                payload,
                fallback,
            )

            if not isinstance(parsed, dict):
                parsed = fallback

        except Exception as exc:
            parsed = fallback
            state = add_error(state, "orchestrator", exc)

    # ============================================================
    # Normalize orchestrator output
    # ============================================================

    if not isinstance(parsed, dict):
        parsed = fallback

    next_action = parsed.get("next_action") or "final_response"

    if next_action not in allowed_next_actions:
        parsed = {
            **parsed,
            "next_action": "final_response",
            "reason": (
                parsed.get("reason", "")
                + f" Invalid next_action returned by orchestrator: {next_action}."
            ).strip(),
        }
        next_action = "final_response"

    parsed.setdefault("understanding", fallback["understanding"])
    parsed.setdefault("resolved_goal", fallback["resolved_goal"])
    parsed.setdefault("reason", fallback["reason"])
    parsed.setdefault("depends_on_previous_context", False)
    parsed.setdefault(
        "target_reference",
        {
            "phrase": "",
            "resolved_to": "unknown",
            "confidence": 0.0,
        },
    )
    parsed.setdefault("needs_user_input", False)
    parsed.setdefault("missing_info", [])
    parsed.setdefault("needs_approval", False)
    parsed.setdefault("approval_type", "none")
    parsed.setdefault("stop_condition", "respond")

    new_state = {
        **state,
        "orchestrator_decision": parsed,
        "next_action": next_action,
        "step_count": state.get("step_count", 0) + 1,
    }

    return append_debug(new_state, "orchestrator", parsed)

def context_resolver(state: AgentState) -> AgentState:
    # Deterministic follow-up routes should not be blocked by the LLM resolver.
    # For example: after "make it PDF", the next message
    # "send it as email to x@y.com" should go straight to email_agent using
    # the latest artifact/content, not ask for subject/body.
    decision = state.get("orchestrator_decision", {}) or {}
    if state.get("next_action") in ["email_draft", "email_send", "artifact_export"]:
        parsed = {
            "resolved": True,
            "phrase": state.get("raw_input", ""),
            "resolved_to": decision.get("target_reference", {}).get("resolved_to") or "latest_artifact_or_answer",
            "resolved_value_summary": "Resolved deterministically from latest report/artifact/answer.",
            "confidence": 0.98,
            "needs_user_confirmation": False,
            "question_to_user": "",
        }
        return append_debug(
            {**state, "context_resolution": parsed},
            "context_resolver",
            parsed,
        )

    payload = {
        **build_compact_payload(state),
        "orchestrator_decision": state.get("orchestrator_decision"),
    }

    fallback = {
        "resolved": False,
        "phrase": "",
        "resolved_to": "unknown",
        "resolved_value_summary": "",
        "confidence": 0.0,
        "needs_user_confirmation": False,
        "question_to_user": "",
    }

    try:
        parsed = llm_json(orchestrator_llm, CONTEXT_RESOLVER_PROMPT, payload, fallback)
    except Exception as exc:
        parsed = fallback
        state = add_error(state, "context_resolver", exc)

    # If resolver is uncertain and the orchestrator depends on previous context, ask.
    decision = state.get("orchestrator_decision", {})
    depends = decision.get("depends_on_previous_context", False)

    if depends and parsed.get("needs_user_confirmation") and parsed.get("confidence", 0.0) < 0.65:
        question = parsed.get("question_to_user") or "تقصد أنهي حاجة بالظبط؟"
        new_state = {
            **state,
            "context_resolution": parsed,
            "next_action": "ask_user",
            "latest_text_output": question,
            "latest_answer": question,
        }
        return append_debug(new_state, "context_resolver", parsed)

    # Update current topic if resolver found a meaningful topic.
    resolved_to = parsed.get("resolved_to")
    if resolved_to in ["latest_image_analysis", "current_topic"] and parsed.get("resolved_value_summary"):
        state = {
            **state,
            "current_topic": state.get("current_topic") or parsed.get("resolved_value_summary"),
        }

    new_state = {
        **state,
        "context_resolution": parsed,
    }

    return append_debug(new_state, "context_resolver", parsed)


def task_planner(state: AgentState) -> AgentState:
    payload = {
        **build_compact_payload(state),
        "orchestrator_decision": state.get("orchestrator_decision"),
        "context_resolution": state.get("context_resolution"),
        "next_action": state.get("next_action"),
    }

    fallback = {
        "goal": state.get("next_action") or "final_response",
        "steps": [
            {
                "step": 1,
                "action": state.get("next_action") or "final_response",
                "agent": state.get("next_action") or "response_composer",
                "input_source": "raw_input",
                "requires_tool": False,
                "expected_output": "next response",
            }
        ],
        "approval_gates": [],
        "max_steps": state.get("max_steps", 8),
        "stop_condition": "respond",
    }

    try:
        parsed = llm_json(orchestrator_llm, TASK_PLANNER_PROMPT, payload, fallback)
    except Exception as exc:
        parsed = fallback
        state = add_error(state, "task_planner", exc)

    new_state = {
        **state,
        "plan": parsed,
    }

    return append_debug(new_state, "task_planner", parsed)


# ============================================================
# SPECIALIST AGENTS
# ============================================================

def vision_agent(state: AgentState) -> AgentState:
    image_path = state.get("image_path")
    raw = state.get("raw_input", "")

    validation_error = validate_image_file(image_path)

    if validation_error:
        text = validation_error

        output = {
            "type": "vision_analysis",
            "text": text,
            "confidence": 0.0,
            "metadata": {
                "missing_or_invalid_image": True,
                "image_path": image_path,
            },
        }

        return {
            **state,
            "latest_agent_output": output,
            "latest_text_output": text,
            "latest_answer": text,
        }

    try:
        data_url = image_to_data_url(image_path)

        fallback = {
            "language": state.get("language", "unknown"),
            "image_type": "unknown",
            "topic": "image analysis",
            "visible_text": "",
            "summary": "",
            "explanation": "",
            "uncertainties": ["Could not parse structured vision output."],
            "confidence": 0.5,
            "user_response": "",
        }

        response = vision_llm.invoke(
            [
                SystemMessage(content=f"{GLOBAL_POLICY}\n\n{VISION_AGENT_PROMPT}"),
                HumanMessage(
                    content=[
                        {
                            "type": "text",
                            "text": (
                                "User text:\n"
                                f"{raw or 'Analyze this image.'}\n\n"
                                "Return strict JSON only using the required schema."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url},
                        },
                    ]
                ),
            ]
        )

        raw_response = response.content or ""

        parsed = extract_json(raw_response, fallback)

        if not isinstance(parsed, dict):
            parsed = fallback

        analysis = normalize_vision_json(
            parsed,
            image_path=image_path,
            raw_response=raw_response,
        )

        text = (
            analysis.get("user_response")
            or analysis.get("explanation")
            or analysis.get("summary")
            or "تم تحليل الصورة."
        )

        output = {
            "type": "vision_analysis",
            "content": analysis,
            "text": text,
            "confidence": analysis["confidence"],
            "metadata": {
                "image_path": image_path,
                "image_type": analysis.get("image_type"),
                "topic": analysis.get("topic"),
                "uncertainties_count": len(analysis.get("uncertainties", [])),
            },
        }

        new_state = {
            **state,
            "latest_image_analysis": analysis,
            "latest_agent_output": output,
            "latest_text_output": text,
            "latest_answer": text,
            "current_topic": state.get("current_topic") or analysis.get("topic"),
        }

        return append_debug(
            new_state,
            "vision_agent",
            {
                "topic": analysis.get("topic"),
                "image_type": analysis.get("image_type"),
                "confidence": analysis.get("confidence"),
                "uncertainties": analysis.get("uncertainties", []),
            },
        )

    except Exception as exc:
        text = f"حصلت مشكلة وأنا بحلل الصورة: {exc}"

        output = {
            "type": "vision_analysis",
            "text": text,
            "confidence": 0.0,
            "metadata": {
                "error": str(exc),
                "image_path": image_path,
            },
        }

        return add_error(
            {
                **state,
                "latest_agent_output": output,
                "latest_text_output": text,
                "latest_answer": text,
            },
            "vision_agent",
            exc,
        )

def optimize_rag_query(state: AgentState) -> str:
    """
    Rewrite user question into a better vector search query.

    This is not the final answer.
    It only improves retrieval.
    """
    raw_question = state.get("raw_input", "") or ""

    current_topic = state.get("current_topic")
    context_resolution = state.get("context_resolution", {})
    input_metadata = state.get("input_metadata", {})

    has_reference = bool(input_metadata.get("has_reference"))
    resolved_summary = context_resolution.get("resolved_value_summary")

    if resolved_summary:
        context_hint = resolved_summary
    elif current_topic and has_reference:
        context_hint = current_topic
    else:
        context_hint = ""

    recent_history = format_recent_messages(
        state.get("messages", []),
        limit=4,
    )

    payload = f"""
Recent conversation:
{recent_history}

Current topic:
{current_topic}

Resolved reference:
{context_hint}

User question:
{raw_question}

Optimized retrieval query:
""".strip()

    try:
        rewritten = llm_text(
            general_llm,
            RAG_QUERY_REWRITER_PROMPT,
            payload,
        )

        query = rewritten.strip().splitlines()[0].strip()
        query = query.strip('"').strip("'").strip()

        if not query:
            return raw_question

        if len(query) > 220:
            return raw_question

        return query

    except Exception:
        if context_hint:
            return f"{context_hint} {raw_question}".strip()

        return raw_question
def rag_source_label(doc: dict[str, Any]) -> str:
    """
    Return citation label using only file name and page.

    Examples:
    - file.pdf, page 3
    - notes.docx, page N/A
    """
    metadata = doc.get("metadata") or {}

    filename = (
        metadata.get("source_name")
        or metadata.get("source")
        or doc.get("title")
        or doc.get("path")
        or "unknown"
    )

    # If title is like "file.pdf - page 3", prefer clean source_name from metadata.
    filename = str(filename).split("/")[-1].split("\\")[-1]

    page = metadata.get("page")
    page_text = str(page) if page is not None else "N/A"

    return f"{filename}, page {page_text}"


def format_rag_sources_for_prompt(docs: list[dict[str, Any]], limit: int = 6) -> str:
    """
    Format RAG docs for the answer model with explicit source labels.

    The answer model will cite only:
    (Source: filename, page X)
    """
    if not docs:
        return "No sources available."

    chunks: list[str] = []

    for idx, doc in enumerate(docs[:limit], start=1):
        source_label = rag_source_label(doc)
        content = doc.get("content") or doc.get("snippet") or ""

        chunks.append(
            f"[{idx}]\n"
            f"Source: {source_label}\n"
            f"Content:\n{short_preview(content, 1400)}"
        )

    return "\n\n".join(chunks)


def rerank_rag_docs(
    *,
    query: str,
    docs: list[dict[str, Any]],
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """
    LLM-based reranker.

    It receives more retrieved chunks, asks the LLM to rank them by relevance,
    then returns only the best top_k chunks.

    Safe fallback:
    - If reranking fails, keep original order.
    """
    if not docs:
        return []

    if len(docs) <= top_k:
        return docs

    candidates = []

    for idx, doc in enumerate(docs):
        candidates.append(
            {
                "index": idx,
                "source": rag_source_label(doc),
                "snippet": short_preview(doc.get("content") or doc.get("snippet") or "", 700),
            }
        )

    fallback = {
        "ranked_indices": list(range(min(top_k, len(docs)))),
        "reason": "fallback original retrieval order",
    }

    try:
        parsed = llm_json(
            general_llm,
            """
You are a RAG reranker.

Your job:
- Rank retrieved document chunks by relevance to the user query.
- Prefer chunks that directly answer the query.
- Prefer specific chunks over generic chunks.
- Do not answer the user.
- Return strict JSON only.

Return format:
{
  "ranked_indices": [0, 2, 1],
  "reason": "short reason"
}
""",
            {
                "query": query,
                "candidates": candidates,
                "top_k": top_k,
            },
            fallback,
        )

        ranked_indices = parsed.get("ranked_indices", [])

        if not isinstance(ranked_indices, list):
            return docs[:top_k]

        reranked_docs = []
        seen = set()

        for item in ranked_indices:
            try:
                i = int(item)
            except (TypeError, ValueError):
                continue

            if 0 <= i < len(docs) and i not in seen:
                reranked_docs.append(docs[i])
                seen.add(i)

            if len(reranked_docs) >= top_k:
                break

        # Fill if model returned fewer valid indices.
        if len(reranked_docs) < top_k:
            for i, doc in enumerate(docs):
                if i not in seen:
                    reranked_docs.append(doc)
                    seen.add(i)

                if len(reranked_docs) >= top_k:
                    break

        return reranked_docs

    except Exception:
        return docs[:top_k]
def rag_agent(state: AgentState) -> AgentState:
    """
    RAG Agent flow:

    User question
    -> query rewrite
    -> retrieve more chunks from Qdrant/Ollama embeddings
    -> rerank retrieved chunks
    -> answer using reranked context only
    -> cite file name and page
    -> store sources and latest answer in state
    """
    query = optimize_rag_query(state)

    try:
        # Retrieve more than we need, then rerank down to the best chunks.
        retrieved_docs = retrieve_from_documents(query, k=12)

        if not retrieved_docs:
            text = "المستندات المتاحة مش كافية للإجابة بثقة على السؤال ده."

            output = {
                "type": "rag_answer",
                "status": "insufficient_context",
                "text": text,
                "confidence": 0.3,
                "metadata": {
                    "query": query,
                    "docs_count": 0,
                    "recommended_next_action": "web_research",
                },
            }

            return {
                **state,
                "retrieved_docs": [],
                "latest_agent_output": output,
                "latest_text_output": text,
                "latest_answer": text,
            }

        if retrieved_docs and retrieved_docs[0].get("metadata", {}).get("error"):
            text = f"حصلت مشكلة في RAG retrieval: {retrieved_docs[0].get('content')}"

            output = {
                "type": "rag_answer",
                "status": "retrieval_error",
                "text": text,
                "confidence": 0.0,
                "metadata": {
                    "query": query,
                    "docs_count": 0,
                },
            }

            return {
                **state,
                "retrieved_docs": [],
                "latest_agent_output": output,
                "latest_text_output": text,
                "latest_answer": text,
            }

        docs = rerank_rag_docs(
            query=query,
            docs=retrieved_docs,
            top_k=5,
        )

        context = format_rag_sources_for_prompt(docs, limit=5)

        answer_payload = f"""
User question:
{state.get("raw_input", "")}

Optimized retrieval query:
{query}

Retrieved and reranked document context:
{context}

Instructions:
Answer the user using only the retrieved document context.
Every important factual point must include a citation using only file name and page.
Citation format:
(Source: filename, page X)

If the retrieved context is insufficient, say so clearly.
""".strip()

        text = llm_text(
            general_llm,
            RAG_ANSWER_AGENT_PROMPT,
            answer_payload,
        )

        output = {
            "type": "rag_answer",
            "status": "answered",
            "text": text,
            "confidence": 0.82,
            "metadata": {
                "query": query,
                "retrieved_docs_count": len(retrieved_docs),
                "reranked_docs_count": len(docs),
                "sources": [
                    {
                        "file": (
                            (d.get("metadata") or {}).get("source_name")
                            or (d.get("metadata") or {}).get("source")
                            or d.get("title")
                        ),
                        "page": (d.get("metadata") or {}).get("page"),
                        "file_type": (d.get("metadata") or {}).get("file_type"),
                    }
                    for d in docs
                ],
            },
        }

        return {
            **state,
            "retrieved_docs": docs,
            "sources": state.get("sources", []) + docs,
            "latest_agent_output": output,
            "latest_text_output": text,
            "latest_answer": text,
        }

    except Exception as exc:
        text = f"حصل خطأ في RAG Agent: {exc}"

        output = {
            "type": "rag_answer",
            "status": "error",
            "text": text,
            "confidence": 0.0,
            "metadata": {
                "query": query,
                "error": str(exc),
            },
        }

        return add_error(
            {
                **state,
                "latest_agent_output": output,
                "latest_text_output": text,
                "latest_answer": text,
            },
            "rag_agent",
            exc,
        )
def optimize_web_query(state: AgentState) -> str:
    """
    Build a clear research topic from user input and resolved context.
    """
    raw = (state.get("raw_input") or "").strip()
    resolution = state.get("context_resolution") or {}
    decision = state.get("orchestrator_decision") or {}
    current_topic = state.get("current_topic")

    if resolution.get("resolved") and resolution.get("resolved_value_summary"):
        base_query = resolution.get("resolved_value_summary")
    elif current_topic and (
        state.get("input_metadata", {}).get("has_reference")
        or decision.get("depends_on_previous_context")
        or user_wants_report(raw)
    ):
        base_query = current_topic
    else:
        base_query = raw or current_topic or "research topic"

    base_query = re.sub(r"\b(make|do|perform|web search|search web|search online|look up|latest|current|sources|pdf|report|email|send|about|on|and|then|please)\b", " ", str(base_query), flags=re.IGNORECASE)
    base_query = re.sub(r"\s+", " ", base_query).strip(" .؟?،,")
    if len(base_query) > 180:
        base_query = base_query[:180].rsplit(" ", 1)[0].strip()

    return base_query or raw or current_topic or "research topic"


def expand_web_queries(query: str) -> list[str]:
    """
    Query expansion for better research coverage.
    Keeps this deterministic so fast routes do not need an extra LLM call.
    """
    q = (query or "research topic").strip()
    lowered = q.lower()
    queries = [q]

    if "api" in lowered or "openapi" in lowered or "open api" in lowered:
        queries.extend([
            f"{q} benefits business value integration efficiency",
            f"{q} security risks authentication authorization rate limiting",
            "OWASP API Security Top 10 broken object level authorization excessive data exposure",
            "public API third party dependency reliability security risks",
        ])
    elif any(x in lowered for x in ["ai", "artificial intelligence", "llm", "machine learning"]):
        queries.extend([
            f"{q} benefits risks security",
            f"{q} governance privacy data leakage",
            f"{q} enterprise security best practices",
        ])
    elif any(x in lowered for x in ["vulnerability", "security", "risk", "risks", "cyber"]):
        queries.extend([
            f"{q} risk analysis",
            f"{q} mitigation best practices",
            f"{q} recent guidance",
        ])
    else:
        queries.extend([
            f"{q} benefits risks",
            f"{q} best practices",
            f"{q} security considerations",
        ])

    deduped: list[str] = []
    seen: set[str] = set()
    for item in queries:
        item = re.sub(r"\s+", " ", item).strip()
        key = item.lower()
        if item and key not in seen:
            deduped.append(item)
            seen.add(key)
        if len(deduped) >= 5:
            break
    return deduped


def filter_web_sources(sources: list[dict[str, Any]], *, min_content_chars: int = 35) -> list[dict[str, Any]]:
    """
    Remove weak/duplicate/error web sources before sending to the LLM.
    """
    cleaned: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for source in sources:
        metadata = source.get("metadata") or {}
        raw = metadata.get("raw") or {}

        if raw.get("error") or metadata.get("error"):
            continue

        title = (source.get("title") or "").strip()
        url = (source.get("url") or "").strip()
        content = (source.get("content") or source.get("snippet") or "").strip()

        if not title and not url:
            continue

        if len(content) < min_content_chars:
            continue

        if url:
            normalized_url = url.rstrip("/").lower()
            if normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)

        cleaned.append(source)

    return cleaned


def estimate_web_confidence(sources: list[dict[str, Any]]) -> float:
    """
    Simple confidence estimate based on usable source count and content quality.
    """
    if not sources:
        return 0.2

    usable_count = len(sources)
    avg_content_len = sum(len((s.get("content") or s.get("snippet") or "")) for s in sources) / usable_count

    confidence = 0.45
    if usable_count >= 3:
        confidence += 0.15
    if usable_count >= 6:
        confidence += 0.15
    if avg_content_len >= 250:
        confidence += 0.10
    if avg_content_len >= 700:
        confidence += 0.10
    return min(confidence, 0.9)


def format_web_sources_for_prompt(sources: list[dict[str, Any]], limit: int = 10) -> str:
    """
    Format web sources with stable source numbers.
    """
    if not sources:
        return "No usable web sources available."

    chunks: list[str] = []
    for idx, source in enumerate(sources[:limit], start=1):
        title = source.get("title") or "Untitled"
        url = source.get("url") or ""
        content = source.get("content") or source.get("snippet") or ""
        chunks.append(
            f"[Web Source {idx}]\n"
            f"Title: {title}\n"
            f"URL: {url}\n"
            f"Content:\n{short_preview(content, 1800)}"
        )
    return "\n\n".join(chunks)


def format_sources_section(sources: list[dict[str, Any]], limit: int = 10) -> str:
    if not sources:
        return "## Sources\n- No usable web sources were returned."
    lines = ["## Sources"]
    for idx, source in enumerate(sources[:limit], start=1):
        title = source.get("title") or "Untitled"
        url = source.get("url") or ""
        if url:
            lines.append(f"{idx}. {title} — {url}")
        else:
            lines.append(f"{idx}. {title}")
    return "\n".join(lines)


def fallback_web_synthesis(query: str, sources: list[dict[str, Any]], lang: str) -> str:
    """
    Deterministic fallback when the LLM provider fails.
    """
    source_section = format_sources_section(sources)
    if lang == "ar":
        bullets = []
        for s in sources[:5]:
            title = s.get("title") or "مصدر"
            content = short_preview(s.get("content") or s.get("snippet") or "", 280)
            bullets.append(f"- **{title}**: {content}")
        return (
            f"# نتائج بحث الويب: {query}\n\n"
            "## ملخص سريع\n"
            "جمعت مصادر ويب مرتبطة بالموضوع. مزود الموديل لم يرجع تلخيصًا تفصيليًا الآن، "
            "لذلك هذا ملخص آمن مبني على عناوين ومقتطفات المصادر المتاحة.\n\n"
            "## أبرز النتائج من المصادر\n"
            + ("\n".join(bullets) if bullets else "- لا توجد مقتطفات كافية.")
            + "\n\n"
            "## القيود\n"
            "- راجع المصادر الأصلية قبل اتخاذ قرارات إنتاجية أو أمنية مهمة.\n\n"
            f"{source_section}"
        )
    bullets = []
    for s in sources[:5]:
        title = s.get("title") or "Source"
        content = short_preview(s.get("content") or s.get("snippet") or "", 280)
        bullets.append(f"- **{title}**: {content}")
    return (
        f"# Web research findings: {query}\n\n"
        "## Quick summary\n"
        "I gathered relevant web sources. The model provider did not return a full synthesis right now, "
        "so this fallback summary is based on available source titles and snippets.\n\n"
        "## Findings from available sources\n"
        + ("\n".join(bullets) if bullets else "- Not enough source snippets were available.")
        + "\n\n"
        "## Limitations\n"
        "- Review the original sources before using this for production or security decisions.\n\n"
        f"{source_section}"
    )


def web_research_agent(state: AgentState) -> AgentState:
    query = optimize_web_query(state)
    lang = preferred_response_language(state)
    expanded_queries = expand_web_queries(query)

    try:
        all_raw_results: list[dict[str, Any]] = []
        for q in expanded_queries:
            results = tavily_search(q, max_results=6)
            for item in results:
                item = dict(item)
                item.setdefault("query", q)
                all_raw_results.append(item)

        raw_sources = register_sources_from_web(all_raw_results)
        sources = filter_web_sources(raw_sources)

        if not sources:
            text = (
                "I couldn't retrieve enough usable web sources. Check TAVILY_API_KEY in .env or try a more specific query."
                if lang == "en"
                else "مقدرتش أجيب مصادر ويب كافية. اتأكد إن TAVILY_API_KEY متظبط في .env أو جرّب query أوضح."
            )

            output = {
                "type": "web_research",
                "status": "insufficient_web_sources",
                "text": text,
                "confidence": 0.2,
                "metadata": {
                    "query": query,
                    "expanded_queries": expanded_queries,
                    "raw_results_count": len(all_raw_results),
                    "raw_sources_count": len(raw_sources),
                    "usable_sources_count": 0,
                    "recommended_next_action": "ask_user",
                },
            }

            return {
                **state,
                "web_sources": [],
                "web_findings": "",
                "latest_agent_output": output,
                "latest_text_output": text,
                "latest_answer": text,
            }

        # Keep top usable sources after dedupe.
        sources = sources[:10]
        sources_prompt = format_web_sources_for_prompt(sources, limit=10)
        sources_section = format_sources_section(sources, limit=10)

        synthesis_prompt = f"""
Research topic:
{query}

Expanded searches used:
{json.dumps(expanded_queries, ensure_ascii=False)}

Usable web sources:
{sources_prompt}

Write a strong, professional research answer in the user's language.

Required structure:
# Web Research: {query}

## Executive Summary
- 3-5 concise bullets.

## Benefits / Opportunities
- Use evidence from the sources. Mention source titles inline when relevant.

## Risks / Concerns
- Use evidence from the sources. Mention source titles inline when relevant.

## Practical Recommendations
- Actionable defensive/business recommendations.

## Limitations
- Mention if sources are limited, generic, or not enough for high-confidence claims.

{sources_section}

Do not output bare citation numbers like [1] without listing the source.
Do not invent facts not supported by the provided source snippets.
""".strip()

        text = llm_text(
            general_llm,
            WEB_RESEARCH_AGENT_PROMPT,
            synthesis_prompt,
        )

        if not text or "## Sources" not in text:
            if text:
                text = text.rstrip() + "\n\n" + sources_section
            else:
                text = fallback_web_synthesis(query, sources, lang)

        confidence = estimate_web_confidence(sources)

        output = {
            "type": "web_research",
            "status": "findings_ready",
            "text": text,
            "confidence": confidence,
            "metadata": {
                "query": query,
                "expanded_queries": expanded_queries,
                "raw_results_count": len(all_raw_results),
                "usable_sources_count": len(sources),
                "sources": [
                    {
                        "title": s.get("title"),
                        "url": s.get("url"),
                        "score": s.get("score"),
                    }
                    for s in sources
                ],
            },
        }

        return {
            **state,
            "sources": state.get("sources", []) + sources,
            "web_sources": sources,
            "web_findings": text,
            "latest_agent_output": output,
            "latest_text_output": text,
            "latest_answer": text,
            "current_topic": state.get("current_topic") or query,
            "active_task": state.get("active_task") or {
                "type": "research",
                "status": "findings_ready",
                "topic": query,
                "sources_count": len(sources),
            },
        }

    except Exception as exc:
        text = (
            f"I couldn't complete web research because of a tool/provider error: {exc}"
            if lang == "en"
            else f"معرفتش أكمل بحث الويب بسبب خطأ في الأداة/الموديل: {exc}"
        )

        output = {
            "type": "web_research",
            "status": "error",
            "text": text,
            "confidence": 0.0,
            "metadata": {
                "query": query,
                "expanded_queries": expanded_queries,
                "error": str(exc),
            },
        }

        return add_error(
            {
                **state,
                "latest_agent_output": output,
                "latest_text_output": text,
                "latest_answer": text,
            },
            "web_research_agent",
            exc,
        )

# ============================================================
# EXPLANATION HELPERS
# ============================================================

def detect_explanation_target_type(text: str) -> str:
    """
    Detect what the user wants explained.
    Returns: commands|report|image_analysis|answer|artifact|auto
    """
    lowered = (text or "").lower()

    if any(x in lowered for x in ["command", "commands", "code", "script", "الكود", "كود", "الأوامر", "الاوامر", "الأمر", "الامر"]):
        return "commands"

    if any(x in lowered for x in ["report", "تقرير", "ريبورت"]):
        return "report"

    if any(x in lowered for x in ["image", "diagram", "screenshot", "الصورة", "الدياجرام", "السكرين", "تحليل الصورة"]):
        return "image_analysis"

    if any(x in lowered for x in ["answer", "response", "reply", "الإجابة", "الاجابة", "الرد"]):
        return "answer"

    if any(x in lowered for x in ["artifact", "file", "الملف", "الفايل"]):
        return "artifact"

    return "auto"


def resolve_explanation_target(state: AgentState) -> dict[str, Any]:
    """
    Resolve the best content to explain.
    """
    raw = state.get("raw_input", "") or ""
    target_type = detect_explanation_target_type(raw)
    artifacts = state.get("artifacts", [])

    if target_type == "commands":
        content = (
            state.get("generated_commands")
            or state.get("latest_code")
            or state.get("latest_commands")
        )
        if content:
            return {
                "content": content,
                "type": "commands",
                "source": "latest_code_or_commands",
            }

    if target_type == "report":
        content = state.get("report_draft") or state.get("latest_report")
        if content:
            return {
                "content": content,
                "type": "report",
                "source": "latest_report",
            }

    if target_type == "image_analysis":
        image = state.get("latest_image_analysis") or {}
        if image:
            content = (
                image.get("explanation")
                or image.get("summary")
                or image.get("user_response")
                or str(image)
            )
            return {
                "content": content,
                "type": "image_analysis",
                "source": "latest_image_analysis",
            }

    if target_type == "answer":
        content = state.get("latest_answer") or state.get("latest_text_output")
        if content:
            return {
                "content": content,
                "type": "answer",
                "source": "latest_answer",
            }

    if target_type == "artifact":
        target_artifact = find_artifact_by_id(
            artifacts,
            state.get("latest_artifact_id"),
        ) or latest_artifact(artifacts)

        if target_artifact:
            return {
                "content": read_artifact_content(target_artifact) or target_artifact.get("content_preview", ""),
                "type": target_artifact.get("type", "artifact"),
                "source": "latest_artifact",
                "source_artifact_id": target_artifact.get("artifact_id"),
            }

    # Auto fallback: prefer the latest meaningful generated content.
    if state.get("latest_agent_output"):
        output = state.get("latest_agent_output") or {}
        output_type = output.get("type")

        if output_type == "commands_or_code":
            content = state.get("generated_commands") or state.get("latest_code") or output.get("text")
            if content:
                return {"content": content, "type": "commands", "source": "latest_agent_output"}

        if output_type == "vision_analysis" and state.get("latest_image_analysis"):
            image = state.get("latest_image_analysis") or {}
            content = image.get("explanation") or image.get("summary") or output.get("text")
            if content:
                return {"content": content, "type": "image_analysis", "source": "latest_agent_output"}

        if output_type == "report":
            content = state.get("report_draft") or state.get("latest_report") or output.get("text")
            if content:
                return {"content": content, "type": "report", "source": "latest_agent_output"}

    if state.get("generated_commands") or state.get("latest_code") or state.get("latest_commands"):
        return {
            "content": state.get("generated_commands") or state.get("latest_code") or state.get("latest_commands"),
            "type": "commands",
            "source": "generated_commands_or_latest_code",
        }

    if state.get("report_draft") or state.get("latest_report"):
        return {
            "content": state.get("report_draft") or state.get("latest_report"),
            "type": "report",
            "source": "latest_report",
        }

    if state.get("latest_image_analysis"):
        image = state.get("latest_image_analysis") or {}
        return {
            "content": image.get("explanation") or image.get("summary") or str(image),
            "type": "image_analysis",
            "source": "latest_image_analysis",
        }

    if state.get("latest_answer") or state.get("latest_text_output"):
        return {
            "content": state.get("latest_answer") or state.get("latest_text_output"),
            "type": "answer",
            "source": "latest_answer_or_text_output",
        }

    if state.get("current_topic"):
        return {
            "content": state.get("current_topic"),
            "type": "topic",
            "source": "current_topic",
        }

    return {
        "content": "",
        "type": "unknown",
        "source": "none",
    }
def explanation_agent(state: AgentState) -> AgentState:
    target = resolve_explanation_target(state)
    target_content = target.get("content") or ""

    if not target_content:
        raw_question = state.get("raw_input", "") or ""
        if is_general_question_request(raw_question):
            target_content = raw_question
            target = {"content": raw_question, "type": "question", "source": "raw_input"}
        else:
            text = localized(
                state,
                "I need to know which part you want me to explain: the image, report, code, or previous answer?",
                "محتاج أعرف أنهي جزء تحب أشرحه: الصورة، التقرير، الكود، ولا الرد اللي فات؟",
            )

            output = {
                "type": "explanation",
                "text": text,
                "confidence": 0.3,
                "metadata": {
                    "status": "missing_explanation_target",
                    "target": target,
                },
            }

            return {
                **state,
                "latest_agent_output": output,
                "latest_text_output": text,
                "latest_answer": text,
            }

    try:
        explanation_input = f"""
User request:
{state.get("raw_input", "")}

Target type:
{target.get("type")}

Target source:
{target.get("source")}

Content to explain:
{target_content}
""".strip()

        text = llm_text(
            general_llm,
            EXPLANATION_AGENT_PROMPT,
            explanation_input,
        )

        output = {
            "type": "explanation",
            "text": text,
            "confidence": 0.88,
            "metadata": {
                "status": "explained",
                "explained_type": target.get("type"),
                "explained_from": target.get("source"),
                "source_artifact_id": target.get("source_artifact_id"),
            },
        }

        return {
            **state,
            "latest_agent_output": output,
            "latest_text_output": text,
            # Command/code generation is substantive content. Keep it as the
            # latest answer/source so follow-ups like "explain more",
            # "save it as PDF", or "send it" use the commands, not a status bubble.
            "latest_answer": text,
            "latest_export_source_content": text,
            "active_task": {
                **(state.get("active_task") or {}),
                "latest_explanation_type": target.get("type"),
                "latest_explanation_source": target.get("source"),
            },
        }

    except Exception as exc:
        text = f"حصل خطأ وأنا ببسط الشرح: {exc}"

        output = {
            "type": "explanation",
            "text": text,
            "confidence": 0.0,
            "metadata": {
                "status": "error",
                "error": str(exc),
                "target": target,
            },
        }

        return add_error(
            {
                **state,
                "latest_agent_output": output,
                "latest_text_output": text,
                "latest_answer": text,
            },
            "explanation_agent",
            exc,
        )

def is_bad_report_source_text(text: str) -> bool:
    """
    Prevent report_agent from using error/clarification/missing-content messages as factual sources.
    """
    lowered = (text or "").lower().strip()
    bad_markers = [
        "مفيش محتوى جاهز",
        "محتاج مادة أو مصادر",
        "couldn't find previous content",
        "could not find previous content",
        "please send the content first",
        "no ready content",
        "insufficient source material",
        "محتاج توضيح",
        "تقصد أنهي حاجة",
        "could you please specify",
    ]
    return any(marker in lowered for marker in bad_markers)

def build_report_source_material(state: AgentState) -> tuple[str, dict[str, Any]]:
    """
    Build report source material and metadata.

    Returns:
    - source_material: text passed to report_llm
    - metadata: source quality/availability information
    """
    parts: list[str] = []
    source_types: list[str] = []

    web_findings = state.get("web_findings")
    retrieved_docs = state.get("retrieved_docs") or []
    latest_image_analysis = state.get("latest_image_analysis")
    latest_answer = state.get("latest_answer")

    if web_findings:
        parts.append(f"Web findings:\n{web_findings}")
        source_types.append("web")

    if retrieved_docs:
        parts.append(
            "Document/RAG sources:\n"
            f"{format_sources_for_prompt(retrieved_docs, limit=8)}"
        )
        source_types.append("rag")

    if latest_image_analysis:
        parts.append(f"Image analysis:\n{latest_image_analysis}")
        source_types.append("image_analysis")

    if latest_answer:
        parts.append(f"Previous answer:\n{latest_answer}")
        source_types.append("latest_answer")

    source_material = "\n\n---\n\n".join(parts)

    metadata = {
        "has_sources": bool(parts),
        "source_types": source_types,
        "web_findings_available": bool(web_findings),
        "retrieved_docs_count": len(retrieved_docs),
        "has_image_analysis": bool(latest_image_analysis),
        "has_latest_answer": bool(latest_answer),
    }

    return source_material, metadata


def report_agent(state: AgentState) -> AgentState:
    raw = state.get("raw_input", "")

    source_material, source_metadata = build_report_source_material(state)

    # If there is no external source material but the user clearly gave a topic,
    # create a general report from the topic instead of asking for unnecessary
    # clarification. This is what users expect for prompts like:
    # "make pdf about risk of api".
    if not source_metadata.get("has_sources"):
        topic = state.get("current_topic") or infer_topic_from_generation_request(raw)
        if topic and len(topic.strip()) >= 3:
            source_material = (
                "User requested a general report about this topic. "
                "No external files/web/RAG sources were provided, so the report must be clear that it is based on general security knowledge and the user's topic only.\n\n"
                f"Topic: {topic}"
            )
            source_metadata = {
                **source_metadata,
                "has_sources": True,
                "source_types": ["user_topic"],
                "user_topic_only": True,
            }
        else:
            text = localized(
                state,
                "I need source material or a clear topic before I can create the report.",
                "محتاج مادة أو موضوع واضح أقدر أبني عليه التقرير.",
            )

            output = {
                "type": "report",
                "text": text,
                "confidence": 0.25,
                "metadata": {
                    "status": "insufficient_source_material",
                    **source_metadata,
                    "recommended_next_action": "web_research",
                },
            }

            return {
                **state,
                "latest_agent_output": output,
                "latest_text_output": text,
                "latest_answer": text,
                "active_task": {
                    "type": "report_generation",
                    "status": "needs_sources",
                    "available_actions": ["web_research", "upload_sources", "provide_content"],
                },
            }

    try:
        report_input = f"""
User request:
{raw}

Source metadata:
{json.dumps(source_metadata, ensure_ascii=False, default=str)}

Source material:
{source_material}

Instructions:
Write a professional report using only the provided source material.
If the source material is limited, say that clearly in the Limitations section.
Do not invent facts that are not supported by the source material.
Include a Sources section based on the provided source material.
""".strip()

        text = llm_text(
            report_llm,
            REPORT_AGENT_PROMPT,
            report_input,
        )

        artifact = save_report_artifact(text)

        output = {
            "type": "report",
            "text": text,
            "confidence": 0.85 if source_metadata.get("has_sources") else 0.3,
            "metadata": {
                "status": "draft_ready",
                "artifact_id": artifact.get("artifact_id"),
                "path": artifact.get("path"),
                **source_metadata,
            },
        }

        artifacts = state.get("artifacts", []) + [artifact]
        generated_files = state.get("generated_files", []) + [artifact]
        latest_artifact_id = artifact.get("artifact_id")
        latest_output = output
        latest_text = text

        desired_format = state.get("desired_output_format")
        if desired_format is None:
            desired_format = (
                "pdf" if user_wants_pdf(raw) else
                "docx" if user_wants_docx(raw) else
                "markdown" if user_wants_markdown(raw) else
                None
            )

        # If the user asked for a report as a file (PDF/DOCX/Markdown), generate
        # the report first, then export it immediately in the same turn.
        if desired_format in ["pdf", "docx", "markdown"]:
            filename_base = safe_filename(state.get("current_topic") or "report", default="report")
            if desired_format == "pdf":
                export_artifact = export_pdf_from_markdown(
                    text,
                    filename=f"{filename_base}.pdf",
                    title="Generated Report",
                    created_from="report_agent_auto_export",
                )
            elif desired_format == "docx":
                export_artifact = export_docx_from_markdown(
                    text,
                    filename=f"{filename_base}.docx",
                    title="Generated Report",
                    created_from="report_agent_auto_export",
                )
            else:
                export_artifact = save_markdown(
                    text,
                    filename=f"{filename_base}.md",
                    title="Generated Report",
                    artifact_type="report",
                    created_from="report_agent_auto_export",
                )

            export_artifact.setdefault("metadata", {})
            export_artifact["metadata"].update({
                "source_content": text,
                "source_content_preview": text[:1200],
                "source_kind": "report",
                "source_artifact_id": artifact.get("artifact_id"),
                "auto_exported_from_report_agent": True,
            })

            artifacts.append(export_artifact)
            generated_files.append(export_artifact)
            latest_artifact_id = export_artifact.get("artifact_id")
            path = export_artifact.get("path")
            lang = preferred_response_language(state)
            if desired_format == "pdf" and not str(path).lower().endswith(".pdf"):
                latest_text = (
                    f"I created the report, but PDF rendering failed, so I saved a fallback file: {path}"
                    if lang == "en" else
                    f"كتبت التقرير، لكن إنشاء PDF فشل فحفظت ملف بديل هنا: {path}"
                )
            else:
                label = "PDF" if desired_format == "pdf" else "Word" if desired_format == "docx" else "Markdown"
                latest_text = (
                    f"{label} report ready: {path}"
                    if lang == "en" else
                    f"تم تجهيز تقرير {label}: {path}"
                )

            latest_output = {
                "type": "artifact",
                "text": latest_text,
                "confidence": 0.9,
                "metadata": {
                    "status": "ready",
                    "artifact": export_artifact,
                    "report_artifact": artifact,
                    "export_format": desired_format,
                },
            }

        # Compound task: generate report/file and prepare an email draft in the same turn.
        # Example: "make a PDF about API risk and send it to user@example.com".
        detected_email = find_email_in_text(raw)
        if detected_email and user_wants_email_followup(raw):
            attach_for_email = export_artifact if 'export_artifact' in locals() else artifact
            source = {"kind": "report", "title": state.get("current_topic") or "Requested Report", "content": text, "artifact": attach_for_email}
            # If no export artifact was created, create a PDF for email attachment.
            if not attach_for_email or not (attach_for_email.get("type") == "pdf" or str(attach_for_email.get("path", "")).lower().endswith(".pdf")):
                attach_for_email = export_pdf_from_markdown(
                    text,
                    filename=f"{safe_filename(state.get('current_topic') or 'requested_report', default='requested_report')}.pdf",
                    title="Requested Report",
                    created_from="report_agent_email_attachment",
                )
                attach_for_email.setdefault("metadata", {})
                attach_for_email["metadata"].update({
                    "source_content": text,
                    "source_content_preview": text[:1200],
                    "source_kind": "report",
                    "source_artifact_id": artifact.get("artifact_id"),
                })
                artifacts.append(attach_for_email)
                generated_files.append(attach_for_email)
                latest_artifact_id = attach_for_email.get("artifact_id")

            lang = preferred_response_language(state)
            draft = create_email_draft(
                to=detected_email,
                subject=email_subject_for_source(source),
                body=professional_email_body(source, lang),
                attachment_artifact_id=attach_for_email.get("artifact_id") if attach_for_email else None,
                attachment_path=attach_for_email.get("path") if attach_for_email else None,
            )
            latest_text = (
                f"I created the report and prepared an email draft to {detected_email} with the attachment. Should I send it now?"
                if lang == "en" else
                f"كتبت التقرير وجهزت مسودة إيميل لـ {detected_email} بالمرفق. أبعته دلوقتي؟"
            )
            latest_output = {
                "type": "email_draft",
                "text": latest_text,
                "confidence": 0.92,
                "metadata": {"draft": draft, "attachment": attach_for_email, "report_artifact": artifact},
            }
            return {
                **state,
                "report_draft": text,
                "latest_report": text,
                "latest_report_id": artifact.get("artifact_id"),
                "latest_artifact_id": latest_artifact_id,
                "artifacts": artifacts,
                "generated_files": generated_files,
                "latest_agent_output": latest_output,
                "latest_text_output": latest_text,
                "latest_answer": text,
                "latest_export_source_content": text,
                "email_draft": draft,
                "latest_email_draft_id": draft.get("draft_id"),
                "pending_approval": {
                    "type": "confirm_send_email",
                    "message": latest_text,
                    "email_draft": draft,
                    "artifact_id": draft.get("attachment_artifact_id"),
                    "action": "email_send",
                    "metadata": {"to": detected_email},
                },
                "active_task": {"type": "email", "status": "awaiting_confirmation", "draft_id": draft.get("draft_id")},
            }

        return {
            **state,
            "report_draft": text,
            "latest_report": text,
            "latest_report_id": artifact.get("artifact_id"),
            "latest_artifact_id": latest_artifact_id,
            "artifacts": artifacts,
            "generated_files": generated_files,
            "latest_agent_output": latest_output,
            "latest_text_output": latest_text,
            "latest_answer": text,
            "active_task": {
                "type": "report_generation",
                "status": "draft_ready",
                "topic": state.get("current_topic"),
                "artifact_id": artifact.get("artifact_id"),
                "latest_export_artifact_id": latest_artifact_id if latest_artifact_id != artifact.get("artifact_id") else None,
                "source_types": source_metadata.get("source_types", []),
                "available_actions": ["edit", "export_pdf", "export_docx", "export_markdown", "email"],
            },
        }

    except Exception as exc:
        text = f"حصل خطأ وأنا بكتب التقرير: {exc}"

        output = {
            "type": "report",
            "text": text,
            "confidence": 0.0,
            "metadata": {
                "status": "error",
                "error": str(exc),
                **source_metadata,
            },
        }

        return add_error(
            {
                **state,
                "latest_agent_output": output,
                "latest_text_output": text,
                "latest_answer": text,
            },
            "report_agent",
            exc,
        )
def command_code_agent(state: AgentState) -> AgentState:
    try:
        text = llm_text(
            command_llm,
            COMMAND_AGENT_PROMPT,
            f"User request:\n{state.get('raw_input', '')}\n\nContext:\n{format_state_summary_for_prompt(state)}",
        )

        artifact = save_commands_artifact(text)

        output = {
            "type": "commands_or_code",
            "text": text,
            "confidence": 0.8,
            "metadata": {"artifact_id": artifact.get("artifact_id"), "path": artifact.get("path")},
        }

        return {
            **state,
            "generated_commands": text,
            "latest_commands": text,
            "latest_code": text,
            "latest_code_id": artifact.get("artifact_id"),
            "latest_artifact_id": artifact.get("artifact_id"),
            "artifacts": state.get("artifacts", []) + [artifact],
            "generated_files": state.get("generated_files", []) + [artifact],
            "latest_agent_output": output,
            "latest_text_output": text,
            # Command/code generation is substantive content. Keep it as the
            # latest answer/source so follow-ups like "explain more",
            # "save it as PDF", or "send it" use the commands, not a status bubble.
            "latest_answer": text,
            "latest_export_source_content": text,
            "active_task": {
                "type": "command_code_generation",
                "status": "ready",
                "artifact_id": artifact.get("artifact_id"),
                "available_actions": ["edit", "save", "email"],
            },
        }

    except Exception as exc:
        text = f"حصل خطأ وأنا بجهز الأوامر/الكود: {exc}"
        output = {"type": "commands_or_code", "text": text, "confidence": 0.0, "metadata": {"error": str(exc)}}
        return add_error({**state, "latest_agent_output": output, "latest_text_output": text, "latest_answer": text}, "command_code_agent", exc)
# ============================================================
# ARTIFACT HYBRID RESOLUTION HELPERS
# ============================================================

def detect_artifact_format(text: str) -> str:
    """
    Detect requested artifact format from user text.
    Returns: pdf|docx|markdown|text|auto
    """
    lowered = (text or "").lower()

    if any(x in lowered for x in ["pdf", "بي دي اف", "بى دى اف"]):
        return "pdf"

    if any(x in lowered for x in ["docx", ".docx", "word", "ms word", "وورد", "ورد", "ملف وورد"]):
        return "docx"

    if any(x in lowered for x in ["markdown", ".md", "ماركداون", "md"]):
        return "markdown"

    if any(x in lowered for x in ["txt", "text file", ".txt", "تكست", "نص"]):
        return "text"

    return "auto"


def detect_artifact_target_type(text: str) -> str:
    """
    Detect clear target type from user text.
    Returns: report|commands|answer|image_analysis|latest_artifact|auto
    """
    lowered = (text or "").lower()

    if any(x in lowered for x in ["report", "تقرير", "ريبورت"]):
        return "report"

    if any(x in lowered for x in ["code", "commands", "command", "script", "الكود", "كود", "الأوامر", "الاوامر"]):
        return "commands"

    if any(x in lowered for x in ["answer", "response", "reply", "الإجابة", "الاجابة", "الرد"]):
        return "answer"

    if any(x in lowered for x in ["image", "diagram", "الدياجرام", "الصورة", "تحليل الصورة"]):
        return "image_analysis"

    if any(x in lowered for x in ["artifact", "file", "الملف", "الفايل"]):
        return "latest_artifact"

    return "auto"


def artifact_request_is_ambiguous(text: str) -> bool:
    """
    Decide whether we need LLM help to resolve artifact target/format.
    """
    lowered = (text or "").lower()

    vague_phrases = [
        "احفظه",
        "احفظها",
        "احفظ ده",
        "احفظ دي",
        "طلعه",
        "طلعها",
        "صدره",
        "صدّره",
        "صدرها",
        "اعمله ملف",
        "اعملها ملف",
        "النسخة دي",
        "النسخة النهائية",
        "final version",
        "save it",
        "save them",
        "export it",
        "export them",
        "make it",
        "make them",
        "convert it",
        "convert them",
        "this",
        "that",
        "it",
        "them",
        "these",
        "those",
    ]

    target = detect_artifact_target_type(lowered)
    fmt = detect_artifact_format(lowered)

    has_vague_reference = any(p in lowered for p in vague_phrases)

    if target == "auto" and fmt == "auto":
        return True

    if has_vague_reference and target == "auto":
        return True

    return False


def safe_artifact_filename(filename: str, *, fallback: str) -> str:
    """
    Keep filename safe and simple.
    """
    filename = (filename or "").strip()

    if not filename:
        return fallback

    filename = safe_filename(filename, default=fallback)

    return filename or fallback


def resolve_artifact_target_deterministic(
    state: AgentState,
    target_type: str = "auto",
) -> dict[str, Any]:
    """
    Resolve artifact target using deterministic rules.
    """
    artifacts = state.get("artifacts", [])

    if target_type == "commands":
        content = (
            state.get("generated_commands")
            or state.get("latest_code")
            or state.get("latest_commands")
        )
        if content:
            return {
                "content": content,
                "type": "commands",
                "source": "latest_code",
            }

    if target_type == "report":
        content = state.get("report_draft") or state.get("latest_report")
        if content:
            return {
                "content": content,
                "type": "report",
                "source": "latest_report",
            }

    if target_type == "image_analysis":
        image = state.get("latest_image_analysis") or {}
        if image:
            content = (
                image.get("explanation")
                or image.get("summary")
                or image.get("user_response")
                or str(image)
            )
            return {
                "content": content,
                "type": "image_analysis",
                "source": "latest_image_analysis",
            }

    if target_type == "answer":
        content = state.get("latest_answer") or state.get("latest_text_output")
        if content:
            return {
                "content": content,
                "type": "answer",
                "source": "latest_answer",
            }

    if target_type == "latest_artifact":
        target_artifact = find_artifact_by_id(
            artifacts,
            state.get("latest_artifact_id"),
        ) or latest_artifact(artifacts)

        if target_artifact:
            return {
                "content": read_artifact_content(target_artifact) or target_artifact.get("content_preview", ""),
                "type": target_artifact.get("type", "artifact"),
                "source": "latest_artifact",
                "source_artifact_id": target_artifact.get("artifact_id"),
            }

    # Auto priority fallback.
    if state.get("report_draft") or state.get("latest_report"):
        return {
            "content": state.get("report_draft") or state.get("latest_report"),
            "type": "report",
            "source": "report_draft_or_latest_report",
        }

    if state.get("generated_commands") or state.get("latest_code") or state.get("latest_commands"):
        return {
            "content": (
                state.get("generated_commands")
                or state.get("latest_code")
                or state.get("latest_commands")
            ),
            "type": "commands",
            "source": "generated_commands_or_latest_code",
        }

    target_artifact = find_artifact_by_id(
        artifacts,
        state.get("latest_artifact_id"),
    ) or latest_artifact(artifacts)

    if target_artifact:
        return {
            "content": read_artifact_content(target_artifact) or target_artifact.get("content_preview", ""),
            "type": target_artifact.get("type", "artifact"),
            "source": "latest_artifact",
            "source_artifact_id": target_artifact.get("artifact_id"),
        }

    if state.get("latest_image_analysis"):
        image = state.get("latest_image_analysis") or {}
        return {
            "content": image.get("explanation") or image.get("summary") or str(image),
            "type": "image_analysis",
            "source": "latest_image_analysis",
        }

    if state.get("latest_answer") or state.get("latest_text_output"):
        return {
            "content": state.get("latest_answer") or state.get("latest_text_output"),
            "type": "answer",
            "source": "latest_answer_or_text_output",
        }

    history_answer = latest_assistant_message_from_history(state)
    if history_answer:
        return {
            "content": history_answer,
            "type": "answer",
            "source": "latest_assistant_message_from_history",
        }

    return {
        "content": "",
        "type": "unknown",
        "source": "none",
    }


def llm_resolve_artifact_request(
    state: AgentState,
    fallback: dict[str, Any],
) -> dict[str, Any]:
    """
    Use LLM only to resolve ambiguous artifact requests.
    The LLM does not save/export anything.
    It only chooses target_type, format, and filename.
    """
    raw = state.get("raw_input", "") or ""

    payload = {
        "user_request": raw,
        "available_context": {
            "has_report": bool(state.get("report_draft") or state.get("latest_report")),
            "has_commands": bool(
                state.get("generated_commands")
                or state.get("latest_code")
                or state.get("latest_commands")
            ),
            "has_latest_answer": bool(state.get("latest_answer") or state.get("latest_text_output")),
            "has_image_analysis": bool(state.get("latest_image_analysis")),
            "has_latest_artifact": bool(state.get("latest_artifact_id") or state.get("artifacts")),
            "latest_artifact_id": state.get("latest_artifact_id"),
            "active_task": state.get("active_task"),
            "current_topic": state.get("current_topic"),
        },
        "allowed_target_types": [
            "report",
            "commands",
            "answer",
            "image_analysis",
            "latest_artifact",
            "auto",
        ],
        "allowed_formats": [
            "pdf",
            "markdown",
            "text",
            "auto",
        ],
    }

    try:
        parsed = llm_json(
            general_llm,
            ARTIFACT_REQUEST_RESOLVER_PROMPT,
            payload,
            fallback,
        )

        if not isinstance(parsed, dict):
            return fallback

        target_type = parsed.get("target_type") or fallback.get("target_type", "auto")
        fmt = parsed.get("format") or fallback.get("format", "auto")

        allowed_targets = {
            "report",
            "commands",
            "answer",
            "image_analysis",
            "latest_artifact",
            "auto",
        }

        allowed_formats = {
            "pdf",
            "markdown",
            "text",
            "auto",
        }

        if target_type not in allowed_targets:
            target_type = "auto"

        if fmt not in allowed_formats:
            fmt = "auto"

        return {
            **fallback,
            **parsed,
            "target_type": target_type,
            "format": fmt,
        }

    except Exception:
        return fallback


def resolve_artifact_request(state: AgentState) -> dict[str, Any]:
    """
    Hybrid resolver:
    - deterministic for clear requests
    - LLM resolver for ambiguous requests
    - deterministic execution later
    """
    raw = state.get("raw_input", "") or ""

    deterministic_target_type = detect_artifact_target_type(raw)
    deterministic_format = detect_artifact_format(raw)

    fallback_resolution = {
        "target_type": deterministic_target_type,
        "format": deterministic_format,
        "filename": "",
        "reason": "deterministic fallback resolution",
    }

    used_llm_resolver = artifact_request_is_ambiguous(raw)

    if used_llm_resolver:
        resolution = llm_resolve_artifact_request(state, fallback_resolution)
    else:
        resolution = fallback_resolution

    target_type = resolution.get("target_type") or "auto"
    fmt = resolution.get("format") or "auto"

    target = resolve_artifact_target_deterministic(state, target_type)

    return {
        "target": target,
        "format": fmt,
        "filename": resolution.get("filename") or "",
        "reason": resolution.get("reason", ""),
        "used_llm_resolver": used_llm_resolver,
    }
def artifact_agent(state: AgentState) -> AgentState:
    raw = state.get("raw_input", "")

    # Do not export unsafe blocked content.
    if state.get("risk_level") == "blocked":
        text = "مش هقدر أصدّر محتوى اتصنّف كغير آمن."

        output = {
            "type": "artifact",
            "text": text,
            "confidence": 0.2,
            "metadata": {
                "status": "blocked_content_not_exported",
            },
        }

        return {
            **state,
            "latest_agent_output": output,
            "latest_text_output": text,
            "latest_answer": text,
        }

    resolution = resolve_artifact_request(state)

    target = resolution.get("target") or {}
    target_content = target.get("content") or ""
    target_type = target.get("type") or "unknown"
    requested_format = resolution.get("format") or "auto"
    requested_filename = resolution.get("filename") or ""

    if not target_content:
        if detect_language_simple(raw) == "en":
            text = (
                "I couldn't find previous content to save or export. "
                "Please send the content first, or ask me to create an answer/report/code first."
            )
        else:
            text = (
                "مفيش محتوى جاهز أحفظه أو أصدّره. "
                "ابعتلي المحتوى، أو اطلب مني أعمل report/code/answer الأول."
            )

        output = {
            "type": "artifact",
            "text": text,
            "confidence": 0.3,
            "metadata": {
                "status": "missing_content",
                "resolution": resolution,
            },
        }

        return {
            **state,
            "latest_agent_output": output,
            "latest_text_output": text,
            "latest_answer": text,
        }

    try:
        # Decide final export format.
        export_format = requested_format

        if export_format == "auto":
            if user_wants_pdf(raw):
                export_format = "pdf"
            elif user_wants_docx(raw):
                export_format = "docx"
            elif user_wants_markdown(raw):
                export_format = "markdown"
            elif target_type in ["report", "commands"]:
                export_format = "markdown"
            else:
                export_format = "text"

        # Build safe filename fallback.
        if target_type == "report":
            fallback_name = "report"
        elif target_type == "commands":
            fallback_name = "commands"
        elif target_type == "image_analysis":
            fallback_name = "image_analysis"
        elif target_type == "answer":
            fallback_name = "answer"
        else:
            fallback_name = "artifact"

        filename = build_export_filename(
            requested_filename,
            fallback=fallback_name,
            export_format=export_format,
        )

        # Ensure extension matches selected format.
        if export_format == "pdf":
            if not filename.lower().endswith(".pdf"):
                filename = f"{filename}.pdf"

            artifact = export_pdf_from_markdown(
                target_content,
                filename=filename,
                title="Exported Report" if target_type == "report" else "Exported Artifact",
                created_from="artifact_agent",
            )

        elif export_format == "docx":
            if not filename.lower().endswith(".docx"):
                filename = f"{filename}.docx"

            artifact = export_docx_from_markdown(
                target_content,
                filename=filename,
                title="Exported Report" if target_type == "report" else "Exported Artifact",
                created_from="artifact_agent",
            )

        elif export_format == "markdown":
            if not filename.lower().endswith(".md"):
                filename = f"{filename}.md"

            artifact = save_markdown(
                target_content,
                filename=filename,
                title="Exported Report" if target_type == "report" else "Exported Artifact",
                artifact_type="report" if target_type == "report" else "markdown",
                created_from="artifact_agent",
            )

        else:
            if not filename.lower().endswith(".txt"):
                filename = f"{filename}.txt"

            artifact = save_text_file(
                target_content,
                filename=filename,
                title="Exported Artifact",
                artifact_type="text",
                created_from="artifact_agent",
            )

        # Add stronger metadata.
        artifact.setdefault("metadata", {})
        artifact["metadata"].update(
            {
                "source_content": target_content,
                "source_content_preview": target_content[:1200],
                "source_kind": target_type,
                "source_content_type": target_type,
                "source": target.get("source"),
                "source_artifact_id": target.get("source_artifact_id"),
                "requested_format": requested_format,
                "export_format": export_format,
                "resolver_reason": resolution.get("reason"),
                "used_llm_resolver": resolution.get("used_llm_resolver", False),
            }
        )

        artifact_path = artifact.get("path")
        artifact_filename = artifact.get("filename") or Path(str(artifact_path)).name or "file"
        lang = preferred_response_language(state)

        if export_format == "pdf" and not str(artifact_path).lower().endswith(".pdf"):
            text = (
                f"I could not create a PDF, so I saved a fallback file instead: {artifact_filename}"
                if lang == "en"
                else f"معرفتش أطلع PDF، فحفظت ملف بديل هنا: {artifact_filename}"
            )
        else:
            text = (
                f"File ready: {artifact_filename}"
                if lang == "en"
                else f"تم تجهيز الملف: {artifact_filename}"
            )

        if artifact.get("metadata", {}).get("note"):
            if lang == "en":
                text += f"\nNote: {artifact['metadata']['note']}"
            else:
                text += f"\nملاحظة: {artifact['metadata']['note']}"

        output = {
            "type": "artifact",
            "text": text,
            "confidence": 0.9,
            "metadata": {
                "status": "ready",
                "artifact": artifact,
                "resolution": {
                    "target_type": target_type,
                    "requested_format": requested_format,
                    "export_format": export_format,
                    "used_llm_resolver": resolution.get("used_llm_resolver", False),
                },
            },
        }

        return {
            **state,
            "artifacts": state.get("artifacts", []) + [artifact],
            "generated_files": state.get("generated_files", []) + [artifact],
            "latest_artifact_id": artifact.get("artifact_id"),
            "latest_agent_output": output,
            "latest_text_output": text,
            # Command/code generation is substantive content. Keep it as the
            # latest answer/source so follow-ups like "explain more",
            # "save it as PDF", or "send it" use the commands, not a status bubble.
            "latest_answer": text,
            "latest_export_source_content": text,
            "active_task": {
                **(state.get("active_task") or {}),
                "latest_export_artifact_id": artifact.get("artifact_id"),
                "latest_export_format": export_format,
                "latest_export_target_type": target_type,
            },
        }

    except Exception as exc:
        text = f"حصل خطأ وأنا بحفظ/بصدر الملف: {exc}"

        output = {
            "type": "artifact",
            "text": text,
            "confidence": 0.0,
            "metadata": {
                "status": "error",
                "error": str(exc),
                "resolution": resolution,
            },
        }

        return add_error(
            {
                **state,
                "latest_agent_output": output,
                "latest_text_output": text,
                "latest_answer": text,
            },
            "artifact_agent",
            exc,
        )

def _looks_like_artifact_status(text: str) -> bool:
    """
    Avoid using short status replies such as "PDF ready: ..." as source content.
    These are UI/status messages, not the real content the user wants emailed.
    """
    lowered = (text or "").strip().lower()
    status_prefixes = [
        "pdf ready:",
        "file ready:",
        "تم تجهيز ملف pdf",
        "تم تجهيز الملف",
        "i could not create a pdf",
        "معرفتش أطلع pdf",
    ]
    return any(lowered.startswith(prefix) for prefix in status_prefixes)


def _artifact_source_content(artifact: Optional[dict[str, Any]]) -> str:
    """
    Recover the original content used to create an artifact.

    For binary files like PDF/DOCX, read_artifact_content usually returns empty,
    so we intentionally store and reuse source_content/source_content_preview
    in artifact metadata.
    """
    if not artifact:
        return ""

    metadata = artifact.get("metadata") or {}

    return (
        metadata.get("source_content")
        or metadata.get("source_content_preview")
        or read_artifact_content(artifact)
        or artifact.get("content_preview")
        or ""
    )


def select_best_email_source(state: AgentState) -> dict[str, Any]:
    """
    Choose the best current content to email, in a context-aware order.

    Critical behavior:
    - If the user just exported a PDF, reuse that PDF as the attachment.
    - The email body must be based on the original source content, not on
      the status message "PDF ready: ...".
    """
    artifacts = state.get("artifacts", []) or []

    # Prefer the current latest artifact when it is a PDF, then any latest PDF.
    latest_selected = find_artifact_by_id(artifacts, state.get("latest_artifact_id"))
    pdf_artifact = latest_selected if latest_selected and (
        latest_selected.get("type") == "pdf"
        or str(latest_selected.get("path", "")).lower().endswith(".pdf")
    ) else latest_artifact(artifacts, preferred_types=["pdf"])
    if pdf_artifact:
        content = (
            _artifact_source_content(pdf_artifact)
            or state.get("latest_export_source_content")
            or state.get("report_draft")
            or state.get("latest_report")
            or state.get("generated_commands")
            or state.get("latest_code")
            or state.get("latest_commands")
            or state.get("latest_answer")
            or ""
        )

        if _looks_like_artifact_status(content):
            content = (
                state.get("latest_export_source_content")
                or state.get("report_draft")
                or state.get("latest_report")
                or state.get("generated_commands")
                or state.get("latest_code")
                or state.get("latest_commands")
                or ""
            )

        return {
            "kind": (pdf_artifact.get("metadata") or {}).get("source_kind") or "artifact",
            "title": pdf_artifact.get("title") or "Attached Report",
            "content": content,
            "artifact": pdf_artifact,
        }

    if state.get("report_draft") or state.get("latest_report"):
        return {
            "kind": "report",
            "title": "Requested Report",
            "content": state.get("report_draft") or state.get("latest_report"),
            "artifact": None,
        }

    artifact = find_artifact_by_id(artifacts, state.get("latest_artifact_id")) or latest_artifact(artifacts)
    if artifact:
        content = (
            _artifact_source_content(artifact)
            or state.get("latest_export_source_content")
            or state.get("latest_answer")
            or ""
        )

        if _looks_like_artifact_status(content):
            content = state.get("latest_export_source_content") or ""

        return {
            "kind": artifact.get("type") or "artifact",
            "title": artifact.get("title") or "Requested Attachment",
            "content": content,
            "artifact": artifact,
        }

    if state.get("generated_commands") or state.get("latest_code") or state.get("latest_commands"):
        return {
            "kind": "commands",
            "title": "Requested Commands / Code",
            "content": state.get("generated_commands") or state.get("latest_code") or state.get("latest_commands"),
            "artifact": None,
        }

    if state.get("latest_image_analysis"):
        image = state.get("latest_image_analysis") or {}
        return {
            "kind": "image_analysis",
            "title": image.get("topic") or "Image Analysis",
            "content": image.get("explanation") or image.get("summary") or image.get("user_response") or str(image),
            "artifact": None,
        }

    answer = state.get("latest_answer") or state.get("latest_text_output") or ""
    if _looks_like_artifact_status(answer):
        answer = state.get("latest_export_source_content") or ""

    return {
        "kind": "answer",
        "title": "Requested Content",
        "content": answer,
        "artifact": None,
    }


def ensure_pdf_artifact_for_email(state: AgentState) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    """
    Prefer a PDF attachment for professional emails.

    If a PDF already exists, reuse it.
    Otherwise export the best available report/answer/artifact to PDF.
    """
    source = select_best_email_source(state)
    artifact = source.get("artifact")

    if artifact and (
        artifact.get("type") == "pdf"
        or str(artifact.get("path", "")).lower().endswith(".pdf")
    ):
        # Make sure existing PDF artifacts also carry source metadata for later turns.
        metadata = artifact.get("metadata") or {}
        if source.get("content") and not metadata.get("source_content"):
            artifact["metadata"] = {
                **metadata,
                "source_content": source.get("content"),
                "source_content_preview": (source.get("content") or "")[:1200],
                "source_kind": source.get("kind") or metadata.get("source_kind") or "artifact",
            }
        return artifact, source

    content = source.get("content") or ""
    if not content:
        return None, source

    filename_base = safe_filename(source.get("title") or "requested_content", default="requested_content")
    if not filename_base.lower().endswith(".pdf"):
        filename_base += ".pdf"

    pdf_artifact = export_pdf_from_markdown(
        content,
        filename=filename_base,
        title=source.get("title") or "Requested Content",
        created_from="email_agent",
    )

    metadata = pdf_artifact.get("metadata") or {}
    pdf_artifact["metadata"] = {
        **metadata,
        "source_content": content,
        "source_content_preview": content[:1200],
        "source_kind": source.get("kind") or "content",
    }

    return pdf_artifact, source


def professional_email_body(source: dict[str, Any], lang: str) -> str:
    """
    Short professional email body. Full content should be attached, not pasted.
    """
    content = source.get("content") or ""
    title = source.get("title") or "the requested content"
    summary = short_preview(content, 450) if content else "The requested file is attached."

    if lang == "ar":
        return (
            "مرحبًا،\\n\\n"
            f"أرفقت لك {title} كملف PDF.\\n\\n"
            "ملخص سريع:\\n"
            f"{summary}\\n\\n"
            "ستجد التفاصيل الكاملة داخل الملف المرفق.\\n\\n"
            "تحياتي،\\n"
            "Shieldy"
        )

    return (
        "Hi,\\n\\n"
        f"I've attached {title} as a PDF.\\n\\n"
        "Quick summary:\\n"
        f"{summary}\\n\\n"
        "Please see the attached file for the full details.\\n\\n"
        "Best,\\n"
        "Shieldy"
    )


def email_subject_for_source(source: dict[str, Any]) -> str:
    kind = source.get("kind")
    title = source.get("title") or "Requested Content"

    if kind == "report":
        return f"Report: {title}" if not str(title).lower().startswith("report") else title
    if kind == "commands":
        return "Requested Commands / Code"
    if kind == "image_analysis":
        return f"Image Analysis: {title}"
    return title if title else "Requested Content"


def email_agent(state: AgentState) -> AgentState:
    raw = state.get("raw_input", "")
    next_action = state.get("next_action")
    pending = state.get("pending_approval") or {}
    lang = preferred_response_language(state)

    def finish(text: str, output_type: str = "email", confidence: float = 0.9, metadata: Optional[dict[str, Any]] = None, **extra_state):
        output = {
            "type": output_type,
            "text": text,
            "confidence": confidence,
            "metadata": metadata or {},
        }
        return {
            **state,
            **extra_state,
            "latest_agent_output": output,
            "latest_text_output": text,
            "latest_answer": text,
        }

    # 1. Cancel pending send.
    if pending.get("type") == "confirm_send_email" and is_no_confirmation(raw):
        text = (
            "Email sending cancelled. The draft is still available if you want to edit or send it later."
            if lang == "en"
            else "تمام، لغيت إرسال الإيميل. المسودة لسه موجودة لو حبيت تعدلها أو تبعتها بعدين."
        )
        return finish(
            text,
            metadata={"status": "send_cancelled"},
            pending_approval=None,
            active_task={"type": "email", "status": "cancelled"},
        )

    # 2. Preview pending draft only if explicitly requested.
    if pending.get("type") == "confirm_send_email" and user_wants_email_preview(raw):
        draft = pending.get("email_draft") or state.get("email_draft")
        if not draft:
            text = (
                "There is no email draft ready to preview."
                if lang == "en"
                else "مفيش مسودة إيميل جاهزة أعملها preview."
            )
            return finish(text, confidence=0.4, metadata={"status": "missing_draft_for_preview"})

        text = format_email_preview(draft, lang)
        return finish(
            text,
            output_type="email_preview",
            metadata={"status": "preview_shown"},
            active_task={"type": "email", "status": "awaiting_confirmation", "draft_id": draft.get("draft_id")},
        )

    # 3. Send only after confirmation.
    if next_action == "email_send":
        if not pending or pending.get("type") != "confirm_send_email":
            text = (
                "There is no email ready to send, or the confirmation was not clear."
                if lang == "en"
                else "مفيش إيميل جاهز للإرسال أو مفيش تأكيد واضح."
            )
            return finish(text, confidence=0.4, metadata={"status": "missing_pending_confirmation"})

        draft = pending.get("email_draft") or state.get("email_draft")
        if not draft:
            text = (
                "There is no saved email draft to send."
                if lang == "en"
                else "مفيش مسودة إيميل محفوظة للإرسال."
            )
            return finish(text, confidence=0.4, metadata={"status": "missing_email_draft"})

        result = send_email_via_smtp(draft)

        if result.get("sent"):
            text = (
                f"Email sent to {result.get('to')}."
                if lang == "en"
                else f"تم إرسال الإيميل إلى {result.get('to')}."
            )
            return finish(
                text,
                metadata={"result": result},
                email_sent=True,
                pending_approval=None,
                email_draft={**draft, "status": "sent"},
                active_task={"type": "email", "status": "sent", "draft_id": draft.get("draft_id")},
            )

        text = (
            f"Email was not sent: {result.get('reason')}"
            if lang == "en"
            else f"الإيميل متبعتش: {result.get('reason')}"
        )
        return finish(text, confidence=0.4, metadata={"result": result}, email_sent=False)

    # 4. Create or recreate draft. This covers:
    # - user asked "send via email"
    # - system is waiting for recipient
    # - user provided another recipient email for the same content
    email = find_email_in_text(raw)

    if not email:
        text = (
            "Sure — what email address should I send it to?"
            if lang == "en"
            else "تمام، ابعته على أي إيميل؟"
        )

        pending_approval = {
            "type": "need_email_recipient",
            "message": text,
            "artifact_id": state.get("latest_artifact_id"),
            "action": "email_draft",
            "metadata": {
                "raw_request": raw,
                "source_hint": select_best_email_source(state).get("kind"),
            },
        }

        return finish(
            text,
            metadata={"status": "need_email_recipient"},
            pending_approval=pending_approval,
            active_task={"type": "email", "status": "waiting_for_recipient"},
        )

    pdf_artifact, source = ensure_pdf_artifact_for_email(state)

    artifacts = state.get("artifacts", [])
    generated_files = state.get("generated_files", [])

    if pdf_artifact and not any(a.get("artifact_id") == pdf_artifact.get("artifact_id") for a in artifacts):
        artifacts = artifacts + [pdf_artifact]
        generated_files = generated_files + [pdf_artifact]

    # Subject/body are automatic by default. Customization can be added later,
    # but should never block a valid email follow-up with attachment/content.
    subject = email_subject_for_source(source)
    body = professional_email_body(source, lang)

    draft = create_email_draft(
        to=email,
        subject=subject,
        body=body,
        attachment_artifact_id=pdf_artifact.get("artifact_id") if pdf_artifact else None,
        attachment_path=pdf_artifact.get("path") if pdf_artifact else None,
    )

    if user_wants_email_preview(raw):
        text = format_email_preview(draft, lang)
        output_type = "email_preview"
    else:
        text = (
            f"I prepared the email draft to {email}. It includes a short message and the PDF attachment. Send it now?"
            if lang == "en"
            else f"جهزت مسودة الإيميل لـ {email}. الإيميل فيه رسالة مختصرة وملف PDF مرفق. أبعته دلوقتي؟"
        )
        output_type = "email_draft"

    return finish(
        text,
        output_type=output_type,
        metadata={"draft": draft, "source": source, "pdf_artifact": pdf_artifact},
        artifacts=artifacts,
        generated_files=generated_files,
        latest_artifact_id=pdf_artifact.get("artifact_id") if pdf_artifact else state.get("latest_artifact_id"),
        email_draft=draft,
        latest_email_draft_id=draft.get("draft_id"),
        latest_export_source_content=source.get("content") or state.get("latest_export_source_content"),
        pending_approval={
            "type": "confirm_send_email",
            "message": text,
            "email_draft": draft,
            "artifact_id": draft.get("attachment_artifact_id"),
            "action": "email_send",
            "metadata": {"to": email},
        },
        active_task={"type": "email", "status": "awaiting_confirmation", "draft_id": draft.get("draft_id")},
    )

# ============================================================
# VERIFICATION / APPROVAL / RESPONSE
# ============================================================

def output_safety_gate(state: AgentState) -> AgentState:
    payload = {
        **build_compact_payload(state),
        "latest_agent_output": state.get("latest_agent_output"),
        "latest_text_output": state.get("latest_text_output"),
        "pending_approval": state.get("pending_approval"),
    }

    fallback = {
        "status": "safe",
        "reason": "fallback output safety",
        "safe_version_required": False,
        "safe_alternative": "",
    }

    try:
        parsed = llm_json(general_llm, OUTPUT_SAFETY_PROMPT, payload, fallback)
    except Exception as exc:
        parsed = fallback
        state = add_error(state, "output_safety_gate", exc)

    if parsed.get("status") == "blocked":
        text = parsed.get("safe_alternative") or "مش هقدر أساعد في الجزء ده، لكن أقدر أقدملك بديل آمن."
        new_state = {
            **state,
            "output_safety": parsed,
            "next_action": "refuse",
            "latest_text_output": text,
            "latest_answer": text,
        }
    else:
        new_state = {
            **state,
            "output_safety": parsed,
        }

    return append_debug(new_state, "output_safety_gate", parsed)


def critic_agent(state: AgentState) -> AgentState:
    payload = {
        **build_compact_payload(state),
        "latest_agent_output": state.get("latest_agent_output"),
        "latest_text_output": state.get("latest_text_output"),
        "input_safety": state.get("input_safety"),
        "output_safety": state.get("output_safety"),
        "sources_count": len(state.get("sources", [])),
        "artifacts_count": len(state.get("artifacts", [])),
    }

    # Fail-safe fallback:
    # If the critic cannot verify the output, do NOT treat it as passed.
    fallback = {
        "passed": False,
        "checks": {
            "correctness": "unknown",
            "relevance": "unknown",
            "completeness": "unknown",
            "grounding": "unknown",
            "safety": "unknown",
            "language": "unknown",
            "approval": "unknown",
        },
        "failure_type": "critic_error",
        "confidence": 0.0,
        "feedback": "Critic could not verify the latest output safely.",
        "recommended_action": "ask_user",
    }

    try:
        parsed = llm_json(critic_llm, CRITIC_PROMPT, payload, fallback)

        # Extra safety normalization:
        # If the LLM returns malformed/partial critic output, keep the graph stable.
        if not isinstance(parsed, dict):
            parsed = fallback

        parsed.setdefault("passed", False)
        parsed.setdefault("checks", fallback["checks"])
        parsed.setdefault("failure_type", "critic_error")
        parsed.setdefault("confidence", 0.0)
        parsed.setdefault("feedback", "Critic returned incomplete verification output.")
        parsed.setdefault("recommended_action", "ask_user")

        # If passed is missing or not boolean, fail safely.
        if not isinstance(parsed.get("passed"), bool):
            parsed["passed"] = False
            parsed["failure_type"] = "critic_error"
            parsed["recommended_action"] = "ask_user"
            parsed["feedback"] = "Critic returned invalid passed value."

    except Exception as exc:
        parsed = {
            **fallback,
            "feedback": f"Critic crashed or failed to verify output: {exc}",
        }
        state = add_error(state, "critic_agent", exc)

    # Deterministic guard:
    # Email draft awaiting confirmation is a valid state.
    # It should pass to response_composer so the user can confirm or reject sending.
    pending = state.get("pending_approval") or {}

    if pending.get("type") == "confirm_send_email":
        try:
            confidence = float(parsed.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0

        parsed = {
            **parsed,
            "passed": True,
            "checks": {
                **fallback["checks"],
                **parsed.get("checks", {}),
                "approval": "pass",
                "safety": parsed.get("checks", {}).get("safety", "unknown"),
            },
            "failure_type": "none",
            "recommended_action": "continue",
            "feedback": "Email draft is ready and waiting for user confirmation.",
            "confidence": max(confidence, 0.8),
        }

    history = state.get("critic_history", []) + [parsed]

    new_state = {
        **state,
        "critic_result": parsed,
        "critic_history": history[-20:],
    }

    return append_debug(new_state, "critic_agent", parsed)


def approval_gate(state: AgentState) -> AgentState:
    """
    Ensures external actions are not silently executed.
    Most sending logic is already protected in orchestrator/email_agent.
    """
    pending = state.get("pending_approval")

    if pending and pending.get("type") == "confirm_send_email":
        text = state.get("latest_text_output") or pending.get("message") or "الإيميل جاهز. أبعته دلوقتي؟"
        return {
            **state,
            "latest_text_output": text,
            "latest_answer": text,
        }

    return state


def response_composer(state: AgentState) -> AgentState:
    latest_output = state.get("latest_agent_output") or {}
    # Safety refusal path.
    if state.get("next_action") == "refuse" or state.get("risk_level") == "blocked":
        input_safety = state.get("input_safety", {})
        text = (
            input_safety.get("clarifying_question")
            or input_safety.get("safe_alternative")
            or latest_output.get("text")
            or state.get("orchestrator_decision", {}).get("missing_info")
            or "مش هقدر أساعد في الجزء ده، لكن أقدر أساعدك بطريقة آمنة ومصرح بيها."
        )
        return {
            **state,
            "final_response": str(text),
            "latest_answer": str(text),
        }

    # Ask user path.
    if state.get("next_action") == "ask_user":
        input_safety = state.get("input_safety", {})
        text = (
            input_safety.get("clarifying_question")
            or input_safety.get("safe_alternative")
            or latest_output.get("text")
            or state.get("orchestrator_decision", {}).get("missing_info")
            or "I need a bit more detail before I can help."
        )
        if isinstance(text, list):
            prefix = "I need a bit more detail: " if preferred_response_language(state) == "en" else "محتاج توضيح بسيط: "
            text = prefix + ", ".join(map(str, text))
        return {
            **state,
            "final_response": str(text),
            "latest_answer": str(text),
        }

    # Do not rewrite deterministic specialist outputs.
    # This prevents hallucinations like claiming a report was created
    # when artifact/email agents only returned a concrete status.
    latest_type = latest_output.get("type")

    if latest_type in ["artifact", "email", "email_draft", "email_preview", "report", "code", "commands", "commands_or_code"]:
        text = latest_output.get("text") or "Done."
        # Do not let status lines such as "PDF ready" or "draft ready" replace
        # the real substantive answer/report used by future follow-ups.
        preserve_answer_for = {"artifact", "email", "email_draft", "email_preview"}
        latest_answer = state.get("latest_answer") if latest_type in preserve_answer_for else str(text)
        return {
            **state,
            "final_response": str(text),
            "latest_answer": latest_answer or str(text),
        }

    # If specialist already wrote a good user-facing response, let composer polish it.
    payload = {
        **build_compact_payload(state),
        "latest_text_output": state.get("latest_text_output"),
        "latest_agent_output": state.get("latest_agent_output"),
        "active_task": state.get("active_task"),
        "pending_approval": state.get("pending_approval"),
        "critic_result": state.get("critic_result"),
    }

    try:
        text = llm_text(
            general_llm,
            RESPONSE_COMPOSER_PROMPT,
            json.dumps(payload, ensure_ascii=False, default=str),
        )

        # Fallback if composer gets weirdly empty.
        if not text.strip():
            text = state.get("latest_text_output") or ("Done." if preferred_response_language(state) == "en" else "تمام.")

    except Exception as exc:
        state = add_error(state, "response_composer", exc)
        text = state.get("latest_text_output") or ("I couldn't complete that properly. Please try again." if preferred_response_language(state) == "en" else "مقدرتش أكمل الطلب بشكل صحيح. جرّب تاني من فضلك.")

    return {
        **state,
        "final_response": text,
        "latest_answer": text,
    }


def memory_writer(state: AgentState) -> AgentState:
    raw = state.get("raw_input", "")
    final_response = state.get("final_response", "")

    messages = state.get("messages", [])

    if raw:
        messages.append({"role": "user", "content": raw})

    if final_response:
        messages.append({"role": "assistant", "content": final_response})

    messages = messages[-30:]

    memory_summary = update_memory_summary(
        {
            **state,
            "messages": messages,
        }
    )

    return {
        **state,
        "messages": messages,
        "memory_summary": memory_summary,
    }


# Alias if your graph uses save_memory.
save_memory = memory_writer
