import os
import sys
import subprocess
import shutil
import hashlib
import time

SRC_DIR = r"C:\Users\dell\Desktop\EyeGuard-Source"
DESKTOP = r"C:\Users\dell\Desktop"
ISCC = r"C:\Users\dell\AppData\Local\Programs\Inno Setup 6\ISCC.exe"

def run(cmd, cwd=SRC_DIR):
    print(f"--> Running: {cmd}")
    res = subprocess.run(cmd, shell=True, cwd=cwd)
    if res.returncode != 0:
        print(f"FAILED (code {res.returncode}): {cmd}")
        sys.exit(res.returncode)

def kill_running():
    print("Terminating running EyeGuard processes...")
    subprocess.run("taskkill /F /IM EyeGuard* /T", shell=True, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    time.sleep(1)
    # Safely clear orphaned WebView2 lock files to prevent dark empty windows
    wv_dir = os.path.expandvars(r"%LOCALAPPDATA%\EyeGuardWebview\EBWebView")
    if os.path.isdir(wv_dir):
        for root, _, files in os.walk(wv_dir):
            for file in files:
                if file.upper() in ("LOCK", "LOCKFILE"):
                    try:
                        os.remove(os.path.join(root, file))
                    except Exception:
                        pass

def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()

def main():
    kill_running()

    print("\n=== STEP 1: PyInstaller Build ===")
    run(f'"{sys.executable}" -m PyInstaller --noconfirm EyeGuard.spec')

    print("\n=== STEP 2: Inno Setup Compilation ===")
    if os.path.exists(ISCC):
        run(f'"{ISCC}" EyeGuard_Setup.iss')
    else:
        print(f"WARNING: ISCC not found at {ISCC}")

    print("\n=== STEP 3: Deploy to Desktop ===")
    portable_src = os.path.join(SRC_DIR, "dist", "EyeGuard_Portable.exe")
    portable_dst = os.path.join(DESKTOP, "EyeGuard_Portable.exe")
    if os.path.exists(portable_src):
        shutil.copy2(portable_src, portable_dst)
        print(f"Copied {portable_src} -> {portable_dst}")
    else:
        print(f"ERROR: {portable_src} not found!")

    setup_src = os.path.join(SRC_DIR, "dist", "installer", "EyeGuard_Setup.exe")
    setup_dst = os.path.join(DESKTOP, "EyeGuard_Setup.exe")
    if os.path.exists(setup_src):
        shutil.copy2(setup_src, setup_dst)
        print(f"Copied {setup_src} -> {setup_dst}")
    else:
        print(f"ERROR: {setup_src} not found!")

    print("\n=== STEP 4: SHA256 Hashes ===")
    for p in [portable_dst, setup_dst]:
        if os.path.exists(p):
            print(f"{os.path.basename(p)}: {sha256_of(p)}")

    print("\n=== BUILD COMPLETE ===")

if __name__ == "__main__":
    main()
