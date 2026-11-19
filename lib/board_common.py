"""Motherboard identity for hardware-keyed sensor ids (hwmon_sensors.py, windows/lhm_sensors.py)."""
from lib.mqtt_common import id_slug, id_part

BOARD_PLACEHOLDERS = {
    "", "default_string", "to_be_filled_by_o_e_m", "o_e_m", "oem", "none", "not_specified", "not_applicable",
    "n_a", "na", "unknown", "system_serial_number", "base_board_serial_number", "chassis_serial_number",
    "serial_number", "123456789", "03000200_0400_0500_0006_000700080009",
}

def pick_board_id(serial, uuid, vendor, name):
    """Board serial, else system UUID (placeholders skipped), else vendor + name; slugged, case kept."""
    for value in (serial, uuid):
        s = id_slug(value)
        if s not in BOARD_PLACEHOLDERS and len(set(s.replace("_", ""))) > 1:
            return id_part(value)
    return id_part(f"{vendor} {name}") or "unknown"

def dmi_board_id(root="/sys/class/dmi/id"):
    def read(name):
        try:
            with open(f"{root}/{name}") as f:
                return f.read().strip()
        except OSError:
            return ""
    return pick_board_id(read("board_serial"), read("product_uuid"), read("board_vendor"), read("board_name"))
