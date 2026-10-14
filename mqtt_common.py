#!/usr/bin/env python3
"""Minimal shared MQTT + Home Assistant discovery helpers."""
import os, json, socket, subprocess
from contextlib import contextmanager
from pathlib import Path
import paho.mqtt.client as mqtt

def load_dotenv(path=".env"):
    if not os.path.isfile(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

def get_hostname():
    return socket.gethostname().split(".")[0]

def chia_root():
    import pwd
    return Path(pwd.getpwuid(1000).pw_dir) / ".chia" / "mainnet"

def get_mqtt_settings():
    load_dotenv()
    return {
        "host": os.getenv("MQTT_HOST", "localhost"),
        "port": int(os.getenv("MQTT_PORT", "1883")),
        "user": os.getenv("MQTT_USER") or None,
        "password": os.getenv("MQTT_PASS") or None,
        "prefix": os.getenv("MQTT_PREFIX", "homeassistant"),
    }

def make_device(hostname):
    device_id = f"linux_host_{hostname}"
    return {"identifiers": [device_id], "name": hostname}, device_id

def make_sensor_discovery(name, state_topic, availability_topic, unique_id, device,
                          unit, device_class, state_class):
    return {
        "name": name,
        "state_topic": state_topic,
        "availability_topic": availability_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
        "unit_of_measurement": unit,
        "device_class": device_class,
        "state_class": state_class,
        "unique_id": unique_id,
        "device": device,
    }

def make_power_discovery(name, state_topic, availability_topic, unique_id, device):
    return make_sensor_discovery(
        name, state_topic, availability_topic, unique_id, device,
        unit="W", device_class="power", state_class="measurement"
    )

def make_energy_discovery(name, state_topic, availability_topic, unique_id, device):
    return make_sensor_discovery(
        name, state_topic, availability_topic, unique_id, device,
        unit="kWh", device_class="energy", state_class="total_increasing"
    )

def make_switch_discovery(name, state_topic, command_topic, availability_topic, unique_id, device):
    return {
        "name": name,
        "state_topic": state_topic,
        "command_topic": command_topic,
        "availability_topic": availability_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
        "payload_on": "ON",
        "payload_off": "OFF",
        "unique_id": unique_id,
        "device": device,
    }

def make_number_discovery(name, state_topic, command_topic, availability_topic, unique_id, device,
                          min_value=0, max_value=100, step=1, mode="slider", unit=None):
    d = {
        "name": name,
        "state_topic": state_topic,
        "command_topic": command_topic,
        "availability_topic": availability_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
        "min": min_value,
        "max": max_value,
        "step": step,
        "mode": mode,
        "unique_id": unique_id,
        "device": device,
    }
    if unit is not None:
        d["unit_of_measurement"] = unit
    return d

def switch_mode(env_name):
    mode = os.getenv(env_name) or "read_only"
    if mode not in ("read_only", "control"):
        raise SystemExit(f"{env_name} must be read_only or control, got {mode!r}")
    return mode

def make_switch_on_message(tag, mode, state_topic, get_state, act):
    def on_message(client, userdata, msg):
        payload = msg.payload.decode()
        if mode == "control" and payload in ("ON", "OFF"):
            act(payload)
        else:
            print(f"{tag}: ignored {payload!r} (mode={mode})", flush=True)
        client.publish(state_topic, get_state(), retain=True)
    return on_message

class SystemdServiceManager:
    """Service manager backend. Contract: is_active(name) -> bool, start(name), stop(name), follow_log(name) -> context manager yielding an iterator of new log lines (text, newline kept; stopped on exit); name is the bare service name ("lightdm", "ollama", "chia_recompute_server").
    Collectors only use service_manager() and these four methods, never systemd directly. systemd is the only shipped backend.
    Another init system (OpenRC, runit, s6, ...): add a class with the same four methods and return it from service_manager(); no collector changes."""

    def is_active(self, name):
        return os.path.isdir(f"/sys/fs/cgroup/system.slice/{name}.service")

    def start(self, name):
        self._systemctl("start", name)

    def stop(self, name):
        self._systemctl("stop", name)

    def _systemctl(self, action, name):
        rc = subprocess.run(["systemctl", action, f"{name}.service"]).returncode
        print(f"{'ON' if action == 'start' else 'OFF'} -> systemctl {action} {name}.service rc={rc}", flush=True)

    @contextmanager
    def follow_log(self, name):
        proc = subprocess.Popen(
            ["journalctl", "-u", name, "-f", "-n", "0", "-o", "cat", "--no-pager"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        try:
            yield proc.stdout
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait()

def service_manager():
    return SystemdServiceManager()

class LogindSessionSource:
    """Login session backend. Contract: active_users() -> list of user names with an active or online session (unsorted).
    Collectors only use session_source() and this method, never logind directly. logind's /run/systemd/users is the only shipped backend; elogind writes the same files, so non-systemd hosts with elogind already work.
    Another session tracker (ConsoleKit2, seatd, utmp, ...): add a class with the same method and return it from session_source(); no collector changes."""

    USERS = "/run/systemd/users"

    def active_users(self):
        names = []
        for fn in os.listdir(self.USERS):
            if not fn.isdigit():
                continue
            user = state = None
            try:
                with open(os.path.join(self.USERS, fn)) as f:
                    for line in f:
                        if line.startswith("NAME="):
                            user = line[5:].strip()
                        elif line.startswith("STATE="):
                            state = line[6:].strip()
            except FileNotFoundError:
                continue
            if user and state in ("active", "online"):
                names.append(user)
        return names

def session_source():
    return LogindSessionSource()

def create_client(client_id, settings, will_topic=None, will_payload="offline"):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    if settings["user"]:
        client.username_pw_set(settings["user"], settings["password"])
    if will_topic:
        client.will_set(will_topic, will_payload, qos=1, retain=True)
    return client

def log(msg):
    print(f"xmrig_status: {msg}", flush=True)

def take_measured(label, unit, hinted, current, measured):
    if hinted and abs(measured - current) > 0.05 * current:
        log(f"measured {label} {measured:.1f} {unit} rejected, outside ±5% of hint {current:.1f} {unit}")
        return current, True
    log(f"full-intensity {label} {measured:.1f} {unit}" + (f", replaces hint {current:.1f} {unit}" if hinted else ""))
    return measured, False

class MiningIntensity:
    def __init__(self, power_hint, plateau_hint, contention):
        from collections import deque
        self.contention = contention
        self.baseline_w = float(power_hint or 0)
        self.power_hinted = bool(power_hint)
        self.plateau_hr = float(plateau_hint or 0)
        self.plateau_hinted = bool(plateau_hint)
        if power_hint:
            log(f"using power baseline hint {power_hint} W")
        if plateau_hint:
            log(f"using hashrate plateau hint {plateau_hint} H/s")
        self.samples = 0
        self.establish_w = 0.0
        self.establish_hr = 0.0
        self.power_window = deque(maxlen=900)
        self.hr_window = deque(maxlen=900)
        self.last_check = 0.0
        self.auto_capped = False
        self.fast_rampup = False
        self.contention_since = None
        self.clear_since = None
        self.last_step = 0.0
        self.last_n = None
        self.settle_until = 0.0
        self.contended_max = 0
        self.wait = 300.0
        self.raised = False
        self.calm_since = None

    def manual(self, percent):
        if self.auto_capped:
            log(f"manual slider {percent}%, automatic contention state cancelled")
        self.auto_capped = False
        self.fast_rampup = False
        self.reset_backoff("manual slider")

    def reset_backoff(self, why):
        if self.contended_max or self.wait > 300.0:
            log(f"recovery backoff reset ({why}), wait 300 s")
        self.contended_max = 0
        self.wait = 300.0
        self.raised = False

    def sample(self, now, paused, n, mt, step, power_w, hr10, set_threads):
        if not paused and mt > 0 and n == mt and power_w > 0:
            self.power_window.append(power_w)
            self.hr_window.append(hr10)
            if self.samples < 300:
                self.samples += 1
                self.establish_w = max(self.establish_w, power_w)
                self.establish_hr = max(self.establish_hr, hr10)
                if self.samples == 300:
                    self.baseline_w, self.power_hinted = take_measured("power baseline", "W", self.power_hinted, self.baseline_w, self.establish_w)
                    self.plateau_hr, self.plateau_hinted = take_measured("hashrate plateau", "H/s", self.plateau_hinted, self.plateau_hr, self.establish_hr)
                    self.last_check = now
            elif now - self.last_check >= 300.0:
                self.last_check = now
                recent_w = max(self.power_window)
                if abs(recent_w - self.baseline_w) > 0.05 * self.baseline_w:
                    self.baseline_w, self.power_hinted = take_measured("power baseline", "W", self.power_hinted, self.baseline_w, recent_w)
                recent_hr = max(self.hr_window)
                if abs(recent_hr - self.plateau_hr) > 0.05 * self.plateau_hr:
                    self.plateau_hr, self.plateau_hinted = take_measured("hashrate plateau", "H/s", self.plateau_hinted, self.plateau_hr, recent_hr)
        if self.baseline_w <= 0 or mt <= 0:
            return power_w
        f = n / mt
        if self.contention and not paused and self.plateau_hr > 0:
            self.contend(now, n, mt, step, f, power_w, hr10, set_threads)
        if f <= 0:
            return 0.0
        if f >= 1:
            return power_w
        return min(power_w, f * self.baseline_w)

    def contend(self, now, n, mt, step, f, power_w, hr10, set_threads):
        if n != self.last_n:
            self.last_n = n
            self.settle_until = now + 30.0
        if now < self.settle_until:
            return
        rel = hr10 / self.plateau_hr
        if n > 0 and power_w >= 0.9 * f * self.baseline_w and rel < 0.95 * f:
            self.clear_since = None
            self.calm_since = None
            if self.fast_rampup:
                log("fast rampup cleared by contention")
            self.fast_rampup = False
            if self.contention_since is None:
                self.contention_since = now
                log(f"contention detected: power {power_w:.1f} W vs expected {f * self.baseline_w:.1f} W, hashrate {rel:.2f} of plateau vs thread fraction {f:.2f}")
                if self.raised and n <= self.contended_max:
                    self.wait *= 2
                    self.contended_max = n
                    log(f"raise to {n} failed, recovery wait now {self.wait:.0f} s, remembered contended thread count {n}")
                elif n > self.contended_max:
                    self.contended_max = n
                    log(f"remembered contended thread count {n}")
                self.raised = False
            if n > step and now - self.contention_since >= 15.0 and now - self.last_step >= 60.0:
                target = max(step, n - step)
                log(f"auto drop, hashrate {rel:.2f} of plateau vs thread fraction {f:.2f}: threads {n} -> {target}")
                set_threads(n, target)
                self.auto_capped = True
                self.last_step = now
            return
        self.contention_since = None
        if n >= mt and not self.auto_capped:
            if self.calm_since is None:
                self.calm_since = now
            if now - self.calm_since >= 900.0 and (self.contended_max or self.wait > 300.0):
                self.reset_backoff("15 min calm at 100%")
        if not self.auto_capped:
            return
        slow = not self.fast_rampup or (self.wait > 300.0 and n + 1 == self.contended_max)
        delay = self.wait if slow else 60.0
        if self.clear_since is None:
            self.clear_since = now
            log(f"contention cleared at {n}/{mt} threads, raise in {delay:.0f} s (remembered contended count {self.contended_max})")
        if now - self.clear_since >= delay:
            log(f"auto raise, {now - self.clear_since:.0f} s without contention: threads {n} -> {n + 1}")
            set_threads(n, n + 1)
            self.raised = True
            self.clear_since = now
            if n + 1 >= mt:
                log(f"recovery complete at 100% ({mt} threads)")
                if self.fast_rampup:
                    log("fast rampup cleared at 100%")
                self.fast_rampup = False
                self.auto_capped = False
            elif not self.fast_rampup:
                self.fast_rampup = True
                log("fast rampup set, next raise in 60 s")
