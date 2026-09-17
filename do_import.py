import urllib.request
import json

with open('payload_sessions.json', 'r') as f:
    payload_data = f.read().encode('utf-8')

req = urllib.request.Request(
    'https://leadradar.win/api/system/swarm/import-batch',
    data=payload_data,
    headers={
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
)
try:
    resp = urllib.request.urlopen(req)
    body = resp.read()
    try:
        data = json.loads(body.decode('utf-8'))
        print(f"Success! Response: {data}")
    except json.JSONDecodeError:
        print("Failed to decode JSON. Got HTML:", body.decode('utf-8')[:200])
except Exception as e:
    print("HTTP Request failed:", e)
