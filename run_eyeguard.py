import sys
import os

# Ensure base directory is in sys.path
if getattr(sys, "frozen", False):
    BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def _enable_early_diagnostics():
    try:
        from core.startup_env import enable_native_fault_handler, install_stdio_fallback

        install_stdio_fallback()
        if "--crash-guard" not in sys.argv:
            enable_native_fault_handler()
    except Exception:
        pass


def main():
    _enable_early_diagnostics()

    if "--disable-gpu" in sys.argv:
        os.environ["EYEGUARD_DISABLE_GPU"] = "1"

    if len(sys.argv) >= 4 and sys.argv[1] == "--crash-guard":
        from core.crash_guard import run_watchdog

        run_watchdog(int(sys.argv[2]), sys.argv[3])
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] in ("--reset", "-r", "--reset-hw"):
        from core.crash_guard import emergency_restore_display

        emergency_restore_display(reset_hw_brightness=(sys.argv[1] == "--reset-hw"))
        print("EyeGuard: Screen reset to factory identity (6500K / 100%).")
        sys.exit(0)

    try:
        from app import run_app

        run_app()
    except SystemExit:
        raise
    except BaseException as exc:
        if "--crash-guard" not in sys.argv:
            try:
                from core.startup_env import MB_ICONERROR, MB_OK, show_native_error_box, write_startup_log

                log_path = write_startup_log("Unhandled fatal error in EyeGuard.", exc=exc)
                msg = (
                    f"EyeGuard encountered an unexpected error and could not start.\n\n"
                    f"{type(exc).__name__}: {exc}"
                )
                if log_path:
                    msg += f"\n\nDiagnostics log saved to:\n{log_path}"
                show_native_error_box("EyeGuard — Startup Error", msg, MB_OK | MB_ICONERROR)
            except Exception:
                pass
        raise


if __name__ == "__main__":
    main()
