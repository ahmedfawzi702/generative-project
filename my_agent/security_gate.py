from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

SafetyAction = Literal["allow", "refuse", "ask_scope"]
RiskLevel = Literal["low", "medium", "high", "blocked"]


@dataclass
class SafetyDecision:
    action: SafetyAction
    risk_level: RiskLevel
    reason: str
    user_message_en: str = ""
    user_message_ar: str = ""
    blocked_parts: list[str] = field(default_factory=list)
    critic_level: Literal["none", "light", "full"] = "light"


PROMPT_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior)\s+instructions",
    r"disregard\s+(all\s+)?(previous|prior)\s+instructions",
    r"forget\s+(all\s+)?(previous|prior)\s+instructions",
    r"override\s+(the\s+)?(system|developer)\s+(prompt|instructions)",
    r"reveal\s+(your\s+)?(system|developer|hidden)\s+(prompt|instructions)",
    r"show\s+(me\s+)?(your\s+)?(system|developer|hidden)\s+(prompt|instructions|rules)",
    r"print\s+(your\s+)?(system|developer|hidden)\s+(prompt|instructions)",
    r"what\s+are\s+your\s+(system|developer|hidden)\s+(prompt|instructions|rules)",
    r"routing\s+rules.*(secret|hidden|internal)",
    r"dump\s+(your\s+)?(system|developer|hidden)",
    r"jailbreak",
    r"developer\s+mode",
    r"act\s+as\s+dan",
    r"تجاهل\s+(كل\s+)?التعليمات",
    r"تجاهل\s+التعليمات\s+السابقة",
    r"انس[ى|ي]\s+(كل\s+)?التعليمات",
    r"اكشف\s+(لي\s+)?(البرومبت|التعليمات|القواعد)",
    r"اظهر\s+(لي\s+)?(البرومبت|التعليمات|القواعد)",
    r"وريني\s+(البرومبت|تعليماتك|قواعدك)",
    r"قول\s*(لي)?\s+(البرومبت|تعليماتك|قواعدك)",
    r"اطبع\s+(البرومبت|تعليماتك|قواعدك)",
]


INTERNAL_ARCHITECTURE_EXTRACTION_PATTERNS = [
    r"\b(what|show|tell|give|print|reveal)\b.*\b(routing|router|routes|orchestrator|agent workflow|architecture|internal architecture|hidden workflow)\b.*\b(rule|rules|secret|secrets|prompt|internal|system)\b",
    r"\b(routing|router|routes|orchestrator|agent workflow|architecture|internal architecture|hidden workflow)\b.*\b(secret|secrets|hidden|internal|system prompt|rules)\b",
    r"\b(give|show|reveal|print)\b.*\b(secret|secrets)\b.*\b(arch|architecture|routing|workflow|agent)\b",
    r"\bhow\s+do\s+you\s+route\b.*\b(secret|hidden|internal|rules)\b",
    r"(قواعد|رولز|روتينج|routing).*?(الداخلية|السرية|secrets|secret|hidden)",
    r"(اكشف|وريني|هات|اظهر|اطبع).*?(routing|روتينج|architecture|الهيكل|الوكلاء|agents).*?(secret|secrets|سر|اسرار|داخلي|داخلية)",
]

SAFE_OWNED_STATIC_RE_PATTERNS = [
    r"\bmy\b.*\b(device|android|phone)\b.*\b(vulnerable app|apk|application|app)\b.*\b(reverse engineering|static analysis|pull|adb|jadx|apktool)\b",
    r"\b(vulnerable app|apk|application|app)\b.*\bmy\b.*\b(device|android|phone)\b.*\b(reverse engineering|static analysis|pull|adb|jadx|apktool)\b",
    r"\bowned\b.*\b(device|android|phone|app|apk)\b.*\b(reverse engineering|static analysis|pull|adb|jadx|apktool)\b",
    r"\bauthorized\b.*\b(app|apk|device|android)\b.*\b(reverse engineering|static analysis|pull|adb|jadx|apktool)\b",
    r"\blab\b.*\b(app|apk|device|android)\b.*\b(reverse engineering|static analysis|pull|adb|jadx|apktool)\b",
    r"(جهازي|موبايلي|تليفوني|ملكي|بتاعي).*?(apk|app|تطبيق).*?(static analysis|reverse engineering|adb|pull|اسحب|حلل)",
]

DANGEROUS_PATTERNS = [
    r"\bsteal\b.*\b(password|token|cookie|session|credential)s?\b",
    r"\bdump\b.*\b(password|token|cookie|session|credential)s?\b",
    r"\bextract\b.*\b(password|token|cookie|session|credential)s?\b",
    r"\bexfiltrat(e|ion)\b",
    r"\bbypass\b.*\b(authentication|login|2fa|mfa|otp)\b",
    r"\bphishing\b",
    r"\bcredential\s+theft\b",
    r"\bkeylogger\b",
    r"\bmalware\b",
    r"\bransomware\b",
    r"\breverse\s+shell\b",
    r"\bpersistence\b.*\b(evasion|stealth|hide)\b",
    r"\brm\s+-rf\s+(/|\*)",
    r"\bformat\s+[a-zA-Z]:",
    r"\bcurl\b.*\|\s*(bash|sh|zsh|python|perl)",
    r"\bwget\b.*\|\s*(bash|sh|zsh|python|perl)",
    r"سرقة\s+(باسورد|كلمات\s+السر|توكن|كوكيز|سيشن)",
    r"اسحب\s+(باسورد|كلمات\s+السر|توكن|كوكيز|سيشن)",
    r"اخترق\s+(حساب|ايميل|إيميل|موبايل|جهاز)",
    r"اعمل\s+(مالوير|فيروس|رانسوموير|صفحة\s+تصيد)",
]

AMBIGUOUS_TARGET_PATTERNS = [
    r"\bscan\b.*\b(ip|host|domain|network)\b",
    r"\bexploit\b.*\b(ip|host|domain|server|site|target)\b",
    r"\bextract\b.*\b(data|files|messages|photos|contacts)\b",
    r"\bpull\b.*\b(data|files|messages|photos|contacts)\b",
    r"\bphone\b",
    r"\bandroid\b",
    r"\biphone\b",
    r"\bwhatsapp\b",
    r"\btelegram\b",
    r"\baccount\b",
    r"موبايل",
    r"تليفون",
    r"جهاز",
    r"حساب",
    r"واتساب",
    r"تليجرام",
]

AUTHORIZED_OR_DEFENSIVE_HINTS = [
    r"\bmy\b",
    r"\bowned\b",
    r"\bauthorized\b",
    r"\bpermission\b",
    r"\bcompany-approved\b",
    r"\blab\b",
    r"\bctf\b",
    r"\bsandbox\b",
    r"\btest\s+environment\b",
    r"\bdefensive\b",
    r"\bhardening\b",
    r"\bsecure\b",
    r"\bprotect\b",
    r"\blocal\b",
    r"\blocal\s+project\b",
    r"\blocal\s+android\s+project\b",
    r"\bmy\s+project\b",
    r"\bsource\s+code\b",
    r"\brepository\b",
    r"\brepo\b",
    r"\bcodebase\b",
    r"\bhardcoded\s+api\s+keys?\b",
    r"\bi\s+own\b",
    r"\bown\s+this\b",
    r"\btest\s+apk\b",
    r"\bmy\s+apk\b",
    r"\bowned\s+apk\b",
    r"جهازي",
    r"بتاعي",
    r"ملكي",
    r"عندي\s+تصريح",
    r"مصرح",
    r"لاب",
    r"معمل",
    r"اختبار",
    r"حماية",
    r"تأمين",
]

LOW_RISK_EDUCATIONAL_MARKERS = [
    "what is", "what are", "why", "explain", "overview", "summarize", "summary",
    "requirements", "controls", "policy", "standard", "guideline", "checklist",
    "recommend", "recommendations", "mitigation", "mitigations",
    "best practice", "best practices",
    "how to secure", "how do i secure", "protect", "defend", "mitigation", "hardening",
    "authentication", "authorization", "cybersecurity", "security", "vulnerability explanation",
    "web search", "search web", "search online", "search the web", "search for",
    "research", "sources", "references", "latest", "current", "recent",
    "risk", "risks", "recommendation", "recommendations", "owasp", "masvs",
    "mobile security", "android security", "insecure data storage",
    "ما هو", "ما هي", "يعني ايه", "اشرح", "وضح", "لخص", "ملخص",
    "متطلبات", "ضوابط", "سياسة", "معيار", "توصيات", "قائمة", "ازاي احمي", "تأمين", "حماية", "ممارسات",
    "ابحث", "بحث", "سيرش", "مصادر", "احدث", "أحدث", "مخاطر", "توصيات",
]

SECRET_LOGGING_DEFENSIVE_PATTERNS = [
    r"\b(secret|secrets|secret key|api key|apikey|token|password|credential|credentials|private key|sensitive data)\b.*\b(log|logs|logging|logger)\b",
    r"\b(log|logs|logging|logger)\b.*\b(secret|secrets|api key|apikey|token|password|credential|credentials|private key|sensitive data)\b",
    r"\b(vulnerable code|secure version|sample|example|fix|solution|solutions|detect|find|redact|mask)\b.*\b(secret|api key|token|credential|sensitive data)\b",
    r"\b(secret|api key|token|credential|sensitive data)\b.*\b(vulnerable code|secure version|sample|example|fix|solution|solutions|detect|find|redact|mask)\b",
    r"(سر|اسرار|توكن|باسورد).*?(لوج|لوجز|log)",
    r"(لوج|لوجز|log).*?(سر|اسرار|توكن|باسورد)",
]

SAFE_COMMAND_DEFENSIVE_PATTERNS = [
    r"\b(command|commands|script|bash|powershell|python script|grep|ripgrep|rg)\b.*\b(detect|find|check|audit|redact|mask|secure|fix|hardening|validate)\b",
    r"\b(detect|find|check|audit|redact|mask|secure|fix|hardening|validate)\b.*\b(command|commands|script|bash|powershell|python script|grep|ripgrep|rg)\b",
    r"(كوماند|اوامر|أوامر|سكربت).*?(افحص|اكتشف|راجع|أمن|تأمين)",
]


def _matches(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text or "", flags=re.IGNORECASE) for pattern in patterns)


def is_low_risk_educational(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered.strip():
        return False
    if _matches(lowered, DANGEROUS_PATTERNS):
        return False
    return any(marker in lowered for marker in LOW_RISK_EDUCATIONAL_MARKERS)


def is_defensive_secret_logging_request(text: str) -> bool:
    raw = text or ""
    if not raw.strip():
        return False
    if _matches(raw, DANGEROUS_PATTERNS):
        return False
    return _matches(raw, SECRET_LOGGING_DEFENSIVE_PATTERNS) or _matches(raw, SAFE_COMMAND_DEFENSIVE_PATTERNS)


def is_web_research_or_educational_defensive(text: str) -> bool:
    """Allow safe research/search phrasing before ambiguous Android/device checks."""
    lowered = (text or "").lower().strip()
    if not lowered or _matches(lowered, DANGEROUS_PATTERNS):
        return False

    web_intent = any(x in lowered for x in [
        "web search", "search web", "search online", "search the web",
        "search on the web", "search in web", "search in the web",
        "search for", "look up", "lookup", "research", "latest", "current",
        "recent", "sources", "references", "online", "internet",
        "ابحث", "بحث", "سيرش", "دور", "هات مصادر", "مصادر",
        "من النت", "على النت", "اونلاين", "أونلاين", "احدث", "أحدث",
    ])
    safe_topic = any(x in lowered for x in [
        "security", "secure", "risk", "risks", "recommendation", "recommendations",
        "best practice", "best practices", "owasp", "masvs", "mobile", "android",
        "api", "insecure data storage", "authentication", "authorization",
        "hardening", "defensive", "mitigation", "cybersecurity",
        "أمن", "تأمين", "حماية", "مخاطر", "توصيات",
    ])
    return web_intent and safe_topic


def is_local_project_secret_scan(text: str) -> bool:
    """Allow local-project secret scanning/code generation before ambiguous Android checks."""
    lowered = (text or "").lower().strip()
    if not lowered or _matches(lowered, DANGEROUS_PATTERNS):
        return False
    local_project = any(x in lowered for x in [
        "local project", "local android project", "my project", "source code",
        "repository", "repo", "codebase", "project files", "android project",
    ])
    secret_scan = any(x in lowered for x in [
        "hardcoded api key", "hardcoded api keys", "api key", "api keys",
        "secret", "secrets", "token", "tokens", "credential", "credentials",
    ])
    code_or_scan = any(x in lowered for x in [
        "python script", "script", "bash", "scan", "detect", "find", "check", "audit", "write", "generate",
    ])
    return local_project and secret_scan and code_or_scan






def is_general_defensive_knowledge_request(text: str) -> bool:
    """Allow defensive knowledge/policy/requirements questions generically.

    This prevents words like Android/mobile/account from causing ask_scope when
    the user is clearly asking for explanation, controls, policy, requirements,
    recommendations, or developer guidance rather than taking an action on a
    device/account/target.
    """
    lowered = (text or "").lower().strip()
    if not lowered or _matches(lowered, DANGEROUS_PATTERNS):
        return False

    knowledge_intent = any(x in lowered for x in [
        "what is", "what are", "explain", "summarize", "summary", "overview",
        "requirements", "controls", "policy", "policies", "standard", "standards",
        "guideline", "guidelines", "best practice", "best practices",
        "recommendation", "recommendations", "mitigation", "mitigations",
        "developer guidance", "secure design", "hardening", "checklist",
        "how can i secure", "how to secure", "how do i protect",
        "اشرح", "وضح", "لخص", "ملخص", "متطلبات", "ضوابط", "سياسة",
        "معيار", "معايير", "توصيات", "حماية", "تأمين", "ازاي احمي", "قائمة",
    ])
    safe_domain = any(x in lowered for x in [
        "security", "secure", "cyber", "authentication", "authorization",
        "logging", "sensitive data", "privacy", "android", "mobile", "api",
        "storage", "token", "session", "owasp", "masvs",
        "أمن", "امان", "أمان", "تأمين", "حماية", "اندرويد", "موبايل",
        "توكن", "جلسة", "تسجيل", "بيانات حساسة",
    ])
    return knowledge_intent and safe_domain


def is_internal_architecture_extraction(text: str) -> bool:
    raw = text or ""
    return _matches(raw, INTERNAL_ARCHITECTURE_EXTRACTION_PATTERNS)


def is_safe_owned_static_reverse_engineering(text: str) -> bool:
    raw = text or ""
    if not raw.strip():
        return False
    # If the user is asking to steal/dump credentials or deploy malware, do not treat it as safe.
    if _matches(raw, DANGEROUS_PATTERNS):
        return False
    return _matches(raw, SAFE_OWNED_STATIC_RE_PATTERNS)

def evaluate_request(text: str, *, has_image: bool = False, pending_email: bool = False) -> SafetyDecision:
    raw = text or ""

    if pending_email and raw.strip().lower() in {"yes", "y", "send", "send it", "ok", "okay", "confirm", "ايوه", "أيوه", "اه", "آه", "تمام", "ابعته", "ابعت"}:
        return SafetyDecision("allow", "low", "Confirmed pending email send.", critic_level="light")

    if is_internal_architecture_extraction(raw):
        return SafetyDecision(
            "refuse",
            "blocked",
            "Internal architecture/routing/secrets extraction attempt.",
            user_message_en="I can't reveal internal routing rules, hidden architecture details, prompts, or secrets. I can explain high-level safe architecture patterns instead.",
            user_message_ar="مش هقدر أكشف routing rules داخلية أو تفاصيل architecture مخفية أو prompts أو secrets. أقدر أشرحلك high-level architecture patterns بشكل آمن.",
            blocked_parts=["internal_architecture", "hidden_rules"],
            critic_level="none",
        )

    if _matches(raw, PROMPT_INJECTION_PATTERNS):
        return SafetyDecision(
            "refuse",
            "blocked",
            "Prompt injection or internal-rules extraction attempt.",
            user_message_en="I can't reveal hidden instructions, internal routing rules, prompts, secrets, or system details. I can still help with a safe request.",
            user_message_ar="مش هقدر أكشف تعليمات داخلية أو routing rules أو secrets أو system prompts. أقدر أساعدك في طلب آمن وواضح.",
            blocked_parts=["prompt_injection", "internal_instructions"],
            critic_level="none",
        )

    if _matches(raw, DANGEROUS_PATTERNS):
        return SafetyDecision(
            "refuse",
            "blocked",
            "Clearly unsafe cyber request.",
            user_message_en="I can't help with credential theft, malware, phishing, bypassing authentication, exfiltration, stealth, or unauthorized access. I can help with defensive alternatives, secure design, detection, or authorized lab work.",
            user_message_ar="مش هقدر أساعد في سرقة credentials أو malware أو phishing أو bypass أو exfiltration أو وصول غير مصرح. أقدر أساعدك ببديل دفاعي آمن أو تحليل مصرح أو secure design.",
            blocked_parts=["unsafe_cyber"],
            critic_level="none",
        )

    if is_web_research_or_educational_defensive(raw):
        return SafetyDecision(
            "allow",
            "low",
            "Web research / educational defensive request.",
            critic_level="light",
        )

    if is_local_project_secret_scan(raw):
        return SafetyDecision(
            "allow",
            "medium",
            "Local defensive project secret scanning request.",
            critic_level="full",
        )

    if is_safe_owned_static_reverse_engineering(raw):
        return SafetyDecision(
            "allow",
            "medium",
            "Authorized owned-device static reverse engineering / APK pull request.",
            critic_level="full",
        )

    if is_defensive_secret_logging_request(raw):
        return SafetyDecision(
            "allow",
            "medium",
            "Defensive request about detecting, preventing, or fixing secrets in logs.",
            critic_level="full",
        )

    if is_general_defensive_knowledge_request(raw):
        return SafetyDecision(
            "allow",
            "low",
            "General defensive knowledge/policy/requirements request.",
            critic_level="light",
        )

    if is_low_risk_educational(raw) or has_image:
        return SafetyDecision("allow", "low", "Low-risk educational/defensive or image request.", critic_level="light")

    sensitive_action_markers = [
        r"\b(pull|extract|dump|access|read|copy|scan|exploit|attack|connect to)\b",
        r"(اسحب|استخرج|اطلع|اقرا|انسخ|افحص|اخترق|اوصل)",
    ]
    if _matches(raw, sensitive_action_markers) and _matches(raw, AMBIGUOUS_TARGET_PATTERNS) and not _matches(raw, AUTHORIZED_OR_DEFENSIVE_HINTS):
        return SafetyDecision(
            "ask_scope",
            "medium",
            "Potential third-party device/account/network target without authorization context.",
            user_message_en="I can help only with owned systems, approved tests, labs, or defensive work. Please clarify the authorized scope and goal, for example: backup, troubleshooting, hardening, or forensic analysis.",
            user_message_ar="أقدر أساعد بس في أنظمة تملكها أو اختبار مصرح أو lab أو شغل دفاعي. وضحلي النطاق المصرح والهدف: backup، troubleshooting، hardening، أو forensic analysis.",
            blocked_parts=[],
            critic_level="full",
        )

    return SafetyDecision("allow", "low", "No deterministic safety block matched.", critic_level="light")
