import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
try:
    from langfuse.langchain import CallbackHandler
except Exception:
    CallbackHandler = None

from my_agent.agent import graph
from my_agent.state import create_initial_state, reset_turn_state
from my_agent.rag import build_index_from_folder, rag_status
from my_agent.tools import (
    OUTPUT_DIR,
    build_export_filename,
    create_email_draft,
    export_docx_from_markdown,
    export_pdf_from_markdown,
    find_email_in_text,
    latest_artifact,
    safe_filename,
    save_markdown,
    save_report_artifact,
    short_preview,
)
from my_agent.models import report_llm
from my_agent.prompts import GLOBAL_POLICY, REPORT_AGENT_PROMPT
from my_agent.security_gate import evaluate_request
from my_agent.fast_router import RouteDecision, route_request, wants_rag, is_general_direct_request
from my_agent.workflow_engine import execute_fast_workflow



load_dotenv()

def build_langfuse_callbacks() -> list:
    """
    Build Langfuse callbacks with explicit configuration.

    Notes:
    - For local Docker Langfuse, LANGFUSE_HOST/LANGFUSE_BASE_URL should be reachable
      from the FastAPI process.
    - If FastAPI also runs in Docker, do not use localhost unless Langfuse is in
      the same container. Use the Docker service name or host.docker.internal.
    """
    if CallbackHandler is None:
        print("[Langfuse] disabled: langfuse package/import failed")
        return []

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    host = os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_BASE_URL")

    if not public_key or not secret_key:
        print("[Langfuse] disabled: missing LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY")
        return []

    if host:
        os.environ["LANGFUSE_HOST"] = host
        os.environ["LANGFUSE_BASE_URL"] = host

    try:
        handler = CallbackHandler(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
        print(f"[Langfuse] enabled: host={host}")
        return [handler]
    except TypeError:
        # Older langfuse versions may only read from environment variables.
        try:
            handler = CallbackHandler()
            print(f"[Langfuse] enabled via env: host={host}")
            return [handler]
        except Exception as exc:
            print(f"[Langfuse] disabled: {exc}")
            return []
    except Exception as exc:
        print(f"[Langfuse] disabled: {exc}")
        return []


def flush_langfuse_callbacks(callbacks: list) -> None:
    for callback in callbacks or []:
        for method_name in ("flush", "shutdown"):
            method = getattr(callback, method_name, None)
            if callable(method):
                try:
                    method()
                except Exception as exc:
                    print(f"[Langfuse] {method_name} failed: {exc}")
                break


def artifact_display_name(artifact: Optional[dict[str, Any]], fallback: str = "file") -> str:
    if not artifact:
        return fallback
    filename = artifact.get("filename")
    if filename:
        return str(filename)
    path = artifact.get("path")
    if path:
        return os.path.basename(str(path))
    return fallback


app = FastAPI(title="Shieldy API", version="1.1.0")

# In-memory session store for local development.
# Keeps the real graph state between turns so follow-ups can refer to reports,
# artifacts, email drafts, sources, and active tasks without forgetting context.
SESSION_STATES: Dict[str, Dict[str, Any]] = {}

# Allow local frontend index.html to call backend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    question: str
    chat_history: List[ChatMessage] = []
    chat_id: Optional[str] = None
    regenerate: bool = False
    previous_answer: Optional[str] = ""
    variation_seed: Optional[int] = None
    # Optional local image path used by the UI/multipart endpoint.
    # When present, the request enters the fast vision route before LangGraph.
    image_path: Optional[str] = None


def make_thread_id(req: ChatRequest) -> str:
    """
    Use frontend chat_id when available. This prevents different chats from
    overwriting each other's state. Falls back to a local thread id.
    """
    return req.chat_id or os.getenv("THREAD_ID", "shieldy-ui-thread")


def detect_request_language(text: str) -> str:
    text = text or ""
    has_ar = bool(re.search(r"[\u0600-\u06FF]", text))
    has_en = bool(re.search(r"[A-Za-z]", text))

    if has_ar and has_en:
        return "mixed"
    if has_ar:
        return "ar"
    if has_en:
        return "en"
    return "unknown"




def response_language(text: str, detected: str) -> str:
    """Choose user-facing language. Mixed Arabic/English should usually answer Arabic.

    Example: "اشرحلي secure storage في Android" must respond in Arabic,
    not English, because the user's sentence frame is Arabic.
    """
    if detected in {"ar", "en"}:
        return detected
    if detected == "mixed":
        ar_count = len(re.findall(r"[\u0600-\u06FF]", text or ""))
        en_count = len(re.findall(r"[A-Za-z]", text or ""))
        return "ar" if ar_count >= max(1, en_count // 2) else "en"
    return "en"


def latest_assistant_from_history(chat_history: List[ChatMessage]) -> str:
    for msg in reversed(chat_history or []):
        if msg.role == "assistant" and msg.content:
            return msg.content
    return ""


def latest_user_from_history(chat_history: List[ChatMessage]) -> str:
    for msg in reversed(chat_history or []):
        if msg.role == "user" and msg.content:
            return msg.content
    return ""


VISION_UPLOAD_DIR = OUTPUT_DIR / "uploads" / "vision"
VISION_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
CONTENT_TYPE_TO_EXTENSION = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _safe_image_suffix(filename: str | None, content_type: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix in ALLOWED_IMAGE_EXTENSIONS:
        return suffix
    return CONTENT_TYPE_TO_EXTENSION.get((content_type or "").lower(), ".png")


async def save_uploaded_vision_image(image: UploadFile) -> str:
    if not image:
        raise HTTPException(status_code=400, detail="No image file uploaded.")

    content_type = (image.content_type or "").lower()
    suffix = _safe_image_suffix(image.filename, content_type)

    if suffix not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported image type.")

    data = await image.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded image is empty.")

    max_bytes = int(os.getenv("VISION_UPLOAD_MAX_BYTES", str(8 * 1024 * 1024)))
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail="Uploaded image is too large.")

    path = VISION_UPLOAD_DIR / f"vision_{uuid.uuid4().hex}{suffix}"
    path.write_bytes(data)
    return str(path)


def parse_chat_history_form(value: str | None) -> List[ChatMessage]:
    if not value:
        return []
    try:
        raw_items = json.loads(value)
    except Exception:
        return []
    if not isinstance(raw_items, list):
        return []

    messages: List[ChatMessage] = []
    for item in raw_items[-40:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str):
            messages.append(ChatMessage(role=role, content=content))
    return messages


def is_direct_pdf_export_request(text: str) -> bool:
    lowered = (text or "").strip().lower()

    if "pdf" not in lowered and "بي دي اف" not in lowered and "بى دى اف" not in lowered:
        return False

    export_words = [
        "make", "export", "save", "convert", "download", "file",
        "it", "them", "this", "that", "previous", "report",
        "طلع", "طلعه", "صدر", "صدّر", "احفظ", "حوله", "حوّله", "ملف",
    ]
    return any(word in lowered for word in export_words)


def is_new_content_pdf_request(text: str) -> bool:
    """
    True when the user is asking to CREATE a new PDF/report about a topic,
    not merely export previous content.

    Examples that should be True:
    - make pdf about risk of api
    - create a PDF on API security risks
    - اعمل PDF عن مخاطر API

    Examples that should be False:
    - make it PDF
    - export this as PDF
    - save previous answer as PDF
    """
    lowered = (text or "").strip().lower()

    if not ("pdf" in lowered or "بي دي اف" in lowered or "بى دى اف" in lowered):
        return False

    topic_markers = [
        "about", "on ", "regarding", "concerning", "for ",
        "risk of", "risks of", "security of", "مخاطر", "عن", "حول", "بخصوص", "على",
    ]

    reference_only_markers = [
        "make it", "make this", "make them", "export it", "export this", "save it",
        "save this", "convert it", "convert this", "previous", "last answer",
        "الكلام ده", "ده", "دي", "دا", "اللي فات", "السابق",
    ]

    has_topic_marker = any(marker in lowered for marker in topic_markers)
    has_reference_only = any(marker in lowered for marker in reference_only_markers)

    return has_topic_marker and not has_reference_only



def is_simple_chat_fast_path(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False

    phrases = {
        "hi", "hello", "hey", "yo",
        "how are you", "how r u", "what's up", "whats up",
        "thanks", "thank you",
        "ok", "okay",
        "تمام", "شكرا", "تسلم",
        "ازيك", "إزيك", "ازايك", "إزايك",
        "عامل ايه", "عامل إيه",
        "اهلا", "أهلا", "السلام عليكم",
    }

    cleaned = re.sub(r"[!?؟\.\s]+", " ", lowered).strip()
    return lowered in phrases or cleaned in phrases


def simple_chat_reply(text: str) -> str:
    lowered = (text or "").strip().lower()
    lang = detect_request_language(text)

    if lowered in {"thanks", "thank you", "شكرا", "تسلم"}:
        return "You're welcome!" if lang == "en" else "العفو يا أحمد."

    if lowered in {"ok", "okay", "تمام"}:
        return "Got it." if lang == "en" else "تمام."

    if lang == "ar":
        return "أهلًا يا أحمد، أنا تمام. أقدر أساعدك في إيه؟"

    return "I'm good — how can I help you?"



def is_email_followup_request(text: str) -> bool:
    """
    General email intent detector. It must never depend on a specific address.
    """
    lowered = (text or "").strip().lower()
    if not lowered:
        return False

    email_tokens = [
        "email", "e-mail", "mail", "gmail",
        "send it", "send this", "send them", "send the", "send as email",
        "send this pdf", "send this file", "email it", "email this",
        "ايميل", "إيميل", "ابعته", "ابعت", "ارسله", "أرسله", "ابعته على", "ابعته ل",
    ]
    return any(token in lowered for token in email_tokens)


def latest_artifact_by_id(artifacts: list[dict[str, Any]], artifact_id: Optional[str]) -> Optional[dict[str, Any]]:
    if not artifact_id:
        return None
    for artifact in reversed(artifacts or []):
        if artifact.get("artifact_id") == artifact_id:
            return artifact
    return None


def looks_like_artifact_status(text: str) -> bool:
    lowered = (text or "").strip().lower()
    return (
        lowered.startswith("pdf ready:")
        or lowered.startswith("docx ready:")
        or lowered.startswith("word ready:")
        or lowered.startswith("markdown ready:")
        or lowered.startswith("email draft")
        or lowered.startswith("i prepared the email")
        or lowered.startswith("جهزت مسودة")
        or lowered.startswith("تم تجهيز ملف")
        or "data\\outputs" in lowered
        or "data/outputs" in lowered
    )


def select_email_source_for_api(state: Dict[str, Any]) -> dict[str, Any]:
    """
    Pick the best attachment/content for a generic email follow-up.
    Priority is latest selected artifact, latest PDF, then useful state content.
    """
    artifacts = state.get("artifacts", []) or []
    latest = latest_artifact_by_id(artifacts, state.get("latest_artifact_id"))
    pdf = latest if latest and (latest.get("type") == "pdf" or str(latest.get("path", "")).lower().endswith(".pdf")) else None
    if not pdf:
        pdf = latest_artifact(artifacts, preferred_types=["pdf"])

    artifact = pdf or latest or latest_artifact(
        artifacts,
        preferred_types=["docx", "report", "markdown", "commands", "code", "text"],
    )

    metadata = (artifact.get("metadata") or {}) if artifact else {}
    content = (
        metadata.get("source_content")
        or state.get("latest_export_source_content")
        or state.get("report_draft")
        or state.get("latest_report")
        or state.get("generated_commands")
        or state.get("latest_code")
        or state.get("latest_commands")
        or state.get("latest_answer")
        or ""
    )

    if looks_like_artifact_status(content):
        content = (
            metadata.get("source_content")
            or state.get("latest_export_source_content")
            or state.get("report_draft")
            or state.get("latest_report")
            or state.get("generated_commands")
            or state.get("latest_code")
            or state.get("latest_commands")
            or ""
        )

    if artifact and not content:
        content = artifact.get("content_preview") or ""

    kind = metadata.get("source_kind") or (artifact.get("type") if artifact else "content")
    title = (artifact.get("title") or artifact.get("filename") if artifact else None) or "Requested Content"

    return {"artifact": artifact, "content": content, "kind": kind, "title": title}


def ensure_email_attachment_for_api(state: Dict[str, Any]) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    """
    Reuse latest PDF if available. Otherwise export source content to PDF.
    """
    source = select_email_source_for_api(state)
    artifact = source.get("artifact")
    content = source.get("content") or ""

    if artifact and (artifact.get("type") == "pdf" or str(artifact.get("path", "")).lower().endswith(".pdf")):
        metadata = artifact.get("metadata") or {}
        if content and not metadata.get("source_content"):
            artifact["metadata"] = {
                **metadata,
                "source_content": content,
                "source_content_preview": content[:1200],
                "source_kind": source.get("kind") or "artifact",
            }
        return artifact, source

    if content:
        filename = safe_filename(source.get("title") or "email_attachment", default="email_attachment")
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"
        pdf_artifact = export_pdf_from_markdown(
            content,
            filename=filename,
            title=source.get("title") or "Requested Content",
            created_from="api_email_followup",
        )
        metadata = pdf_artifact.get("metadata") or {}
        pdf_artifact["metadata"] = {
            **metadata,
            "source_content": content,
            "source_content_preview": content[:1200],
            "source_kind": source.get("kind") or "content",
        }
        return pdf_artifact, {**source, "artifact": pdf_artifact}

    return artifact, source


def email_subject_for_api(source: dict[str, Any], attachment: Optional[dict[str, Any]]) -> str:
    kind = (source.get("kind") or "").lower()
    if kind == "report":
        return "Requested Report"
    if kind in {"commands", "code"}:
        return "Requested Commands / Code"
    if attachment and (attachment.get("type") == "pdf" or str(attachment.get("path", "")).lower().endswith(".pdf")):
        return "Requested PDF"
    return "Requested Content"


def email_body_for_api(source: dict[str, Any], lang: str) -> str:
    content = source.get("content") or ""
    summary = short_preview(content, 550) if content else "The requested file is attached."
    if lang == "ar":
        return (
            "مرحبًا،\n\n"
            "أرفقت لك الملف المطلوب.\n\n"
            "ملخص سريع:\n"
            f"{summary}\n\n"
            "ستجد التفاصيل الكاملة داخل الملف المرفق.\n\n"
            "تحياتي،\n"
            "Shieldy"
        )
    return (
        "Hi,\n\n"
        "I've attached the requested file.\n\n"
        "Quick summary:\n"
        f"{summary}\n\n"
        "Please see the attachment for the full details.\n\n"
        "Best,\n"
        "Shieldy"
    )


def has_usable_email_context(state: Dict[str, Any]) -> bool:
    source = select_email_source_for_api(state)
    return bool(source.get("artifact") or source.get("content"))



def requested_output_format(text: str) -> Optional[str]:
    lowered = (text or "").lower()
    if any(t in lowered for t in ["docx", ".docx", "word", "وورد", "ورد"]):
        return "docx"
    if any(t in lowered for t in ["pdf", "بي دي اف", "بى دى اف"]):
        return "pdf"
    if any(t in lowered for t in ["markdown", ".md", "ماركداون"]):
        return "markdown"
    return None


def is_report_or_content_generation_request(text: str) -> bool:
    """
    Detect new-content requests that need planning/generation, not export of prior content.
    Examples:
    - make pdf about risk of api
    - create report on cloud security
    - write a word document about X
    - اعمل تقرير عن مخاطر API
    """
    lowered = (text or "").strip().lower()
    if not lowered:
        return False

    creation_words = [
        "make", "create", "write", "generate", "prepare", "build",
        "اعمل", "اكتب", "جهز", "حضّر", "حضر",
    ]
    output_words = [
        "report", "pdf", "docx", "word", "markdown", "document", "file",
        "تقرير", "وورد", "ورد", "ملف", "بي دي اف", "بى دى اف",
    ]
    topic_markers = [
        "about", "on ", "regarding", "concerning", "for ", "risk of", "risks of",
        "security of", "analysis of", "عن", "حول", "بخصوص", "مخاطر", "تحليل",
    ]
    reference_only = [
        "make it", "make this", "make them", "export it", "export this", "save it",
        "save this", "convert it", "convert this", "previous", "last answer",
        "الكلام ده", "اللي فات", "الرد ده", "ده pdf", "دي pdf",
    ]

    has_creation = any(w in lowered for w in creation_words)
    has_output = any(w in lowered for w in output_words)
    has_topic = any(m in lowered for m in topic_markers)
    is_reference = any(m in lowered for m in reference_only)

    return has_creation and has_output and has_topic and not is_reference


def extract_generation_topic(text: str) -> str:
    raw = (text or "").strip()
    patterns = [
        r"(?:make|create|write|generate|prepare|build)\s+(?:a\s+|an\s+)?(?:detailed\s+|professional\s+)?(?:pdf|report|docx|word|markdown|document|file)?\s*(?:about|on|regarding|concerning|for)\s+(.+)$",
        r"(?:اعمل|اكتب|جهز|حضّر|حضر)\s+(?:تقرير|pdf|بي\s*دي\s*اف|وورد|ورد|ملف)?\s*(?:عن|حول|بخصوص)\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            topic = match.group(1).strip(" .؟?؛;")
            topic = re.sub(r"\s+(?:and|و)\s+(?:send|email|ابعته|ابعت).*$", "", topic, flags=re.IGNORECASE).strip()
            if topic:
                return topic
    cleaned = raw
    cleaned = re.sub(r"\b(make|create|write|generate|prepare|build|pdf|report|docx|word|markdown|document|file|about|on|regarding|concerning|for)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(send|email|mail)\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .؟?")
    return cleaned or raw


def generate_report_from_topic(topic: str, raw_question: str, lang: str) -> str:
    """
    API-level fallback/fast path for multi-step requests like:
    'make pdf about risk of api'. This avoids router failures that export the
    previous clarification instead of generating the requested content.
    """
    prompt = f"""
User request:
{raw_question}

Topic:
{topic}

Write a professional, useful security report. Structure it with:
- Title
- Executive Summary
- Key risks / analysis
- Practical mitigations
- Recommended next steps
- Limitations / assumptions

If no external sources are provided, clearly state that the report is based on general security knowledge and the user's topic only. Do not invent citations.
""".strip()
    try:
        response = report_llm.invoke([
            SystemMessage(content=f"{GLOBAL_POLICY}\n\n{REPORT_AGENT_PROMPT}"),
            HumanMessage(content=prompt),
        ])
        text = (response.content or "").strip()
        if text:
            return text
    except Exception as exc:
        # Deterministic fallback keeps the app useful even if the report model fails.
        pass

    if lang == "ar":
        return f"""# تقرير: {topic}

## ملخص تنفيذي
هذا تقرير عام عن {topic}. لم يتم تزويدي بمصادر خارجية أو مستندات محددة، لذلك يعتمد التقرير على معرفة أمنية عامة وعلى الموضوع الذي طلبته فقط.

## أهم المخاطر
- ضعف التوثيق أو إدارة الصلاحيات.
- تسريب بيانات حساسة عبر endpoints أو logs.
- غياب rate limiting أو مراقبة الاستخدام.
- أخطاء في التحقق من المدخلات.
- ضعف إدارة المفاتيح والأسرار.

## التوصيات
- تطبيق مصادقة وتفويض واضحين.
- استخدام rate limiting وlogging آمن.
- مراجعة endpoints الحساسة واختبارها.
- عدم تسجيل secrets أو tokens.
- استخدام monitoring وalerting.

## القيود
هذا تقرير عام بدون مصادر أو تفاصيل نظام محدد. للحصول على تقرير أدق، زودني بنوع النظام أو API أو المتطلبات الفنية.
""".strip()

    return f"""# Report: {topic}

## Executive Summary
This is a general security report about {topic}. No external sources or system-specific documentation were provided, so the analysis is based on general security knowledge and the user's requested topic only.

## Key Risks
- Weak authentication or authorization controls.
- Sensitive data exposure through endpoints, logs, or error messages.
- Missing rate limiting, abuse detection, or monitoring.
- Input validation flaws that can lead to injection or data corruption.
- Poor secret management for tokens, API keys, and credentials.

## Practical Mitigations
- Enforce strong authentication and least-privilege authorization.
- Validate and sanitize inputs at every trust boundary.
- Apply rate limits, quotas, and abuse monitoring.
- Avoid logging secrets and redact sensitive fields.
- Rotate keys regularly and store secrets in a secure vault.

## Recommended Next Steps
1. Document the API surface and sensitive endpoints.
2. Review authentication, authorization, and rate-limit controls.
3. Test for common API vulnerabilities.
4. Add logging, monitoring, and alerting for suspicious behavior.
5. Create a remediation plan for discovered gaps.

## Limitations
This report is general. For a deeper assessment, provide the API type, endpoints, auth model, and any existing documentation.
""".strip()


def make_artifact_from_report(report_text: str, fmt: Optional[str], topic: str) -> dict[str, Any]:
    export_format = fmt or "pdf"

    filename = build_export_filename(
        topic or "security_report",
        fallback="security_report",
        export_format=export_format,
    )

    if export_format == "docx":
        artifact = export_docx_from_markdown(
            report_text,
            filename=filename,
            title=f"Report: {topic}",
            created_from="api_report_generation",
        )
    elif export_format == "markdown":
        artifact = save_markdown(
            report_text,
            filename=filename,
            title=f"Report: {topic}",
            artifact_type="report",
            created_from="api_report_generation",
        )
    else:
        artifact = export_pdf_from_markdown(
            report_text,
            filename=filename,
            title=f"Report: {topic}",
            created_from="api_report_generation",
        )

    metadata = artifact.get("metadata") or {}
    artifact["metadata"] = {
        **metadata,
        "source_content": report_text,
        "source_content_preview": report_text[:1200],
        "source_kind": "report",
        "topic": topic,
        "created_by": "api_multi_task_report_pipeline",
    }
    return artifact



def append_turn_messages(state: Dict[str, Any], user_text: str, assistant_text: str, route: str) -> Dict[str, Any]:
    messages = state.get("messages", []) or []
    if user_text and (not messages or messages[-1].get("role") != "user" or messages[-1].get("content") != user_text):
        messages.append({"role": "user", "content": user_text, "metadata": {}})
    if assistant_text:
        messages.append({"role": "assistant", "content": assistant_text, "metadata": {"route": route}})
    state["messages"] = messages[-40:]
    return state

def build_state_from_request(req: ChatRequest) -> Dict[str, Any]:
    thread_id = make_thread_id(req)
    user_id = os.getenv("USER_ID", "local-user")

    previous_state = SESSION_STATES.get(thread_id)
    if previous_state:
        state = dict(previous_state)
        state["thread_id"] = thread_id
        state["user_id"] = state.get("user_id") or user_id
    else:
        state = create_initial_state(thread_id=thread_id, user_id=user_id)

    # Restore visible chat history from UI localStorage. Keep this in sync with
    # the frontend while preserving richer backend-only state (artifacts, drafts).
    messages = []
    for msg in req.chat_history[-40:]:
        if msg.role in ["user", "assistant"]:
            messages.append({"role": msg.role, "content": msg.content, "metadata": {}})

    if messages:
        state["messages"] = messages

    last_assistant_message = latest_assistant_from_history(req.chat_history)
    last_user_message = latest_user_from_history(req.chat_history)

    state = reset_turn_state(state, raw_input=req.question, image_path=req.image_path)

    # reset_turn_state clears per-turn text fields. Rehydrate useful previous
    # context from both the backend session and the visible UI history so
    # follow-ups after image analysis still work after reloads or server restarts.
    if last_assistant_message and not looks_like_artifact_status(last_assistant_message):
        if not state.get("latest_answer"):
            state["latest_answer"] = last_assistant_message
        if not state.get("latest_export_source_content"):
            state["latest_export_source_content"] = last_assistant_message
        if not state.get("latest_vision_output") and "[Image:" in (last_user_message or ""):
            state["latest_vision_output"] = last_assistant_message
            state["latest_image_analysis"] = {
                "image_path": state.get("image_path") or "",
                "image_type": "unknown",
                "topic": "uploaded image analysis",
                "visible_text": "",
                "summary": last_assistant_message,
                "explanation": last_assistant_message,
                "confidence": 0.7,
                "raw_output": last_assistant_message,
            }
        if not state.get("latest_text_output"):
            state["latest_text_output"] = last_assistant_message

    if last_user_message and not state.get("current_topic"):
        state["current_topic"] = "uploaded image analysis" if "[Image:" in last_user_message else last_user_message

    if req.regenerate:
        state["regenerate"] = True
        state["previous_answer"] = req.previous_answer
        state["variation_seed"] = req.variation_seed

    return state


def update_session_state(thread_id: str, state: Dict[str, Any]) -> None:
    SESSION_STATES[thread_id] = state


@app.get("/status")
def status() -> Dict[str, Any]:
    try:
        info = rag_status()
        index_ready = bool(info.get("index_ready") or info.get("ready") or info.get("valid"))
        meta = info.get("meta") or info
        return {"index_ready": index_ready, "meta": meta}
    except Exception as exc:
        return {"index_ready": False, "meta": {}, "error": str(exc)}


@app.post("/build-index")
def build_index() -> Dict[str, Any]:
    try:
        result = build_index_from_folder()
        return {
            "ok": True,
            "files_count": result.get("files_count", 0),
            "chunks_count": result.get("chunks_count", 0),
            "meta": result,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """
    NDJSON stream.
    The full graph now streams progress events using graph.stream(...).
    Token streaming can be added later at model level, but this already prevents
    the UI from looking frozen while the graph is working.
    """

    async def event_generator():
        try:
            raw_question = req.question or ""
            image_path = req.image_path or None
            has_image = bool(image_path)
            lang = detect_request_language(raw_question)
            thread_id = make_thread_id(req)

            yield json.dumps({"type": "meta", "route": "starting"}, ensure_ascii=False) + "\n"

            # Build a turn state once so fast paths can see pending approvals,
            # artifacts, and previous content without entering LangGraph.
            state_for_fast_paths = build_state_from_request(req)
            pending_fast = state_for_fast_paths.get("pending_approval") or {}

            # Ultra-fast path: simple greetings/thanks/acknowledgements.
            # Do NOT enter LangGraph for these, unless an email confirmation is pending.
            if not has_image and is_simple_chat_fast_path(raw_question) and pending_fast.get("type") != "confirm_send_email":
                answer = simple_chat_reply(raw_question)
                state = state_for_fast_paths
                previous_substantive_answer = state.get("latest_answer")
                state.update({
                    "next_action": "final_response",
                    "latest_text_output": answer,
                    # Do not replace the last useful answer/report/artifact with small talk.
                    # Follow-ups like "send it" or "make it PDF" should still refer to
                    # the previous substantive content, not "I'm good".
                    "latest_answer": previous_substantive_answer or answer,
                    "latest_smalltalk_answer": answer,
                    "final_response": answer,
                    "orchestrator_decision": {
                        "understanding": "Simple chat fast path handled in API.",
                        "resolved_goal": "final_response",
                        "next_action": "final_response",
                        "reason": "No agent needed.",
                        "depends_on_previous_context": False,
                        "needs_user_input": False,
                        "missing_info": [],
                        "needs_approval": False,
                        "approval_type": "none",
                        "stop_condition": "respond",
                    },
                })
                messages = state.get("messages", [])
                if raw_question:
                    messages.append({"role": "user", "content": raw_question, "metadata": {}})
                messages.append({"role": "assistant", "content": answer, "metadata": {"route": "final_response"}})
                state["messages"] = messages[-40:]
                update_session_state(thread_id, state)
                yield json.dumps({"type": "done", "answer": answer, "route": "final_response"}, ensure_ascii=False) + "\n"
                return

            # Unified deterministic fast router.
            # V26: RAG is explicit only by default. Do not auto-route direct questions to RAG;
            # use RAG only when the user says from files/docs/knowledge base/vector database.
            # This must run BEFORE the LangGraph fallback.
            # If a route is clear (direct answer, explain more, safe commands, artifact, email),
            # execute it here and return. LangGraph is reserved for genuinely complex/unclear tasks.
            try:
                pending_email = (pending_fast.get("type") == "confirm_send_email")
                safety = evaluate_request(raw_question, has_image=has_image, pending_email=pending_email)
                decision = route_request(raw_question, has_image=has_image, safety=safety, state=state_for_fast_paths)

                # Absolute direct-first override: a normal educational question must never
                # enter RAG unless the user explicitly says files/docs/knowledge base/RAG.
                # This also protects you if an older cached router or state tries to mark
                # the turn as rag_answer.
                if decision.route == "rag_answer" and not wants_rag(raw_question):
                    decision = RouteDecision(
                        "direct_answer",
                        "Forced direct answer because RAG was not explicitly requested.",
                        topic=raw_question,
                        critic_level="none",
                    )
                elif is_general_direct_request(raw_question) and decision.route == "graph_fallback":
                    decision = RouteDecision(
                        "direct_answer",
                        "Forced direct answer for safe educational question.",
                        topic=raw_question,
                        critic_level="none",
                    )

                if decision.route == "refuse":
                    answer = safety.user_message_en if lang == "en" else safety.user_message_ar
                    answer = answer or (
                        "I can't help with that request, but I can help with a safe defensive alternative."
                        if lang == "en"
                        else "مش هقدر أساعد في الطلب ده، لكن أقدر أساعدك ببديل دفاعي آمن."
                    )
                    state = state_for_fast_paths
                    state.update({
                        "next_action": "refuse",
                        "latest_text_output": answer,
                        "final_response": answer,
                        "risk_level": "blocked",
                    })
                    state = append_turn_messages(state, raw_question, answer, "refuse")
                    update_session_state(thread_id, state)
                    yield json.dumps({"type": "done", "answer": answer, "route": "refuse"}, ensure_ascii=False) + "\n"
                    return

                if decision.route == "ask_scope":
                    answer = safety.user_message_en if lang == "en" else safety.user_message_ar
                    answer = answer or (
                        "Please clarify the authorized scope and goal before I continue."
                        if lang == "en"
                        else "وضحلي النطاق المصرح والهدف قبل ما أكمل."
                    )
                    state = state_for_fast_paths
                    state.update({
                        "next_action": "ask_user",
                        "latest_text_output": answer,
                        "final_response": answer,
                        "risk_level": "needs_clarification",
                    })
                    state = append_turn_messages(state, raw_question, answer, "ask_user")
                    update_session_state(thread_id, state)
                    yield json.dumps({"type": "done", "answer": answer, "route": "ask_user"}, ensure_ascii=False) + "\n"
                    return

                if decision.route != "graph_fallback":
                    state, answer, route = execute_fast_workflow(
                        decision,
                        state_for_fast_paths,
                        raw_question,
                        response_language(raw_question, lang),
                        image_path=image_path,
                    )
                    if not answer:
                        answer = (
                            "I couldn't produce a response for this fast route, so please try rephrasing."
                            if lang == "en"
                            else "معرفتش أطلع رد للمسار السريع ده، جرّب تعيد صياغة الطلب."
                        )
                        route = route or "final_response"
                    state = append_turn_messages(state, raw_question, answer, route)
                    update_session_state(thread_id, state)
                    yield json.dumps({"type": "meta", "route": route}, ensure_ascii=False) + "\n"
                    yield json.dumps({"type": "done", "answer": answer, "route": route}, ensure_ascii=False) + "\n"
                    return

            except Exception as fast_exc:
                # Do not leak raw provider/internal errors to the user.
                print(f"[api fast_router] failed, falling back safely: {fast_exc}", flush=True)

            # Smart multi-step path: create a NEW report/document about a topic,
            # export it if requested, and optionally prepare an email draft in
            # the same turn. This prevents prompts like "make pdf about risk of api"
            # from being misread as "export the previous answer".
            if is_report_or_content_generation_request(raw_question):
                fmt = requested_output_format(raw_question) or "pdf"
                topic = extract_generation_topic(raw_question)
                yield json.dumps(
                    {"type": "status", "content": "Writing report..." if lang == "en" else "بكتب التقرير..."},
                    ensure_ascii=False,
                ) + "\n"
                report_text = generate_report_from_topic(topic, raw_question, lang)
                report_artifact = save_report_artifact(report_text)
                export_artifact = make_artifact_from_report(report_text, fmt, topic)

                state = state_for_fast_paths
                artifacts = state.get("artifacts", []) + [report_artifact, export_artifact]
                generated_files = state.get("generated_files", []) + [report_artifact, export_artifact]

                detected_email_for_report = find_email_in_text(raw_question)
                if detected_email_for_report and is_email_followup_request(raw_question):
                    source = {"kind": "report", "title": f"Report: {topic}", "content": report_text, "artifact": export_artifact}
                    subject = email_subject_for_api(source, export_artifact)
                    body = email_body_for_api(source, response_language(raw_question, lang))
                    draft = create_email_draft(
                        to=detected_email_for_report,
                        subject=subject,
                        body=body,
                        attachment_artifact_id=export_artifact.get("artifact_id"),
                        attachment_path=export_artifact.get("path"),
                    )
                    answer = (
                        f"I created the report, exported it as {fmt.upper()}, and prepared an email draft to {detected_email_for_report} with the attachment. Should I send it now?"
                        if lang == "en"
                        else f"كتبت التقرير، وحفظته بصيغة {fmt.upper()}، وجهزت مسودة إيميل لـ {detected_email_for_report} بالمرفق. أبعته دلوقتي؟"
                    )
                    state.update({
                        "email_draft": draft,
                        "latest_email_draft_id": draft.get("draft_id"),
                        "pending_approval": {
                            "type": "confirm_send_email",
                            "message": answer,
                            "email_draft": draft,
                            "artifact_id": export_artifact.get("artifact_id"),
                            "action": "email_send",
                            "metadata": {"to": detected_email_for_report, "created_by": "api_multi_task_report_pipeline"},
                        },
                        "active_task": {"type": "email", "status": "awaiting_confirmation", "draft_id": draft.get("draft_id")},
                        "next_action": "email_draft",
                        "latest_agent_output": {"type": "email_draft", "text": answer, "metadata": {"draft": draft, "artifact": export_artifact}},
                    })
                    route = "email_draft"
                else:
                    filename = artifact_display_name(export_artifact)
                    label = "PDF" if fmt == "pdf" else "Word" if fmt == "docx" else "Markdown"
                    answer = (f"{label} report ready: {filename}" if lang == "en" else f"تم تجهيز تقرير {label}: {filename}")
                    state.update({
                        "active_task": {
                            "type": "report_generation",
                            "status": "ready",
                            "topic": topic,
                            "latest_export_artifact_id": export_artifact.get("artifact_id"),
                            "available_actions": ["email", "export_pdf", "export_docx", "export_markdown"],
                        },
                        "next_action": "artifact_export",
                        "latest_agent_output": {"type": "artifact", "text": answer, "metadata": {"artifact": export_artifact, "report_artifact": report_artifact}},
                    })
                    route = "artifact_export"

                state.update({
                    "artifacts": artifacts,
                    "generated_files": generated_files,
                    "latest_artifact_id": export_artifact.get("artifact_id"),
                    "report_draft": report_text,
                    "latest_report": report_text,
                    "latest_report_id": report_artifact.get("artifact_id"),
                    "latest_answer": report_text,
                    "latest_export_source_content": report_text,
                    "latest_text_output": answer,
                    "final_response": answer,
                    "current_topic": topic,
                })
                state = append_turn_messages(state, raw_question, answer, route)
                update_session_state(thread_id, state)
                yield json.dumps({"type": "done", "answer": answer, "route": route}, ensure_ascii=False) + "\n"
                return

            # Ultra-fast path: generic email follow-up with an explicit recipient.
            # This fixes cases like:
            # "send this PDF to email someone@example.com".
            # It is fully generic: no recipient address is hardcoded.
            detected_email = find_email_in_text(raw_question)
            if (
                detected_email
                and is_email_followup_request(raw_question)
                and has_usable_email_context(state_for_fast_paths)
            ):
                attachment, source = ensure_email_attachment_for_api(state_for_fast_paths)

                artifacts = state_for_fast_paths.get("artifacts", []) or []
                generated_files = state_for_fast_paths.get("generated_files", []) or []

                if attachment and not any(a.get("artifact_id") == attachment.get("artifact_id") for a in artifacts):
                    artifacts = artifacts + [attachment]
                    generated_files = generated_files + [attachment]

                subject = email_subject_for_api(source, attachment)
                body = email_body_for_api(source, response_language(raw_question, lang))

                draft = create_email_draft(
                    to=detected_email,
                    subject=subject,
                    body=body,
                    attachment_artifact_id=attachment.get("artifact_id") if attachment else None,
                    attachment_path=attachment.get("path") if attachment else None,
                )

                answer = (
                    f"I prepared the email draft to {detected_email} with the attachment. Should I send it now?"
                    if lang == "en"
                    else f"جهزت مسودة الإيميل لـ {detected_email} ومعاها الملف المرفق. أبعته دلوقتي؟"
                )

                state = state_for_fast_paths
                state.update({
                    "artifacts": artifacts,
                    "generated_files": generated_files,
                    "latest_artifact_id": attachment.get("artifact_id") if attachment else state.get("latest_artifact_id"),
                    "email_draft": draft,
                    "latest_email_draft_id": draft.get("draft_id"),
                    "pending_approval": {
                        "type": "confirm_send_email",
                        "message": answer,
                        "email_draft": draft,
                        "artifact_id": draft.get("attachment_artifact_id"),
                        "action": "email_send",
                        "metadata": {"to": detected_email, "created_by": "api_email_followup_fast_path"},
                    },
                    "active_task": {"type": "email", "status": "awaiting_confirmation", "draft_id": draft.get("draft_id")},
                    "next_action": "email_draft",
                    "latest_agent_output": {
                        "type": "email_draft",
                        "text": answer,
                        "metadata": {"draft": draft, "source": source, "attachment": attachment},
                    },
                    "latest_text_output": answer,
                    "final_response": answer,
                })
                update_session_state(thread_id, state)

                yield json.dumps({"type": "done", "answer": answer, "route": "email_draft"}, ensure_ascii=False) + "\n"
                return

            # Ultra-fast path: export the latest visible assistant answer directly.
            # This avoids the full graph for a pure file conversion request.
            previous_answer = latest_assistant_from_history(req.chat_history)
            state_for_fast_export = state_for_fast_paths
            content_to_export = (
                state_for_fast_export.get("report_draft")
                or state_for_fast_export.get("latest_report")
                or state_for_fast_export.get("generated_commands")
                or state_for_fast_export.get("latest_code")
                or state_for_fast_export.get("latest_commands")
                or state_for_fast_export.get("latest_answer")
                or previous_answer
            )

            if is_direct_pdf_export_request(raw_question) and not is_new_content_pdf_request(raw_question) and not is_email_followup_request(raw_question) and content_to_export:
                yield json.dumps(
                    {"type": "status", "content": "Exporting PDF..." if lang == "en" else "بجهز ملف PDF..."},
                    ensure_ascii=False,
                ) + "\n"

                artifact = export_pdf_from_markdown(
                    content_to_export,
                    filename="exported_response.pdf",
                    title="Exported Response",
                    created_from="api_direct_pdf_export",
                )

                path = artifact.get("path")
                filename = artifact_display_name(artifact)
                note = (artifact.get("metadata") or {}).get("note")
                is_pdf = artifact.get("type") == "pdf" or str(path).lower().endswith(".pdf")

                if is_pdf:
                    answer = f"PDF ready: {filename}" if lang == "en" else f"تم تجهيز ملف PDF: {filename}"
                else:
                    answer = (
                        f"I could not create a PDF, so I saved a fallback file instead: {filename}"
                        if lang == "en"
                        else f"معرفتش أطلع PDF، فحفظت ملف بديل هنا: {filename}"
                    )
                if note:
                    answer += f"\nNote: {note}" if lang == "en" else f"\nملاحظة: {note}"

                state = state_for_fast_export

                # Keep the original content attached to the artifact metadata.
                # This is important for follow-ups like:
                # "send it as email to x@y.com"
                # The email agent should summarize/attach the exported PDF,
                # not use the status message "PDF ready: ..." as the email body.
                artifact_metadata = artifact.get("metadata") or {}
                artifact["metadata"] = {
                    **artifact_metadata,
                    "source_content": content_to_export,
                    "source_content_preview": (content_to_export or "")[:1200],
                    "source_kind": (
                        "report" if state.get("report_draft") or state.get("latest_report")
                        else "commands" if state.get("generated_commands") or state.get("latest_code") or state.get("latest_commands")
                        else "answer"
                    ),
                }

                previous_latest_answer = state.get("latest_answer")
                previous_latest_text_output = state.get("latest_text_output")

                artifacts = state.get("artifacts", []) + [artifact]
                state.update({
                    "artifacts": artifacts,
                    "generated_files": state.get("generated_files", []) + [artifact],
                    "latest_artifact_id": artifact.get("artifact_id"),
                    "latest_agent_output": {
                        "type": "artifact",
                        "text": answer,
                        "metadata": {
                            "artifact": artifact,
                            "source_content_preview": (content_to_export or "")[:1200],
                        },
                    },
                    "latest_text_output": answer,
                    "final_response": answer,

                    # Do NOT replace the real latest content with "PDF ready: ..."
                    # Keep latest_answer useful for future email/report/explain follow-ups.
                    "latest_answer": previous_latest_answer or content_to_export,
                    "latest_export_source_content": content_to_export,

                    "active_task": {
                        **(state.get("active_task") or {}),
                        "type": "artifact",
                        "status": "ready",
                        "latest_export_artifact_id": artifact.get("artifact_id"),
                        "source_content_preview": (content_to_export or "")[:1200],
                        "available_actions": ["email", "export_pdf", "export_docx", "export_markdown"],
                    },
                })
                update_session_state(thread_id, state)

                yield json.dumps({"type": "done", "answer": answer, "route": "artifact_export"}, ensure_ascii=False) + "\n"
                return

            state = build_state_from_request(req)
            callbacks = build_langfuse_callbacks()

            config = {
                "configurable": {"thread_id": state.get("thread_id", thread_id)},
                "callbacks": callbacks,
                "metadata": {
                    "chat_id": thread_id,
                    "user_id": state.get("user_id"),
                    "app": "Shieldy",
                },
            }

            final_state = None
            last_status = ""

            yield json.dumps(
                {"type": "status", "content": "Thinking..." if lang == "en" else "بفكر..."},
                ensure_ascii=False,
            ) + "\n"

            for event in graph.stream(state, config=config, stream_mode="values"):
                if not isinstance(event, dict):
                    continue
                final_state = event
                route = (
                    event.get("next_action")
                    or (event.get("orchestrator_decision") or {}).get("next_action")
                    or (event.get("latest_agent_output") or {}).get("type")
                    or "working"
                )
                status_text = str(route).replace("_", " ")
                if status_text != last_status:
                    last_status = status_text
                    yield json.dumps({"type": "status", "content": status_text, "route": route}, ensure_ascii=False) + "\n"

            flush_langfuse_callbacks(callbacks)

            result = final_state or state

            answer = (
                result.get("final_response")
                or result.get("latest_text_output")
                or ((result.get("latest_agent_output") or {}).get("text"))
                or "No response."
            )
            route = (
                result.get("next_action")
                or (result.get("orchestrator_decision") or {}).get("next_action")
                or (result.get("latest_agent_output") or {}).get("type")
                or "direct"
            )
            result = append_turn_messages(result, raw_question, answer, route)
            update_session_state(thread_id, result)

            yield json.dumps({"type": "meta", "route": route}, ensure_ascii=False) + "\n"
            yield json.dumps({"type": "done", "answer": answer, "route": route}, ensure_ascii=False) + "\n"

        except Exception as exc:
            try:
                flush_langfuse_callbacks(locals().get("callbacks", []))
            except Exception:
                pass
            yield json.dumps({"type": "error", "detail": str(exc)}, ensure_ascii=False) + "\n"

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")


@app.post("/chat/stream/vision")
async def chat_stream_vision(
    question: str = Form(""),
    chat_id: Optional[str] = Form(None),
    chat_history: str = Form("[]"),
    regenerate: bool = Form(False),
    previous_answer: str = Form(""),
    variation_seed: Optional[int] = Form(None),
    image: UploadFile = File(...),
):
    """
    Multipart UI endpoint for image + text chat.

    Important:
    This endpoint executes the vision fast route directly. It does NOT bounce
    through the normal /chat/stream graph fallback, because a provider error in
    vision used to fall back into the generic safety graph and produce the
    confusing "هل الطلب ده آمن ومصرّح بيه؟" message.
    """
    image_path = await save_uploaded_vision_image(image)
    req = ChatRequest(
        question=(question or "Analyze this image."),
        chat_history=parse_chat_history_form(chat_history),
        chat_id=chat_id,
        regenerate=regenerate,
        previous_answer=previous_answer or "",
        variation_seed=variation_seed,
        image_path=image_path,
    )

    async def event_generator():
        raw_question = req.question or "Analyze this image."
        lang = detect_request_language(raw_question)
        lang_for_reply = response_language(raw_question, lang)
        thread_id = make_thread_id(req)

        try:
            yield json.dumps({"type": "meta", "route": "vision_upload_received"}, ensure_ascii=False) + "\n"

            state = build_state_from_request(req)
            safety = evaluate_request(raw_question, has_image=True, pending_email=False)

            if safety.action == "refuse":
                answer = safety.user_message_en if lang_for_reply == "en" else safety.user_message_ar
                answer = answer or ("I can't help with that image request." if lang_for_reply == "en" else "مش هقدر أساعد في طلب الصورة ده.")
                state = append_turn_messages(state, raw_question, answer, "refuse")
                update_session_state(thread_id, state)
                yield json.dumps({"type": "done", "answer": answer, "route": "refuse"}, ensure_ascii=False) + "\n"
                return

            decision = RouteDecision(
                route="vision_workflow",
                reason="Direct multipart image upload from UI.",
                topic=raw_question,
                critic_level="light",
                steps=[{"agent": "vision_agent"}],
            )

            state, answer, route = execute_fast_workflow(
                decision,
                state,
                raw_question,
                lang_for_reply,
                image_path=image_path,
            )

            if not answer:
                answer = "وصلت الصورة للـ vision route، بس مفيش رد رجع من موديل الرؤية. راجع VISION_MODEL في .env." if lang_for_reply == "ar" else "The image reached the vision route, but no response came back from the vision model. Check VISION_MODEL in .env."
                route = "vision_analysis"

            state = append_turn_messages(state, raw_question, answer, route or "vision_analysis")
            update_session_state(thread_id, state)
            yield json.dumps({"type": "meta", "route": route or "vision_analysis"}, ensure_ascii=False) + "\n"
            yield json.dumps({"type": "done", "answer": answer, "route": route or "vision_analysis"}, ensure_ascii=False) + "\n"

        except Exception as exc:
            print(f"[vision endpoint] failed: {exc}", flush=True)
            answer = "الصورة وصلت للباك إند، لكن حصل خطأ في مسار الـ vision. راجع لوج السيرفر و VISION_MODEL في .env." if lang_for_reply == "ar" else "The image reached the backend, but the vision route failed. Check the server logs and VISION_MODEL in .env."
            yield json.dumps({"type": "done", "answer": answer, "route": "vision_error"}, ensure_ascii=False) + "\n"

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")


# Optional: serve the frontend from FastAPI too.
# Put index.html and assets inside ./frontend
if os.path.isdir("frontend"):
    app.mount("/artifacts", StaticFiles(directory=str(OUTPUT_DIR)), name="artifacts")
    app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
