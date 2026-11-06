# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_all

block_cipher = None
BASE_DIR = os.path.abspath(SPECPATH)

# Collect all assets, binaries and hidden imports for webview, clr_loader, pythonnet
wv_datas, wv_binaries, wv_hidden = collect_all('webview')
clr_datas, clr_binaries, clr_hidden = collect_all('clr_loader')
py_datas, py_binaries, py_hidden = collect_all('pythonnet')

datas = [
    (os.path.join(BASE_DIR, 'web'), 'web'),
    (os.path.join(BASE_DIR, 'assets'), 'assets'),
] + wv_datas + clr_datas + py_datas

binaries = wv_binaries + clr_binaries + py_binaries

hiddenimports = [
    'app',
    'config',
    'core',
    'core.gamma_engine',
    'core.window_focus',
    'core.crash_guard',
    'core.system_events',
    'core.solar_engine',
    'core.timer_engine',
    'core.hotkey_engine',
    'core.idle_detector',
    'core.win32_ruler',
    'core.audio_player',
    'core.desktop_overlay',
    'win32gui',
    'win32con',
    'win32api',
    'ctypes',
    'ctypes.wintypes',
    'pystray',
    'pystray._win32',
    'PIL',
    'PIL.Image',
    'PIL.ImageDraw',
    'clr',
    'clr_loader',
    'pythonnet',
] + wv_hidden + clr_hidden + py_hidden

a = Analysis(
    [os.path.join(BASE_DIR, 'run_eyeguard.py')],
    pathex=[BASE_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

version_path = os.path.join(BASE_DIR, 'version_info.txt') if os.path.exists(os.path.join(BASE_DIR, 'version_info.txt')) else None
icon_path = os.path.join(BASE_DIR, 'assets', 'app_icon.ico') if os.path.exists(os.path.join(BASE_DIR, 'assets', 'app_icon.ico')) else None

# 1. Onedir executable for Inno Setup installer
exe_dir = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='EyeGuard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
    version=version_path,
)

coll = COLLECT(
    exe_dir,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='EyeGuard',
)

# 2. Standalone Single-File Portable Executable (No installation required)
exe_portable = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='EyeGuard_Portable',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_console=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
    version=version_path,
)
