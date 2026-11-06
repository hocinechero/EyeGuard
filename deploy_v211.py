import os
import sys
import shutil
import hashlib
import zipfile
import subprocess
import base64
import json
import re

REPO_DIR = r"C:\Users\dell\Desktop\EyeGuard-Source"
DESKTOP_DIR = r"C:\Users\dell\Desktop"
GH_EXE = r"C:\Program Files\GitHub CLI\gh.exe"
GIT_EXE = r"C:\Users\dell\AppData\Local\Programs\Git\cmd\git.exe"

def sha256_file(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()

portable_dst = os.path.join(DESKTOP_DIR, "EyeGuard_Portable.exe")
setup_dst = os.path.join(DESKTOP_DIR, "EyeGuard_Setup.exe")
zip_dst = os.path.join(DESKTOP_DIR, "EyeGuard-Source.zip")

print("=== Creating EyeGuard-Source.zip ===")
EXCLUDES = {".git", "build", "dist", "__pycache__", ".venv"}
with zipfile.ZipFile(zip_dst, "w", zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(REPO_DIR):
        dirs[:] = [d for d in dirs if d not in EXCLUDES]
        for file in files:
            if file.endswith((".pyc", ".zip")) or file == "deploy_v211.py":
                continue
            abs_path = os.path.join(root, file)
            rel_path = os.path.relpath(abs_path, REPO_DIR)
            zf.write(abs_path, os.path.join("EyeGuard-Source", rel_path))

setup_hash = sha256_file(setup_dst)
portable_hash = sha256_file(portable_dst)
zip_hash = sha256_file(zip_dst)

print(f"EyeGuard_Portable.exe SHA256: {portable_hash}")
print(f"EyeGuard_Setup.exe SHA256:    {setup_hash}")
print(f"EyeGuard-Source.zip SHA256:   {zip_hash}")

print("=== Uploading Binaries to GitHub Releases ===")
cmd_upload = [GH_EXE, "release", "upload", "v2.1.1", portable_dst, setup_dst, zip_dst, "--repo", "hocinechero/EyeGuard", "--clobber"]
subprocess.run(cmd_upload, check=True)

print("=== Updating GitHub Release Description ===")
release_notes_cmd = [GH_EXE, "release", "view", "v2.1.1", "--repo", "hocinechero/EyeGuard", "--json", "body", "--jq", ".body"]
res_notes = subprocess.run(release_notes_cmd, capture_output=True, encoding="utf-8", check=True)
current_body = res_notes.stdout

updated_body = re.sub(
    r"\|\s*\*\*`EyeGuard_Portable\.exe`\*\*\s*\|\s*`[^`]+`\s*\|\s*`[a-fA-F0-9]+`\s*\|",
    f"| **`EyeGuard_Portable.exe`** | `{(os.path.getsize(portable_dst)/(1024*1024)):.2f} MB` | `{portable_hash.lower()}` |",
    current_body
)
updated_body = re.sub(
    r"\|\s*\*\*`EyeGuard_Setup\.exe`\*\*\s*\|\s*`[^`]+`\s*\|\s*`[a-fA-F0-9]+`\s*\|",
    f"| **`EyeGuard_Setup.exe`** | `{(os.path.getsize(setup_dst)/(1024*1024)):.2f} MB` | `{setup_hash.lower()}` |",
    updated_body
)
updated_body = re.sub(
    r"\|\s*\*\*`EyeGuard-Source\.zip`\*\*\s*\|\s*`[^`]+`\s*\|\s*`[a-fA-F0-9]+`\s*\|",
    f"| **`EyeGuard-Source.zip`** | `{(os.path.getsize(zip_dst)/(1024*1024)):.2f} MB` | `{zip_hash.lower()}` |",
    updated_body
)

body_file = os.path.join(REPO_DIR, "release_notes_tmp.md")
with open(body_file, "w", encoding="utf-8") as f:
    f.write(updated_body)

subprocess.run([GH_EXE, "release", "edit", "v2.1.1", "--repo", "hocinechero/EyeGuard", "--notes-file", body_file], check=True)
if os.path.exists(body_file):
    os.remove(body_file)

print("=== Updating Winget Installer Manifest ===")
yaml_path = os.path.join(REPO_DIR, "winget", "manifests", "h", "hocinechero", "EyeGuard", "2.1.1", "hocinechero.EyeGuard.installer.yaml")
with open(yaml_path, "r", encoding="utf-8") as f:
    yaml_text = f.read()

yaml_text = re.sub(r"InstallerSha256:\s+[A-Fa-f0-9]+", f"InstallerSha256: {setup_hash}", yaml_text)
with open(yaml_path, "w", encoding="utf-8") as f:
    f.write(yaml_text)

print("=== Git Commit & Push to Main ===")
subprocess.run([GIT_EXE, "-C", REPO_DIR, "add", "."], check=True)
subprocess.run([GIT_EXE, "-C", REPO_DIR, "commit", "-m", "fix(suite): blend palette kelvin warmth, lock foreground break kiosk, and add windows auto dark mode"], check=True)
subprocess.run([GIT_EXE, "-C", REPO_DIR, "push", "origin", "main"], check=True)

print("=== Updating Winget PR Branch via GitHub API ===")
with open(yaml_path, "rb") as f:
    b64_content = base64.b64encode(f.read()).decode("utf-8")

get_sha_cmd = [GH_EXE, "api", "repos/hocinechero/winget-pkgs/contents/manifests/h/hocinechero/EyeGuard/2.1.1/hocinechero.EyeGuard.installer.yaml?ref=hocinechero.EyeGuard-2.1.1-500fd769-30d2-4ff0-ac37-52891cfbc87b", "--jq", ".sha"]
res_sha = subprocess.run(get_sha_cmd, capture_output=True, encoding="utf-8", check=True)
current_file_sha = res_sha.stdout.strip()
print(f"Current Winget YAML SHA on PR branch: {current_file_sha}")

if current_file_sha:
    payload = {
        "message": f"fix(installer): update EyeGuard_Setup.exe sha256 to {setup_hash}",
        "content": b64_content,
        "sha": current_file_sha,
        "branch": "hocinechero.EyeGuard-2.1.1-500fd769-30d2-4ff0-ac37-52891cfbc87b"
    }
    temp_json = os.path.join(REPO_DIR, "temp_winget.json")
    with open(temp_json, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    subprocess.run([GH_EXE, "api", "--method", "PUT", "repos/hocinechero/winget-pkgs/contents/manifests/h/hocinechero/EyeGuard/2.1.1/hocinechero.EyeGuard.installer.yaml", "--input", temp_json], check=True)
    if os.path.exists(temp_json):
        os.remove(temp_json)
    print("Successfully updated Winget PR branch!")

print("\n==========================================")
print("  DEPLOYMENT COMPLETED WITH 100% SUCCESS")
print("==========================================")
