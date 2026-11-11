import os
import sys
import subprocess
import shutil
import hashlib
import time

SRC_DIR = r"C:\Users\dell\Desktop\EyeGuard-Source"
DESKTOP = r"C:\Users\dell\Desktop"
ISCC = r"C:\Users\dell\AppData\Local\Programs\Inno Setup 6\ISCC.exe"
PYTHON = sys.executable

def run(cmd, cwd=SRC_DIR):
    print(f"--> Running: {cmd if isinstance(cmd, str) else ' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str))
    if res.returncode != 0:
        print(f"FAILED (code {res.returncode}): {cmd}")
        sys.exit(res.returncode)

def kill_running():
    print("Terminating running EyeGuard processes...")
    subprocess.run("taskkill /F /IM EyeGuard* /T", shell=True, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    time.sleep(1)
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
    os.chdir(SRC_DIR)

    common_flags = [
        "--assume-yes-for-downloads",
        "--windows-console-mode=disable",
        "--enable-plugin=pywebview",
        "--enable-plugin=no-qt",
        "--include-package=core",
        "--include-package=ui",
        "--include-package=clr_loader",
        "--include-package=pythonnet",
        "--include-module=clr",
        "--include-module=pystray._win32",
        "--include-package-data=webview",
        "--include-package-data=pythonnet",
        "--include-package-data=clr_loader",
        "--include-data-dir=web=web",
        "--include-data-dir=assets=assets",
        "--windows-icon-from-ico=assets/app_icon.ico",
    ]

    print("\n=== STEP 1: Nuitka Standalone Compilation (for Installer) ===")
    nuitka_standalone = [
        PYTHON, "-m", "nuitka",
        "--standalone",
        *common_flags,
        "--output-dir=dist_nuitka",
        "--output-filename=EyeGuard.exe",
        "run_eyeguard.py"
    ]
    run(nuitka_standalone)

    target_dist = os.path.join(SRC_DIR, "dist", "EyeGuard")
    if os.path.exists(target_dist):
        shutil.rmtree(target_dist)
    shutil.copytree(os.path.join(SRC_DIR, "dist_nuitka", "run_eyeguard.dist"), target_dist)

    print("\n=== STEP 2: Nuitka Onefile Compilation (for Portable) ===")
    nuitka_onefile = [
        PYTHON, "-m", "nuitka",
        "--onefile",
        *common_flags,
        "--output-dir=dist_nuitka_onefile",
        "--output-filename=EyeGuard_Portable.exe",
        "run_eyeguard.py"
    ]
    run(nuitka_onefile)

    print("\n=== STEP 3: Inno Setup Compilation ===")
    if os.path.exists(ISCC):
        run(f'"{ISCC}" EyeGuard_Setup.iss')
    else:
        print(f"WARNING: ISCC not found at {ISCC}")

    print("\n=== STEP 4: Deploy to Desktop ===")
    portable_src = os.path.join(SRC_DIR, "dist_nuitka_onefile", "EyeGuard_Portable.exe")
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

    print("\n=== STEP 5: SHA256 Hashes ===")
    for p in [portable_dst, setup_dst]:
        if os.path.exists(p):
            print(f"{os.path.basename(p)}: {sha256_of(p)}")

    print("\n=== BUILD COMPLETE ===")

if __name__ == "__main__":
    main()
