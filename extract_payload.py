import glob
import os
import json
import base64
import struct
import urllib.request

def convert_to_pyrogram_session(sqlite_path, json_path):
    import sqlite3
    with open(json_path, 'r', encoding='utf-8') as f:
        meta = json.load(f)
    user_id = meta.get("user_id") or meta.get("id") or 0
    phone = meta.get("phone") or ""
    conn = sqlite3.connect(sqlite_path)
    c = conn.cursor()
    c.execute("SELECT dc_id, auth_key FROM sessions")
    row = c.fetchone()
    conn.close()
    if not row:
        raise ValueError("No session row found in SQLite")
    dc_id, auth_key = row[0], row[1]
    packed = struct.pack(">B?256sQ?", dc_id, False, auth_key, user_id, False)
    session_str = base64.urlsafe_b64encode(packed).decode("utf-8").rstrip("=")
    return session_str, str(phone), str(user_id)

payload = []
for acc_dir in glob.glob("scratch/downloaded_accounts/extracted/acc_*"):
    s_files = glob.glob(os.path.join(acc_dir, "*.session"))
    j_files = glob.glob(os.path.join(acc_dir, "*.json"))
    if s_files and j_files:
        try:
            s_str, phone, uid = convert_to_pyrogram_session(s_files[0], j_files[0])
            payload.append({"session_string": s_str, "phone_number": phone})
        except Exception as e:
            print("Error", e)

print(f"Extracted {len(payload)} sessions.")
with open("payload_sessions.json", "w") as f:
    json.dump({"accounts": payload}, f)
