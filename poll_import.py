import urllib.request
import json
import time

with open('payload_sessions.json', 'r') as f:
    payload_data = f.read().encode('utf-8')

for _ in range(30):
    try:
        req = urllib.request.Request('https://leadradar.win/api/system/swarm/import-batch', data=payload_data, headers={'Content-Type': 'application/json'})
        resp = urllib.request.urlopen(req)
        body = resp.read()
        try:
            data = json.loads(body.decode('utf-8'))
            if data.get('status') == 'ok':
                print(f"Successfully imported {data.get('added')} sessions!")
                break
            else:
                print("Deploy not ready yet, got HTML/Invalid JSON.")
        except json.JSONDecodeError:
            print("Got HTML, waiting for deploy...")
    except Exception as e:
        print("Error:", e)
    time.sleep(5)
