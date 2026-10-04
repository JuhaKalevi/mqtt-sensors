#!/usr/bin/env python3
"""Windows tray: run the collectors in this folder with no console window."""
import ctypes, os, subprocess, sys, tempfile
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATE = HERE / "tray_enabled.txt"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
WM_DESTROY = 0x0002
WM_TIMER = 0x0113
WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_APP = 0x8000
NIM_ADD = 0
NIM_DELETE = 2
NIF_MESSAGE = 0x1
NIF_ICON = 0x2
NIF_TIP = 0x4
MF_STRING = 0
MF_CHECKED = 0x8
MF_SEPARATOR = 0x800
TPM_RIGHTBUTTON = 0x2
TPM_NONOTIFY = 0x80
TPM_RETURNCMD = 0x100
MB_ICONERROR = 0x10
IDI_APPLICATION = 32512
ID_ENV = 1001
ID_QUIT = 1002

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]

class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
        ("lPrivate", wintypes.DWORD),
    ]

user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
user32.MessageBoxW.restype = ctypes.c_int
user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
user32.LoadIconW.restype = wintypes.HICON
kernel32 = ctypes.windll.kernel32
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
user32.AppendMenuW.restype = wintypes.BOOL
user32.TrackPopupMenu.argtypes = [
    wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, ctypes.c_void_p,
]
user32.TrackPopupMenu.restype = wintypes.UINT
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL

hwnd = None
nid = None
nid_added = False
enabled = set()
procs = {}
stopping = False
exit_code = 0
in_loop = False
wndproc_ref = None

def collectors():
    return sorted(p.name for p in HERE.glob("*.py") if p.name != "tray.py")

INTERPRETER = None

def venv_pythonw():
    for name in ("venv", ".venv"):
        path = ROOT / name / "Scripts" / "pythonw.exe"
        if path.is_file():
            return path.resolve()
    return None

def same_exe(left, right):
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return os.path.normcase(str(left)) == os.path.normcase(str(right))

def relaunch_without_console():
    global INTERPRETER
    target = venv_pythonw()
    if target is None:
        user32.MessageBoxW(None, "pythonw.exe was not found in venv\\Scripts or .venv\\Scripts.", "mqtt-sensors", MB_ICONERROR)
        raise SystemExit(1)
    INTERPRETER = str(target)
    launcher = os.environ.get("__PYVENV_LAUNCHER__") or ""
    on_venv = same_exe(sys.executable, target) or (launcher and same_exe(launcher, target))
    if on_venv:
        return
    if os.environ.get("MQTT_SENSORS_PYTHONW") == INTERPRETER:
        user32.MessageBoxW(None, f"repo venv pythonw did not stay active:\n{sys.executable}", "mqtt-sensors", MB_ICONERROR)
        raise SystemExit(1)
    env = os.environ.copy()
    env["MQTT_SENSORS_PYTHONW"] = INTERPRETER
    env["__PYVENV_LAUNCHER__"] = INTERPRETER
    env["VIRTUAL_ENV"] = str(target.parent.parent)
    try:
        subprocess.Popen(
            [INTERPRETER, str(Path(__file__).resolve())],
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError as err:
        user32.MessageBoxW(None, f"could not start {INTERPRETER}: {err}", "mqtt-sensors", MB_ICONERROR)
        raise SystemExit(1)
    kernel32.FreeConsole()
    os._exit(0)

def fail(msg):
    global exit_code
    exit_code = 1
    user32.MessageBoxW(hwnd, str(msg), "mqtt-sensors", MB_ICONERROR)
    shutdown()
    if not in_loop:
        raise SystemExit(1)

def load_enabled():
    if not STATE.is_file():
        return set()
    known = set(collectors())
    found = set()
    for line in STATE.read_text(encoding="utf-8").splitlines():
        name = line.strip()
        if not name:
            continue
        if name not in known:
            fail(f"unknown collector {name}")
        found.add(name)
    return found

def save_enabled():
    names = [name for name in collectors() if name in enabled]
    STATE.write_text(("\n".join(names) + "\n") if names else "", encoding="utf-8")

def read_err(path):
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        text = ""
    try:
        os.unlink(path)
    except OSError:
        pass
    return text[-1500:]

def start_one(name):
    path = HERE / name
    if not path.is_file():
        fail(f"missing {name}")
        return
    fd, err_path = tempfile.mkstemp(prefix="mqtt-sensors-", suffix=".txt")
    err_fh = os.fdopen(fd, "wb")
    try:
        env = os.environ.copy()
        env["MQTT_SENSORS_PYTHONW"] = INTERPRETER
        env["__PYVENV_LAUNCHER__"] = INTERPRETER
        env["VIRTUAL_ENV"] = str(Path(INTERPRETER).parent.parent)
        proc = subprocess.Popen(
            [INTERPRETER, str(path)],
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=err_fh,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError as err:
        err_fh.close()
        read_err(err_path)
        fail(f"could not start {name}: {err}")
        return
    err_fh.close()
    if stopping:
        if proc.poll() is None:
            proc.terminate()
        read_err(err_path)
        return
    procs[name] = (proc, err_path)

def stop_one(name):
    item = procs.pop(name, None)
    if not item:
        return
    proc, err_path = item
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    read_err(err_path)

def supervise():
    for name, (proc, err_path) in list(procs.items()):
        if proc.poll() is None:
            continue
        code = proc.returncode
        detail = read_err(err_path)
        del procs[name]
        if name not in enabled:
            continue
        if code == 0:
            start_one(name)
            continue
        enabled.discard(name)
        save_enabled()
        body = f"{name} exited {code}"
        if detail:
            body = f"{body}\n{detail}"
        user32.MessageBoxW(hwnd, body, "mqtt-sensors", MB_ICONERROR)

def open_env():
    path = ROOT / ".env"
    if not path.is_file():
        fail(".env is missing")
    try:
        os.startfile(path)
    except OSError as err:
        fail(f"could not open .env: {err}")

def toggle(name):
    if name in enabled:
        enabled.discard(name)
        save_enabled()
        stop_one(name)
        return
    start_one(name)
    if stopping or name not in procs:
        return
    enabled.add(name)
    save_enabled()

def show_menu():
    names = collectors()
    menu = user32.CreatePopupMenu()
    for i, name in enumerate(names, 1):
        flags = MF_STRING | (MF_CHECKED if name in enabled else 0)
        user32.AppendMenuW(menu, flags, i, name)
    user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
    user32.AppendMenuW(menu, MF_STRING, ID_ENV, "Open .env")
    user32.AppendMenuW(menu, MF_STRING, ID_QUIT, "Quit")
    user32.SetForegroundWindow(hwnd)
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    cmd = user32.TrackPopupMenu(
        menu, TPM_RIGHTBUTTON | TPM_NONOTIFY | TPM_RETURNCMD, pt.x, pt.y, 0, hwnd, None,
    )
    user32.DestroyMenu(menu)
    user32.PostMessageW(hwnd, WM_NULL, 0, 0)
    if 1 <= cmd <= len(names):
        toggle(names[cmd - 1])
    elif cmd == ID_ENV:
        open_env()
    elif cmd == ID_QUIT:
        shutdown()

def shutdown():
    global stopping
    if stopping:
        return
    stopping = True
    for name in list(procs):
        stop_one(name)
    if nid_added and nid is not None:
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
    if hwnd:
        user32.DestroyWindow(hwnd)

def wndproc(window, msg, wparam, lparam):
    if msg == WM_APP and (lparam & 0xFFFF) in (WM_LBUTTONUP, WM_RBUTTONUP, WM_CONTEXTMENU):
        show_menu()
        return 0
    if msg == WM_TIMER:
        supervise()
        return 0
    if msg == WM_DESTROY:
        user32.PostQuitMessage(exit_code)
        return 0
    return user32.DefWindowProcW(window, msg, wparam, lparam)

def main():
    global hwnd, nid, nid_added, enabled, wndproc_ref, in_loop
    relaunch_without_console()
    enabled = load_enabled()
    wndproc_ref = WNDPROC(wndproc)
    hinst = kernel32.GetModuleHandleW(None)
    cls = WNDCLASSW()
    cls.lpfnWndProc = wndproc_ref
    cls.hInstance = hinst
    cls.lpszClassName = "mqtt-sensors-tray"
    if not user32.RegisterClassW(ctypes.byref(cls)):
        fail("RegisterClassW failed")
    hwnd = user32.CreateWindowExW(0, "mqtt-sensors-tray", "mqtt-sensors", 0, 0, 0, 0, 0, None, None, hinst, None)
    if not hwnd:
        fail("CreateWindowExW failed")
    icon = user32.LoadIconW(None, IDI_APPLICATION)
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = hwnd
    nid.uID = 1
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = WM_APP
    nid.hIcon = icon
    nid.szTip = "mqtt-sensors"
    if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
        fail("Shell_NotifyIconW failed")
    nid_added = True
    if not user32.SetTimer(hwnd, 1, 1000, None):
        fail("SetTimer failed")
    for name in sorted(enabled):
        start_one(name)
        if stopping:
            raise SystemExit(exit_code)
    in_loop = True
    msg = MSG()
    while True:
        rc = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if rc == 0:
            break
        if rc == -1:
            fail("GetMessageW failed")
            break
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    if exit_code:
        raise SystemExit(exit_code)

if __name__ == "__main__":
    main()
