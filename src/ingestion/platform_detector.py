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
        
def clean_telegram_target(raw_target: str) -> str:
    """
    Sanitizes any raw Telegram target string (url, handle, invite link) into a clean format:
      - '@t.me/change_th' -> '@change_th'
      - '@https://t.me/change_th' -> '@change_th'
      - 'http://t.me/change_th' -> '@change_th'
      - 'https://t.me/s/change_th?start=1' -> '@change_th'
      - 'change_th' -> '@change_th'
      - 'https://t.me/+ABCDEF' -> '+ABCDEF'
      - 'https://t.me/joinchat/ABCDEF' -> 'joinchat/ABCDEF'
    """
    if not raw_target:
        return ""
        
    target = raw_target.strip()
    
    # Remove leading @ if placed before URL e.g. @t.me/foo or @https://t.me/foo
    if target.startswith("@"):
        target = target[1:].strip()
        
    # Remove protocol prefix http:// or https://
    target = re.sub(r'^https?://', '', target, flags=re.IGNORECASE)
    
    # Remove t.me/s/ or t.me/ or telegram.me/
    target = re.sub(r'^(?:s/)?t\.me/(?:s/)?', '', target, flags=re.IGNORECASE)
    target = re.sub(r'^telegram\.me/(?:s/)?', '', target, flags=re.IGNORECASE)
    
    # Strip query parameters or trailing fragment (e.g. ?start=123 or #foo)
    target = target.split("?")[0].split("#")[0].strip()
    
    # Strip trailing slash
    target = target.rstrip("/")
    
    # Remove any remaining leading @
    if target.startswith("@"):
        target = target.lstrip("@").strip()
        
    if not target:
        return ""
        
    # Preserve private invite links
    if target.startswith("+") or target.startswith("joinchat/"):
        return target
        
    return f"@{target}"


def detect_platform_and_clean_target(raw_input: str) -> Tuple[str, str]:
    """
    Parses user input URL or handle and returns (platform, clean_identifier).
    Supported platforms: 'telegram', 'vk', 'ok', 'max'.
    
    Examples:
      - 'https://t.me/dubai_rent' -> ('telegram', '@dubai_rent')
      - '@t.me/dubai_rent' -> ('telegram', '@dubai_rent')
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
    clean_tg = clean_telegram_target(clean)
    return ("telegram", clean_tg)

