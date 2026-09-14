import sys
import os
import uvicorn

# Ensure UTF-8 stream output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.abspath("."))

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    print(f"🚀 Starting Intent Hunter CDP on port {port} via run_bot.py...")
    
    # ⚠️ CRITICAL: 15-second delay to prevent AUTH_KEY_DUPLICATED on Railway zero-downtime deploys
    # This allows the old container to receive SIGTERM and cleanly disconnect MTProto sessions 
    # BEFORE the new container tries to connect with the exact same session strings.
    import time
    print("⏳ Sleeping for 15 seconds to allow old instances to disconnect from Telegram...")
    time.sleep(15)
    
    uvicorn.run("src.api.app:app", host="0.0.0.0", port=port)
