import requests
import json
import time

SERVER = "https://argonar.co/filemanager/api.php"
WT = "rfm_web_argonar_2026"

# Simulate clicking C:\ in web UI
path = "C:\\"
print(f"Sending list command for: {path}")

r = requests.post(SERVER, data={
    "action": "web.command",
    "web_token": WT,
    "command": "list",
    "params": json.dumps({"path": path})
})
print(f"Command response: {r.json()}")
cmd_id = r.json()["command_id"]

# Poll for result
for i in range(10):
    time.sleep(1)
    r = requests.get(SERVER, params={
        "action": "web.result",
        "web_token": WT,
        "command_id": cmd_id,
    })
    data = r.json()
    if data.get("status") == "completed":
        result = data["result"]
        if result.get("success"):
            files = result.get("files", [])
            print(f"\nSuccess! Found {len(files)} items:")
            for f in files:
                t = "[DIR]" if f["is_dir"] else "[FILE]"
                print(f"  {t} {f['name']}")
        else:
            print(f"\nError: {result.get('error')}")
        break
    print(f"  Waiting... status={data.get('status')}")
else:
    print("\nTimeout - agent didn't respond in 10 seconds")
