"""Pre-bundled ISO and custom Unicode flags registry with emoji mapping."""
from typing import List, Dict

ISO_FLAGS: List[Dict[str, str]] = [
    {"code": "UN", "emoji": "🇺🇳", "name_en": "United Nations", "name_ru": "Организация Объединенных Наций"},
    {"code": "FI", "emoji": "🇫🇮", "name_en": "Finland", "name_ru": "Финляндия"},
    {"code": "PRIDE", "emoji": "🏳️‍🌈", "name_en": "Pride Flag", "name_ru": "Радужный флаг (Pride)"},
    {"code": "FINISH", "emoji": "🏁", "name_en": "Finish Flag (Checkered)", "name_ru": "Финишный флаг (Клетка)"},
    {"code": "PIRATE", "emoji": "🏴‍☠️", "name_en": "Pirate Flag (Jolly Roger)", "name_ru": "Пиратский флаг"},
    {"code": "PEACE", "emoji": "🏳️", "name_en": "Peace Flag (White)", "name_ru": "Белый флаг мира"},
    {"code": "BLACK", "emoji": "🏴", "name_en": "Black Flag", "name_ru": "Черный флаг"},
    {"code": "EU", "emoji": "🇪🇺", "name_en": "European Union", "name_ru": "Европейский Союз"},
    {"code": "US", "emoji": "🇺🇸", "name_en": "United States", "name_ru": "Соединенные Штаты"},
    {"code": "GB", "emoji": "🇬🇧", "name_en": "United Kingdom", "name_ru": "Великобритания"},
    {"code": "RU", "emoji": "🇷🇺", "name_en": "Russian Federation", "name_ru": "Российская Федерация"},
    {"code": "CN", "emoji": "🇨🇳", "name_en": "China", "name_ru": "Китай"},
    {"code": "DE", "emoji": "🇩🇪", "name_en": "Germany", "name_ru": "Германия"},
    {"code": "FR", "emoji": "🇫🇷", "name_en": "France", "name_ru": "Франция"},
    {"code": "JP", "emoji": "🇯🇵", "name_en": "Japan", "name_ru": "Япония"},
    {"code": "KR", "emoji": "🇰🇷", "name_en": "South Korea", "name_ru": "Южная Корея"},
    {"code": "CA", "emoji": "🇨🇦", "name_en": "Canada", "name_ru": "Канада"},
    {"code": "AU", "emoji": "🇦🇺", "name_en": "Australia", "name_ru": "Австралия"},
    {"code": "BR", "emoji": "🇧🇷", "name_en": "Brazil", "name_ru": "Бразилия"},
    {"code": "IN", "emoji": "🇮🇳", "name_en": "India", "name_ru": "Индия"},
    {"code": "IT", "emoji": "🇮🇹", "name_en": "Italy", "name_ru": "Италия"},
    {"code": "ES", "emoji": "🇪🇸", "name_en": "Spain", "name_ru": "Испания"},
    {"code": "MX", "emoji": "🇲🇽", "name_en": "Mexico", "name_ru": "Мексика"},
    {"code": "NL", "emoji": "🇳🇱", "name_en": "Netherlands", "name_ru": "Нидерланды"},
    {"code": "SE", "emoji": "🇸🇪", "name_en": "Sweden", "name_ru": "Швеция"},
    {"code": "NO", "emoji": "🇳🇴", "name_en": "Norway", "name_ru": "Норвегия"},
    {"code": "PL", "emoji": "🇵🇱", "name_en": "Poland", "name_ru": "Польша"},
    {"code": "UA", "emoji": "🇺🇦", "name_en": "Ukraine", "name_ru": "Украина"},
    {"code": "KZ", "emoji": "🇰🇿", "name_en": "Kazakhstan", "name_ru": "Казахстан"},
    {"code": "BY", "emoji": "🇧🇾", "name_en": "Belarus", "name_ru": "Беларусь"},
    {"code": "TR", "emoji": "🇹🇷", "name_en": "Turkey", "name_ru": "Турция"},
    {"code": "SA", "emoji": "🇸🇦", "name_en": "Saudi Arabia", "name_ru": "Саудовская Аравия"},
    {"code": "ZA", "emoji": "🇿🇦", "name_en": "South Africa", "name_ru": "Южная Африка"},
    {"code": "AR", "emoji": "🇦🇷", "name_en": "Argentina", "name_ru": "Аргентина"},
    {"code": "SG", "emoji": "🇸🇬", "name_en": "Singapore", "name_ru": "Сингапур"},
    {"code": "CH", "emoji": "🇨🇭", "name_en": "Switzerland", "name_ru": "Швейцария"},
    {"code": "AT", "emoji": "🇦🇹", "name_en": "Austria", "name_ru": "Австрия"},
    {"code": "BE", "emoji": "🇧🇪", "name_en": "Belgium", "name_ru": "Бельгия"},
    {"code": "DK", "emoji": "🇩🇰", "name_en": "Denmark", "name_ru": "Дания"},
    {"code": "IE", "emoji": "🇮🇪", "name_en": "Ireland", "name_ru": "Ирландия"},
    {"code": "NZ", "emoji": "🇳🇿", "name_en": "New Zealand", "name_ru": "Новая Зеландия"},
    {"code": "IL", "emoji": "🇮🇱", "name_en": "Israel", "name_ru": "Израиль"},
    {"code": "GR", "emoji": "🇬🇷", "name_en": "Greece", "name_ru": "Греция"},
    {"code": "PT", "emoji": "🇵🇹", "name_en": "Portugal", "name_ru": "Португалия"},
    {"code": "CZ", "emoji": "🇨🇿", "name_en": "Czech Republic", "name_ru": "Чехия"},
    {"code": "HU", "emoji": "🇭🇺", "name_en": "Hungary", "name_ru": "Венгрия"},
    {"code": "RO", "emoji": "🇷🇴", "name_en": "Romania", "name_ru": "Румыния"},
    {"code": "BG", "emoji": "🇧🇬", "name_en": "Bulgaria", "name_ru": "Болгария"},
    {"code": "RS", "emoji": "🇷🇸", "name_en": "Serbia", "name_ru": "Сербия"},
    {"code": "HR", "emoji": "🇭🇷", "name_en": "Croatia", "name_ru": "Хорватия"},
    {"code": "AE", "emoji": "🇦🇪", "name_en": "United Arab Emirates", "name_ru": "ОАЭ"},
    {"code": "EG", "emoji": "🇪🇬", "name_en": "Egypt", "name_ru": "Египет"},
    {"code": "NG", "emoji": "🇳🇬", "name_en": "Nigeria", "name_ru": "Нигерия"},
    {"code": "KE", "emoji": "🇰🇪", "name_en": "Kenya", "name_ru": "Кения"},
    {"code": "CL", "emoji": "🇨🇱", "name_en": "Chile", "name_ru": "Чили"},
    {"code": "CO", "emoji": "🇨🇴", "name_en": "Colombia", "name_ru": "Колумбия"},
    {"code": "VN", "emoji": "🇻🇳", "name_en": "Vietnam", "name_ru": "Вьетнам"},
    {"code": "TH", "emoji": "🇹🇭", "name_en": "Thailand", "name_ru": "Таиланд"},
    {"code": "ID", "emoji": "🇮🇩", "name_en": "Indonesia", "name_ru": "Индонезия"},
    {"code": "MY", "emoji": "🇲🇾", "name_en": "Malaysia", "name_ru": "Малайзия"},
    {"code": "PH", "emoji": "🇵🇭", "name_en": "Philippines", "name_ru": "Филиппины"},
    {"code": "IS", "emoji": "🇮🇸", "name_en": "Iceland", "name_ru": "Исландия"},
    {"code": "EE", "emoji": "🇪🇪", "name_en": "Estonia", "name_ru": "Эстония"},
    {"code": "LV", "emoji": "🇱🇻", "name_en": "Latvia", "name_ru": "Латвия"},
    {"code": "LT", "emoji": "🇱🇹", "name_en": "Lithuania", "name_ru": "Литва"},
    {"code": "GE", "emoji": "🇬🇪", "name_en": "Georgia", "name_ru": "Грузия"},
    {"code": "AM", "emoji": "🇦🇲", "name_en": "Armenia", "name_ru": "Армения"},
    {"code": "AZ", "emoji": "🇦🇿", "name_en": "Azerbaijan", "name_ru": "Азербайджан"},
    {"code": "UZ", "emoji": "🇺🇿", "name_en": "Uzbekistan", "name_ru": "Узбекистан"}
]

# Quick code lookup set
VALID_COUNTRY_CODES = {f["code"] for f in ISO_FLAGS}
CODE_TO_EMOJI_MAP = {f["code"]: f["emoji"] for f in ISO_FLAGS}


def code_to_emoji(code_or_emoji: str) -> str:
    """Resolve code or emoji string to actual Unicode emoji flag."""
    if not code_or_emoji:
        return "🇺🇳"
    val = code_or_emoji.strip()
    if val in CODE_TO_EMOJI_MAP:
        return CODE_TO_EMOJI_MAP[val]
    # Check case-insensitive
    val_upper = val.upper()
    if val_upper in CODE_TO_EMOJI_MAP:
        return CODE_TO_EMOJI_MAP[val_upper]
    # Check if standard 2-letter country code
    if len(val_upper) == 2 and val_upper.isalpha():
        try:
            return chr(ord(val_upper[0]) + 127397) + chr(ord(val_upper[1]) + 127397)
        except Exception:
            pass
    # If already an emoji (or unrecognized string), return as is
    return val


def is_valid_country_code(code: str) -> bool:
    """Validate ISO alpha-2 country code or custom flag."""
    if not code:
        return False
    val = code.strip()
    if val.upper() in VALID_COUNTRY_CODES:
        return True
    if len(val) == 2 and val.isalpha():
        return True
    # Allow any valid unicode flag / emoji string
    if len(val) >= 1:
        return True
    return False


def get_country_info(code: str) -> Dict[str, str]:
    """Retrieve country metadata by code."""
    code_upper = code.upper() if code else "UN"
    for item in ISO_FLAGS:
        if item["code"] == code_upper:
            return item
    return {"code": code_upper, "emoji": code_to_emoji(code), "name_en": code_upper, "name_ru": code_upper}
