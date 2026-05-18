from __future__ import annotations

import re
from typing import Any


def detect_language(text: str) -> str:
    text = text or ""
    has_ar = bool(re.search(r"[\u0600-\u06FF]", text))
    has_en = bool(re.search(r"[A-Za-z]", text))
    if has_ar and has_en:
        ar_count = len(re.findall(r"[\u0600-\u06FF]", text))
        en_count = len(re.findall(r"[A-Za-z]", text))
        return "ar" if ar_count >= en_count else "en"
    if has_ar:
        return "ar"
    if has_en:
        return "en"
    return "unknown"


def explicit_language(text: str) -> str:
    lowered = (text or "").lower()
    if any(x in lowered for x in ["reply in arabic", "respond in arabic", "in arabic", "بالعربي", "عربي بس", "اتكلم عربي", "كلمني عربي"]):
        return "ar"
    if any(x in lowered for x in ["reply in english", "respond in english", "in english", "english", "بالانجليزي", "بالإنجليزي", "انجليزي بس"]):
        return "en"
    return "unknown"


def choose_response_language(current_text: str, messages: list[dict[str, Any]] | None = None, state: dict[str, Any] | None = None) -> str:
    explicit = explicit_language(current_text)
    if explicit in {"ar", "en"}:
        return explicit

    current = detect_language(current_text)
    if current in {"ar", "en"}:
        return current

    state_lang = (state or {}).get("preferred_language") or (state or {}).get("language")
    if state_lang in {"ar", "en"}:
        return state_lang

    for msg in reversed(messages or []):
        if msg.get("role") == "user":
            lang = detect_language(msg.get("content") or "")
            if lang in {"ar", "en"}:
                return lang

    return "en"


def localize(lang: str, en: str, ar: str) -> str:
    return ar if lang == "ar" else en
