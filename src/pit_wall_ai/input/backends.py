"""Where the presses come from: a fake for tests, WinMM joysticks, and Windows low-level hooks.

No dependency beyond the standard library (``ctypes``). Everything here is *read only*: the hooks pass every
event on (``CallNextHookEx``) and the joysticks are polled, so the game sees exactly what it saw before.

* **WinMM** (``joyGetPosEx``) reports up to 32 buttons per device: enough for a steering wheel (a G29 has 25);
  a large button box would need another backend (SDL), which this interface leaves room for.
* The **hooks** (``WH_KEYBOARD_LL`` / ``WH_MOUSE_LL``) are global: they work while the game has the focus.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from collections.abc import Callable
from typing import Protocol

from .model import InputEvent, InputId, vk_name

log = logging.getLogger(__name__)
Emit = Callable[[InputEvent], None]


class InputBackend(Protocol):
    name: str

    def start(self, emit: Emit) -> None: ...

    def stop(self) -> None: ...


# -- a fake for tests ---------------------------------------------------------------------------------------
class FakeBackend:
    name = "fake"

    def __init__(self) -> None:
        self._emit: Emit | None = None

    def start(self, emit: Emit) -> None:
        self._emit = emit

    def stop(self) -> None:
        self._emit = None

    def press(self, i: InputId) -> None:
        if self._emit:
            self._emit(InputEvent(i, True, time.perf_counter()))

    def release(self, i: InputId) -> None:
        if self._emit:
            self._emit(InputEvent(i, False, time.perf_counter()))


class MultiBackend:
    name = "multi"

    def __init__(self, backends: list[InputBackend]) -> None:
        self.backends = backends

    def start(self, emit: Emit) -> None:
        for b in self.backends:
            try:
                b.start(emit)
            except Exception:  # noqa: BLE001 - one backend failing must not take the others down
                log.exception("input backend failed to start", extra={"backend": b.name})

    def stop(self) -> None:
        for b in self.backends:
            try:
                b.stop()
            except Exception:  # noqa: BLE001
                log.exception("input backend failed to stop", extra={"backend": b.name})


# -- WinMM joysticks ------------------------------------------------------------------------------------------
class JoystickApi(Protocol):
    def num_devs(self) -> int: ...

    def poll(self, dev: int) -> int | None:
        """The button bitmask, or ``None`` when the device is not connected."""

    def caps(self, dev: int) -> tuple[str, int]:
        """(``vid:pid``, number of buttons) of a device."""

    def product_name(self, device_id: str) -> str: ...


class WinMMApi:
    """The real thing, through ``ctypes``."""

    def __init__(self) -> None:
        import ctypes

        self._c = ctypes
        self._winmm = ctypes.WinDLL("winmm")

        class JOYCAPSW(ctypes.Structure):
            _fields_ = [
                ("wMid", ctypes.c_ushort),
                ("wPid", ctypes.c_ushort),
                ("szPname", ctypes.c_wchar * 32),
                *[
                    (n, ctypes.c_uint)
                    for n in (
                        "wXmin",
                        "wXmax",
                        "wYmin",
                        "wYmax",
                        "wZmin",
                        "wZmax",
                        "wNumButtons",
                        "wPeriodMin",
                        "wPeriodMax",
                        "wRmin",
                        "wRmax",
                        "wUmin",
                        "wUmax",
                        "wVmin",
                        "wVmax",
                        "wCaps",
                        "wMaxAxes",
                        "wNumAxes",
                        "wMaxButtons",
                    )
                ],
                ("szRegKey", ctypes.c_wchar * 32),
                ("szOEMVxD", ctypes.c_wchar * 260),
            ]

        class JOYINFOEX(ctypes.Structure):
            _fields_ = [
                (n, ctypes.c_uint)
                for n in (
                    "dwSize",
                    "dwFlags",
                    "dwXpos",
                    "dwYpos",
                    "dwZpos",
                    "dwRpos",
                    "dwUpos",
                    "dwVpos",
                    "dwButtons",
                    "dwButtonNumber",
                    "dwPOV",
                    "dwReserved1",
                    "dwReserved2",
                )
            ]

        self._Caps, self._Info = JOYCAPSW, JOYINFOEX

    def num_devs(self) -> int:
        return int(self._winmm.joyGetNumDevs())

    def poll(self, dev: int) -> int | None:
        info = self._Info()
        info.dwSize = self._c.sizeof(info)
        info.dwFlags = 0xFF  # JOY_RETURNALL
        if self._winmm.joyGetPosEx(dev, self._c.byref(info)) != 0:
            return None
        return int(info.dwButtons)

    def caps(self, dev: int) -> tuple[str, int]:
        caps = self._Caps()
        if self._winmm.joyGetDevCapsW(dev, self._c.byref(caps), self._c.sizeof(caps)) != 0:
            return "", 0
        return f"{caps.wMid:04x}:{caps.wPid:04x}", int(caps.wNumButtons)

    def product_name(self, device_id: str) -> str:
        """WinMM only says "Microsoft PC-joystick driver"; the product name is in the registry."""
        try:
            import winreg

            vid, pid = device_id.split(":")
            key = (
                r"System\CurrentControlSet\Control\MediaProperties\PrivateProperties\Joystick\OEM"
                rf"\VID_{vid.upper()}&PID_{pid.upper()}"
            )
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                return str(winreg.QueryValueEx(k, "OEMName")[0])
        except (OSError, ValueError, ImportError):
            return ""


class WinMMJoystickBackend:
    name = "winmm-joystick"

    def __init__(self, poll_hz: float = 200.0, *, api: JoystickApi | None = None) -> None:
        self.period = 1.0 / max(10.0, poll_hz)
        self._api = api
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._names: dict[str, str] = {}

    def devices(self) -> list[tuple[int, str, str, int]]:
        """(index, ``vid:pid``, product name, buttons) of every connected device that has buttons."""
        api = self._api or WinMMApi()
        self._api = api
        out = []
        for i in range(api.num_devs()):
            if api.poll(i) is None:
                continue
            dev, buttons = api.caps(i)
            if buttons <= 0:
                continue
            out.append((i, dev, self._name(dev), buttons))
        return out

    def _name(self, dev: str) -> str:
        if dev not in self._names:
            self._names[dev] = (self._api.product_name(dev) if self._api else "") or dev
        return self._names[dev]

    def start(self, emit: Emit) -> None:
        if self._api is None:
            self._api = WinMMApi()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(emit,), name="joystick-poll", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def _run(self, emit: Emit) -> None:
        api = self._api
        assert api is not None
        prev: dict[int, int] = {}
        info: dict[int, tuple[str, str]] = {}
        while not self._stop.is_set():
            now = time.perf_counter()
            for dev in range(api.num_devs()):
                state = api.poll(dev)
                if state is None:
                    if dev in prev:  # unplugged: whatever was down is up
                        self._diff(emit, dev, prev.pop(dev), 0, info.pop(dev), now)
                    continue
                if dev not in info:
                    vp, buttons = api.caps(dev)
                    if buttons <= 0:
                        continue
                    info[dev] = (vp, self._name(vp))
                    prev[dev] = state  # what is already down at the start is not a press
                    continue
                if state != prev[dev]:
                    self._diff(emit, dev, prev[dev], state, info[dev], now)
                    prev[dev] = state
            self._stop.wait(self.period)

    @staticmethod
    def _diff(emit: Emit, dev: int, old: int, new: int, info: tuple[str, str], t: float) -> None:
        vp, name = info
        changed = old ^ new
        for bit in range(32):
            if changed & (1 << bit):
                emit(InputEvent(InputId("joy", str(bit + 1), vp, name), bool(new & (1 << bit)), t))


# -- global keyboard and mouse hooks --------------------------------------------------------------------------
_WM_KEYDOWN, _WM_KEYUP, _WM_SYSKEYDOWN, _WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
_MOUSE_MESSAGES = {
    0x201: ("left", True),
    0x202: ("left", False),
    0x204: ("right", True),
    0x205: ("right", False),
    0x207: ("middle", True),
    0x208: ("middle", False),
}
_WM_XBUTTONDOWN, _WM_XBUTTONUP = 0x20B, 0x20C


class WindowsHookBackend:
    name = "windows-hooks"

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._ready = threading.Event()
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._stop = threading.Event()
        self._dispatcher: threading.Thread | None = None
        self.error = ""

    def start(self, emit: Emit) -> None:
        if sys.platform != "win32":
            raise OSError("the keyboard and mouse hooks need Windows")
        self._stop.clear()
        self._thread = threading.Thread(target=self._hook_thread, name="input-hooks", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        if self.error:
            raise OSError(self.error)
        self._dispatcher = threading.Thread(
            target=self._dispatch, args=(emit,), name="input-dispatch", daemon=True
        )
        self._dispatcher.start()

    def stop(self) -> None:
        self._stop.set()
        if self._tid and sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)  # WM_QUIT
        self._queue.put(None)
        for t in (self._thread, self._dispatcher):
            if t:
                t.join(timeout=1.0)

    def _dispatch(self, emit: Emit) -> None:
        while not self._stop.is_set():
            item = self._queue.get()
            if item is None:
                break
            emit(item)

    def _hook_thread(self) -> None:
        import ctypes
        from ctypes import wintypes as w

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, w.WPARAM, w.LPARAM)

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", w.DWORD),
                ("scanCode", w.DWORD),
                ("flags", w.DWORD),
                ("time", w.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("pt", w.POINT),
                ("mouseData", w.DWORD),
                ("flags", w.DWORD),
                ("time", w.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, w.HINSTANCE, w.DWORD]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, w.WPARAM, w.LPARAM]
        user32.CallNextHookEx.restype = LRESULT
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        user32.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
        kernel32.GetModuleHandleW.argtypes = [w.LPCWSTR]
        kernel32.GetModuleHandleW.restype = w.HMODULE

        q = self._queue

        def keyboard(n_code, w_param, l_param):
            if n_code == 0:
                s = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if w_param in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
                    q.put(InputEvent(InputId("key", vk_name(s.vkCode)), True, time.perf_counter()))
                elif w_param in (_WM_KEYUP, _WM_SYSKEYUP):
                    q.put(InputEvent(InputId("key", vk_name(s.vkCode)), False, time.perf_counter()))
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        def mouse(n_code, w_param, l_param):
            if n_code == 0:
                code, pressed = None, False
                if w_param in _MOUSE_MESSAGES:
                    code, pressed = _MOUSE_MESSAGES[w_param]
                elif w_param in (_WM_XBUTTONDOWN, _WM_XBUTTONUP):
                    s = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    code = "x1" if (s.mouseData >> 16) & 0xFFFF == 1 else "x2"
                    pressed = w_param == _WM_XBUTTONDOWN
                if code is not None:
                    q.put(InputEvent(InputId("mouse", code), pressed, time.perf_counter()))
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        kb_proc, ms_proc = (
            HOOKPROC(keyboard),
            HOOKPROC(mouse),
        )  # keep a reference: the GC must not free them
        hmod = kernel32.GetModuleHandleW(None)
        h_kb = user32.SetWindowsHookExW(13, kb_proc, hmod, 0)  # WH_KEYBOARD_LL
        h_ms = user32.SetWindowsHookExW(14, ms_proc, hmod, 0)  # WH_MOUSE_LL
        if not h_kb or not h_ms:
            self.error = f"could not install the input hooks (error {ctypes.get_last_error()})"
            self._ready.set()
            return
        self._tid = kernel32.GetCurrentThreadId()
        self._ready.set()
        msg = w.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
        finally:
            user32.UnhookWindowsHookEx(h_kb)
            user32.UnhookWindowsHookEx(h_ms)


def default_backend(poll_hz: float = 200.0) -> MultiBackend:
    """Joysticks and keyboard/mouse on Windows; nothing elsewhere (the tool is Windows only for now)."""
    if sys.platform != "win32":
        return MultiBackend([])
    return MultiBackend([WinMMJoystickBackend(poll_hz), WindowsHookBackend()])
