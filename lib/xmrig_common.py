#!/usr/bin/env python3
"""XMRig intensity baseline, hashrate plateau and opt-in contention control shared by Linux and Windows."""
import time
from collections import deque

def log(msg):
    print(f"xmrig_status: {msg}", flush=True)

def take_measured(label, unit, hinted, current, measured):
    if hinted and abs(measured - current) > 0.05 * current:
        log(f"measured {label} {measured:.1f} {unit} rejected, outside ±5% of hint {current:.1f} {unit}")
        return current, True
    log(f"full-intensity {label} {measured:.1f} {unit}" + (f", replaces hint {current:.1f} {unit}" if hinted else ""))
    return measured, False

class MiningIntensity:
    def __init__(self, power_hint, plateau_hint):
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

    def sample(self, now, paused, n, mt, power_w, hr10):
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
        if f >= 1:
            return power_w
        return min(power_w, f * self.baseline_w)

class Contention:
    def __init__(self, mining):
        self.mining = mining
        self.auto_capped = False
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
        self.wait = 300.0
        self.raised = False
        self.calm_since = None
        self.low_since = None
        self.override = False
        self.last_pool = None
        self.gap_since = None

    def manual(self, percent):
        self.go("tracking", time.monotonic(), f"manual slider {percent}%")
        self.auto_capped = False
        self.reset_backoff("manual slider")

    def go(self, state, now, reason):
        if state == self.state:
            return
        d = now - self.state_since
        log(f"{state}: {reason} (after {d:.0f} s {self.state})" if d < 120 else f"{state}: {reason} (after {d / 60:.0f} min {self.state})")
        self.state = state
        self.state_since = now

    def reset_backoff(self, why):
        if self.wait > 300.0:
            log(f"recovery backoff reset ({why}), wait 300 s")
        self.wait = 300.0
        self.raised = False

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

    def tick(self, now, paused, n, mt, step, power_w, hr10, set_threads, load, pool):
        m = self.mining
        if m.baseline_w <= 0 or mt <= 0 or paused or m.plateau_hr <= 0:
            return
        f = n / mt
        if self.pause(now, n, mt, hr10, pool):
            return
        rel = sum(self.recent) / len(self.recent) / m.plateau_hr
        line = (0.97 if self.contention_since is not None else 0.93) * f
        if rel < line:
            if self.state == "balanced":
                if self.gap_since is None:
                    self.gap_since = now
                if now - self.gap_since < 60.0:
                    return
            self.clear_since = None
            self.calm_since = None
            self.override = False
            if self.contention_since is None:
                self.contention_since = now
                why = f"hashrate {rel:.2f} of plateau below {line:.2f} at {n}/{mt} threads, power {power_w:.1f} W vs expected {f * m.baseline_w:.1f} W"
                if self.raised:
                    self.wait *= 2
                    why += f"; raise failed, recovery wait {self.wait:.0f} s"
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
                if now - self.calm_since >= 900.0 and (self.raised or self.wait > 300.0):
                    self.reset_backoff("15 min calm at 100%")
            return
        why = f"hashrate {rel:.2f} of plateau above {line:.2f}" if cleared else None
        if load is None:
            limit = 1.10 * f * m.baseline_w
            busy = power_w > limit
            low, seen = not busy, f"power {power_w:.1f} W vs {limit:.1f} W"
        else:
            busy, low, seen = int(load + 0.5) > n, power_w < 0.85 * f * m.baseline_w, f"5 min load {load:.2f}"
        if self.state == "balanced":
            if not busy and load is not None:
                why = f"{seen} rounds to <= {n} threads"
            elif busy and not low:
                self.low_since = None
                return
            else:
                if self.low_since is None:
                    self.low_since = now
                if now - self.low_since < 30.0:
                    return
                self.override = busy
                why = f"power {power_w:.1f} W below {0.85 * f * m.baseline_w:.1f} W for 30 s despite {seen} (I/O stall override)" if busy else f"{seen} for 30 s"
            self.low_since = None
        elif busy and not self.override:
            self.clear_since = None
            self.go("balanced", now, f"no hashrate gap at {n}/{mt} threads ({rel:.2f} vs {f:.2f}), {seen}")
            return
        delay = 60.0 if self.raised else self.wait
        if self.clear_since is None:
            self.clear_since = now
            self.go("recovering", now, f"{why or 'no contention'} at {n}/{mt} threads; raise in {delay:.0f} s")
        if now - self.clear_since >= delay:
            set_threads(n, n + 1)
            self.raised = True
            self.clear_since = now
            if n + 1 >= mt:
                log(f"auto raise after {delay:.0f} s: threads {n} -> {n + 1}")
                self.auto_capped = False
                self.go("tracking", now, f"recovery complete at 100% ({mt} threads)")
            else:
                log(f"auto raise after {delay:.0f} s: threads {n} -> {n + 1}, next raise in 60 s")
