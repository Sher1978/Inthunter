import re
from typing import Tuple

def detect_platform_and_clean_target(raw_input: str) -> Tuple[str, str]:
    """
    Parses user input URL or handle and returns (platform, clean_identifier).
    Supported platforms: 'telegram', 'vk', 'ok', 'max'.
    
    Examples:
      - 'https://t.me/dubai_rent' -> ('telegram', '@dubai_rent')
      - '@dubai_rent' -> ('telegram', '@dubai_rent')
      - 'https://vk.com/dubai_realty' -> ('vk', 'dubai_realty')
      - 'https://vk.ru/public12345' -> ('vk', 'public12345')
      - 'https://ok.ru/group/554433' -> ('ok', 'group/554433')
      - 'https://max.ru/s/dubai_max' -> ('max', 'dubai_max')
      - 'max.ru/group/9912' -> ('max', 'group/9912')
    """
    clean = (raw_input or "").strip()
    
    # VK URL patterns
    if "vk.com" in clean.lower() or "vk.ru" in clean.lower():
        match = re.search(r'(?:vk\.com|vk\.ru)/([a-zA-Z0-9_\.]+)', clean)
        if match:
            slug = match.group(1)
            return ("vk", slug)
        return ("vk", clean)
        
    # Odnoklassniki URL patterns
    if "ok.ru" in clean.lower():
        match = re.search(r'ok\.ru/(group/[0-9]+|[a-zA-Z0-9_\.]+)', clean)
        if match:
            slug = match.group(1)
            return ("ok", slug)
        return ("ok", clean)
        
    # MAX Messenger URL patterns
    if "max.ru" in clean.lower() or "max.im" in clean.lower():
        match = re.search(r'(?:max\.ru|max\.im)/(?:s/|group/)?([a-zA-Z0-9_\.]+)', clean)
        if match:
            slug = match.group(1)
            return ("max", slug)
        return ("max", clean)
        
    # Telegram URL patterns or plain handle
    clean_tg = clean.replace("https://t.me/s/", "").replace("https://t.me/", "").replace("t.me/", "")
    clean_tg = f"@{clean_tg.strip().lstrip('@')}"
    return ("telegram", clean_tg)
