def get_device_profile(db_id: int) -> dict:
    """
    Returns a deterministic device profile based on db_id to avoid Telegram banning bots
    due to default Pyrogram 'PC 64bit' / 'Pyrogram' app_version footprints.
    """
    devices = [
        {"device_model": "Samsung Galaxy S23 Ultra", "system_version": "13.0", "app_version": "10.14.5", "lang_code": "ru"},
        {"device_model": "iPhone 14 Pro Max", "system_version": "16.6", "app_version": "10.14.5", "lang_code": "ru"},
        {"device_model": "Google Pixel 8 Pro", "system_version": "14.0", "app_version": "10.15.1", "lang_code": "en"},
        {"device_model": "OnePlus 11", "system_version": "13.0", "app_version": "10.12.0", "lang_code": "en"},
        {"device_model": "Xiaomi 13 Pro", "system_version": "13.0", "app_version": "10.11.2", "lang_code": "ru"},
        {"device_model": "Samsung Galaxy A54", "system_version": "13.0", "app_version": "10.14.5", "lang_code": "ru"},
        {"device_model": "iPhone 13", "system_version": "15.7", "app_version": "10.13.4", "lang_code": "en"},
        {"device_model": "Samsung Galaxy Z Fold 5", "system_version": "13.0", "app_version": "10.14.5", "lang_code": "ru"}
    ]
    
    # Use fallback 0 if db_id is None
    idx = (db_id or 0) % len(devices)
    return devices[idx]
