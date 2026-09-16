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
    
    # Removed useless time.sleep(15) here because it delays the Railway healthcheck,
    # meaning the old container never gets killed during this sleep anyway!
    
    uvicorn.run("src.api.app:app", host="0.0.0.0", port=port)
