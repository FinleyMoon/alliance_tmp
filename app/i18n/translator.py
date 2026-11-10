"""Internationalization helper module for English and Russian localization."""
import json
from pathlib import Path
from typing import Dict, Any, Optional

I18N_DIR = Path(__file__).resolve().parent

with open(I18N_DIR / "en.json", "r", encoding="utf-8") as f:
    _CATALOG_EN = json.load(f)

with open(I18N_DIR / "ru.json", "r", encoding="utf-8") as f:
    _CATALOG_RU = json.load(f)

CATALOGS = {
    "en": _CATALOG_EN,
    "ru": _CATALOG_RU
}


def resolve_language(accept_language_header: Optional[str], user_preference: Optional[str] = None) -> str:
    """Resolve preferred language ('en' or 'ru') from user preference or HTTP header."""
    if user_preference in ("en", "ru"):
        return user_preference
    if accept_language_header:
        header_lower = accept_language_header.lower()
        if "ru" in header_lower:
            return "ru"
    return "en"


def translate(key: str, lang: str = "en", **kwargs) -> str:
    """Translate a key into target language with optional formatting placeholders."""
    catalog = CATALOGS.get(lang, _CATALOG_EN)
    template = catalog.get(key, _CATALOG_EN.get(key, key))
    if kwargs:
        try:
            return template.format(**kwargs)
        except Exception:
            return template
    return template


def get_all_translations(lang: str = "en") -> Dict[str, str]:
    """Retrieve full catalog dictionary for client-side i18n initialization."""
    return CATALOGS.get(lang, _CATALOG_EN)
