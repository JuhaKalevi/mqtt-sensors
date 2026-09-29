#!/usr/bin/env python3
"""Drive temperature (drivetemp + nvme hwmon, else in-process SAT SMART via SG_IO) → MQTT + Home Assistant discovery."""
import ctypes, errno, fcntl, json, os, re, sys, time
from pathlib import Path
from mqtt_common import (
    get_hostname, get_mqtt_settings, make_device,
    make_sensor_discovery, create_client
)

HOSTNAME = get_hostname()
DEVICE, DEVICE_ID = make_device(HOSTNAME)
CLIENT_ID = f"hwmon-drive-temperature-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
INTERVAL = float(os.getenv("DRIVE_TEMPERATURE_INTERVAL") or "60")
DEBUG = os.getenv("DRIVE_TEMPERATURE_DEBUG") == "1"
AVAIL_T = f"{PREFIX}/sensor/hwmon_drive_temperature_{HOSTNAME}/availability"
ROOT = Path("/sys/class/hwmon")
BLOCK = Path("/sys/block")
DEV = "/dev"
SG_IO = 0x2285
ACTIVE = (0x41, 0x80, 0x81, 0x82, 0x83, 0xFF)
drives = {}
sat_drives = {}
skipped = {}
quirk_suggestions = set()
collecting_quirks = True

class SgIoHdr(ctypes.Structure):
    _fields_ = [
        ("interface_id", ctypes.c_int),
        ("dxfer_direction", ctypes.c_int),
        ("cmd_len", ctypes.c_ubyte),
        ("mx_sb_len", ctypes.c_ubyte),
        ("iovec_count", ctypes.c_ushort),
        ("dxfer_len", ctypes.c_uint),
        ("dxferp", ctypes.c_void_p),
        ("cmdp", ctypes.c_void_p),
        ("sbp", ctypes.c_void_p),
        ("timeout", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("pack_id", ctypes.c_int),
        ("usr_ptr", ctypes.c_void_p),
        ("status", ctypes.c_ubyte),
        ("masked_status", ctypes.c_ubyte),
        ("msg_status", ctypes.c_ubyte),
        ("sb_len_wr", ctypes.c_ubyte),
        ("host_status", ctypes.c_ushort),
        ("driver_status", ctypes.c_ushort),
        ("resid", ctypes.c_int),
        ("duration", ctypes.c_uint),
        ("info", ctypes.c_uint),
    ]

def log(msg):
    print(f"hwmon_drive_temperature: {msg}", flush=True)

def read_opt(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ""

def read_bin(path):
    try:
        return path.read_bytes()
    except OSError:
        return b""

def text(b):
    return "".join(c for c in b.decode("ascii", "ignore") if c.isprintable()).strip()

def ata_string(b):
    return text(bytes(b[i ^ 1] for i in range(len(b) & ~1)))

def ata_ident(ident):
    return ata_string(ident[54:94]), ata_string(ident[20:40])

def usb_parent(dev):
    for p in dev.parents:
        if (p / "idVendor").is_file():
            return p
    return None

def identity(dev, model="", serial=""):
    usb = usb_parent(dev)
    pg89 = read_bin(dev / "vpd_pg89")
    if not serial and len(pg89) >= 572:
        model, serial = ata_ident(pg89[60:])
    model = model or read_opt(dev / "model")
    serial = serial or read_opt(dev / "serial") or text(read_bin(dev / "vpd_pg80")[4:])
    ident = serial or read_opt(dev / "wwid")
    if not ident and usb:
        usb_serial = read_opt(usb / "serial")
        if usb_serial:
            lun = dev.name.split(":")[-1]
            ident = f"usb {read_opt(usb / 'idVendor')} {read_opt(usb / 'idProduct')} {usb_serial} {lun}"
    if not ident:
        return None
    return model or "Drive", serial, re.sub(r"[^a-z0-9]+", "_", ident.lower()).strip("_"), usb is not None

def entry(ids):
    model, serial, ident, usb = ids
    obj = f"hwmon_drive_temperature_{ident}_{HOSTNAME}"
    state = f"{PREFIX}/sensor/{obj}/state"
    name = f"{model} {serial}" if serial else model
    name = f"{name} Temperature (USB)" if usb else f"{name} Temperature"
    return {
        "obj": obj,
        "label": f"{model} {serial}".strip(),
        "state": state,
        "config": f"{PREFIX}/sensor/{obj}/config",
        "discovery": make_sensor_discovery(
            name, state, AVAIL_T, obj, DEVICE,
            unit="°C", device_class="temperature", state_class="measurement"
        ),
    }

def block_name(dev, fallback):
    try:
        return sorted(os.listdir(dev / "block"))[0]
    except (OSError, IndexError):
        return fallback

def open_drive(hw, dev):
    kind = read_opt(hw / "name")
    if kind not in ("drivetemp", "nvme"):
        return None
    ids = identity(dev)
    if not ids:
        if skipped.get(hw.name) != dev:
            skipped[hw.name] = dev
            log(f"skipping {hw.name} ({dev}): no stable serial, wwid or USB serial")
        return None
    try:
        f = open(hw / "temp1_input")
    except OSError:
        return None
    d = entry(ids)
    d["dev"] = dev
    d["fd"] = f
    log(f"{block_name(dev, dev.name)}: reading via {kind} hwmon, {d['label']}")
    return d

def drop_drive(name):
    d = drives.pop(name, None)
    if not d:
        return
    try:
        d["fd"].close()
    except OSError:
        pass
    client.publish(d["config"], "", retain=True)

def sync_drives():
    present = set()
    for hw in sorted(ROOT.iterdir()):
        present.add(hw.name)
        dev = (hw / "device").resolve()
        if hw.name in drives:
            if drives[hw.name]["dev"] == dev:
                continue
            drop_drive(hw.name)
        d = open_drive(hw, dev)
        if not d:
            continue
        drives[hw.name] = d
        client.publish(d["config"], json.dumps(d["discovery"]), retain=True)
    for name in list(drives):
        if name not in present:
            drop_drive(name)

def cdb(op, data_in, command, feature=0, lba_mid=0, lba_high=0):
    proto = (4 if data_in else 3) << 1
    flags = 0x0E if data_in else 0x2C
    count = 1 if data_in else 0
    if op == 0x85:
        return bytes([0x85, proto, flags, 0, feature, 0, count, 0, 0, 0, lba_mid, 0, lba_high, 0, command, 0])
    return bytes([0xA1, proto, flags, feature, count, 0, lba_mid, lba_high, 0, command, 0, 0])

def sense_key(sb):
    code = sb[0] & 0x7F if sb else 0
    if code in (0x72, 0x73) and len(sb) > 1:
        return sb[1] & 0x0F
    if code in (0x70, 0x71) and len(sb) > 2:
        return sb[2] & 0x0F
    return 0

def ata_regs(sb):
    if len(sb) < 8:
        return None
    code = sb[0] & 0x7F
    if code in (0x72, 0x73):
        i, end = 8, min(len(sb), 8 + sb[7])
        while i + 1 < end:
            if sb[i] == 0x09 and i + 13 < end:
                return {"error": sb[i + 3], "count": sb[i + 5], "status": sb[i + 13]}
            i += sb[i + 1] + 2
        return None
    if code in (0x70, 0x71) and len(sb) >= 14 and sb[12] == 0x00 and sb[13] == 0x1D:
        return {"error": sb[3], "count": sb[6], "status": sb[4]}
    return None

def usb_driver(dev):
    for p in dev.parents:
        if (p / "bInterfaceNumber").is_file():
            return os.path.basename(os.path.realpath(p / "driver"))
    return "-"

def usb_info(dev):
    drv, usb = usb_driver(dev), usb_parent(dev)
    if not usb:
        return drv
    return f"{drv} {read_opt(usb / 'idVendor')}:{read_opt(usb / 'idProduct')}"

def suggest_quirk(s):
    if not collecting_quirks or usb_driver(s["dev"]) != "uas":
        return
    for p in s["dev"].parents:
        vid = read_opt(p / "idVendor")
        pid = read_opt(p / "idProduct")
        if not vid or not pid:
            continue
        quirk_suggestions.add(f"{vid.lower()}:{pid.lower()}")
        return

def log_quirk_suggestions():
    if not quirk_suggestions:
        return
    listed = ",".join(f"{key}:u" for key in sorted(quirk_suggestions))
    log(f"usb-storage quirks suggested: {listed}")

def sat(fd, op, command, data_in=False, feature=0, lba_mid=0, lba_high=0, dbg=None):
    c = cdb(op, data_in, command, feature, lba_mid, lba_high)
    cmd = ctypes.create_string_buffer(c, len(c))
    buf = ctypes.create_string_buffer(512)
    sense = ctypes.create_string_buffer(64)
    h = SgIoHdr(
        interface_id=ord("S"), dxfer_direction=-3 if data_in else -1,
        cmd_len=len(c), mx_sb_len=64, dxfer_len=512 if data_in else 0,
        dxferp=ctypes.addressof(buf) if data_in else None,
        cmdp=ctypes.addressof(cmd), sbp=ctypes.addressof(sense), timeout=5000,
    )
    try:
        fcntl.ioctl(fd, SG_IO, h)
        e = None
    except OSError as ex:
        e = errno.errorcode.get(ex.errno, ex.errno)
    sb = sense.raw[:h.sb_len_wr]
    if dbg:
        log(
            f"debug {dbg} ATA_{len(c)} {command:02X}h cdb={c.hex()} errno={e or 0} "
            f"status=0x{h.status:02x} masked=0x{h.masked_status:02x} host=0x{h.host_status:04x} "
            f"driver=0x{h.driver_status:04x} sb_len_wr={h.sb_len_wr} resid={h.resid} "
            f"duration={h.duration}ms sense={sb.hex() or '-'}"
            + (f" data={buf.raw[:16].hex()}" if data_in and not e else "")
        )
    if e:
        return f"SG_IO {e}", None, None
    if h.host_status or h.driver_status & 0x07:
        return f"transport error (host 0x{h.host_status:x}, driver 0x{h.driver_status:x})", None, None
    regs = ata_regs(sb)
    if h.status not in (0, 2) or (h.status == 2 and regs is None):
        key = sense_key(sb)
        if key == 5:
            return "ATA pass-through rejected (ILLEGAL REQUEST)", None, None
        return f"SCSI status 0x{h.status:02x} sense key {key}", None, None
    if regs and regs["status"] & 0x01:
        return f"ATA command 0x{command:02x} aborted (error 0x{regs['error']:02x})", regs, None
    return None, regs, buf.raw if data_in else None

def ops(s):
    return [s["op"]] if s["op"] else [0x85, 0xA1]

def op_name(op):
    return f"ATA_{16 if op == 0x85 else 12}"

def power_mode(s, dbg=None):
    errs = []
    for op in ops(s):
        err, regs, _ = sat(s["fd"], op, 0xE5, dbg=dbg)
        if not err and not regs:
            err = "bridge returns no ATA registers"
        elif not err and not regs["status"] & 0x40 and regs["count"] not in ACTIVE:
            err = f"invalid ATA registers, status 0x{regs['status']:02x} count 0x{regs['count']:02x}"
        if not err:
            s["op"] = op
            return regs["count"], None
        errs.append(f"{op_name(op)}: {err}")
    return None, "; ".join(errs)

def smart_temp(data):
    attrs = {data[i]: data[i + 5] for i in range(2, 362, 12) if data[i]}
    for a in (194, 190):
        t = attrs.get(a)
        if t and 1 <= t <= 99:
            return t
    return None

def read_smart(s, op, dbg=None):
    err, _, data = sat(s["fd"], op, 0xB0, True, 0xD0, 0x4F, 0xC2, dbg)
    if err:
        suggest_quirk(s)
    return err, None if err else smart_temp(data)

def setup_sat(s):
    errs = []
    for op in ops(s):
        err, _, ident = sat(s["fd"], op, 0xEC, True, dbg=s["dbg"])
        model = serial = ""
        if not err:
            if ident[1] & 0x80:
                return "IDENTIFY: not an ATA device"
            if not ident[164] & 0x01:
                return "SMART not supported"
            if not ident[170] & 0x01:
                return "SMART disabled"
            model, serial = ata_ident(ident)
        smart_err, t = read_smart(s, op, s["dbg"])
        if not smart_err:
            break
        errs.append(f"{op_name(op)}: {smart_err}")
    else:
        return "; ".join(errs)
    if t is None:
        return "no SMART temperature (attribute 194/190)"
    s["op"] = op
    ids = identity(s["dev"], model, serial)
    if not ids:
        return "no stable serial, wwid or USB serial"
    s.update(entry(ids))
    client.publish(s["config"], json.dumps(s["discovery"]), retain=True)
    s["mode"] = "sat"
    log(f"{s['name']}: reading via SAT {op_name(s['op'])}, {s['label']}")
    return None

def unsupported(s, reason):
    s["mode"] = "unsupported"
    os.close(s["fd"])
    log(f"{s['name']}: unsupported: {reason}")

def poll_sat(s):
    mode, err = power_mode(s, s["dbg"] if s["mode"] == "probe" else None)
    if not err and mode not in ACTIVE:
        if s["mode"] == "probe":
            s["mode"] = "standby"
            log(f"{s['name']}: in standby (power mode 0x{mode:02x}), not reading until it spins up")
        return
    if s["mode"] != "sat":
        setup_err = setup_sat(s)
        if setup_err:
            unsupported(s, setup_err)
            return
    if err and not s["unknown"]:
        s["unknown"] = True
        log(f"{s['name']}: power mode unknown ({err}), reading anyway; may keep drive spinning")
    _, t = read_smart(s, s["op"])
    if t is not None:
        client.publish(s["state"], "%.1f" % t, retain=True)

def candidate(b):
    return read_opt(b / "removable") != "1" and read_opt(b / "size") not in ("", "0")

def new_sat(b, dev):
    s = {"name": b.name, "dev": dev, "op": None, "mode": "probe", "dbg": None, "unknown": False}
    if DEBUG:
        s["dbg"] = f"{b.name} [{usb_info(dev)}]"
        quirks = read_opt(Path("/sys/module/usb_storage/parameters/quirks")) or "-"
        log(f"debug {s['dbg']} dev={dev} vpd_pg89={'yes' if (dev / 'vpd_pg89').exists() else 'no'} usb-storage.quirks={quirks}")
    if not candidate(b):
        s["mode"] = "skipped"
        log(f"{b.name}: skipped: removable or no media")
        return s
    try:
        s["fd"] = os.open(f"{DEV}/{b.name}", os.O_RDONLY | os.O_NONBLOCK)
    except OSError as e:
        s["mode"] = "unsupported"
        log(f"{b.name}: unsupported: open {errno.errorcode.get(e.errno, e.errno)}")
    return s

def drop_sat(name):
    s = sat_drives.pop(name)
    if s["mode"] in ("probe", "standby", "sat"):
        os.close(s["fd"])
    if "config" in s and s["obj"] not in {d["obj"] for d in drives.values()}:
        client.publish(s["config"], "", retain=True)

def sync_sat():
    covered = {d["dev"] for d in drives.values()}
    present = set()
    for b in sorted(BLOCK.glob("sd*")):
        dev = (b / "device").resolve()
        present.add(b.name)
        s = sat_drives.get(b.name)
        if s and (s["dev"] != dev or dev in covered):
            drop_sat(b.name)
            s = None
        if s or dev in covered:
            continue
        sat_drives[b.name] = new_sat(b, dev)
    for name in list(sat_drives):
        if name not in present:
            drop_sat(name)
    for s in list(sat_drives.values()):
        if s["mode"] in ("probe", "standby", "sat"):
            poll_sat(s)

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        for d in list(drives.values()) + [s for s in sat_drives.values() if s["mode"] == "sat"]:
            client.publish(d["config"], json.dumps(d["discovery"]), retain=True)
        client.publish(AVAIL_T, "online", retain=True)

hwmons = [hw for hw in ROOT.iterdir() if read_opt(hw / "name") in ("drivetemp", "nvme")]
disks = [b for b in BLOCK.glob("sd*") if candidate(b)]
log(f"host={HOSTNAME} interval={INTERVAL:g}s drivetemp/nvme hwmon={len(hwmons)} scsi disks={len(disks)} debug={int(DEBUG)}")
if not hwmons and not disks:
    sys.exit("hwmon_drive_temperature: no drivetemp or nvme hwmon and no SCSI disks; for SATA try: modprobe drivetemp")

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

try:
    while True:
        sync_drives()
        for d in drives.values():
            try:
                d["fd"].seek(0)
                val = int(d["fd"].read())
            except (OSError, ValueError):
                continue
            client.publish(d["state"], "%.1f" % (val / 1000.0), retain=True)
        sync_sat()
        if collecting_quirks:
            log_quirk_suggestions()
            collecting_quirks = False
        time.sleep(INTERVAL)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
    for d in drives.values():
        d["fd"].close()
    for s in sat_drives.values():
        if s["mode"] in ("probe", "standby", "sat"):
            os.close(s["fd"])
