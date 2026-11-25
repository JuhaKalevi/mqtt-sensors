"""Motherboard identity for hardware-keyed sensor ids, and machine manufacturer/model for HA devices."""
import functools, subprocess
from lib.mqtt_common import id_slug, id_part

BOARD_PLACEHOLDERS = {
    "", "default_string", "to_be_filled_by_o_e_m", "o_e_m", "oem", "none", "not_specified", "not_applicable",
    "n_a", "na", "unknown", "system_serial_number", "base_board_serial_number", "chassis_serial_number",
    "serial_number", "123456789", "03000200_0400_0500_0006_000700080009",
}

def is_placeholder(value):
    """True for empty, known DMI/WMI filler strings, or a slug of all one character."""
    s = id_slug(value or "")
    return s in BOARD_PLACEHOLDERS or len(set(s.replace("_", ""))) <= 1

def first_real(*values):
    """First stripped value that is not a placeholder, else empty."""
    for value in values:
        text = (value or "").strip()
        if text and not is_placeholder(text):
            return text
    return ""

def pick_machine_info(board_vendor, sys_vendor, board_name, product_name):
    """Manufacturer: board vendor, else system vendor. Model: board name, else product name."""
    return first_real(board_vendor, sys_vendor), first_real(board_name, product_name)

def pick_board_id(serial, uuid, vendor, name):
    """Board serial, else system UUID (placeholders skipped), else vendor + name; slugged, case kept."""
    for value in (serial, uuid):
        s = id_slug(value)
        if s not in BOARD_PLACEHOLDERS and len(set(s.replace("_", ""))) > 1:
            return id_part(value)
    return id_part(f"{vendor} {name}") or "unknown"

def _dmi_read(root, name):
    try:
        with open(f"{root}/{name}") as f:
            return f.read().replace("\x00", "").strip()
    except OSError:
        return ""

def dmi_board_id(root="/sys/class/dmi/id"):
    def read(name):
        return _dmi_read(root, name)
    return pick_board_id(read("board_serial"), read("product_uuid"), read("board_vendor"), read("board_name"))

def dmi_machine_info(root="/sys/class/dmi/id"):
    """(manufacturer, model) from DMI; empty strings when unknown."""
    return pick_machine_info(
        _dmi_read(root, "board_vendor"), _dmi_read(root, "sys_vendor"),
        _dmi_read(root, "board_name"), _dmi_read(root, "product_name"),
    )

def wmi_machine_info():
    """(manufacturer, model) from Win32_BaseBoard then Win32_ComputerSystem (one PowerShell query)."""
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "$b = Get-CimInstance Win32_BaseBoard | Select-Object -First 1; "
         "$s = Get-CimInstance Win32_ComputerSystem | Select-Object -First 1; "
         "@($b.Manufacturer, $s.Manufacturer, $b.Product, $s.Model) -join [char]10"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, startupinfo=info,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )
    lines = ((proc.stdout or "").splitlines() + ["", "", "", ""])[:4]
    return pick_machine_info(*(line.strip() for line in lines))

@functools.cache
def machine_hardware_info(kind="linux"):
    """(manufacturer, model) for make_device(): DMI on Linux, WMI on Windows."""
    if kind == "windows":
        return wmi_machine_info()
    return dmi_machine_info()
