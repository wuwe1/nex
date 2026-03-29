#!/usr/bin/env python3
"""
Nex - Cross-platform keyboard/mouse sharing.

Windows (server) shares its keyboard and mouse with a Mac (client).
Mouse hitting the LEFT edge on Windows switches control to Mac.
Mouse hitting the RIGHT edge on Mac switches control back to Windows.

Uses Raw Input API on Windows and Quartz CGEvent API on Mac for
low-latency, high-fidelity input capture and injection.

Usage:
  Windows:  python nex.py
  Mac:      python nex.py --host <windows-ip>
"""

import argparse
import atexit
import platform
import signal
import socket
import struct
import sys
import threading
import logging
import time

try:
    from rich.console import Console
    from rich.logging import RichHandler
    from rich.theme import Theme
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------
IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

if IS_WINDOWS:
    import ctypes
    import ctypes.wintypes

if IS_MAC:
    import Quartz  # type: ignore
    from Quartz import (  # type: ignore
        CGDisplayPixelsHigh,
        CGDisplayPixelsWide,
        CGEventCreate,
        CGEventCreateKeyboardEvent,
        CGEventCreateMouseEvent,
        CGEventCreateScrollWheelEvent,
        CGEventGetLocation,
        CGEventPost,
        CGEventSetIntegerValueField,
        CGMainDisplayID,
        kCGEventLeftMouseDown,
        kCGEventLeftMouseDragged,
        kCGEventLeftMouseUp,
        kCGEventMouseMoved,
        kCGEventOtherMouseDown,
        kCGEventOtherMouseUp,
        kCGEventRightMouseDown,
        kCGEventRightMouseDragged,
        kCGEventRightMouseUp,
        kCGEventScrollWheel,
        kCGHIDEventTap,
        kCGMouseButtonCenter,
        kCGMouseButtonLeft,
        kCGMouseButtonRight,
        kCGMouseEventDeltaX,
        kCGMouseEventDeltaY,
        kCGScrollEventUnitLine,
    )

LOG = logging.getLogger("nex")

# ---------------------------------------------------------------------------
# Protocol constants (binary, TCP)
# ---------------------------------------------------------------------------
MSG_MOUSE_MOVE = 1      # 1B type + 2B dx(int16) + 2B dy(int16) = 5 bytes
MSG_MOUSE_BUTTON = 2    # 1B type + 1B button_id + 1B is_pressed = 3 bytes
MSG_KEY_EVENT = 3        # 1B type + 2B vkey(uint16) + 1B is_down + 2B scancode = 6 bytes
MSG_SCROLL = 4           # 1B type + 2B delta(int16) = 3 bytes
MSG_SWITCH = 5           # 1B type + 1B direction = 2 bytes

SWITCH_TO_CLIENT = 0
SWITCH_TO_SERVER = 1

# Button IDs
BTN_LEFT = 0
BTN_RIGHT = 1
BTN_MIDDLE = 2

# Struct formats
FMT_MOUSE_MOVE = "!Bhh"       # type, dx, dy
FMT_MOUSE_BUTTON = "!BBB"     # type, button_id, is_pressed
FMT_KEY_EVENT = "!BHBH"       # type, vkey, is_down, scancode
FMT_SCROLL = "!Bh"            # type, delta
FMT_SWITCH = "!BB"            # type, direction

DEFAULT_PORT = 24800

# ---------------------------------------------------------------------------
# Windows VK to Mac keycode mapping
# ---------------------------------------------------------------------------
VK_TO_MAC = {
    # Letters A-Z (VK 0x41-0x5A)
    0x41: 0x00,  # A
    0x42: 0x0B,  # B
    0x43: 0x08,  # C
    0x44: 0x02,  # D
    0x45: 0x0E,  # E
    0x46: 0x03,  # F
    0x47: 0x05,  # G
    0x48: 0x04,  # H
    0x49: 0x22,  # I
    0x4A: 0x26,  # J
    0x4B: 0x28,  # K
    0x4C: 0x25,  # L
    0x4D: 0x2E,  # M
    0x4E: 0x2D,  # N
    0x4F: 0x1F,  # O
    0x50: 0x23,  # P
    0x51: 0x0C,  # Q
    0x52: 0x0F,  # R
    0x53: 0x01,  # S
    0x54: 0x11,  # T
    0x55: 0x20,  # U
    0x56: 0x09,  # V
    0x57: 0x0D,  # W
    0x58: 0x07,  # X
    0x59: 0x10,  # Y
    0x5A: 0x06,  # Z
    # Numbers 0-9 (VK 0x30-0x39)
    0x30: 0x1D,  # 0
    0x31: 0x12,  # 1
    0x32: 0x13,  # 2
    0x33: 0x14,  # 3
    0x34: 0x15,  # 4
    0x35: 0x17,  # 5
    0x36: 0x16,  # 6
    0x37: 0x1A,  # 7
    0x38: 0x1C,  # 8
    0x39: 0x19,  # 9
    # Common keys
    0x0D: 0x24,  # Return
    0x1B: 0x35,  # Escape
    0x08: 0x33,  # Backspace
    0x09: 0x30,  # Tab
    0x20: 0x31,  # Space
    # Modifiers
    0x10: 0x38,  # Shift
    0xA0: 0x38,  # Left Shift
    0xA1: 0x3C,  # Right Shift
    0x11: 0x3B,  # Control
    0xA2: 0x3B,  # Left Control
    0xA3: 0x3E,  # Right Control
    0x12: 0x37,  # Alt -> Cmd (user preference: Alt=Cmd on Mac)
    0xA4: 0x37,  # Left Alt -> Cmd
    0xA5: 0x37,  # Right Alt -> Cmd
    0x5B: 0x3A,  # Left Win -> Option
    0x5C: 0x36,  # Right Win -> Right Cmd
    # Arrow keys
    0x25: 0x7B,  # Left
    0x26: 0x7E,  # Up
    0x27: 0x7C,  # Right
    0x28: 0x7D,  # Down
    # F1-F12
    0x70: 0x7A,  # F1
    0x71: 0x78,  # F2
    0x72: 0x63,  # F3
    0x73: 0x76,  # F4
    0x74: 0x60,  # F5
    0x75: 0x61,  # F6
    0x76: 0x62,  # F7
    0x77: 0x64,  # F8
    0x78: 0x65,  # F9
    0x79: 0x6D,  # F10
    0x7A: 0x67,  # F11
    0x7B: 0x6F,  # F12
    # Punctuation
    0xBA: 0x29,  # ; (semicolon)
    0xBB: 0x18,  # = (equals)
    0xBC: 0x2B,  # , (comma)
    0xBD: 0x1B,  # - (minus)
    0xBE: 0x2F,  # . (period)
    0xBF: 0x2C,  # / (slash)
    0xC0: 0x32,  # ` (grave accent)
    0xDB: 0x21,  # [ (left bracket)
    0xDC: 0x2A,  # \ (backslash)
    0xDD: 0x1E,  # ] (right bracket)
    0xDE: 0x27,  # ' (quote)
    # Other common keys
    0x2D: 0x72,  # Insert -> Help
    0x2E: 0x75,  # Delete -> Forward Delete
    0x24: 0x73,  # Home
    0x23: 0x77,  # End
    0x21: 0x74,  # Page Up
    0x22: 0x79,  # Page Down
    0x14: 0x39,  # Caps Lock
}

# ---------------------------------------------------------------------------
# Binary protocol helpers
# ---------------------------------------------------------------------------

def send_raw(sock: socket.socket, data: bytes):
    """Send raw bytes over socket, silently ignore errors."""
    try:
        sock.sendall(data)
    except OSError:
        pass


def send_mouse_move(sock: socket.socket, dx: int, dy: int):
    dx = max(-32768, min(32767, dx))
    dy = max(-32768, min(32767, dy))
    send_raw(sock, struct.pack(FMT_MOUSE_MOVE, MSG_MOUSE_MOVE, dx, dy))


def send_mouse_button(sock: socket.socket, button_id: int, is_pressed: bool):
    send_raw(sock, struct.pack(FMT_MOUSE_BUTTON, MSG_MOUSE_BUTTON, button_id, int(is_pressed)))


def send_key_event(sock: socket.socket, vkey: int, is_down: bool, scancode: int):
    send_raw(sock, struct.pack(FMT_KEY_EVENT, MSG_KEY_EVENT, vkey, int(is_down), scancode))


def send_scroll(sock: socket.socket, delta: int):
    delta = max(-32768, min(32767, delta))
    send_raw(sock, struct.pack(FMT_SCROLL, MSG_SCROLL, delta))


def send_switch(sock: socket.socket, direction: int):
    send_raw(sock, struct.pack(FMT_SWITCH, MSG_SWITCH, direction))


class ProtocolReader:
    """Reads binary protocol messages from a TCP socket."""

    # Map message type byte to (struct format, total size)
    MSG_FORMATS = {
        MSG_MOUSE_MOVE:   (FMT_MOUSE_MOVE, 5),
        MSG_MOUSE_BUTTON: (FMT_MOUSE_BUTTON, 3),
        MSG_KEY_EVENT:    (FMT_KEY_EVENT, 6),
        MSG_SCROLL:       (FMT_SCROLL, 3),
        MSG_SWITCH:       (FMT_SWITCH, 2),
    }

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.buf = b""

    def _recv_exact(self, n: int) -> bytes:
        """Read exactly n bytes from socket+buffer."""
        while len(self.buf) < n:
            try:
                chunk = self.sock.recv(4096)
            except OSError:
                return b""
            if not chunk:
                return b""
            self.buf += chunk
        data = self.buf[:n]
        self.buf = self.buf[n:]
        return data

    def __iter__(self):
        return self

    def __next__(self) -> tuple:
        # Read message type byte
        type_byte = self._recv_exact(1)
        if not type_byte:
            raise StopIteration
        msg_type = type_byte[0]
        fmt_info = self.MSG_FORMATS.get(msg_type)
        if fmt_info is None:
            LOG.warning("Unknown message type: %d", msg_type)
            raise StopIteration
        fmt, size = fmt_info
        # We already have the type byte, read the rest
        remaining = self._recv_exact(size - 1)
        if len(remaining) < size - 1:
            raise StopIteration
        full = type_byte + remaining
        return struct.unpack(fmt, full)


# ---------------------------------------------------------------------------
# Windows Raw Input structures and constants (ctypes)
# ---------------------------------------------------------------------------

if IS_WINDOWS:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # Window message constants
    WM_INPUT = 0x00FF
    WM_DESTROY = 0x0002
    WM_QUIT = 0x0012
    WM_CLOSE = 0x0010

    # Raw Input constants
    RID_INPUT = 0x10000003
    RIM_TYPEMOUSE = 0
    RIM_TYPEKEYBOARD = 1
    RIDEV_INPUTSINK = 0x00000100
    RIDEV_NOLEGACY = 0x00000030
    RIDEV_REMOVE = 0x00000001

    # Mouse button flags
    RI_MOUSE_LEFT_BUTTON_DOWN = 0x0001
    RI_MOUSE_LEFT_BUTTON_UP = 0x0002
    RI_MOUSE_RIGHT_BUTTON_DOWN = 0x0004
    RI_MOUSE_RIGHT_BUTTON_UP = 0x0008
    RI_MOUSE_MIDDLE_BUTTON_DOWN = 0x0010
    RI_MOUSE_MIDDLE_BUTTON_UP = 0x0020
    RI_MOUSE_WHEEL = 0x0400

    MOUSE_MOVE_RELATIVE = 0x00

    # Keyboard flags
    RI_KEY_MAKE = 0x0000
    RI_KEY_BREAK = 0x0001
    RI_KEY_E0 = 0x0002

    # Window class style
    CS_HREDRAW = 0x0002
    CS_VREDRAW = 0x0001
    CW_USEDEFAULT = 0x80000000

    # HID usage page / usage
    HID_USAGE_PAGE_GENERIC = 0x01
    HID_USAGE_GENERIC_MOUSE = 0x02
    HID_USAGE_GENERIC_KEYBOARD = 0x06

    class RAWINPUTDEVICE(ctypes.Structure):
        _fields_ = [
            ("usUsagePage", ctypes.c_ushort),
            ("usUsage", ctypes.c_ushort),
            ("dwFlags", ctypes.c_ulong),
            ("hwndTarget", ctypes.c_void_p),
        ]

    class RAWMOUSE(ctypes.Structure):
        _fields_ = [
            ("usFlags", ctypes.c_ushort),
            ("_padding", ctypes.c_ushort),
            ("usButtonFlags", ctypes.c_ushort),
            ("usButtonData", ctypes.c_short),
            ("ulRawButtons", ctypes.c_ulong),
            ("lLastX", ctypes.c_long),
            ("lLastY", ctypes.c_long),
            ("ulExtraInformation", ctypes.c_ulong),
        ]

    class RAWKEYBOARD(ctypes.Structure):
        _fields_ = [
            ("MakeCode", ctypes.c_ushort),
            ("Flags", ctypes.c_ushort),
            ("Reserved", ctypes.c_ushort),
            ("VKey", ctypes.c_ushort),
            ("Message", ctypes.c_uint),
            ("ExtraInformation", ctypes.c_ulong),
        ]

    class RAWINPUTHEADER(ctypes.Structure):
        _fields_ = [
            ("dwType", ctypes.c_uint),
            ("dwSize", ctypes.c_uint),
            ("hDevice", ctypes.c_void_p),
            ("wParam", ctypes.c_void_p),
        ]

    class RAWINPUT_MOUSE(ctypes.Structure):
        _fields_ = [
            ("header", RAWINPUTHEADER),
            ("mouse", RAWMOUSE),
        ]

    class RAWINPUT_KEYBOARD(ctypes.Structure):
        _fields_ = [
            ("header", RAWINPUTHEADER),
            ("keyboard", RAWKEYBOARD),
        ]

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_uint),
            ("style", ctypes.c_uint),
            ("lpfnWndProc", ctypes.c_void_p),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", ctypes.c_void_p),
            ("hIcon", ctypes.c_void_p),
            ("hCursor", ctypes.c_void_p),
            ("hbrBackground", ctypes.c_void_p),
            ("lpszMenuName", ctypes.c_wchar_p),
            ("lpszClassName", ctypes.c_wchar_p),
            ("hIconSm", ctypes.c_void_p),
        ]

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("message", ctypes.c_uint),
            ("wParam", ctypes.c_void_p),
            ("lParam", ctypes.c_void_p),
            ("time", ctypes.c_uint),
            ("pt", POINT),
        ]

    WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_long,       # return LRESULT
        ctypes.c_void_p,     # HWND
        ctypes.c_uint,       # UINT msg
        ctypes.c_void_p,     # WPARAM
        ctypes.c_void_p,     # LPARAM
    )

    # Low-level keyboard hook callback type
    HOOKPROC = ctypes.WINFUNCTYPE(
        ctypes.c_long,       # LRESULT
        ctypes.c_int,        # nCode
        ctypes.c_void_p,     # wParam
        ctypes.c_void_p,     # lParam
    )

    WH_KEYBOARD_LL = 13
    HC_ACTION = 0

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", ctypes.c_ulong),
            ("scanCode", ctypes.c_ulong),
            ("flags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    # Function prototypes
    user32.RegisterRawInputDevices.argtypes = [
        ctypes.POINTER(RAWINPUTDEVICE), ctypes.c_uint, ctypes.c_uint
    ]
    user32.RegisterRawInputDevices.restype = ctypes.c_bool

    user32.GetRawInputData.argtypes = [
        ctypes.c_void_p, ctypes.c_uint,
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint), ctypes.c_uint
    ]
    user32.GetRawInputData.restype = ctypes.c_uint

    user32.DefWindowProcW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p
    ]
    user32.DefWindowProcW.restype = ctypes.c_long

    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int

    SM_CXSCREEN = 0
    SM_CYSCREEN = 1


# ---------------------------------------------------------------------------
# Server (Windows)
# ---------------------------------------------------------------------------

if IS_WINDOWS:

    class Server:
        def __init__(self, host: str, port: int, sensitivity: float, verbose: bool):
            self.host = host
            self.port = port
            self.sensitivity = sensitivity
            self.verbose = verbose
            self.client_sock: socket.socket | None = None
            self.active_on_client = False
            self.lock = threading.Lock()
            self.running = True

            # Screen dimensions
            self.screen_w = user32.GetSystemMetrics(SM_CXSCREEN)
            self.screen_h = user32.GetSystemMetrics(SM_CYSCREEN)
            LOG.info("Screen size: %dx%d", self.screen_w, self.screen_h)

            # Virtual cursor position (tracked with raw deltas)
            self.virtual_x = self.screen_w // 2
            self.virtual_y = self.screen_h // 2

            # Window handle for raw input
            self.hwnd = None
            self._wndproc_ref = None  # prevent GC
            self._hookproc_ref = None  # prevent GC
            self._kb_hook = None

            # Cursor hiding
            self._cursor_hidden = False
            self._blank_cursor = None
            self._saved_cursor = None

            # Register cleanup
            atexit.register(self._cleanup)

        def _cleanup(self):
            """Ensure cursor is unlocked and visible on exit."""
            try:
                self._uninstall_kb_hook()
                user32.ClipCursor(None)
                if self._cursor_hidden:
                    SPI_SETCURSORS = 0x0057
                    user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)
                    self._cursor_hidden = False
                self._unregister_raw_input()
            except Exception:
                pass

        def _get_blank_cursor(self):
            """Create a transparent cursor (1x1 pixel, fully transparent)."""
            if self._blank_cursor:
                return self._blank_cursor
            # Create a 1x1 blank cursor via CreateCursor
            # AND mask = 0xFF (transparent), XOR mask = 0x00
            and_mask = (ctypes.c_ubyte * 1)(0xFF)
            xor_mask = (ctypes.c_ubyte * 1)(0x00)
            user32.CreateCursor.restype = ctypes.c_void_p
            self._blank_cursor = user32.CreateCursor(
                None, 0, 0, 1, 1, and_mask, xor_mask
            )
            return self._blank_cursor

        def _lock_cursor(self):
            """Hide cursor and clip to a tiny box at left edge."""
            # Lock to left edge where the switch happens, not center
            cx = 0
            cy = self.screen_h // 2
            r = RECT(cx, cy - 1, cx + 2, cy + 1)
            user32.ClipCursor(ctypes.byref(r))
            user32.SetCursorPos(cx, cy)
            if not self._cursor_hidden:
                # Set a blank (transparent) cursor instead of using ShowCursor counter
                blank = self._get_blank_cursor()
                if blank:
                    user32.SetSystemCursor.argtypes = [ctypes.c_void_p, ctypes.wintypes.DWORD]
                    user32.SetSystemCursor.restype = ctypes.wintypes.BOOL
                    # Copy the blank cursor (SetSystemCursor destroys the handle)
                    user32.CopyCursor = user32.CopyIcon
                    user32.CopyCursor.restype = ctypes.c_void_p
                    copy = user32.CopyCursor(blank)
                    OCR_NORMAL = 32512
                    user32.SetSystemCursor(copy, OCR_NORMAL)
                self._cursor_hidden = True

        def _unlock_cursor(self):
            """Restore cursor and remove clip."""
            user32.ClipCursor(None)
            if self._cursor_hidden:
                # Restore default system cursors
                SPI_SETCURSORS = 0x0057
                user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)
                self._cursor_hidden = False

        def _register_raw_input(self, suppress: bool):
            """Register for raw mouse and keyboard input."""
            flags = RIDEV_INPUTSINK
            if suppress:
                flags |= RIDEV_NOLEGACY

            devices = (RAWINPUTDEVICE * 2)()
            # Mouse
            devices[0].usUsagePage = HID_USAGE_PAGE_GENERIC
            devices[0].usUsage = HID_USAGE_GENERIC_MOUSE
            devices[0].dwFlags = flags
            devices[0].hwndTarget = self.hwnd
            # Keyboard
            devices[1].usUsagePage = HID_USAGE_PAGE_GENERIC
            devices[1].usUsage = HID_USAGE_GENERIC_KEYBOARD
            devices[1].dwFlags = RIDEV_INPUTSINK
            devices[1].hwndTarget = self.hwnd

            if not user32.RegisterRawInputDevices(devices, 2, ctypes.sizeof(RAWINPUTDEVICE)):
                LOG.error("Failed to register raw input devices")

        def _unregister_raw_input(self):
            """Unregister raw input devices."""
            devices = (RAWINPUTDEVICE * 2)()
            devices[0].usUsagePage = HID_USAGE_PAGE_GENERIC
            devices[0].usUsage = HID_USAGE_GENERIC_MOUSE
            devices[0].dwFlags = RIDEV_REMOVE
            devices[0].hwndTarget = None
            devices[1].usUsagePage = HID_USAGE_PAGE_GENERIC
            devices[1].usUsage = HID_USAGE_GENERIC_KEYBOARD
            devices[1].dwFlags = RIDEV_REMOVE
            devices[1].hwndTarget = None
            user32.RegisterRawInputDevices(devices, 2, ctypes.sizeof(RAWINPUTDEVICE))

        def _install_kb_hook(self):
            """Install low-level keyboard hook to suppress system hotkeys and forward keys."""
            WM_KEYDOWN = 0x0100
            WM_KEYUP = 0x0101
            WM_SYSKEYDOWN = 0x0104
            WM_SYSKEYUP = 0x0105

            def hook_proc(nCode, wParam, lParam):
                if nCode == HC_ACTION:
                    with self.lock:
                        is_active = self.active_on_client
                    if is_active:
                        # Extract key info from KBDLLHOOKSTRUCT
                        kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                        vkey = kb.vkCode
                        scancode = kb.scanCode
                        is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)

                        # ESC = emergency switch back
                        if vkey == 0x1B and is_down:
                            LOG.info("ESC pressed - emergency switch back to Windows")
                            self._deactivate_client()
                            return user32.CallNextHookEx(None, nCode, wParam, lParam)

                        # Forward to Mac
                        sock = self.client_sock
                        if sock:
                            send_key_event(sock, vkey, is_down, scancode)
                            if self.verbose:
                                LOG.debug("Key: vk=0x%02X scan=0x%04X %s",
                                          vkey, scancode, "down" if is_down else "up")

                        # Block the key on Windows
                        return 1
                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            self._hookproc_ref = HOOKPROC(hook_proc)
            self._kb_hook = user32.SetWindowsHookExW(
                WH_KEYBOARD_LL, self._hookproc_ref, None, 0
            )
            if not self._kb_hook:
                LOG.warning("Failed to install keyboard hook")

        def _uninstall_kb_hook(self):
            """Remove low-level keyboard hook."""
            if self._kb_hook:
                user32.UnhookWindowsHookEx(self._kb_hook)
                self._kb_hook = None

        def _disable_ime(self):
            """Disable Windows IME when control is on Mac."""
            try:
                # Save current IME state and disable
                hwnd = user32.GetForegroundWindow()
                self._saved_ime = ctypes.windll.imm32.ImmAssociateContext(hwnd, None)
            except Exception:
                self._saved_ime = None

        def _enable_ime(self):
            """Re-enable Windows IME when control returns."""
            try:
                if hasattr(self, '_saved_ime') and self._saved_ime:
                    hwnd = user32.GetForegroundWindow()
                    ctypes.windll.imm32.ImmAssociateContext(hwnd, self._saved_ime)
                    self._saved_ime = None
            except Exception:
                pass

        def _activate_client(self):
            """Switch control to Mac client."""
            with self.lock:
                if self.active_on_client:
                    return
                self.active_on_client = True
            LOG.info("Switching control to Mac")
            self._lock_cursor()
            self._disable_ime()
            self._register_raw_input(suppress=True)
            self._install_kb_hook()
            if self.client_sock:
                send_switch(self.client_sock, SWITCH_TO_CLIENT)

        def _deactivate_client(self):
            """Switch control back to Windows."""
            with self.lock:
                if not self.active_on_client:
                    return
                self.active_on_client = False
            LOG.info("Switching control back to Windows")
            self._uninstall_kb_hook()
            self._enable_ime()
            self._unlock_cursor()
            self._register_raw_input(suppress=False)
            # Place cursor at left side so it doesn't immediately re-trigger
            user32.SetCursorPos(50, self.screen_h // 2)
            # Reset virtual position
            self.virtual_x = 50
            self.virtual_y = self.screen_h // 2

        def _handle_raw_mouse(self, raw):
            """Process raw mouse input data."""
            mouse = raw.mouse
            sock = self.client_sock

            with self.lock:
                is_active = self.active_on_client

            # Process button events (always, so we can forward them when active)
            btn_flags = mouse.usButtonFlags
            if btn_flags:
                button_events = []
                if btn_flags & RI_MOUSE_LEFT_BUTTON_DOWN:
                    button_events.append((BTN_LEFT, True))
                if btn_flags & RI_MOUSE_LEFT_BUTTON_UP:
                    button_events.append((BTN_LEFT, False))
                if btn_flags & RI_MOUSE_RIGHT_BUTTON_DOWN:
                    button_events.append((BTN_RIGHT, True))
                if btn_flags & RI_MOUSE_RIGHT_BUTTON_UP:
                    button_events.append((BTN_RIGHT, False))
                if btn_flags & RI_MOUSE_MIDDLE_BUTTON_DOWN:
                    button_events.append((BTN_MIDDLE, True))
                if btn_flags & RI_MOUSE_MIDDLE_BUTTON_UP:
                    button_events.append((BTN_MIDDLE, False))

                if is_active and sock:
                    for btn_id, pressed in button_events:
                        send_mouse_button(sock, btn_id, pressed)

                # Scroll
                if btn_flags & RI_MOUSE_WHEEL:
                    if is_active and sock:
                        send_scroll(sock, mouse.usButtonData)

            # Process movement
            if mouse.usFlags == MOUSE_MOVE_RELATIVE:
                dx = mouse.lLastX
                dy = mouse.lLastY
                if dx == 0 and dy == 0:
                    return

                if is_active:
                    # Apply sensitivity and forward to client
                    sdx = int(dx * self.sensitivity)
                    sdy = int(dy * self.sensitivity)
                    if sock:
                        send_mouse_move(sock, sdx, sdy)
                    # Track virtual position for edge detection is not needed
                    # while active since we wait for switch_back from client.
                    # Reset physical cursor to center to keep getting deltas.
                    user32.SetCursorPos(self.screen_w // 2, self.screen_h // 2)
                else:
                    # Track virtual position for edge detection
                    self.virtual_x += dx
                    self.virtual_y += dy
                    # Clamp Y
                    self.virtual_y = max(0, min(self.screen_h - 1, self.virtual_y))

                    if self.virtual_x <= 0 and sock:
                        self.virtual_x = 0
                        self._activate_client()

        def _handle_raw_keyboard(self, raw):
            """Process raw keyboard input data."""
            kb = raw.keyboard
            vkey = kb.VKey
            scancode = kb.MakeCode
            is_down = not bool(kb.Flags & RI_KEY_BREAK)

            # E0 prefix for extended keys
            if kb.Flags & RI_KEY_E0:
                scancode |= 0xE000

            with self.lock:
                is_active = self.active_on_client

            # ESC = emergency switch back to Windows
            if vkey == 0x1B and is_down and is_active:
                LOG.info("ESC pressed - emergency switch back to Windows")
                self._deactivate_client()
                return

            if is_active and self.client_sock:
                send_key_event(self.client_sock, vkey, is_down, scancode)
                if self.verbose:
                    LOG.debug("Key: vk=0x%02X scan=0x%04X %s",
                              vkey, scancode, "down" if is_down else "up")

        def _wndproc(self, hwnd, msg, wparam, lparam):
            """Window procedure for raw input messages."""
            if msg == WM_INPUT:
                # Get required buffer size
                size = ctypes.c_uint()
                user32.GetRawInputData(
                    lparam, RID_INPUT, None, ctypes.byref(size),
                    ctypes.sizeof(RAWINPUTHEADER)
                )
                # Read into appropriately sized struct based on size
                # Try mouse first (larger struct)
                raw_mouse = RAWINPUT_MOUSE()
                raw_size = ctypes.c_uint(ctypes.sizeof(raw_mouse))
                ret = user32.GetRawInputData(
                    lparam, RID_INPUT,
                    ctypes.byref(raw_mouse), ctypes.byref(raw_size),
                    ctypes.sizeof(RAWINPUTHEADER)
                )
                if ret != ctypes.c_uint(-1).value and ret > 0:
                    if raw_mouse.header.dwType == RIM_TYPEMOUSE:
                        self._handle_raw_mouse(raw_mouse)
                    elif raw_mouse.header.dwType == RIM_TYPEKEYBOARD:
                        # Re-read as keyboard struct
                        raw_kb = RAWINPUT_KEYBOARD()
                        # Data already consumed, cast from mouse buffer
                        ctypes.memmove(ctypes.byref(raw_kb), ctypes.byref(raw_mouse),
                                       min(ctypes.sizeof(raw_kb), ctypes.sizeof(raw_mouse)))
                        self._handle_raw_keyboard(raw_kb)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        def _create_message_window(self):
            """Create a hidden window to receive WM_INPUT messages."""
            hinstance = kernel32.GetModuleHandleW(None)
            class_name = "NexRawInputWindow"

            self._wndproc_ref = WNDPROC(self._wndproc)

            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.style = CS_HREDRAW | CS_VREDRAW
            wc.lpfnWndProc = ctypes.cast(self._wndproc_ref, ctypes.c_void_p).value
            wc.hInstance = hinstance
            wc.lpszClassName = class_name

            atom = user32.RegisterClassExW(ctypes.byref(wc))
            if not atom:
                err = ctypes.GetLastError()
                LOG.error("Failed to register window class, error=%d", err)
                return

            HWND_MESSAGE = ctypes.c_void_p(-3)
            self.hwnd = user32.CreateWindowExW(
                0, class_name, "Nex",
                0,  # style (not visible)
                0, 0, 0, 0,
                HWND_MESSAGE,  # message-only window
                None, hinstance, None
            )
            if not self.hwnd:
                LOG.error("Failed to create message window")

        def _network_listener(self):
            """Accept connections and read messages from client (runs in thread)."""
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((self.host, self.port))
            srv.listen(1)
            LOG.info("Server listening on %s:%d", self.host, self.port)

            while self.running:
                srv.settimeout(1.0)
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break

                LOG.info("Client connected from %s", addr)
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.client_sock = conn

                reader = ProtocolReader(conn)
                try:
                    for msg in reader:
                        self._handle_client_msg(msg)
                except Exception as e:
                    LOG.error("Session error: %s", e)
                finally:
                    LOG.info("Client disconnected")
                    # If we were active on client, switch back
                    self._deactivate_client()
                    try:
                        conn.close()
                    except OSError:
                        pass
                    self.client_sock = None

        def _handle_client_msg(self, msg: tuple):
            """Handle a message from the Mac client."""
            msg_type = msg[0]
            if msg_type == MSG_SWITCH:
                direction = msg[1]
                if direction == SWITCH_TO_SERVER:
                    self._deactivate_client()

        def start(self):
            """Start the server: message pump in main thread, network in background."""
            # Start network thread
            net_thread = threading.Thread(target=self._network_listener, daemon=True)
            net_thread.start()

            # Create message window and register raw input (main thread)
            self._create_message_window()
            self._register_raw_input(suppress=False)

            LOG.info("Listening on %s:%d — move mouse to [bold]left edge[/bold] to switch to Mac",
                     self.host, self.port, extra={"markup": True})

            # Run Windows message pump (using PeekMessage so Ctrl+C works)
            PM_REMOVE = 0x0001
            msg = MSG()
            while self.running:
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    if msg.message == 0x0012:  # WM_QUIT
                        self.running = False
                        break
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                time.sleep(0.001)  # 1ms yield to allow Ctrl+C

            self._cleanup()


# ---------------------------------------------------------------------------
# Client (Mac)
# ---------------------------------------------------------------------------

if IS_MAC:

    class Client:
        def __init__(self, host: str, port: int, sensitivity: float, verbose: bool):
            self.host = host
            self.port = port
            self.sensitivity = sensitivity
            self.verbose = verbose
            self.active = False
            self.lock = threading.Lock()
            self.running = True
            self.sock: socket.socket | None = None

            # Screen dimensions
            display_id = CGMainDisplayID()
            self.screen_w = CGDisplayPixelsWide(display_id)
            self.screen_h = CGDisplayPixelsHigh(display_id)
            LOG.info("Screen size: %dx%d", self.screen_w, self.screen_h)

            # Virtual absolute position (for button events that need coords)
            self.abs_x = 0.0
            self.abs_y = float(self.screen_h) / 2.0

            # Track which mouse buttons are currently held
            self.left_down = False
            self.right_down = False
            self.middle_down = False

        def start(self):
            """Connect to server with auto-reconnect."""
            while self.running:
                LOG.info("Connecting to %s:%d ...", self.host, self.port)
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(5.0)
                    s.connect((self.host, self.port))
                    s.settimeout(None)
                    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError as e:
                    LOG.warning("Connection failed: %s - retrying in 3s", e)
                    time.sleep(3)
                    continue

                LOG.info("Connected to server")
                self.sock = s
                self._run_session(s)
                self.sock = None
                LOG.info("Disconnected - retrying in 3s")
                time.sleep(3)

        def _run_session(self, s: socket.socket):
            reader = ProtocolReader(s)
            try:
                for msg in reader:
                    self._handle_msg(s, msg)
            except Exception as e:
                LOG.error("Session error: %s", e)
            finally:
                with self.lock:
                    self.active = False
                try:
                    s.close()
                except OSError:
                    pass

        def _handle_msg(self, s: socket.socket, msg: tuple):
            msg_type = msg[0]

            if msg_type == MSG_SWITCH:
                direction = msg[1]
                if direction == SWITCH_TO_CLIENT:
                    LOG.info("Control switched to Mac")
                    with self.lock:
                        self.active = True
                    # Keep mouse at its current position
                    cur_pos = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
                    self.abs_x = cur_pos.x
                    self.abs_y = cur_pos.y
                    LOG.debug("Mac cursor starts at (%.0f, %.0f)", self.abs_x, self.abs_y)

            elif msg_type == MSG_MOUSE_MOVE:
                _, dx, dy = msg
                with self.lock:
                    if not self.active:
                        return

                # Apply sensitivity
                sdx = int(dx * self.sensitivity)
                sdy = int(dy * self.sensitivity)

                # Update virtual absolute position
                self.abs_x += sdx
                self.abs_y += sdy

                # Check right edge before clamping
                if self.abs_x >= self.screen_w:
                    LOG.info("Mouse hit right edge - switching back to Windows")
                    with self.lock:
                        self.active = False
                    send_switch(s, SWITCH_TO_SERVER)
                    return

                # Clamp
                self.abs_x = max(0.0, min(float(self.screen_w - 1), self.abs_x))
                self.abs_y = max(0.0, min(float(self.screen_h - 1), self.abs_y))

                self._move_mouse(sdx, sdy)

            elif msg_type == MSG_MOUSE_BUTTON:
                _, button_id, is_pressed = msg
                with self.lock:
                    if not self.active:
                        return
                self._mouse_button(button_id, bool(is_pressed))

            elif msg_type == MSG_KEY_EVENT:
                _, vkey, is_down, scancode = msg
                with self.lock:
                    if not self.active:
                        return
                self._key_event(vkey, bool(is_down))

            elif msg_type == MSG_SCROLL:
                _, delta = msg
                with self.lock:
                    if not self.active:
                        return
                self._scroll(delta)

        def _move_mouse(self, dx: int, dy: int):
            """Inject a mouse move event using Quartz CGEvent with raw deltas."""
            point = Quartz.CGPointMake(self.abs_x, self.abs_y)

            # Choose event type based on button state
            if self.left_down:
                event_type = kCGEventLeftMouseDragged
            elif self.right_down:
                event_type = kCGEventRightMouseDragged
            else:
                event_type = kCGEventMouseMoved

            event = CGEventCreateMouseEvent(None, event_type, point, kCGMouseButtonLeft)
            if event:
                # Set delta fields so macOS can apply its acceleration curve
                CGEventSetIntegerValueField(event, kCGMouseEventDeltaX, dx)
                CGEventSetIntegerValueField(event, kCGMouseEventDeltaY, dy)
                CGEventPost(kCGHIDEventTap, event)

        def _mouse_button(self, button_id: int, pressed: bool):
            """Inject a mouse button event."""
            point = Quartz.CGPointMake(self.abs_x, self.abs_y)

            if button_id == BTN_LEFT:
                event_type = kCGEventLeftMouseDown if pressed else kCGEventLeftMouseUp
                cg_button = kCGMouseButtonLeft
                self.left_down = pressed
            elif button_id == BTN_RIGHT:
                event_type = kCGEventRightMouseDown if pressed else kCGEventRightMouseUp
                cg_button = kCGMouseButtonRight
                self.right_down = pressed
            elif button_id == BTN_MIDDLE:
                event_type = kCGEventOtherMouseDown if pressed else kCGEventOtherMouseUp
                cg_button = kCGMouseButtonCenter
                self.middle_down = pressed
            else:
                return

            event = CGEventCreateMouseEvent(None, event_type, point, cg_button)
            if event:
                CGEventPost(kCGHIDEventTap, event)

        def _key_event(self, vkey: int, is_down: bool):
            """Map Windows VK code to Mac keycode and inject."""
            mac_keycode = VK_TO_MAC.get(vkey)
            if mac_keycode is None:
                if self.verbose:
                    LOG.debug("Unmapped VK: 0x%02X", vkey)
                return

            event = CGEventCreateKeyboardEvent(None, mac_keycode, is_down)
            if event:
                CGEventPost(kCGHIDEventTap, event)

            if self.verbose:
                LOG.debug("Key: VK=0x%02X -> Mac=0x%02X %s",
                          vkey, mac_keycode, "down" if is_down else "up")

        def _scroll(self, delta: int):
            """Inject a scroll wheel event. Windows sends 120 per notch."""
            # Convert Windows delta (120 per notch) to macOS lines
            lines = delta // 120
            if lines == 0:
                lines = 1 if delta > 0 else -1

            event = CGEventCreateScrollWheelEvent(None, kCGScrollEventUnitLine, 1, lines)
            if event:
                CGEventPost(kCGHIDEventTap, event)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Set DPI awareness on Windows to get real screen resolution
    if IS_WINDOWS:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    parser = argparse.ArgumentParser(
        description="Nex - Cross-platform keyboard/mouse sharing"
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0" if IS_WINDOWS else "192.168.31.99",
        help="Bind address (server/Windows) or server address (client/Mac). "
             "Default: 0.0.0.0 on Windows, 192.168.31.99 on Mac.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="TCP port (default: %(default)d)")
    parser.add_argument("--sensitivity", type=float, default=1.0,
                        help="Mouse sensitivity multiplier (default: 1.0)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable verbose/debug logging")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    if RICH_AVAILABLE:
        console = Console(theme=Theme({
            "logging.level.info": "cyan",
            "logging.level.warning": "yellow",
            "logging.level.error": "bold red",
            "logging.level.debug": "dim",
        }))
        logging.basicConfig(
            level=log_level,
            format="%(message)s",
            datefmt="[%H:%M:%S]",
            handlers=[RichHandler(
                console=console,
                show_path=False,
                rich_tracebacks=True,
                markup=True,
            )],
        )
    else:
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
        )

    # Handle Ctrl+C gracefully
    def sigint_handler(sig, frame):
        LOG.info("Shutting down...")
        sys.exit(0)
    signal.signal(signal.SIGINT, sigint_handler)

    # Startup banner
    if RICH_AVAILABLE:
        console = Console()
        console.print()
        console.print("  [bold cyan]nex[/bold cyan] [dim]v0.1.0[/dim]", highlight=False)
        console.print("  [dim]cross-platform keyboard & mouse sharing[/dim]")
        console.print()
    else:
        print("\n  nex v0.1.0\n  cross-platform keyboard & mouse sharing\n")

    if IS_WINDOWS:
        LOG.info("Server mode [bold](Windows)[/bold]", extra={"markup": True})
        server = Server(args.host, args.port, args.sensitivity, args.verbose)
        server.start()
    elif IS_MAC:
        LOG.info("Client mode [bold](Mac)[/bold]", extra={"markup": True})
        client = Client(args.host, args.port, args.sensitivity, args.verbose)
        client.start()
    else:
        LOG.error("Unsupported platform: %s", platform.system())
        sys.exit(1)


if __name__ == "__main__":
    main()
