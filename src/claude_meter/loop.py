"""Push loop: fetch data, render, push, dedup, sleep.

One service can drive several displays. Each device has its own mode, so a
cycle gathers whatever data the configured modes need and pushes to each
device independently:
  - usage cards (gif80/photo240) share ONE Anthropic API fetch per cycle so
    we never multiply API calls (and rate limits) by the device count;
  - the fan card (fan80) reads the local SMC on a faster cadence.
Each device keeps its own dedup state, so a slow-changing usage card and a
live fan card coexist without re-pushing each other's frames.
"""
from __future__ import annotations

import datetime
import json
import sys
import time

from claude_meter import fan, renderers, transports
from claude_meter.config import Config, Device

# Read the fan a little more often than the API default so the RPM number
# feels live; pushes still only happen when the value actually changes.
FAN_POLL_SEC = 5

_USAGE_MODES = ("gif80", "photo240")


class _DeviceState:
    def __init__(self, device: Device, transport):
        self.device = device
        self.transport = transport
        self.last_key: tuple | int | None = None
        self.last_push_ts = 0.0


def run(cfg: Config) -> None:
    devices = [d for d in cfg.devices if d.host]
    if not devices:
        raise SystemExit(
            "no devices configured. Add one with "
            "`claude-meter device add <ip> --mode <mode>`."
        )

    states = [
        _DeviceState(d, transports.get(d.transport or cfg.transport,
                                       host=d.host, mode=d.mode))
        for d in devices
    ]
    print(f"driving {len(states)} device(s): "
          + ", ".join(f"{s.device.host}={s.device.mode}" for s in states),
          flush=True)

    need_usage = any(s.device.mode in _USAGE_MODES for s in states)
    need_fan   = any(s.device.mode == "fan80" for s in states)
    base_tick  = min(cfg.push_interval_sec, FAN_POLL_SEC) if need_fan \
        else cfg.push_interval_sec

    _renderers: dict[str, object] = {}

    def renderer_for(mode: str):
        if mode not in _renderers:
            _renderers[mode] = renderers.get(mode)
        return _renderers[mode]

    usage_tuple: tuple | None = None   # (five_pct, five_reset, week_pct, week_reset)
    last_usage_ts = 0.0
    usage_retry_at = 0.0
    usage_logged = False

    while True:
        try:
            now = time.time()

            if need_usage and now >= usage_retry_at and (
                    usage_tuple is None or now - last_usage_ts >= cfg.push_interval_sec):
                usage_tuple, last_usage_ts, usage_retry_at, usage_logged = _refresh_usage(
                    cfg, now, usage_tuple, last_usage_ts, usage_retry_at, usage_logged)

            fan_reading = None
            if need_fan:
                try:
                    fan_reading = fan.read_fan()
                except Exception as e:
                    print(f"{_ts()} [warn] fan read {type(e).__name__}: {e}",
                          flush=True)

            for s in states:
                _service_device(s, cfg, now, renderer_for, usage_tuple, fan_reading)

        except KeyboardInterrupt:
            print("bye", flush=True)
            sys.exit(0)
        except Exception as e:  # never let one bad cycle kill the loop
            print(f"{_ts()} [warn] cycle {type(e).__name__}: {e}", flush=True)

        time.sleep(base_tick)


def _refresh_usage(cfg, now, usage_tuple, last_usage_ts, usage_retry_at, logged):
    from claude_meter.usage import RateLimited, extract, fetch_usage
    try:
        data = fetch_usage()
        if not logged:
            print("API response:", json.dumps(data, indent=2), flush=True)
            logged = True
        return extract(data), now, usage_retry_at, logged
    except RateLimited as e:
        wait = max(e.retry_after, cfg.push_interval_sec)
        print(f"{_ts()} [warn] 429 rate limited, usage retry in {wait}s", flush=True)
        return usage_tuple, last_usage_ts, now + wait, logged
    except Exception as e:
        print(f"{_ts()} [warn] usage fetch {type(e).__name__}: {e}", flush=True)
        return usage_tuple, last_usage_ts, now + cfg.push_interval_sec, logged


def _service_device(s: _DeviceState, cfg: Config, now: float,
                    renderer_for, usage_tuple, fan_reading) -> None:
    keyed = _key_for(s.device, usage_tuple, fan_reading)
    if keyed is None:
        return  # the data this device needs isn't available yet
    key, label = keyed

    if s.last_key == key and (now - s.last_push_ts) < cfg.force_push_sec:
        return

    try:
        frames = _frames_for(s.device, renderer_for, usage_tuple, fan_reading)
        n = s.transport.push(frames)
        s.last_key = key
        s.last_push_ts = now
        print(f"{_ts()} [{s.device.host} {s.device.mode}] {label} pushed {n}B",
              flush=True)
    except Exception as e:
        print(f"{_ts()} [{s.device.host}] [warn] {type(e).__name__}: {e}",
              flush=True)


def _key_for(device: Device, usage_tuple, fan_reading):
    if device.mode == "fan80":
        if fan_reading is None:
            return None
        # Bucket to 25 RPM so idle jitter doesn't trigger constant pushes.
        key = int(round(fan_reading.rpm / 25.0)) * 25
        return key, f"fan {fan_reading.rpm:.0f} RPM"
    if usage_tuple is None:
        return None
    five_pct, _, week_pct, _ = usage_tuple
    return (int(round(five_pct)), int(round(week_pct))), \
        f"5h {five_pct:.0f}% 7d {week_pct:.0f}%"


def _frames_for(device: Device, renderer_for, usage_tuple, fan_reading) -> list[bytes]:
    if device.mode == "fan80":
        r = fan_reading
        return renderer_for("fan80").render(r.rpm, r.min, r.max)
    return renderer_for(device.mode).render(*usage_tuple)


def _ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")
