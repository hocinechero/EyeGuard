"""
EyeGuard — Native Win32 Dynamic Hotkey Engine
Supports customizable global keyboard shortcuts with live runtime re-registration,
conflict detection, and PostThreadMessageW lifecycle management.
"""

import ctypes
import queue
import threading
from ctypes import wintypes
from typing import Callable, Dict, Optional, Set, Tuple, List

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Win32 Modifier Constants
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

# Virtual Key Codes (Direct exports for tests & backwards compatibility)
VK_UP = 0x26
VK_DOWN = 0x28
VK_LEFT = 0x25
VK_RIGHT = 0x27
VK_F = 0x46
VK_D = 0x44

# Win32 Message Constants
WM_QUIT = 0x0012
WM_HOTKEY = 0x0312

# Explicit ctypes argtypes & restypes for 64-bit safety
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL

user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL

user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL

user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL

user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.restype = wintypes.BOOL

user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL

user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t

kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

kernel32.GetLastError.argtypes = []
kernel32.GetLastError.restype = wintypes.DWORD

# Virtual Key Mapping Table
VK_MAP = {
    "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
    "ARROWUP": 0x26, "ARROWDOWN": 0x28, "ARROWLEFT": 0x25, "ARROWRIGHT": 0x27,
    "SPACE": 0x20, "TAB": 0x09, "RETURN": 0x0D, "ENTER": 0x0D, "ESC": 0x1B, "ESCAPE": 0x1B,
    "BACKSPACE": 0x08, "DELETE": 0x2E, "INSERT": 0x2D, "HOME": 0x24, "END": 0x23,
    "PAGEUP": 0x21, "PAGEDOWN": 0x22,
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74, "F6": 0x75,
    "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "PLUS": 0xBB, "=": 0xBB, "MINUS": 0xBD, "-": 0xBD,
    "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}
for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    VK_MAP[c] = ord(c)
for d in "0123456789":
    VK_MAP[d] = ord(d)

VK_REVERSE = {
    0x26: "Up", 0x28: "Down", 0x25: "Left", 0x27: "Right",
    0x20: "Space", 0x09: "Tab", 0x0D: "Enter", 0x1B: "Esc",
    0x08: "Backspace", 0x2E: "Delete", 0x2D: "Insert", 0x24: "Home", 0x23: "End",
    0x21: "PageUp", 0x22: "PageDown",
    0xBB: "+", 0xBD: "-", 0xDB: "[", 0xDD: "]", 0xBA: ";", 0xDE: "'", 0xBC: ",", 0xBE: ".", 0xBF: "/"
}
for i in range(1, 13):
    VK_REVERSE[0x6F + i] = f"F{i}"
for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
    VK_REVERSE[ord(c)] = c


def parse_shortcut(shortcut_str: str) -> Optional[Tuple[int, int]]:
    """
    Parses a human-readable shortcut string (e.g. 'Ctrl + Alt + Up')
    into Win32 (modifiers, virtual_key_code).
    """
    if not shortcut_str or not isinstance(shortcut_str, str):
        return None
    raw_parts = [p.strip() for p in shortcut_str.split("+") if p.strip()]
    if not raw_parts:
        return None

    mods = MOD_NOREPEAT
    key_part = None

    for p in raw_parts:
        up = p.upper()
        if up in ("CTRL", "CONTROL"):
            mods |= MOD_CONTROL
        elif up == "ALT":
            mods |= MOD_ALT
        elif up == "SHIFT":
            mods |= MOD_SHIFT
        elif up in ("WIN", "WINDOWS", "SUPER", "META"):
            mods |= MOD_WIN
        else:
            key_part = up

    if not key_part or key_part not in VK_MAP:
        return None

    return mods, VK_MAP[key_part]


def format_shortcut(mods: int, vk: int) -> str:
    """Converts Win32 (modifiers, virtual_key_code) into a human-readable shortcut string."""
    parts = []
    if mods & MOD_CONTROL:
        parts.append("Ctrl")
    if mods & MOD_ALT:
        parts.append("Alt")
    if mods & MOD_SHIFT:
        parts.append("Shift")
    if mods & MOD_WIN:
        parts.append("Win")

    key_name = VK_REVERSE.get(vk, chr(vk) if 0x30 <= vk <= 0x5A else hex(vk))
    parts.append(key_name)
    return " + ".join(parts)


class HotkeyEngine:
    """
    High-performance Win32 global hotkey engine with dynamic runtime reloading,
    zero-hang thread teardown, and thread-affinity compliance.
    """
    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._lock = threading.Lock()
        self._ready_event = threading.Event()

        # Action definitions: action_name -> {'combo': str, 'callback': Callable, 'id': int}
        self._bindings: Dict[str, dict] = {}
        # Mapping: numeric_id -> action_name
        self._id_to_action: Dict[int, str] = {}
        # Currently registered IDs on Win32 thread
        self._registered_ids: Set[int] = set()
        self.failed_registrations: Dict[str, int] = {}

        # Dynamic command queue to listener thread
        self._cmd_queue: queue.Queue = queue.Queue()
        self._next_id = 1

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def bind_action(self, action_name: str, combo_str: str, callback: Callable[[], None]):
        """
        Bind or update an action with a shortcut string and callback.
        If engine is already running, sends live command to Win32 thread to register it.
        """
        with self._lock:
            if action_name in self._bindings:
                hid = self._bindings[action_name]["id"]
            else:
                hid = self._next_id
                self._next_id += 1

            self._bindings[action_name] = {
                "id": hid,
                "combo": combo_str.strip() if combo_str else "",
                "callback": callback
            }
            self._id_to_action[hid] = action_name

        if self.is_running:
            ack = threading.Event()
            self._cmd_queue.put(('RELOAD', ack))
            self._wake_thread()
            ack.wait(timeout=1.0)

    def unbind_action(self, action_name: str):
        """Remove a shortcut binding for an action."""
        with self._lock:
            binding = self._bindings.pop(action_name, None)
            if binding:
                self._id_to_action.pop(binding["id"], None)

        if self.is_running:
            ack = threading.Event()
            self._cmd_queue.put(('RELOAD', ack))
            self._wake_thread()
            ack.wait(timeout=1.0)

    def set_combo(self, action_name: str, combo_str: str):
        """Update only the combo string for an already bound action."""
        with self._lock:
            if action_name in self._bindings:
                self._bindings[action_name]["combo"] = combo_str.strip() if combo_str else ""

        if self.is_running:
            ack = threading.Event()
            self._cmd_queue.put(('RELOAD', ack))
            self._wake_thread()
            ack.wait(timeout=1.0)

    def get_bindings(self) -> Dict[str, str]:
        """Return a mapping of action_name -> combo_str."""
        with self._lock:
            return {k: v["combo"] for k, v in self._bindings.items()}

    def register(self, hotkey_id: int, callback: Callable[[], None]):
        """Backward-compatibility register method."""
        action_name = f"_legacy_{hotkey_id}"
        self.bind_action(action_name, "", callback)

    def unregister(self, hotkey_id: int):
        """Backward-compatibility unregister method."""
        action_name = f"_legacy_{hotkey_id}"
        self.unbind_action(action_name)

    def start(self):
        """Start the background hotkey message loop thread."""
        with self._lock:
            if self._running and self._thread and self._thread.is_alive():
                return
            self._running = True
            self._ready_event.clear()
            self._thread = threading.Thread(
                target=self._msg_loop,
                daemon=True,
                name="EyeGuard-HotkeyThread"
            )
            self._thread.start()

        self._ready_event.wait(timeout=2.0)

    def stop(self):
        """
        Cleanly terminate the hotkey background thread using PostThreadMessageW.
        Unregisters all system hotkeys and waits for the thread to exit.
        """
        with self._lock:
            if not self._running:
                return
            self._running = False
            tid = self._thread_id
            thread = self._thread

        if tid:
            user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)

        if thread and thread.is_alive():
            thread.join(timeout=1.0)

        with self._lock:
            self._thread = None
            self._thread_id = None
            self._registered_ids.clear()

    def _wake_thread(self):
        tid = self._thread_id
        if tid:
            # Post a harmless null thread message to wake GetMessage
            user32.PostThreadMessageW(tid, 0, 0, 0)

    def _apply_registrations(self):
        """Executes strictly on the listener thread to satisfy Win32 thread affinity."""
        # 1. Unregister all existing
        for hid in list(self._registered_ids):
            user32.UnregisterHotKey(None, hid)
        self._registered_ids.clear()
        self.failed_registrations.clear()

        # 2. Register all active valid combos
        with self._lock:
            items_to_reg = list(self._bindings.items())

        for name, data in items_to_reg:
            try:
                combo_str = data.get("combo")
                if not combo_str:
                    continue
                parsed = parse_shortcut(combo_str)
                if not parsed:
                    continue
                mods, vk = parsed
                hid = data["id"]
                ok = user32.RegisterHotKey(None, hid, mods, vk)
                if ok:
                    self._registered_ids.add(hid)
                else:
                    err = kernel32.GetLastError()
                    self.failed_registrations[name] = err
            except Exception:
                pass

    def _msg_loop(self):
        # 1. Force Win32 message queue creation
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)

        # 2. Record Win32 thread ID
        self._thread_id = kernel32.GetCurrentThreadId()

        # 3. Initial hotkey registration
        self._apply_registrations()
        self._ready_event.set()

        # 4. Standard Win32 Message Pump
        while self._running:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0 or msg.message == WM_QUIT:
                break

            # Process command queue
            while not self._cmd_queue.empty():
                try:
                    cmd_item = self._cmd_queue.get_nowait()
                    if cmd_item[0] == 'RELOAD':
                        self._apply_registrations()
                        if len(cmd_item) > 1 and cmd_item[1]:
                            cmd_item[1].set()
                except queue.Empty:
                    break

            if msg.message == WM_HOTKEY:
                hid = int(msg.wParam)
                callback = None
                with self._lock:
                    action_name = self._id_to_action.get(hid)
                    if action_name and action_name in self._bindings:
                        callback = self._bindings[action_name]["callback"]
                if callback:
                    try:
                        # Execute callback asynchronously on a worker thread to never block hotkey pump
                        threading.Thread(target=callback, daemon=True).start()
                    except Exception as e:
                        pass

            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        # 5. Guaranteed cleanup on shutdown
        for hid in list(self._registered_ids):
            user32.UnregisterHotKey(None, hid)
        self._registered_ids.clear()
