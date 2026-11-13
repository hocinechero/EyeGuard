import sys
import os

# Ensure base directory is in sys.path
if getattr(sys, "frozen", False):
    BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app import run_app

def main():
    if len(sys.argv) >= 4 and sys.argv[1] == "--crash-guard":
        from core.crash_guard import run_watchdog
        run_watchdog(int(sys.argv[2]), sys.argv[3])
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] in ("--reset", "-r", "--reset-hw"):
        from core.crash_guard import emergency_restore_display
        emergency_restore_display(reset_hw_brightness=(sys.argv[1] == "--reset-hw"))
        print("EyeGuard: Screen reset to factory identity (6500K / 100%).")
        sys.exit(0)
    run_app()

if __name__ == "__main__":
    main()
