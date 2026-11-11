import os
import sys
import subprocess
import shutil
import hashlib
import time
import zipfile

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

def make_portable_zip(source_dir, output_zip):
    print(f"Creating portable zip: {output_zip} ...")
    if os.path.exists(output_zip):
        os.remove(output_zip)
    
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, _, files in os.walk(source_dir):
            for file in files:
                abs_path = os.path.join(root, file)
                rel_path = os.path.join("EyeGuard", os.path.relpath(abs_path, source_dir))
                zf.write(abs_path, rel_path)
    print(f"Portable zip created ({round(os.path.getsize(output_zip) / (1024*1024), 2)} MB).")

def main():
    kill_running()
    os.chdir(SRC_DIR)

    common_flags = [
        "--assume-yes-for-downloads",
        "--windows-console-mode=disable",
        "--enable-plugin=pywebview",
        "--enable-plugin=no-qt",
        "--company-name=EyeGuard",
        "--product-name=EyeGuard",
        "--file-version=3.0.0.0",
        "--product-version=3.0.0.0",
        "--file-description=EyeGuard - Intelligent Screen Ergonomics & Eye Care",
        "--copyright=Copyright (c) 2026 EyeGuard. MIT License.",
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

    print("\n=== STEP 1: Nuitka Standalone Compilation ===")
    nuitka_standalone = [
        PYTHON, "-m", "nuitka",
        "--standalone",
        *common_flags,
        "--output-dir=dist_nuitka",
        "--output-filename=EyeGuard.exe",
        "run_eyeguard.py"
    ]
    run(nuitka_standalone)

    compiled_dist = os.path.join(SRC_DIR, "dist_nuitka", "run_eyeguard.dist")
    target_dist = os.path.join(SRC_DIR, "dist", "EyeGuard")
    if os.path.exists(target_dist):
        shutil.rmtree(target_dist)
    shutil.copytree(compiled_dist, target_dist)

    print("\n=== STEP 2: Packaging Portable ZIP Archive ===")
    zip_output = os.path.join(SRC_DIR, "dist", "EyeGuard_Portable.zip")
    make_portable_zip(target_dist, zip_output)

    print("\n=== STEP 3: Inno Setup Compilation (Installer) ===")
    if os.path.exists(ISCC):
        run(f'"{ISCC}" EyeGuard_Setup.iss')
    else:
        print(f"WARNING: ISCC not found at {ISCC}")

    print("\n=== STEP 4: Deploy to Desktop ===")
    portable_zip_dst = os.path.join(DESKTOP, "EyeGuard_Portable.zip")
    shutil.copy2(zip_output, portable_zip_dst)
    print(f"Copied {zip_output} -> {portable_zip_dst}")

    setup_src = os.path.join(SRC_DIR, "dist", "installer", "EyeGuard_Setup.exe")
    setup_dst = os.path.join(DESKTOP, "EyeGuard_Setup.exe")
    if os.path.exists(setup_src):
        shutil.copy2(setup_src, setup_dst)
        print(f"Copied {setup_src} -> {setup_dst}")
    else:
        print(f"ERROR: {setup_src} not found!")

    # Clean old portable exe from desktop if present to avoid confusion
    old_portable_exe = os.path.join(DESKTOP, "EyeGuard_Portable.exe")
    if os.path.exists(old_portable_exe):
        try:
            os.remove(old_portable_exe)
            print(f"Cleaned deprecated {old_portable_exe}")
        except Exception:
            pass

    print("\n=== STEP 5: SHA256 Hashes ===")
    for p in [portable_zip_dst, setup_dst]:
        if os.path.exists(p):
            print(f"{os.path.basename(p)}: {sha256_of(p)}")

    print("\n=== BUILD COMPLETE ===")

if __name__ == "__main__":
    main()
