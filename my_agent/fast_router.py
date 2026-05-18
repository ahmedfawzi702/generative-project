from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from my_agent.security_gate import SafetyDecision

Route = Literal[
    "refuse",
    "ask_scope",
    "send_email",
    "direct_answer",
    "explanation_agent_fast",
    "context_followup_fast",
    "vision_workflow",
    "web_answer",
    "web_research_agent_fast",
    "web_report_artifact_email",
    "web_report_artifact",
    "rag_answer",
    "rag_code_artifact",
    "code_artifact",
    "safe_code_guidance",
    "safe_command_workflow",
    "command_code_agent_fast",
    "report_artifact_email",
    "report_artifact",
    "artifact_export",
    "email_draft",
    "graph_fallback",
]


@dataclass
class RouteDecision:
    route: Route
    reason: str
    topic: str = ""
    email: Optional[str] = None
    output_format: Optional[str] = None
    steps: list[dict] = field(default_factory=list)
    critic_level: Literal["none", "light", "full"] = "light"
    needs_confirmation: bool = False


EMAIL_RE = re.compile(r"[\w.\-+%]+@[\w.\-]+\.[A-Za-z]{2,}")


def find_email(text: str) -> Optional[str]:
    match = EMAIL_RE.search(text or "")
    return match.group(0).strip(".,;:()[]{}<>") if match else None


def has_any(text: str, markers: list[str]) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in markers)


def is_yes(text: str) -> bool:
    return (text or "").strip().lower() in {"yes", "y", "send", "send it", "confirm", "ok", "okay", "sure", "go ahead", "تمام", "اوك", "أوك", "اه", "آه", "ايوه", "أيوه", "ابعته", "ابعت"}


def detect_output_format(text: str) -> Optional[str]:
    lowered = (text or "").lower()
    if any(x in lowered for x in ["pdf", "بي دي اف", "بى دى اف"]):
        return "pdf"
    if any(x in lowered for x in ["docx", ".docx", "word", "وورد", "ورد"]):
        return "docx"
    if any(x in lowered for x in ["markdown", ".md", "ماركداون"]):
        return "markdown"
    if any(x in lowered for x in ["txt", "text file", ".txt"]):
        return "text"
    return None


def extract_topic(text: str) -> str:
    raw = (text or "").strip()
    patterns = [
        r"(?:about|on|regarding|concerning|for)\s+(.+?)(?:\s+(?:and|then)\s+(?:make|save|export|send|email|write)|$)",
        r"(?:عن|حول|بخصوص|على)\s+(.+?)(?:\s+(?:و|وبعدين)\s*(?:اعمل|ابعته|احفظه|اكتب)|$)",
    ]
    for pattern in patterns:
        m = re.search(pattern, raw, flags=re.IGNORECASE)
        if m:
            topic = re.sub(EMAIL_RE, "", m.group(1)).strip(" .؟?،,")
            if topic:
                return topic
    cleaned = re.sub(EMAIL_RE, " ", raw)
    cleaned = re.sub(r"\b(make|create|write|generate|prepare|build|web search|search|report|pdf|docx|word|markdown|send|email|mail|as|it|about|on|and|then|to|please)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .؟?،,")
    return cleaned or raw


def wants_web(text: str) -> bool:
    """Detect web/research intent across many natural English/Arabic forms.

    This is intentionally broad because the fast router must catch obvious
    web/search requests before LangGraph. It supports:
    - search for X in web
    - search the web for X
    - find latest X online
    - get sources/references for X
    - research X / look up X
    - Arabic/Egyptian forms such as: ابحث، دور، سيرش، هات مصادر، أحدث.
    """
    raw = text or ""
    lowered = raw.lower().strip()
    if not lowered:
        return False

    # Avoid confusing local source-code requests with web sources.
    local_source_false_positives = [
        "source code", "local source", "source file", "source files",
        "مصدر الكود", "ملفات السورس",
    ]
    if any(x in lowered for x in local_source_false_positives):
        return False

    direct_markers = [
        # explicit web/search
        "web search", "search web", "search the web", "search online",
        "search on the web", "search in web", "search in the web",
        "search internet", "search the internet", "internet search",
        "online search", "google search", "browse", "browse web",
        "browse the web", "look up", "lookup", "google",
        # research / sources
        "research", "web research", "online research", "internet research",
        "find sources", "get sources", "collect sources", "list sources",
        "online sources", "web sources", "internet sources",
        "sources", "references", "citations", "source list",
        # freshness
        "latest", "current", "recent", "newest", "updated",
        "up to date", "up-to-date", "2024", "2025", "2026",
        # location on web
        "from the web", "on the web", "in web", "in the web",
        "from internet", "on internet", "online", "internet",
        # Arabic / Egyptian Arabic
        "ابحث", "بحث", "بحث ويب", "سيرش", "دور", "دورلي", "هات مصادر",
        "هاتلي مصادر", "مصادر", "مراجع", "من النت", "على النت",
        "اونلاين", "أونلاين", "احدث", "أحدث", "آخر", "اخر", "جديد",
    ]
    if has_any(lowered, direct_markers):
        return True

    patterns = [
        # search/find/get/collect ... web/online/internet/sources
        r"\b(?:search|find|get|fetch|collect|gather|look\s*up|research)\b\s+(?:for\s+|about\s+|on\s+)?(?:the\s+)?(?:latest|current|recent|updated|newest)?[\w\s\-\/\.]*\b(?:web|online|internet|sources|references|citations)\b",
        # web/online/internet ... for/about topic
        r"\b(?:web|online|internet)\b\s+(?:search|research|sources|references)\b",
        r"\b(?:search|research|sources|references)\b.*\b(?:for|about|on)\b.*",
        # topic + freshness/source markers
        r"\b(?:latest|current|recent|updated|newest)\b.*\b(?:owasp|masvs|cve|nist|android|mobile|api|security|cybersecurity|risk|risks)\b",
        r"\b(?:owasp|masvs|cve|nist|android|mobile|api|security|cybersecurity|risk|risks)\b.*\b(?:latest|current|recent|updated|web|online|internet|sources|references)\b",
        # "X in web/on web"
        r"\b(?:search|find|research|look\s*up)\b.*\b(?:in|on|from)\s+(?:the\s+)?(?:web|internet|online)\b",
        # Arabic flexible forms
        r"(?:ابحث|دور|سيرش|هات|اجمع)\s+.*(?:مصادر|مراجع|الويب|النت|اونلاين|أونلاين|احدث|أحدث|آخر|اخر)",
        r"(?:مصادر|مراجع|احدث|أحدث|آخر|اخر)\s+.*(?:ويب|نت|اونلاين|أونلاين|موبايل|اندرويد|API|OWASP)",
    ]
    return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in patterns)

def _contains_explicit_phrase(text: str, phrase: str) -> bool:
    """Safe phrase matcher for explicit routing markers.

    Do NOT use naive substring matching for short markers like "rag" or "kb":
    "storage" contains "rag", which previously caused normal questions like
    "explain secure storage at android" to be routed to RAG.
    """
    lowered = (text or "").lower().strip()
    phrase = (phrase or "").lower().strip()
    if not lowered or not phrase:
        return False

    # Short English route keywords must be standalone words.
    if phrase in {"rag", "kb", "qdrant"}:
        return re.search(rf"(?<![a-z0-9_]){re.escape(phrase)}(?![a-z0-9_])", lowered) is not None

    # For phrases containing spaces, normal substring matching is acceptable.
    # This still requires the user to explicitly say "from my files",
    # "knowledge base", "indexed documents", etc.
    return phrase in lowered


def is_explicit_rag_request(text: str) -> bool:
    """Return True only when the user clearly asks to use local files/RAG.

    Normal educational questions like:
    - "explain secure storage at android"
    - "اشرحلي secure storage في Android"
    - "Summarize Android controls"

    must NOT go to RAG just because the vector database contains Android PDFs.
    """
    lowered = (text or "").lower().strip()
    if not lowered:
        return False

    explicit_phrases = [
        # standalone/technical explicit RAG markers
        "rag",
        "knowledge base", "kb",
        "vector database", "vector db", "qdrant",

        # indexed/local document intent
        "indexed documents", "indexed docs",
        "uploaded files", "uploaded documents",
        "from my files", "from my file",
        "from the files", "from the file",
        "from my documents", "from the documents",
        "from my docs", "from the docs",
        "according to the documents", "according to the docs",
        "according to my files",
        "based on the indexed documents", "based on my files",
        "use my files", "use the files", "use the documents",
        "answer from files", "answer from the files",
        "answer from the vector database",

        # Arabic explicit file/document intent
        "من ملفاتي", "من الملفات", "من الملف",
        "من المستندات", "من الدوكيومنت", "من الدوكيومنتس",
        "من قاعدة المعرفة", "من قاعدة البيانات",
        "حسب الملفات", "حسب المستندات",
        "بناء على الملفات", "بناءً على الملفات",
        "بناء على المستندات", "بناءً على المستندات",
        "استخدم الملفات", "استعمل الملفات",
        "جاوب من الملفات", "جاوب من ملفاتي",
    ]

    return any(_contains_explicit_phrase(lowered, phrase) for phrase in explicit_phrases)


def wants_rag(text: str) -> bool:
    # RAG is explicit-only. This function is intentionally strict.
    return is_explicit_rag_request(text)

def wants_code(text: str) -> bool:
    return has_any(text, [
        "code", "script", "command", "commands", "docker", "python", "javascript",
        "bash", "powershell", "shell", "regex", "grep", "rg",
        # Mobile / Android security command workflows
        "adb", "apk", "apktool", "jadx", "decompile", "reverse engineering",
        "static analysis", "androidmanifest", "manifest", "review api endpoints",
        "hardcoded api key", "hardcoded api keys", "local android project",
        "اكتب كود", "كود", "سكربت", "اوامر", "أوامر", "كوماند",
    ])


def wants_report(text: str) -> bool:
    return has_any(text, ["report", "write-up", "document", "تقرير", "ريبورت"])


def wants_email(text: str) -> bool:
    return find_email(text) is not None and has_any(text, ["email", "e-mail", "mail", "send", "send it", "send this", "ابعته", "ابعت", "ارسله", "إيميل", "ايميل"])


def wants_artifact(text: str) -> bool:
    return detect_output_format(text) is not None or has_any(text, ["save", "export", "download", "file", "احفظ", "طلع", "صدر", "صدّر", "ملف"])


def wants_explicit_artifact_output(text: str) -> bool:
    """Return True only when the user clearly asks for a file/export.

    This is stricter than wants_artifact(). It prevents web queries like
    "find online sources about Android..." from being treated as "make a PDF".
    Sources/references are research evidence, not output files.
    """
    lowered = (text or "").lower().strip()
    if not lowered:
        return False

    # Explicit extensions/formats always mean an artifact.
    if detect_output_format(lowered) is not None:
        return True

    explicit_phrases = [
        "make it a file", "make this a file", "create a file", "generate a file",
        "save as", "save it as", "save this as", "export as", "export it as",
        "download as", "download it", "download this", "attach as",
        "as a pdf", "as pdf", "as a docx", "as word", "as markdown",
        "pdf file", "word file", "docx file", "markdown file", "text file",
        "output file", "attachment", "attach it", "attach this",
        "احفظه ك", "احفظها ك", "صدّره", "صدره", "صدّرها", "صدرها",
        "طلعه pdf", "طلعها pdf", "ملف pdf", "ملف وورد", "ملف markdown",
        "كمرفق", "ارفقه", "أرفقه",
    ]
    if has_any(lowered, explicit_phrases):
        return True

    # Generic words like "file" should only count when paired with an output verb.
    output_verbs = ["save", "export", "download", "create", "generate", "make", "prepare", "attach", "احفظ", "صدر", "صدّر", "طلع", "جهز", "ارفق"]
    file_words = ["file", "document", "attachment", "ملف", "مرفق"]
    return any(v in lowered for v in output_verbs) and any(w in lowered for w in file_words)



def contains_secret_logging_topic(text: str) -> bool:
    """Detect defensive tasks about secrets/API keys/tokens appearing in logs."""
    lowered = (text or "").lower()
    sensitive_markers = [
        "secret", "secrets", "secret key", "api key", "apikey",
        "token", "tokens", "password", "credential", "credentials",
        "private key", "sensitive data", "سر", "اسرار", "توكن", "باسورد",
    ]
    log_markers = ["log", "logs", "logging", "logger", "لوج", "لوجز"]
    defensive_markers = [
        "find", "finding", "detect", "check", "scan", "redact", "mask",
        "fix", "secure", "secure version", "solution", "solutions", "suggest",
        "prevent", "avoid", "sanitize", "sample", "example", "vulnerable code",
        "how to fix", "حل", "حلول", "اكتشف", "افحص", "امنع", "أمن", "تأمين",
    ]
    return (
        any(x in lowered for x in sensitive_markers)
        and any(x in lowered for x in log_markers)
        and any(x in lowered for x in defensive_markers)
    )


def wants_safe_command_workflow(text: str) -> bool:
    """Detect safe, defensive command/script tasks that should not enter the deep graph."""
    lowered = (text or "").lower()
    command_markers = [
        "command", "commands", "script", "bash", "shell", "powershell",
        "python script", "docker", "regex", "grep", "ripgrep", "rg",
        "اوامر", "أوامر", "كوماند", "سكربت",
    ]
    defensive_markers = [
        "find", "detect", "check", "audit", "redact", "mask", "validate",
        "secure", "fix", "hardening", "prevent", "scan logs", "search logs",
        "افحص", "اكتشف", "راجع", "أمن", "تأمين", "امسح", "اخفي",
    ]
    if contains_secret_logging_topic(lowered):
        return True
    return any(x in lowered for x in command_markers) and any(x in lowered for x in defensive_markers)


def wants_code_guidance(text: str) -> bool:
    """Detect safe code examples/guidance, especially vulnerable-vs-secure examples."""
    lowered = (text or "").lower()
    if contains_secret_logging_topic(lowered):
        return True
    code_markers = ["code", "sample", "example", "secure version", "vulnerable code", "كود", "مثال"]
    defensive_markers = ["fix", "secure", "sanitize", "redact", "mask", "validate", "protect", "حل", "أمن"]
    return any(x in lowered for x in code_markers) and any(x in lowered for x in defensive_markers)




def wants_owned_static_reverse_engineering(text: str) -> bool:
    """Detect owned/authorized APK/static-analysis workflows for the fast command agent."""
    lowered = (text or "").lower()
    owned = any(x in lowered for x in [
        "my device", "my android", "my phone", "my apk", "my app", "my test apk",
        "i own", "own this", "owned device", "owned apk", "authorized", "permission",
        "approved", "lab", "ctf", "test apk", "test environment", "local apk",
        "جهازي", "موبايلي", "بتاعي", "ملكي", "عندي تصريح", "مصرح", "اختبار",
    ])
    app = any(x in lowered for x in [
        "vulnerable app", "apk", "android app", "application", "app", "تطبيق",
        "androidmanifest", "manifest",
    ])
    analysis = any(x in lowered for x in [
        "reverse engineering", "static analysis", "pull", "adb", "apktool", "jadx",
        "decompile", "review api endpoints", "api endpoints", "analyze", "analysis",
        "hardcoded", "secret", "secrets", "اسحب", "حلل",
    ])
    return owned and app and analysis


def wants_local_project_secret_scan(text: str) -> bool:
    """Detect safe local project secret-scanning/code generation tasks."""
    lowered = (text or "").lower()
    local_project = any(x in lowered for x in [
        "local project", "local android project", "my project", "source code",
        "repository", "repo", "codebase", "project files", "android project",
    ])
    secret_scan = any(x in lowered for x in [
        "hardcoded api key", "hardcoded api keys", "api key", "api keys",
        "secret", "secrets", "token", "tokens", "credential", "credentials",
    ])
    generation = any(x in lowered for x in [
        "write", "generate", "create", "script", "python", "bash", "scan",
        "detect", "find", "check", "audit",
    ])
    return local_project and secret_scan and generation


def has_previous_context(state: dict | None) -> bool:
    """Return True if a follow-up can reuse a previous useful result."""
    state = state or {}
    return bool(
        state.get("latest_answer")
        or state.get("latest_text_output")
        or state.get("latest_image_analysis")
        or state.get("latest_vision_output")
        or state.get("web_findings")
        or state.get("web_sources")
        or state.get("latest_report")
        or state.get("report_draft")
        or state.get("generated_commands")
        or state.get("latest_commands")
        or state.get("latest_code")
        or state.get("latest_export_source_content")
        or state.get("artifacts")
    )


def wants_context_followup(text: str, state: dict | None = None) -> bool:
    """Detect short follow-ups that refine/extract from the previous answer.

    Examples:
    - tell me final list of 10
    - give me the top 10 only
    - summarize those sources
    - extract the final checklist
    - رتبهم / هات النهائي / اديني العشرة
    """
    if not has_previous_context(state):
        return False

    lowered = (text or "").lower().strip()
    if not lowered:
        return False

    # Very common short follow-ups after an answer/image analysis.
    # These must reuse previous context instead of falling through to LangGraph safety.
    generic_followups = {
        "more", "tell me more", "tell me more about it", "more about it",
        "explain more", "explain it more", "explain this more",
        "continue", "go on", "keep going", "details", "more details",
        "yes", "yeah", "yep", "ok", "okay", "sure",
        "كمل", "كمّل", "قول اكتر", "قول أكثر", "اشرح اكتر", "اشرح أكثر",
        "وضح اكتر", "وضح أكثر", "تفاصيل", "زود", "ايوه", "أيوه", "اه", "آه", "تمام",
    }
    if lowered in generic_followups:
        return True

    if re.fullmatch(r"(?:tell|explain|describe|clarify)\s+me\s+more\s+(?:about\s+)?(?:it|this|that)", lowered):
        return True
    if re.fullmatch(r"(?:more|details|more\s+details)\s+(?:about\s+)?(?:it|this|that)", lowered):
        return True

    # Avoid hijacking clearly new tasks.
    if wants_web(lowered) or wants_rag(lowered):
        return False

    # Generic follow-up explanations should reuse the previous result instead
    # of going to the standalone explanation agent. This is especially important
    # after a vision answer, where users often say "explain more" or ask about
    # "point number 6" without re-uploading the image.
    if wants_explanation_followup(lowered):
        return True

    has_vision_context = bool(
        state and (
            state.get("latest_image_analysis")
            or state.get("latest_vision_output")
            or state.get("current_topic") == "uploaded image analysis"
        )
    )

    if has_vision_context:
        vision_markers = [
            "image", "photo", "picture", "screenshot", "screen", "diagram",
            "in the image", "from the image", "visible text", "ocr",
            "point", "number", "point number", "item", "bullet",
            "الصورة", "الصوره", "السكرين", "الاسكرين", "الصورة دي", "النص",
            "النقطة", "نقطة", "رقم", "البند",
        ]
        vision_actions = [
            "what", "where", "which", "explain", "summarize", "summarise",
            "extract", "list", "read", "describe", "ocr", "tell", "about",
            "clarify", "details", "detail", "more",
            "ايه", "إيه", "فين", "اشرح", "لخص", "استخرج", "اقرا", "اقرأ",
            "طلع", "هات", "اعرض", "وصف", "اوصف", "وضح", "فهمني",
        ]
        if has_any(lowered, vision_markers) and has_any(lowered, vision_actions):
            return True
        if re.search(r"\b(?:point|item|bullet|number)\s*(?:number\s*)?\d+\b", lowered):
            return True
        if re.search(r"(?:النقطة|نقطة|البند|رقم)\s*\d+", lowered):
            return True

    if wants_email(lowered) or wants_artifact(lowered):
        return False
    if wants_code(lowered) and not any(x in lowered for x in ["explain", "summarize", "list", "final"]):
        return False

    exact_or_short = [
        "final list", "final answer", "final result", "final version",
        "tell me final", "give me final", "give final", "show final",
        "list of 10", "top 10", "top ten", "final list of 10",
        "give me the list", "show me the list", "list them", "list it",
        "summarize it", "summarize this", "summarize them", "summarise it",
        "extract it", "extract them", "extract the list",
        "clean it", "organize it", "organise it", "order them",
        "rank them", "make it shorter", "short version",
        "what are they", "what are the items", "which are they",
        "point number", "point 1", "point 2", "point 3", "point 4", "point 5", "point 6",
        "item number", "bullet number", "number 1", "number 2", "number 3", "number 4", "number 5", "number 6",
        "from above", "based on the above", "based on this",
        "use previous", "use the previous", "from the previous",
        "هات النهائي", "النهائي", "القائمة النهائية", "الليستة النهائية",
        "هات الليستة", "اديني الليستة", "اعرض الليستة",
        "هات العشرة", "العشرة", "اول عشرة", "أفضل عشرة", "افضل عشرة",
        "رتبهم", "لخصهم", "لخصه", "استخرجهم", "استخرج القائمة",
        "قولهم", "اكتبهم", "نظمهم", "اختصره", "من اللي فوق",
        "بناء على اللي فوق", "من الرد السابق", "من النتيجة السابقة",
    ]
    if has_any(lowered, exact_or_short):
        return True

    patterns = [
        r"\b(?:tell|give|show|list|extract|summarize|summarise|rank|order|clean|organize|organise)\b.*\b(?:final|list|top\s*\d+|top\s*ten|items|results|sources|above|previous)\b",
        r"\b(?:final|top\s*\d+|top\s*ten|list\s+of\s+\d+)\b",
        r"\b(?:what|which)\s+are\s+(?:they|the\s+items|the\s+top\s+\d+)\b",
        r"\b(?:tell|explain|describe|clarify).*\b(?:point|item|bullet|number)\s*(?:number\s*)?\d+\b",
        r"\b(?:point|item|bullet|number)\s*(?:number\s*)?\d+\b",
        r"(?:هات|اديني|اعرض|اكتب|قول|استخرج|لخص|رتب|نظم|اشرح|وضح)\s+.*(?:النهائي|القائمة|الليستة|العشرة|النتائج|المصادر|اللي فوق|السابق|النقطة|نقطة|رقم|البند)",
        r"(?:القائمة|الليستة|النتيجة)\s+(?:النهائية|المختصرة)",
    ]
    return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in patterns)

def wants_explanation_followup(text: str) -> bool:
    """Detect follow-up requests that should use the real Explanation Agent.

    These are not normal first-turn educational questions. They usually mean
    the user did not understand a previous answer/artifact and wants a deeper,
    clearer explanation using conversation memory.
    """
    lowered = (text or "").lower().strip()
    if not lowered:
        return False

    markers = [
        "explain more", "explain again", "explain it again", "explain that",
        "more explanation", "i don't understand", "i dont understand",
        "didn't understand", "didnt understand", "not clear", "make it clearer",
        "simplify", "break it down", "go deeper", "in more detail",
        "وضح اكتر", "وضّح اكتر", "اشرح اكتر", "اشرح أكثر", "اشرح تاني",
        "اشرحلي تاني", "شرح تاني", "مش فاهم", "مش فاهمة", "مفهمتش",
        "مافهمتش", "لا مفهمتش", "مش واضح", "بسطهالي", "بسّطهالي",
        "فهمني", "فهمني اكتر", "يعني ايه الكلام ده", "الكلام ده مش واضح",
    ]
    return any(marker in lowered for marker in markers)


def is_general_direct_request(text: str) -> bool:
    """Generic direct-answer detector for safe educational/security questions.

    This is the hard guard that prevents normal questions from leaking into
    RAG or the long LangGraph path. Explicit web/code/artifact/email/report/RAG
    requests still keep their own routes.
    """
    lowered = (text or "").lower().strip()
    if not lowered:
        return False

    if wants_web(lowered) or wants_rag(lowered) or wants_code(lowered) or wants_report(lowered) or wants_artifact(lowered) or wants_email(lowered):
        return False

    educational_markers = [
        "what is", "what are", "why", "how does", "how do",
        "explain", "tell me about", "summarize", "summary",
        "best practices", "recommendations", "requirements", "controls",
        "risk", "risks", "secure", "security", "cybersecurity",
        "authentication", "authorization", "storage", "logging",
        "ما هو", "ما هي", "يعني ايه", "يعني إيه",
        "اشرح", "وضح", "فهمني", "لخص", "ملخص",
        "متطلبات", "ضوابط", "توصيات", "مخاطر", "أمان", "امن", "تأمين",
    ]
    return has_any(lowered, educational_markers) or lowered.endswith("?") or lowered.endswith("؟")


def is_simple_question(text: str) -> bool:
    lowered = (text or "").lower().strip()
    if not lowered:
        return False
    if wants_web(lowered) or wants_rag(lowered) or wants_code(lowered) or wants_report(lowered) or wants_artifact(lowered) or wants_email(lowered):
        return False
    markers = ["what is", "what are", "why", "explain", "tell me about", "how does", "how do", "best practices", "cybersecurity", "security", "authentication", "authorization", "ما هو", "ما هي", "يعني ايه", "اشرح", "وضح"]
    return has_any(lowered, markers) or lowered.endswith("?") or lowered.endswith("؟")


def route_request(text: str, *, has_image: bool, safety: SafetyDecision, state: dict | None = None) -> RouteDecision:
    raw = text or ""
    state = state or {}
    email = find_email(raw)
    fmt = detect_output_format(raw)
    pending = state.get("pending_approval") or {}

    if safety.action == "refuse":
        return RouteDecision("refuse", safety.reason, critic_level="none")
    if safety.action == "ask_scope":
        return RouteDecision("ask_scope", safety.reason, critic_level="full")

    if pending.get("type") == "confirm_send_email" and is_yes(raw):
        return RouteDecision("send_email", "User confirmed pending email send.", critic_level="light")

    if has_image:
        steps = [{"agent": "vision_agent"}]
        if fmt or wants_artifact(raw):
            steps.append({"agent": "artifact_agent", "format": fmt or "pdf"})
        if email and wants_email(raw):
            steps.append({"agent": "email_agent", "to": email, "requires_confirmation": True})
        return RouteDecision(
            "vision_workflow",
            "Image request with optional artifact/email workflow.",
            email=email,
            output_format=fmt,
            steps=steps,
            critic_level="light",
            needs_confirmation=bool(email),
        )

    # Real primary-agent fast paths. Explanation must be checked BEFORE generic context follow-up.
    # Otherwise phrases like "اشرح أكتر مش فاهم" may be misrouted to context_followup_fast.
    if wants_explanation_followup(raw):
        return RouteDecision(
            "explanation_agent_fast",
            "Follow-up explanation request; run the real explanation_agent directly.",
            topic=raw,
            critic_level="light",
        )

    if wants_context_followup(raw, state):
        return RouteDecision(
            "context_followup_fast",
            "Follow-up refinement/extraction from previous context; handle without LangGraph.",
            topic=raw,
            critic_level="light",
        )

    if wants_owned_static_reverse_engineering(raw):
        return RouteDecision(
            "command_code_agent_fast",
            "Owned-device static reverse engineering command workflow using the real command_code_agent.",
            topic=raw,
            email=email,
            output_format=fmt,
            critic_level="full",
            needs_confirmation=bool(email),
        )

    if wants_local_project_secret_scan(raw):
        return RouteDecision(
            "command_code_agent_fast",
            "Local project secret-scanning/code workflow using the real command_code_agent.",
            topic=raw,
            email=email,
            output_format=fmt or ("pdf" if wants_artifact(raw) else None),
            critic_level="full",
            needs_confirmation=bool(email),
        )

    if contains_secret_logging_topic(raw) or wants_safe_command_workflow(raw) or wants_code_guidance(raw):
        return RouteDecision(
            "command_code_agent_fast",
            "Safe defensive code/command request; run the real command_code_agent directly.",
            topic=raw,
            email=email,
            output_format=fmt or ("pdf" if wants_artifact(raw) else None),
            critic_level="full",
            needs_confirmation=bool(email),
        )

    if wants_web(raw):
        topic = extract_topic(raw)
        explicit_email = bool(email and wants_email(raw))
        explicit_artifact = wants_explicit_artifact_output(raw)

        # Important rule:
        # "sources", "references", "current", "latest", and "online" mean web evidence.
        # They do NOT mean "create a PDF/file" unless the user explicitly asks for
        # PDF/DOCX/Markdown/file/export/save/download/send.
        if explicit_email or explicit_artifact:
            route = "web_report_artifact_email" if explicit_email else "web_report_artifact"
            return RouteDecision(
                route,
                "Web research plus explicit artifact/email workflow.",
                topic=topic,
                email=email if explicit_email else None,
                output_format=fmt or ("pdf" if explicit_email else None),
                critic_level="full",
                needs_confirmation=explicit_email,
            )
        return RouteDecision(
            "web_research_agent_fast",
            "Web research request; answer in chat only because no explicit artifact/email was requested.",
            topic=topic,
            output_format=None,
            critic_level="full",
        )

    # Hard direct-first guard. If the user did not explicitly ask for RAG,
    # safe educational questions must be answered directly. This fixes cases
    # where Android/security questions were incorrectly routed to RAG.
    if is_general_direct_request(raw):
        return RouteDecision("direct_answer", "Safe educational/general question; explicit RAG was not requested.", topic=raw, critic_level="none")

    if wants_rag(raw) and wants_code(raw):
        return RouteDecision(
            "rag_code_artifact",
            "RAG plus code/artifact workflow.",
            topic=extract_topic(raw),
            output_format=fmt or "markdown",
            critic_level="full",
        )

    if wants_rag(raw):
        return RouteDecision("rag_answer", "Answer from local RAG documents.", topic=extract_topic(raw), critic_level="full")

    if wants_code(raw):
        return RouteDecision(
            "command_code_agent_fast",
            "Safe code/command workflow using the real command_code_agent.",
            topic=extract_topic(raw),
            email=email,
            output_format=fmt,
            critic_level="full",
            needs_confirmation=bool(email),
        )

    if wants_report(raw):
        route = "report_artifact_email" if email and wants_email(raw) else "report_artifact"
        return RouteDecision(
            route,
            "Report generation workflow.",
            topic=extract_topic(raw),
            email=email,
            output_format=fmt or "markdown",
            critic_level="full",
            needs_confirmation=bool(email),
        )

    if wants_email(raw) and (
        state.get("latest_artifact_id")
        or state.get("artifacts")
        or state.get("latest_image_analysis")
        or state.get("latest_vision_output")
        or state.get("latest_answer")
        or state.get("latest_report")
        or state.get("web_findings")
        or state.get("latest_export_source_content")
    ):
        return RouteDecision(
            "email_draft",
            "Email latest artifact/content.",
            email=email,
            output_format=fmt,
            critic_level="light",
            needs_confirmation=True,
        )

    if wants_artifact(raw) and (
        state.get("latest_answer")
        or state.get("latest_image_analysis")
        or state.get("latest_vision_output")
        or state.get("latest_report")
        or state.get("report_draft")
        or state.get("latest_code")
        or state.get("web_findings")
        or state.get("latest_export_source_content")
        or state.get("artifacts")
    ):
        return RouteDecision("artifact_export", "Export latest useful content.", output_format=fmt or "pdf", critic_level="light")

    if wants_artifact(raw):
        route = "report_artifact_email" if email and wants_email(raw) else "report_artifact"
        return RouteDecision(
            route,
            "New content artifact workflow.",
            topic=extract_topic(raw),
            email=email,
            output_format=fmt or "pdf",
            critic_level="full",
            needs_confirmation=bool(email),
        )

    if is_simple_question(raw) or is_general_direct_request(raw):
        return RouteDecision("direct_answer", "Simple educational/general question.", topic=raw, critic_level="none")

    return RouteDecision("direct_answer", "Default direct answer; no explicit complex workflow was requested.", topic=raw, critic_level="none")

