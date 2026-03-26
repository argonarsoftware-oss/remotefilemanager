"""
Remote File Manager - Desktop Agent
Runs with admin privileges on the target machine.
Polls the server (argonar.co/filemanager/api.php) for commands,
executes them locally with full admin access, and returns results.

The server web UI at argonar.co/filemanager/ controls this agent remotely.
"""

import os
import sys
import shutil
import hashlib
import secrets
import datetime
import ctypes
import socket
import json
import time
import base64
import platform
import threading
import traceback
import subprocess
import fnmatch
import zipfile
from pathlib import Path

import requests

# ============================================================
# Configuration
# ============================================================
SERVER_URL = "https://argonar.co/filemanager/api.php"
AGENT_TOKEN = "rfm_agent_argonar_2026"
POLL_INTERVAL = 0.3  # seconds between polls

# ============================================================
# Admin Privilege Check (Windows)
# ============================================================
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

if sys.platform == "win32" and not is_admin() and "--no-elevate" not in sys.argv:
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable,
        " ".join([f'"{arg}"' for arg in sys.argv]), None, 1
    )
    sys.exit(0)

# ============================================================
# Hide Console Window
# ============================================================
def hide_window():
    """Hide the console window on Windows."""
    if sys.platform == "win32":
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE = 0

if "--visible" not in sys.argv:
    hide_window()

# ============================================================
# Task Scheduler Auto-Install
# ============================================================
def install_task_scheduler():
    """Register this agent to run at startup via Windows Task Scheduler."""
    task_name = "RemoteFileManagerAgent"

    # Get the path to the current executable or script
    if getattr(sys, 'frozen', False):
        # Running as compiled .exe
        exe_path = sys.executable
    else:
        # Running as python script
        exe_path = sys.executable
        script_path = os.path.abspath(sys.argv[0])

    try:
        # Check if task already exists
        check = subprocess.run(
            ["schtasks", "/Query", "/TN", task_name],
            capture_output=True, text=True
        )
        if check.returncode == 0:
            return True  # Already installed

        # Build the command to run
        if getattr(sys, 'frozen', False):
            run_cmd = f'"{exe_path}"'
        else:
            run_cmd = f'"{exe_path}" "{script_path}" --no-elevate'

        # Create scheduled task: run at logon, with highest privileges
        result = subprocess.run([
            "schtasks", "/Create",
            "/TN", task_name,
            "/TR", run_cmd,
            "/SC", "ONLOGON",
            "/RL", "HIGHEST",
            "/F",  # Force overwrite
            "/DELAY", "0000:30",  # 30 second delay after logon
        ], capture_output=True, text=True)

        if result.returncode == 0:
            return True
        return False
    except Exception:
        return False

def uninstall_task_scheduler():
    """Remove the agent from Task Scheduler."""
    try:
        subprocess.run(
            ["schtasks", "/Delete", "/TN", "RemoteFileManagerAgent", "/F"],
            capture_output=True, text=True
        )
        return True
    except Exception:
        return False

# ============================================================
# Helpers
# ============================================================
def log(msg):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")

def format_size(size):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"

def get_drives():
    drives = []
    if sys.platform == "win32":
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            if bitmask & 1:
                drives.append(f"{letter}:\\")
            bitmask >>= 1
    else:
        drives = ["/"]
    return drives

def safe_resolve(path_str):
    """Resolve a path safely."""
    try:
        return str(Path(path_str).resolve())
    except Exception:
        return None

# ============================================================
# Server Communication
# ============================================================
session = requests.Session()
session.headers.update({"X-Agent-Token": AGENT_TOKEN})

def server_post(action, data=None, files=None):
    """Send POST request to server API."""
    payload = {"action": action, "agent_token": AGENT_TOKEN}
    if data:
        payload.update(data)
    try:
        if files:
            resp = session.post(SERVER_URL, data=payload, files=files, timeout=120)
        else:
            resp = session.post(SERVER_URL, data=payload, timeout=30)
        return resp.json()
    except requests.exceptions.ConnectionError:
        return None
    except Exception as e:
        log(f"Server error: {e}")
        return None

def server_get(action, params=None):
    """Send GET request to server API."""
    qs = {"action": action, "agent_token": AGENT_TOKEN}
    if params:
        qs.update(params)
    try:
        resp = session.get(SERVER_URL, params=qs, timeout=30)
        return resp
    except Exception as e:
        log(f"Server error: {e}")
        return None

# ============================================================
# Command Handlers
# ============================================================
def handle_list(params):
    """List directory contents or drives."""
    path = params.get("path", "")

    # Empty path = return drives
    if not path or path == "":
        drives = get_drives()
        return {"success": True, "drives": drives, "files": None}

    resolved = safe_resolve(path)
    if not resolved or not os.path.isdir(resolved):
        return {"success": False, "error": f"Directory not found: {path}"}

    files = []
    try:
        for item in os.listdir(resolved):
            item_path = os.path.join(resolved, item)
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
                    "extension": os.path.splitext(item)[1].lower(),
                })
            except (PermissionError, OSError):
                files.append({
                    "name": item,
                    "path": item_path,
                    "is_dir": False,
                    "size": 0,
                    "size_fmt": "--",
                    "modified": "--",
                    "extension": "",
                })

        # Sort: directories first, then alphabetical
        files.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

    except PermissionError:
        return {"success": False, "error": "Access denied"}
    except Exception as e:
        return {"success": False, "error": str(e)}

    return {"success": True, "files": files}


def handle_download(params, command_id):
    """Download a file - upload it to server for web UI to fetch."""
    path = params.get("path", "")
    resolved = safe_resolve(path)

    if not resolved or not os.path.isfile(resolved):
        return {"success": False, "error": "File not found"}

    try:
        # Upload file to server
        with open(resolved, "rb") as f:
            result = server_post("agent.upload_file", {
                "command_id": command_id,
            }, files={"file": (os.path.basename(resolved), f)})

        if result and result.get("success"):
            return {"success": True, "command_id": command_id, "filename": os.path.basename(resolved)}
        return {"success": False, "error": "Failed to upload file to server"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_upload(params, command_id):
    """Download file from server and save to target path."""
    target_path = params.get("target_path", "")
    filename = params.get("filename", "")
    download_url = params.get("download_url", "")

    if not target_path or not filename:
        return {"success": False, "error": "Missing target_path or filename"}

    resolved = safe_resolve(target_path)
    if not resolved or not os.path.isdir(resolved):
        return {"success": False, "error": f"Target directory not found: {target_path}"}

    try:
        # Build full URL
        base_url = SERVER_URL.rsplit("/", 1)[0]
        if download_url.startswith("http"):
            url = download_url
        else:
            # Relative URL - build from server base
            url = SERVER_URL + "?action=agent.get_upload&command_id=" + command_id + "&agent_token=" + AGENT_TOKEN

        resp = session.get(url, timeout=120)
        if resp.status_code == 200:
            dest = os.path.join(resolved, filename)
            with open(dest, "wb") as f:
                f.write(resp.content)
            return {"success": True, "message": f"Saved {filename} to {resolved}"}
        return {"success": False, "error": f"Download failed: HTTP {resp.status_code}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_delete(params):
    """Delete a file or directory."""
    path = params.get("path", "")
    resolved = safe_resolve(path)

    if not resolved or not os.path.exists(resolved):
        return {"success": False, "error": "Path not found"}

    try:
        if os.path.isdir(resolved):
            shutil.rmtree(resolved)
        else:
            os.unlink(resolved)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_mkdir(params):
    """Create a new directory."""
    path = params.get("path", "")
    name = params.get("name", "").strip()

    if not name:
        return {"success": False, "error": "Folder name required"}

    resolved = safe_resolve(path)
    if not resolved or not os.path.isdir(resolved):
        return {"success": False, "error": "Parent directory not found"}

    try:
        new_dir = os.path.join(resolved, name)
        os.makedirs(new_dir, exist_ok=False)
        return {"success": True}
    except FileExistsError:
        return {"success": False, "error": "Folder already exists"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_rename(params):
    """Rename a file or directory."""
    path = params.get("path", "")
    new_name = params.get("new_name", "").strip()

    if not new_name:
        return {"success": False, "error": "New name required"}

    resolved = safe_resolve(path)
    if not resolved or not os.path.exists(resolved):
        return {"success": False, "error": "Path not found"}

    try:
        parent = os.path.dirname(resolved)
        new_path = os.path.join(parent, new_name)
        os.rename(resolved, new_path)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_properties(params):
    """Get file/directory properties."""
    path = params.get("path", "")
    resolved = safe_resolve(path)

    if not resolved or not os.path.exists(resolved):
        return {"success": False, "error": "Path not found"}

    try:
        stat = os.stat(resolved)
        return {
            "success": True,
            "properties": {
                "name": os.path.basename(resolved),
                "path": resolved,
                "is_dir": os.path.isdir(resolved),
                "size": stat.st_size,
                "size_fmt": format_size(stat.st_size),
                "created": datetime.datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S"),
                "modified": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "accessed": datetime.datetime.fromtimestamp(stat.st_atime).strftime("%Y-%m-%d %H:%M:%S"),
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# New Command Handlers
# ============================================================
# Persistent shell state - maintains cwd between commands like a real terminal
_shell_cwd = os.path.expanduser("~")

def handle_shell(params):
    """Execute a shell command with persistent working directory (like a real CMD)."""
    global _shell_cwd
    command = params.get("command", "").strip()

    if not command:
        return {"success": False, "error": "No command provided"}

    # Handle 'cd' specially to persist directory changes
    # We append '&& cd' at the end to capture the new cwd after the command runs
    # This way if the user runs 'cd C:\Users' the cwd updates for the next command
    marker = ":::CWD:::"
    wrapped = f'{command} && echo {marker} && cd'
    # For commands that might fail, also get cwd on failure path
    full_cmd = f'cd /d "{_shell_cwd}" && ({wrapped}) || (echo {marker} && cd)'

    try:
        result = subprocess.run(
            full_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ},
        )

        stdout = result.stdout
        stderr = result.stderr

        # Extract the new cwd from stdout
        new_cwd = _shell_cwd
        if marker in stdout:
            parts = stdout.split(marker)
            # Everything before the marker is the actual command output
            actual_output = parts[0]
            # The line after the marker is the current directory from 'cd'
            cwd_line = parts[1].strip() if len(parts) > 1 else ""
            if cwd_line and os.path.isdir(cwd_line):
                new_cwd = cwd_line
            stdout = actual_output

        _shell_cwd = new_cwd

        return {
            "success": True,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": result.returncode,
            "cwd": _shell_cwd,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Command timed out after 60 seconds", "cwd": _shell_cwd}
    except Exception as e:
        return {"success": False, "error": str(e), "cwd": _shell_cwd}


def handle_read_file(params):
    """Read text file content for viewer/editor."""
    path = params.get("path", "")
    resolved = safe_resolve(path)

    if not resolved or not os.path.isfile(resolved):
        return {"success": False, "error": "File not found"}

    try:
        file_size = os.path.getsize(resolved)
        max_size = 5 * 1024 * 1024  # 5MB

        if file_size > max_size:
            return {"success": False, "error": f"File too large ({format_size(file_size)}). Maximum is 5MB."}

        # Try common encodings
        content = None
        detected_encoding = None
        for encoding in ["utf-8", "utf-8-sig", "latin-1", "cp1252", "ascii"]:
            try:
                with open(resolved, "r", encoding=encoding) as f:
                    content = f.read()
                detected_encoding = encoding
                break
            except (UnicodeDecodeError, ValueError):
                continue

        if content is None:
            # Last resort: read as latin-1 which never fails
            with open(resolved, "r", encoding="latin-1") as f:
                content = f.read()
            detected_encoding = "latin-1"

        return {
            "success": True,
            "content": content,
            "size": file_size,
            "encoding": detected_encoding,
        }
    except PermissionError:
        return {"success": False, "error": "Access denied"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_write_file(params):
    """Save text file content from editor."""
    path = params.get("path", "")
    content = params.get("content", "")

    if not path:
        return {"success": False, "error": "No path provided"}

    resolved = safe_resolve(path)
    if not resolved:
        return {"success": False, "error": f"Invalid path: {path}"}

    try:
        # Ensure parent directory exists
        parent = os.path.dirname(resolved)
        if not os.path.isdir(parent):
            return {"success": False, "error": f"Parent directory not found: {parent}"}

        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)

        return {"success": True}
    except PermissionError:
        return {"success": False, "error": "Access denied"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_search(params):
    """Search for files by name pattern."""
    path = params.get("path", "")
    pattern = params.get("pattern", "*")
    max_results = int(params.get("max_results", 100))

    resolved = safe_resolve(path)
    if not resolved or not os.path.isdir(resolved):
        return {"success": False, "error": f"Directory not found: {path}"}

    results = []
    try:
        for root, dirs, files in os.walk(resolved):
            # Check directories
            for d in dirs:
                if fnmatch.fnmatch(d.lower(), pattern.lower()):
                    full_path = os.path.join(root, d)
                    try:
                        stat = os.stat(full_path)
                        results.append({
                            "name": d,
                            "path": full_path,
                            "is_dir": True,
                            "size": 0,
                            "modified": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                        })
                    except (PermissionError, OSError):
                        results.append({
                            "name": d,
                            "path": full_path,
                            "is_dir": True,
                            "size": 0,
                            "modified": "--",
                        })
                    if len(results) >= max_results:
                        break

            # Check files
            for f in files:
                if fnmatch.fnmatch(f.lower(), pattern.lower()):
                    full_path = os.path.join(root, f)
                    try:
                        stat = os.stat(full_path)
                        results.append({
                            "name": f,
                            "path": full_path,
                            "is_dir": False,
                            "size": stat.st_size,
                            "modified": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                        })
                    except (PermissionError, OSError):
                        results.append({
                            "name": f,
                            "path": full_path,
                            "is_dir": False,
                            "size": 0,
                            "modified": "--",
                        })
                    if len(results) >= max_results:
                        break

            if len(results) >= max_results:
                break

    except PermissionError:
        pass  # Skip directories we can't access
    except Exception as e:
        return {"success": False, "error": str(e)}

    return {"success": True, "results": results}


def handle_move(params):
    """Move a file or directory."""
    source = params.get("source", "")
    destination = params.get("destination", "")

    resolved_src = safe_resolve(source)
    resolved_dst = safe_resolve(destination)

    if not resolved_src or not os.path.exists(resolved_src):
        return {"success": False, "error": f"Source not found: {source}"}
    if not resolved_dst:
        return {"success": False, "error": f"Invalid destination: {destination}"}

    try:
        # If destination is a directory, move into it
        if os.path.isdir(resolved_dst):
            resolved_dst = os.path.join(resolved_dst, os.path.basename(resolved_src))
        shutil.move(resolved_src, resolved_dst)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_copy(params):
    """Copy a file or directory."""
    source = params.get("source", "")
    destination = params.get("destination", "")

    resolved_src = safe_resolve(source)
    resolved_dst = safe_resolve(destination)

    if not resolved_src or not os.path.exists(resolved_src):
        return {"success": False, "error": f"Source not found: {source}"}
    if not resolved_dst:
        return {"success": False, "error": f"Invalid destination: {destination}"}

    try:
        # If destination is a directory, copy into it
        if os.path.isdir(resolved_dst):
            resolved_dst = os.path.join(resolved_dst, os.path.basename(resolved_src))

        if os.path.isdir(resolved_src):
            shutil.copytree(resolved_src, resolved_dst)
        else:
            shutil.copy2(resolved_src, resolved_dst)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_zip(params):
    """Create a zip archive from one or more paths."""
    paths = params.get("paths", [])
    destination = params.get("destination", "")

    if not paths:
        return {"success": False, "error": "No paths provided"}

    resolved_dst = safe_resolve(destination)
    if not resolved_dst:
        return {"success": False, "error": f"Invalid destination: {destination}"}

    try:
        with zipfile.ZipFile(resolved_dst, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in paths:
                resolved_p = safe_resolve(p)
                if not resolved_p or not os.path.exists(resolved_p):
                    continue

                if os.path.isfile(resolved_p):
                    zf.write(resolved_p, os.path.basename(resolved_p))
                elif os.path.isdir(resolved_p):
                    base_name = os.path.basename(resolved_p)
                    for root, dirs, files in os.walk(resolved_p):
                        for f in files:
                            full_path = os.path.join(root, f)
                            arcname = os.path.join(base_name, os.path.relpath(full_path, resolved_p))
                            zf.write(full_path, arcname)

        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_unzip(params):
    """Extract a zip archive."""
    path = params.get("path", "")
    destination = params.get("destination", "")

    resolved_src = safe_resolve(path)
    if not resolved_src or not os.path.isfile(resolved_src):
        return {"success": False, "error": f"Archive not found: {path}"}

    resolved_dst = safe_resolve(destination)
    if not resolved_dst:
        return {"success": False, "error": f"Invalid destination: {destination}"}

    try:
        os.makedirs(resolved_dst, exist_ok=True)
        with zipfile.ZipFile(resolved_src, "r") as zf:
            zf.extractall(resolved_dst)
        return {"success": True}
    except zipfile.BadZipFile:
        return {"success": False, "error": "File is not a valid zip archive"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_sysinfo(params):
    """Get system information."""
    info = {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
    }

    # Try psutil for detailed stats
    try:
        import psutil

        # CPU
        info["cpu_count"] = psutil.cpu_count(logical=True)
        info["cpu_count_physical"] = psutil.cpu_count(logical=False)
        info["cpu_percent"] = psutil.cpu_percent(interval=1)

        # Memory
        mem = psutil.virtual_memory()
        info["memory_total"] = mem.total
        info["memory_total_fmt"] = format_size(mem.total)
        info["memory_used"] = mem.used
        info["memory_used_fmt"] = format_size(mem.used)
        info["memory_percent"] = mem.percent

        # Disk
        disks = []
        for part in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disks.append({
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "total": usage.total,
                    "total_fmt": format_size(usage.total),
                    "used": usage.used,
                    "used_fmt": format_size(usage.used),
                    "free": usage.free,
                    "free_fmt": format_size(usage.free),
                    "percent": usage.percent,
                })
            except (PermissionError, OSError):
                pass
        info["disks"] = disks

        # Uptime
        boot_time = datetime.datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.datetime.now() - boot_time
        info["boot_time"] = boot_time.strftime("%Y-%m-%d %H:%M:%S")
        info["uptime_seconds"] = int(uptime.total_seconds())
        days, remainder = divmod(int(uptime.total_seconds()), 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, _ = divmod(remainder, 60)
        info["uptime_fmt"] = f"{days}d {hours}h {minutes}m"

    except ImportError:
        # psutil not available - gather basic info from os/platform
        info["psutil_available"] = False

        # Basic CPU count
        cpu_count = os.cpu_count()
        if cpu_count:
            info["cpu_count"] = cpu_count

        # Basic disk info for drives
        for drive in get_drives():
            try:
                total, used, free = shutil.disk_usage(drive)
                info.setdefault("disks", []).append({
                    "device": drive,
                    "mountpoint": drive,
                    "total": total,
                    "total_fmt": format_size(total),
                    "used": used,
                    "used_fmt": format_size(used),
                    "free": free,
                    "free_fmt": format_size(free),
                    "percent": round((used / total) * 100, 1) if total > 0 else 0,
                })
            except (PermissionError, OSError):
                pass

    return {"success": True, "info": info}


def handle_processes(params):
    """List running processes."""
    sort_by = params.get("sort_by", "memory")  # memory, cpu, name, pid
    limit = int(params.get("limit", 100))

    processes = []
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info', 'status', 'username', 'create_time']):
            try:
                info = proc.info
                mem = info['memory_info']
                processes.append({
                    "pid": info['pid'],
                    "name": info['name'] or "Unknown",
                    "cpu": info['cpu_percent'] or 0,
                    "memory": mem.rss if mem else 0,
                    "memory_fmt": format_size(mem.rss) if mem else "0 B",
                    "status": info['status'] or "",
                    "user": info['username'] or "",
                    "started": datetime.datetime.fromtimestamp(info['create_time']).strftime("%Y-%m-%d %H:%M") if info['create_time'] else "",
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        # Sort
        if sort_by == "cpu":
            processes.sort(key=lambda x: x["cpu"], reverse=True)
        elif sort_by == "memory":
            processes.sort(key=lambda x: x["memory"], reverse=True)
        elif sort_by == "name":
            processes.sort(key=lambda x: x["name"].lower())
        elif sort_by == "pid":
            processes.sort(key=lambda x: x["pid"])

        processes = processes[:limit]

    except ImportError:
        # Fallback: use tasklist command on Windows
        try:
            result = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10
            )
            import csv
            import io
            reader = csv.reader(io.StringIO(result.stdout))
            for row in reader:
                if len(row) >= 5:
                    mem_str = row[4].replace(",", "").replace(" K", "").replace("\"", "").strip()
                    mem_bytes = int(mem_str) * 1024 if mem_str.isdigit() else 0
                    processes.append({
                        "pid": int(row[1].strip('"')) if row[1].strip('"').isdigit() else 0,
                        "name": row[0].strip('"'),
                        "cpu": 0,
                        "memory": mem_bytes,
                        "memory_fmt": format_size(mem_bytes),
                        "status": "",
                        "user": "",
                        "started": "",
                    })
            processes.sort(key=lambda x: x["memory"], reverse=True)
            processes = processes[:limit]
        except Exception as e:
            return {"success": False, "error": str(e)}

    return {"success": True, "processes": processes, "count": len(processes)}


def handle_kill_process(params):
    """Kill a process by PID."""
    pid = int(params.get("pid", 0))
    if not pid:
        return {"success": False, "error": "No PID provided"}

    try:
        import psutil
        proc = psutil.Process(pid)
        name = proc.name()
        proc.kill()
        return {"success": True, "message": f"Killed {name} (PID {pid})"}
    except ImportError:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True, timeout=10)
            return {"success": True, "message": f"Killed PID {pid}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_screenshot(params):
    """Take a screenshot and return as base64 JPEG."""
    quality = int(params.get("quality", 60))

    try:
        import io

        # Try mss first (fast, no display server needed)
        try:
            import mss
            with mss.mss() as sct:
                monitor = sct.monitors[0]  # full virtual screen
                img_bytes = sct.grab(monitor)
                # Convert to JPEG via PIL
                from PIL import Image
                img = Image.frombytes("RGB", img_bytes.size, img_bytes.bgra, "raw", "BGRX")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality)
                b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                return {
                    "success": True,
                    "image": b64,
                    "width": img.width,
                    "height": img.height,
                    "format": "jpeg",
                }
        except ImportError:
            pass

        # Try PIL ImageGrab
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab(all_screens=True)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return {
                "success": True,
                "image": b64,
                "width": img.width,
                "height": img.height,
                "format": "jpeg",
            }
        except ImportError:
            pass

        # Fallback: use Windows API via PowerShell
        temp_path = os.path.join(os.environ.get("TEMP", "."), "rfm_screenshot.jpg")
        ps_cmd = f'''
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$g.Dispose()
$bmp.Save("{temp_path}", [System.Drawing.Imaging.ImageFormat]::Jpeg)
$bmp.Dispose()
'''
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=15
        )

        if os.path.isfile(temp_path):
            with open(temp_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            os.unlink(temp_path)
            return {
                "success": True,
                "image": b64,
                "width": 0,
                "height": 0,
                "format": "jpeg",
            }

        return {"success": False, "error": "No screenshot library available. Install Pillow: pip install Pillow"}

    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_self_update(params):
    """Self-update: download new exe/script from URL, replace, and restart."""
    update_url = params.get("url", "")
    if not update_url:
        # Default: pull latest app.py from GitHub and rebuild
        update_url = "https://raw.githubusercontent.com/argonarsoftware-oss/remotefilemanager/master/app.py"

    try:
        if getattr(sys, 'frozen', False):
            # Running as .exe — download new app.py, rebuild, swap, restart
            exe_path = sys.executable
            exe_dir = os.path.dirname(exe_path)
            src_dir = os.path.dirname(exe_dir)  # parent of dist/
            app_py = os.path.join(src_dir, "app.py")

            # Download latest app.py
            resp = requests.get(update_url, timeout=30)
            if resp.status_code != 200:
                return {"success": False, "error": f"Download failed: HTTP {resp.status_code}"}

            with open(app_py, "w", encoding="utf-8") as f:
                f.write(resp.text)

            # Create a batch script that:
            # 1. Waits for current process to exit
            # 2. Rebuilds the exe
            # 3. Starts the new exe
            # 4. Deletes itself
            bat_path = os.path.join(src_dir, "_update.bat")
            with open(bat_path, "w") as bat:
                bat.write(f'@echo off\n')
                bat.write(f'echo Waiting for agent to exit...\n')
                bat.write(f'timeout /t 3 /nobreak >nul\n')
                bat.write(f'echo Rebuilding...\n')
                bat.write(f'cd /d "{src_dir}"\n')
                bat.write(f'pyinstaller --onefile --noconsole --uac-admin --name RemoteFileManager app.py\n')
                bat.write(f'if %errorlevel% neq 0 (\n')
                bat.write(f'  echo Build failed, restarting old exe...\n')
                bat.write(f'  start "" "{exe_path}"\n')
                bat.write(f'  del "%~f0"\n')
                bat.write(f'  exit\n')
                bat.write(f')\n')
                bat.write(f'echo Starting new agent...\n')
                bat.write(f'start "" "{exe_path}"\n')
                bat.write(f'del "%~f0"\n')

            # Launch the updater and exit
            subprocess.Popen(
                ["cmd", "/c", bat_path],
                creationflags=subprocess.CREATE_NO_WINDOW,
                close_fds=True,
            )

            # Tell server we're updating, then exit
            return {"success": True, "message": "Update started. Agent will restart in ~60 seconds."}

        else:
            # Running as python script — just download and replace app.py, then restart
            script_path = os.path.abspath(sys.argv[0])
            script_dir = os.path.dirname(script_path)

            resp = requests.get(update_url, timeout=30)
            if resp.status_code != 200:
                return {"success": False, "error": f"Download failed: HTTP {resp.status_code}"}

            with open(script_path, "w", encoding="utf-8") as f:
                f.write(resp.text)

            # Create batch script to restart python
            bat_path = os.path.join(script_dir, "_update.bat")
            with open(bat_path, "w") as bat:
                bat.write(f'@echo off\n')
                bat.write(f'timeout /t 3 /nobreak >nul\n')
                bat.write(f'start "" "{sys.executable}" "{script_path}"\n')
                bat.write(f'del "%~f0"\n')

            subprocess.Popen(
                ["cmd", "/c", bat_path],
                creationflags=subprocess.CREATE_NO_WINDOW,
                close_fds=True,
            )

            return {"success": True, "message": "Update started. Agent will restart in ~5 seconds."}

    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# Command Dispatcher
# ============================================================
HANDLERS = {
    "list": handle_list,
    "delete": handle_delete,
    "mkdir": handle_mkdir,
    "rename": handle_rename,
    "properties": handle_properties,
    "shell": handle_shell,
    "read_file": handle_read_file,
    "write_file": handle_write_file,
    "search": handle_search,
    "move": handle_move,
    "copy": handle_copy,
    "zip": handle_zip,
    "unzip": handle_unzip,
    "sysinfo": handle_sysinfo,
    "processes": handle_processes,
    "kill_process": handle_kill_process,
    "self_update": handle_self_update,
    "screenshot": handle_screenshot,
    "shutdown": lambda params: {"success": True, "message": "Agent shutting down"},
}

def process_command(cmd):
    """Process a command from the server."""
    command_id = cmd["id"]
    command = cmd["command"]
    params = cmd.get("params", {})

    log(f"Executing: {command} | {json.dumps(params)[:100]}")

    try:
        # Special handling for download/upload (need command_id)
        if command == "download":
            result = handle_download(params, command_id)
        elif command == "upload":
            result = handle_upload(params, command_id)
        elif command in HANDLERS:
            result = HANDLERS[command](params)
        else:
            result = {"success": False, "error": f"Unknown command: {command}"}
    except Exception as e:
        result = {"success": False, "error": str(e)}
        log(f"Error: {traceback.format_exc()}")

    # Send result back to server (skip for download/upload - they handle their own results)
    if command not in ("download", "upload"):
        server_post("agent.result", {
            "command_id": command_id,
            "result": json.dumps(result),
        })

    log(f"Done: {command} -> {'OK' if result.get('success') else result.get('error', 'FAIL')}")

    # Exit after self_update so the updater script can replace us
    if command == "self_update" and result.get("success"):
        log("Exiting for self-update...")
        time.sleep(1)
        os._exit(0)

    # Kill switch — server can remotely shut down this agent
    if command == "shutdown":
        log("Shutdown command received. Exiting...")
        time.sleep(1)
        os._exit(0)


# ============================================================
# Main Loop
# ============================================================
def register():
    """Register agent with server."""
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except Exception:
        local_ip = "unknown"

    result = server_post("agent.register", {
        "hostname": hostname,
        "local_ip": local_ip,
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
    })

    if result and result.get("success"):
        log("Registered with server")
        return True
    return False


def main():
    print("=" * 55)
    print("  Remote File Manager - Agent")
    print("=" * 55)
    print(f"  Server:  {SERVER_URL}")
    print(f"  Admin:   {'Yes' if is_admin() else 'No'}")
    print(f"  Host:    {socket.gethostname()}")
    print(f"  OS:      {platform.system()} {platform.release()}")
    print("=" * 55)
    print()

    # Try to register
    log("Connecting to server...")
    retries = 0
    while True:
        if register():
            break
        retries += 1
        wait = min(retries * 5, 60)
        log(f"Server unreachable. Retrying in {wait}s...")
        time.sleep(wait)

    log(f"Polling every {POLL_INTERVAL}s. Press Ctrl+C to stop.")
    print()

    # Main poll loop
    while True:
        try:
            result = server_post("agent.poll", {"hostname": socket.gethostname()})

            if result and result.get("success"):
                cmd = result.get("command")
                if cmd:
                    # Process in a thread so we can continue polling
                    threading.Thread(
                        target=process_command,
                        args=(cmd,),
                        daemon=True
                    ).start()

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            log("Shutting down...")
            break
        except Exception as e:
            log(f"Poll error: {e}")
            time.sleep(POLL_INTERVAL * 2)


if __name__ == "__main__":
    if "--install" in sys.argv:
        if install_task_scheduler():
            print("Task Scheduler: Installed successfully.")
            print("Agent will auto-start at logon with admin rights.")
        else:
            print("Task Scheduler: Failed to install.")
        sys.exit(0)

    if "--uninstall" in sys.argv:
        if uninstall_task_scheduler():
            print("Task Scheduler: Removed successfully.")
        else:
            print("Task Scheduler: Failed to remove.")
        sys.exit(0)

    # Auto-install on first run
    install_task_scheduler()

    main()
