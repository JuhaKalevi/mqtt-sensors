#!/usr/bin/env python3
"""Minimal shared MQTT + Home Assistant discovery helpers."""
import os, json, socket, subprocess, time
from collections import deque
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
        self.paused = False
        self.pause_start = 0.0
        self.pause_reason = None
        self.pause_logged = False
        self.pause_own = False
        self.recent = deque(maxlen=30)
        self.state = "tracking"
        self.state_since = time.monotonic()
        self.contended_max = 0
        self.wait = 300.0
        self.raised = False
        self.calm_since = None
        self.balanced = False
        self.low_since = None
        self.load_exit = False
        self.last_pool = None
        self.gap_since = None

    def manual(self, percent):
        self.go("tracking", time.monotonic(), f"manual slider {percent}%")
        self.auto_capped = False
        self.balanced = False
        self.fast_rampup = False
        self.reset_backoff("manual slider")

    def go(self, state, now, reason):
        if state == self.state:
            return
        d = now - self.state_since
        log(f"{state}: {reason} (after {d:.0f} s {self.state})" if d < 120 else f"{state}: {reason} (after {d / 60:.0f} min {self.state})")
        self.state = state
        self.state_since = now

    def reset_backoff(self, why):
        if self.contended_max or self.wait > 300.0:
            log(f"recovery backoff reset ({why}), wait 300 s")
        self.contended_max = 0
        self.wait = 300.0
        self.raised = False

    def sample(self, now, paused, n, mt, step, power_w, hr10, set_threads, load, pool):
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
            self.contend(now, n, mt, step, f, power_w, hr10, set_threads, load, pool)
        if f <= 0:
            return 0.0
        if f >= 1:
            return power_w
        return min(power_w, f * self.baseline_w)

    def pause(self, now, n, mt, hr10, pool):
        reason = None
        own = False
        if n != self.last_n:
            reason, own = f"threads {self.last_n} -> {n}", True
        elif self.last_pool is not None and (pool[0] != self.last_pool[0] or pool[1] < self.last_pool[1]):
            reason = f"pool/connection change ({pool[0]})"
        elif hr10 <= 0:
            reason = "hashrate 0"
        self.last_n = n
        self.last_pool = pool
        if reason is None and not self.paused and len(self.recent) == 30:
            mean = sum(self.recent) / 30
            if max(abs(x - mean) for x in self.recent) > 0.05 * mean:
                reason = "hashrate swinging more than 5% over 30 s"
        if reason:
            if not self.paused:
                self.paused = True
                self.pause_start = now
                self.pause_logged = False
                self.pause_own = own
            self.pause_own = self.pause_own or own
            self.pause_reason = reason
            self.recent.clear()
            if not self.pause_logged and not self.pause_own and not reason.startswith("hashrate swinging"):
                self.pause_logged = True
                log(f"paused at {n}/{mt} threads: {reason}")
        if hr10 > 0:
            self.recent.append(hr10)
        if not self.paused:
            return False
        held = now - self.pause_start
        if not self.pause_logged and (not self.pause_own or hr10 <= 0) and held >= 60.0:
            self.pause_logged = True
            log(f"paused at {n}/{mt} threads: {self.pause_reason} for 60 s")
        if hr10 <= 0 or len(self.recent) < 15:
            return True
        mean = sum(self.recent) / len(self.recent)
        if max(abs(x - mean) for x in self.recent) > 0.05 * mean:
            return True
        self.paused = False
        if self.pause_logged:
            log(f"resumed after {held:.0f} s pause, hashrate {mean:.0f} H/s")
        for k in ("clear_since", "low_since", "gap_since", "contention_since", "calm_since", "state_since"):
            if getattr(self, k) is not None:
                setattr(self, k, getattr(self, k) + held)
        self.last_step += held
        if self.pause_own:
            self.contention_since = None
            self.gap_since = None
            self.last_step = now
            if self.clear_since is not None:
                self.clear_since = now
        return False

    def contend(self, now, n, mt, step, f, power_w, hr10, set_threads, load, pool):
        if self.pause(now, n, mt, hr10, pool):
            return
        rel = sum(self.recent) / len(self.recent) / self.plateau_hr
        line = (0.97 if self.contention_since is not None else 0.93) * f
        if n > 0 and (self.auto_capped or power_w >= 0.9 * f * self.baseline_w) and rel < line:
            if self.balanced:
                if self.gap_since is None:
                    self.gap_since = now
                if now - self.gap_since < 60.0:
                    return
            self.clear_since = None
            self.calm_since = None
            self.load_exit = False
            self.balanced = False
            self.fast_rampup = False
            if self.contention_since is None:
                self.contention_since = now
                why = f"hashrate {rel:.2f} of plateau below {line:.2f} at {n}/{mt} threads, power {power_w:.1f} W vs expected {f * self.baseline_w:.1f} W"
                if self.raised and n <= self.contended_max:
                    self.wait *= 2
                    self.contended_max = n
                    why += f"; raise failed, recovery wait {self.wait:.0f} s, remembered count {n}"
                elif n > self.contended_max:
                    self.contended_max = n
                    why += f"; remembered count {n}"
                self.raised = False
                self.go("contended", now, why)
            if n > 1 and now - self.contention_since >= 15.0 and now - self.last_step >= 60.0:
                target = max(1, n - step)
                log(f"auto drop, hashrate {rel:.2f} of plateau vs thread fraction {f:.2f}: threads {n} -> {target}")
                set_threads(n, target)
                self.auto_capped = True
                self.last_step = now
            return
        cleared = self.contention_since is not None
        self.contention_since = None
        self.gap_since = None
        if not self.auto_capped:
            self.go("tracking", now, f"hashrate {rel:.2f} of plateau above {line:.2f} at {n}/{mt} threads")
            if n >= mt:
                if self.calm_since is None:
                    self.calm_since = now
                if now - self.calm_since >= 900.0 and (self.contended_max or self.wait > 300.0):
                    self.reset_backoff("15 min calm at 100%")
            return
        why = f"hashrate {rel:.2f} of plateau above {line:.2f}" if cleared else None
        limit = 1.10 * f * self.baseline_w
        if self.balanced and load is not None and int(load) <= n:
            self.balanced = False
            self.load_exit = True
            self.low_since = None
            why = f"5 min load {load:.2f} (floor {int(load)}) <= {n} threads"
        if power_w > limit and not self.load_exit:
            self.low_since = None
            if not self.balanced:
                self.balanced = True
                self.clear_since = None
                self.go("balanced", now, f"no hashrate gap at {n}/{mt} threads ({rel:.2f} vs {f:.2f}), power {power_w:.1f} W above {limit:.1f} W")
            return
        if self.balanced:
            if self.low_since is None:
                self.low_since = now
            if now - self.low_since < 30.0:
                return
            self.balanced = False
            why = f"power {power_w:.1f} W at or below {limit:.1f} W for 30 s"
        slow = not self.fast_rampup or (self.wait > 300.0 and n + 1 == self.contended_max)
        delay = self.wait if slow else 60.0
        if self.clear_since is None:
            self.clear_since = now
            self.go("recovering", now, f"{why or 'no contention'} at {n}/{mt} threads; raise in {delay:.0f} s, remembered count {self.contended_max}")
        if now - self.clear_since >= delay:
            set_threads(n, n + 1)
            self.raised = True
            self.clear_since = now
            if n + 1 >= mt:
                log(f"auto raise after {delay:.0f} s: threads {n} -> {n + 1}")
                self.fast_rampup = False
                self.auto_capped = False
                self.go("tracking", now, f"recovery complete at 100% ({mt} threads)")
            else:
                self.fast_rampup = True
                nxt = self.wait if self.wait > 300.0 and n + 2 == self.contended_max else 60.0
                log(f"auto raise after {delay:.0f} s: threads {n} -> {n + 1}, next raise in {nxt:.0f} s")
