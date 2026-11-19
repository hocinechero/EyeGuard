import os
import sys
import subprocess
import shutil
import hashlib
import struct
import time
import zipfile

SRC_DIR = r"C:\Users\dell\Desktop\EyeGuard-Source"
DESKTOP = r"C:\Users\dell\Desktop"
ISCC = r"C:\Users\dell\AppData\Local\Programs\Inno Setup 6\ISCC.exe"
PYTHON = sys.executable

def run(cmd, cwd=SRC_DIR, env=None):
    print(f"--> Running: {cmd if isinstance(cmd, str) else ' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), env=env)
    if res.returncode != 0:
        print(f"FAILED (code {res.returncode}): {cmd}")
        sys.exit(res.returncode)


def verify_baseline_x86_64_pe(exe_path):
    """Ensures compiled executable .text contains zero AVX/VEX YMM instructions (prevents 0xC000001D on non-AVX CPUs)."""
    with open(exe_path, "rb") as f:
        data = f.read()
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    coff = e_lfanew + 4
    _, num_sec, _, _, _, opt_size, _ = struct.unpack_from("<HHIIIHH", data, coff)
    opt = coff + 20
    text_bytes = b""
    for i in range(num_sec):
        s = data[opt + opt_size + i * 40 : opt + opt_size + (i + 1) * 40]
        if s[:8].rstrip(b"\x00") == b".text":
            vsize, _, _, ra = struct.unpack_from("<IIII", s, 8)
            text_bytes = data[ra : ra + vsize]
            break
    if not text_bytes:
        raise RuntimeError(f"Could not locate .text section in {exe_path}")
    forbidden = {
        "vmovups_ymm_load": b"\xc5\xfc\x10",
        "vmovups_ymm_store": b"\xc5\xfc\x11",
        "vmovdqu_ymm_load": b"\xc5\xfe\x6f",
        "vmovdqu_ymm_store": b"\xc5\xfe\x7f",
        "vzeroupper": b"\xc5\xf8\x77",
    }
    hits = {name: text_bytes.count(sig) for name, sig in forbidden.items() if text_bytes.count(sig) > 0}
    if hits:
        raise RuntimeError(
            f"Baseline x86_64 verification FAILED for {exe_path}: detected non-baseline AVX opcodes {hits}. "
            "Ensure LDFLAGS includes -march=x86_64 when linking with Zig."
        )
    print(f"Baseline x86_64 ISA check PASSED for {os.path.basename(exe_path)} (.text={len(text_bytes)} bytes, 0 AVX opcodes).")

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
        "--file-version=4.0.1.0",
        "--product-version=4.0.1.0",
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
    build_env = os.environ.copy()
    existing_ldflags = build_env.get("LDFLAGS", "").strip()
    if "-march=x86_64" not in existing_ldflags:
        build_env["LDFLAGS"] = (existing_ldflags + " -march=x86_64").strip()

    nuitka_standalone = [
        PYTHON, "-m", "nuitka",
        "--standalone",
        *common_flags,
        "--output-dir=dist_nuitka",
        "--output-filename=EyeGuard.exe",
        "run_eyeguard.py"
    ]
    run(nuitka_standalone, env=build_env)

    compiled_dist = os.path.join(SRC_DIR, "dist_nuitka", "run_eyeguard.dist")
    verify_baseline_x86_64_pe(os.path.join(compiled_dist, "EyeGuard.exe"))
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
