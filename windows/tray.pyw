import ctypes, os, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

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

target = venv_pythonw()
if target is None:
    ctypes.windll.user32.MessageBoxW(None, "pythonw.exe was not found in venv\\Scripts or .venv\\Scripts.", "mqtt-sensors", 0x10)
    raise SystemExit(1)
launcher = os.environ.get("__PYVENV_LAUNCHER__") or ""
on_venv = same_exe(sys.executable, target) or (launcher and same_exe(launcher, target))
if not on_venv:
    if os.environ.get("MQTT_SENSORS_PYTHONW") == str(target):
        ctypes.windll.user32.MessageBoxW(None, f"repo venv pythonw did not stay active:\n{sys.executable}", "mqtt-sensors", 0x10)
        raise SystemExit(1)
    env = os.environ.copy()
    env["MQTT_SENSORS_PYTHONW"] = str(target)
    env["__PYVENV_LAUNCHER__"] = str(target)
    env["VIRTUAL_ENV"] = str(target.parent.parent)
    try:
        subprocess.Popen(
            [str(target), str(HERE / "tray.py")],
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError as err:
        ctypes.windll.user32.MessageBoxW(None, f"could not start {target}:\n{err}", "mqtt-sensors", 0x10)
        raise SystemExit(1)
    ctypes.windll.kernel32.FreeConsole()
    os._exit(0)

import runpy
runpy.run_path(str(HERE / "tray.py"), run_name="__main__")
