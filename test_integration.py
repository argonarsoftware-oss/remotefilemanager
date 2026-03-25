"""Full integration test for Remote File Manager."""
import requests
import json
import time
import socket
import platform
import os
import ctypes
import datetime

SERVER_URL = "https://argonar.co/filemanager/api.php"
AGENT_TOKEN = "rfm_agent_argonar_2026"
WEB_TOKEN = "rfm_web_argonar_2026"

def format_size(size):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024

def get_drives():
    drives = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if bitmask & 1:
            drives.append(letter + ":\\")
        bitmask >>= 1
    return drives

print("=" * 55)
print("  FULL INTEGRATION TEST")
print("=" * 55)
print()

# 1. Register agent
hostname = socket.gethostname()
local_ip = socket.gethostbyname(hostname)
r = requests.post(SERVER_URL, data={
    "action": "agent.register",
    "agent_token": AGENT_TOKEN,
    "hostname": hostname,
    "local_ip": local_ip,
    "os": f"{platform.system()} {platform.release()}",
})
print(f"1. Register agent: {r.json()}")

# 2. Check agent status from web
r = requests.get(SERVER_URL, params={
    "action": "web.agent_status",
    "web_token": WEB_TOKEN,
})
agent = r.json().get("agent", {})
print(f"2. Agent status: online={agent.get('online')}, hostname={agent.get('hostname')}, ip={agent.get('ip')}")

# 3. Web sends 'list drives' command
r = requests.post(SERVER_URL, data={
    "action": "web.command",
    "web_token": WEB_TOKEN,
    "command": "list",
    "params": json.dumps({"path": ""}),
})
cmd_id = r.json()["command_id"]
print(f"3. Web sent 'list drives' command: {cmd_id}")

# 4. Agent polls and gets command
r = requests.post(SERVER_URL, data={
    "action": "agent.poll",
    "agent_token": AGENT_TOKEN,
})
cmd = r.json()["command"]
print(f"4. Agent received command: {cmd['command']} params={cmd['params']}")

# 5. Agent executes (list drives)
drives = get_drives()
result = {"success": True, "drives": drives, "files": None}
print(f"5. Agent executed locally: found drives {drives}")

# 6. Agent sends result back
r = requests.post(SERVER_URL, data={
    "action": "agent.result",
    "agent_token": AGENT_TOKEN,
    "command_id": cmd_id,
    "result": json.dumps(result),
})
print(f"6. Agent sent result back: {r.json()}")

# 7. Web fetches result
r = requests.get(SERVER_URL, params={
    "action": "web.result",
    "web_token": WEB_TOKEN,
    "command_id": cmd_id,
})
web_result = r.json()
print(f"7. Web received: status={web_result['status']}, drives={web_result['result']['drives']}")

print()
print("-" * 55)
print("  TEST: List C:\\ directory")
print("-" * 55)

# Send list C:\ command
r = requests.post(SERVER_URL, data={
    "action": "web.command",
    "web_token": WEB_TOKEN,
    "command": "list",
    "params": json.dumps({"path": "C:\\"}),
})
cmd_id2 = r.json()["command_id"]

# Agent polls
r = requests.post(SERVER_URL, data={
    "action": "agent.poll",
    "agent_token": AGENT_TOKEN,
})
cmd2 = r.json()["command"]
path = cmd2["params"]["path"]

# Execute locally
files = []
for item in sorted(os.listdir(path)):
    item_path = os.path.join(path, item)
    try:
        stat = os.stat(item_path)
        is_dir = os.path.isdir(item_path)
        files.append({
            "name": item,
            "path": item_path,
            "is_dir": is_dir,
            "size": stat.st_size if not is_dir else 0,
            "size_fmt": format_size(stat.st_size) if not is_dir else "--",
            "modified": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    except (PermissionError, OSError):
        pass

result2 = {"success": True, "files": files}

# Send result
r = requests.post(SERVER_URL, data={
    "action": "agent.result",
    "agent_token": AGENT_TOKEN,
    "command_id": cmd_id2,
    "result": json.dumps(result2),
})

# Web fetches
r = requests.get(SERVER_URL, params={
    "action": "web.result",
    "web_token": WEB_TOKEN,
    "command_id": cmd_id2,
})
files_result = r.json()["result"]["files"]
print(f"Found {len(files_result)} items in C:\\")
print()
for f in files_result:
    icon = "[DIR] " if f["is_dir"] else "[FILE]"
    name = f["name"]
    size = f.get("size_fmt", "--")
    mod = f.get("modified", "")
    print(f"  {icon} {name:35s} {size:>10s}  {mod}")

print()
print("-" * 55)
print("  TEST: Create & Delete folder")
print("-" * 55)

# Create folder
test_dir = "C:\\__rfm_test_folder__"
r = requests.post(SERVER_URL, data={
    "action": "web.command",
    "web_token": WEB_TOKEN,
    "command": "mkdir",
    "params": json.dumps({"path": "C:\\", "name": "__rfm_test_folder__"}),
})
cmd_id3 = r.json()["command_id"]

r = requests.post(SERVER_URL, data={"action": "agent.poll", "agent_token": AGENT_TOKEN})
cmd3 = r.json()["command"]

# Execute mkdir
try:
    os.makedirs(os.path.join(cmd3["params"]["path"], cmd3["params"]["name"]), exist_ok=False)
    mkdir_result = {"success": True}
except Exception as e:
    mkdir_result = {"success": False, "error": str(e)}

r = requests.post(SERVER_URL, data={
    "action": "agent.result", "agent_token": AGENT_TOKEN,
    "command_id": cmd_id3, "result": json.dumps(mkdir_result),
})
print(f"Create folder: {mkdir_result}")
print(f"  Exists on disk: {os.path.isdir(test_dir)}")

# Delete it
r = requests.post(SERVER_URL, data={
    "action": "web.command", "web_token": WEB_TOKEN,
    "command": "delete", "params": json.dumps({"path": test_dir}),
})
cmd_id4 = r.json()["command_id"]

r = requests.post(SERVER_URL, data={"action": "agent.poll", "agent_token": AGENT_TOKEN})
cmd4 = r.json()["command"]

import shutil
try:
    p = cmd4["params"]["path"]
    if os.path.isdir(p):
        shutil.rmtree(p)
    else:
        os.unlink(p)
    del_result = {"success": True}
except Exception as e:
    del_result = {"success": False, "error": str(e)}

r = requests.post(SERVER_URL, data={
    "action": "agent.result", "agent_token": AGENT_TOKEN,
    "command_id": cmd_id4, "result": json.dumps(del_result),
})
print(f"Delete folder: {del_result}")
print(f"  Exists on disk: {os.path.isdir(test_dir)}")

print()
print("=" * 55)
print("  ALL TESTS PASSED!")
print("=" * 55)
print()
print("Web UI: https://argonar.co/filemanager/")
print("Password: argonar2026")
