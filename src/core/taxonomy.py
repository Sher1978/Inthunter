"""
Canonical Taxonomy for LeadRadar: GEO Locations and Business Niches.
Single source of truth for ingestion, AI scoring, filtering, and outreach.
"""

import re
from typing import Optional, Dict, List, Any

LOCATIONS: Dict[str, Dict[str, Any]] = {
    "goa": {
        "code": "goa",
        "label": "🇮🇳 Гоа (Северный)",
        "country": "Индия",
        "keywords": [
            "goa", "гоа", "арамболь", "arambol", "мандрем", "mandrem", "морджим", "morjim",
            "сиолим", "siolim", "керим", "kerim", "keri", "querim", "вагатор", "vagator",
            "анжуна", "anjuna", "чапора", "chapora", "ашвем", "ashvem", "калангут", "calangute",
            "кандолим", "candolim", "мапуса", "mapusa", "бага", "baga", "панжим", "panjim",
            "тиволим", "tivim", "рупии", "рупий", "inr"
        ]
    },
    "dubai": {
        "code": "dubai",
        "label": "🇦🇪 Дубай / ОАЭ",
        "country": "ОАЭ",
        "keywords": [
            "dubai", "дубай", "оаэ", "uae", "jbr", "marina", "downtown", "jvc", "jlt",
            "business bay", "sharjah", "шарджа", "abu dhabi", "абу даби", "абу-даби",
            "creek harbour", "palm jumeirah", "пальм", "дирхам", "дирхамов", "aed", "эмират"
        ]
    },
    "phuket": {
        "code": "phuket",
        "label": "🇹🇭 Таиланд / Пхукет",
        "country": "Таиланд",
        "keywords": [
            "phuket", "пхукет", "rawai", "раваи", "найхарн", "naiharn", "nai harn",
            "чалонг", "chalong", "ката", "kata", "карон", "karon", "бангтао", "bangtao",
            "bang tao", "камала", "kamala", "патонг", "patong", "таланг", "thalang",
            "самуи", "samui", "паттайя", "pattaya", "бангкок", "bangkok", "таиланд", "тайланд",
            "thailand", "бат", "батов", "thb"
        ]
    },
    "nhatrang": {
        "code": "nhatrang",
        "label": "🇻🇳 Вьетнам / Нячанг",
        "country": "Вьетнам",
        "keywords": [
            "nhatrang", "nha trang", "нячанг", "камрань", "cam ranh", "северный пляж",
            "вьетнам", "vietnam", "муйне", "mui ne", "фукуок", "phu quoc", "донг", "донгов", "vnd"
        ]
    },
    "danang": {
        "code": "danang",
        "label": "🇻🇳 Вьетнам / Дананг",
        "country": "Вьетнам",
        "keywords": [
            "danang", "da nang", "дананг", "хойан", "hoi an"
        ]
    },
    "bali": {
        "code": "bali",
        "label": "🇮🇩 Индонезия / Бали",
        "country": "Индонезия",
        "keywords": [
            "bali", "бали", "убуд", "ubud", "чангу", "canggu", "changgu", "семиньяк", "seminyak",
            "кута", "kuta", "джимбаран", "jimbaran", "улувату", "uluwatu", "санур", "sanur",
            "букит", "bukit", "индонезия", "indonesia", "рупия", "idr"
        ]
    },
    "tbilisi": {
        "code": "tbilisi",
        "label": "🇬🇪 Грузия / Тбилиси",
        "country": "Грузия",
        "keywords": [
            "tbilisi", "тбилиси", "батуми", "batumi", "грузия", "georgia", "лари", "lari", "gel"
        ]
    },
    "moscow": {
        "code": "moscow",
        "label": "🇷🇺 Россия / Москва",
        "country": "Россия",
        "keywords": [
            "moscow", "москва", "мск", "подмосковье", "питер", "петербург", "spb", "спб",
            "сити", "арбат", "тверская", "россия", "russia"
        ]
    },
    "global": {
        "code": "global",
        "label": "🌍 Глобал / Все регионы",
        "country": "Международный",
        "keywords": []
    }
}

# Precompiled regex patterns for accurate location matching (supports Russian declensions)
LOCATION_REGEXES: Dict[str, List[re.Pattern]] = {
    "goa": [
        re.compile(r"(?i)\b(?:goa|гоа)\b"),
        re.compile(r"(?i)\b(?:arambol|арамбол[ьяею]?)\b"),
        re.compile(r"(?i)\b(?:siolim|сиолим[еау]?)\b"),
        re.compile(r"(?i)\b(?:mandrem|мандрем[еау]?)\b"),
        re.compile(r"(?i)\b(?:morjim|морджим[еау]?)\b"),
        re.compile(r"(?i)\b(?:kerim|keri|querim|керим[еау]?)\b"),
        re.compile(r"(?i)\b(?:vagator|вагатор[еау]?)\b"),
        re.compile(r"(?i)\b(?:anjuna|анжун[еыа]?)\b"),
        re.compile(r"(?i)\b(?:chapora|чапор[еыа]?)\b"),
        re.compile(r"(?i)\b(?:ashvem|ашвем[еау]?)\b"),
        re.compile(r"(?i)\b(?:calangute|калангут[еау]?)\b"),
        re.compile(r"(?i)\b(?:candolim|кандолим[еау]?)\b"),
        re.compile(r"(?i)\b(?:mapusa|мапус[еыа]?)\b"),
        re.compile(r"(?i)\b(?:baga|баг[еыа]?)\b"),
        re.compile(r"(?i)\b(?:panjim|панжим[еау]?)\b"),
        re.compile(r"(?i)\b(?:рупи[ийяе]|inr)\b"),
    ],
    "dubai": [
        re.compile(r"(?i)\b(?:dubai|дуба[йяею]|оаэ|uae|эмират[аеы]?)\b"),
        re.compile(r"(?i)\b(?:jbr|джбр|marina|марина|downtown|даунтаун|jvc|jlt)\b"),
        re.compile(r"(?i)\b(?:business\s+bay|creek\s+harbour|palm\s+jumeirah|пальм[аеы]?)\b"),
        re.compile(r"(?i)\b(?:sharjah|шардж[еыа]|abu\s+dhabi|абу[\s\-]даби)\b"),
        re.compile(r"(?i)\b(?:дирхам[аовеы]?|aed)\b"),
    ],
    "phuket": [
        re.compile(r"(?i)\b(?:phuket|пхукет[еау]?|таиланд[еау]?|тайланд[еау]?|thailand)\b"),
        re.compile(r"(?i)\b(?:rawai|рава[йие]|naiharn|nai\s+harn|найхарн[еау]?)\b"),
        re.compile(r"(?i)\b(?:chalong|чалонг[еау]?|kata|кат[аеы]|karon|карон[еау]?)\b"),
        re.compile(r"(?i)\b(?:bangtao|bang\s+tao|бангтао|kamala|камал[аеы]|patong|патонг[еау]?)\b"),
        re.compile(r"(?i)\b(?:thalang|таланг[еау]?|samui|саму[йи]|pattaya|паттай[еия]?|bangkok|бангкок[еау]?)\b"),
        re.compile(r"(?i)\b(?:бат[аовеы]?|thb)\b"),
    ],
    "nhatrang": [
        re.compile(r"(?i)\b(?:nhatrang|nha\s+trang|нячанг[еау]?)\b"),
        re.compile(r"(?i)\b(?:cam\s+ranh|камран[ьея]?|северный\s+пляж)\b"),
        re.compile(r"(?i)\b(?:вьетнам[еау]?|vietnam|муйн[еэ]|mui\s+ne|фукуок[еау]?|phu\s+quoc)\b"),
        re.compile(r"(?i)\b(?:донг[аовеы]?|vnd)\b"),
    ],
    "danang": [
        re.compile(r"(?i)\b(?:danang|da\s+nang|дананг[еау]?|хойан[еау]?|hoi\s+an)\b"),
    ],
    "bali": [
        re.compile(r"(?i)\b(?:bali|бали|индонези[ияе]?|indonesia)\b"),
        re.compile(r"(?i)\b(?:ubud|убуд[еау]?|canggu|changgu|чангу|семиньяк[еау]?|seminyak)\b"),
        re.compile(r"(?i)\b(?:kuta|кут[аеы]|jimbaran|джимбаран[еау]?|uluwatu|улувату|sanur|санур[еау]?|bukit|букит[еау]?)\b"),
        re.compile(r"(?i)\b(?:idr)\b"),
    ],
    "tbilisi": [
        re.compile(r"(?i)\b(?:tbilisi|тбилиси|batumi|батуми|грузи[ияе]?|georgia)\b"),
        re.compile(r"(?i)\b(?:лари|lari|gel)\b"),
    ],
    "moscow": [
        re.compile(r"(?i)\b(?:moscow|москв[аеыу]|мск|подмосковь[ея]|питер[еау]?|петербург[еау]?|spb|спб)\b"),
        re.compile(r"(?i)\b(?:сити|арбат[еау]?|тверск[аяойе]{2}|росси[ияе]|russia)\b"),
    ]
}


def detect_geo(text: Optional[str], fallback: str = "global") -> str:
    """
    Detect location code from title, username, or message body.
    Accurately matches inflected Russian forms while avoiding substring traps like 'tai' in 'detail'.
    """
    if not text:
        return fallback

    # Normalize punctuation/special characters to spaces for clean tokenization
    search_text = text.replace("@", " ").replace("_", " ").replace("/", " ").replace("-", " ")

    # Prioritize specific target regions
    priority_order = ["goa", "nhatrang", "danang", "bali", "dubai", "tbilisi", "phuket", "moscow"]
    for loc_code in priority_order:
        patterns = LOCATION_REGEXES.get(loc_code, [])
        for p in patterns:
            if p.search(search_text):
                return loc_code

    return fallback



NICHES: Dict[str, Dict[str, Any]] = {
    "real_estate": {
        "code": "real_estate",
        "label": "🏠 Недвижимость & Аренда вилл",
        "is_b2b_target": True
    },
    "bike_rent": {
        "code": "bike_rent",
        "label": "🛵 Аренда авто & байков",
        "is_b2b_target": True
    },
    "currency_exchange": {
        "code": "currency_exchange",
        "label": "💱 Обмен валюты & Финтех",
        "is_b2b_target": True
    },
    "services_visa": {
        "code": "services_visa",
        "label": "🛂 Визы, Бордерран & Юристы",
        "is_b2b_target": True
    },
    "tourism": {
        "code": "tourism",
        "label": "🏄 Туризм, Экскурсии & Спорт",
        "is_b2b_target": True
    },
    "medicine": {
        "code": "medicine",
        "label": "💊 Медицина, Спа & Здоровье",
        "is_b2b_target": True
    },
    "hr_hiring": {
        "code": "hr_hiring",
        "label": "💼 Вакансии & Рекрутинг",
        "is_b2b_target": True
    },
    "b2b_marketing": {
        "code": "b2b_marketing",
        "label": "💻 IT, Маркетинг & SMM",
        "is_b2b_target": True
    },
    "events_networking": {
        "code": "events_networking",
        "label": "🎟️ Мероприятия & Нетворкинг",
        "is_b2b_target": True
    },
    "other_b2b": {
        "code": "other_b2b",
        "label": "📦 Прочие B2B-услуги",
        "is_b2b_target": True
    },
    "general": {
        "code": "general",
        "label": "🌐 Общее / Комьюнити",
        "is_b2b_target": False
    }
}

_NICHE_SYNONYMS: Dict[str, str] = {
    # Real estate
    "real_estate": "real_estate",
    "real estate": "real_estate",
    "realty": "real_estate",
    "недвижимость": "real_estate",
    "аренда жилья": "real_estate",
    "виллы": "real_estate",
    "квартиры": "real_estate",

    # Bike & Car rental
    "bike_rent": "bike_rent",
    "auto_rental": "bike_rent",
    "car_rental": "bike_rent",
    "bike": "bike_rent",
    "auto": "bike_rent",
    "байк": "bike_rent",
    "авто": "bike_rent",
    "аренда авто": "bike_rent",
    "аренда байков": "bike_rent",

    # Currency exchange
    "currency_exchange": "currency_exchange",
    "currency": "currency_exchange",
    "exchange": "currency_exchange",
    "finance_crypto": "currency_exchange",
    "crypto": "currency_exchange",
    "крипта": "currency_exchange",
    "обмен": "currency_exchange",
    "обмен валюты": "currency_exchange",
    "p2p": "currency_exchange",
    "fintech": "currency_exchange",

    # Legal & Visa
    "services_visa": "services_visa",
    "legal_services": "services_visa",
    "visa_run": "services_visa",
    "visa": "services_visa",
    "визы": "services_visa",
    "виза": "services_visa",
    "визаран": "services_visa",
    "бордерран": "services_visa",
    "юристы": "services_visa",

    # Tourism
    "tourism": "tourism",
    "туризм": "tourism",
    "экскурсии": "tourism",
    "туры": "tourism",

    # Medicine
    "medicine": "medicine",
    "health": "medicine",
    "медицина": "medicine",
    "врачи": "medicine",
    "спа": "medicine",

    # HR
    "hr_hiring": "hr_hiring",
    "hiring": "hr_hiring",
    "hr": "hr_hiring",
    "вакансии": "hr_hiring",
    "работа": "hr_hiring",

    # B2B Marketing / IT
    "b2b_marketing": "b2b_marketing",
    "marketing_smm": "b2b_marketing",
    "marketing": "b2b_marketing",
    "smm": "b2b_marketing",
    "маркетинг": "b2b_marketing",

    # Other B2B
    "other_b2b": "other_b2b",
    "b2b": "other_b2b",

    # General / Unknown
    "general": "general",
    "community": "general",
    "other": "other_b2b",
    "none": "general",
    "unknown": "general",
    "": "general"
}


def normalize_niche(raw_niche: Optional[str], default: str = "general") -> str:
    """Normalize raw niche string from LLM or UI to canonical niche code."""
    if not raw_niche:
        return default
    clean = str(raw_niche).strip().lower()
    return _NICHE_SYNONYMS.get(clean, default)


def is_valid_b2b_niche(niche: Optional[str]) -> bool:
    """Return True if niche is an actionable B2B target niche."""
    norm = normalize_niche(niche)
    return norm in NICHES and NICHES[norm].get("is_b2b_target", False)


def get_geo_label(code: str) -> str:
    return LOCATIONS.get(code, {}).get("label", code)


def get_niche_label(code: str) -> str:
    return NICHES.get(code, {}).get("label", code)
