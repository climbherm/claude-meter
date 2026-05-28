"""Config file discovery + schema.

Stored as JSON at (in order of preference):
  $CLAUDE_METER_CONFIG                    (explicit override)
  $XDG_CONFIG_HOME/claude-meter/config.json
  ~/.config/claude-meter/config.json      (both macOS and Linux)

The service can drive several displays at once: `devices` is a list of
{host, mode} entries, each pushed independently. Older single-display
configs (a top-level `device_host` + `mode`) are migrated to a one-entry
`devices` list on load, so existing setups keep working.
"""
from __future__ import annotations

import json
import os
import pathlib
from dataclasses import asdict, dataclass, field
from typing import Optional

VALID_MODES = ("gif80", "photo240", "fan80")


@dataclass
class Device:
    host: str = ""
    mode: str = "gif80"          # gif80 | photo240 | fan80
    transport: str = ""          # "" = inherit the top-level transport


@dataclass
class Config:
    devices:     list[Device] = field(default_factory=list)
    transport:   str = "geekmagic"
    push_interval_sec: int = 60
    force_push_sec:    int = 600

    @classmethod
    def defaults(cls) -> "Config":
        return cls()


def config_path() -> pathlib.Path:
    override = os.environ.get("CLAUDE_METER_CONFIG")
    if override:
        return pathlib.Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = pathlib.Path(xdg).expanduser() if xdg else pathlib.Path.home() / ".config"
    return base / "claude-meter" / "config.json"


def _devices_from_data(data: dict) -> list[Device]:
    raw = data.get("devices")
    if isinstance(raw, list):
        out = []
        for d in raw:
            if isinstance(d, dict) and d.get("host"):
                out.append(Device(host=d["host"], mode=d.get("mode", "gif80"),
                                   transport=d.get("transport", "")))
        return out
    # Migrate the legacy single-display shape.
    host = data.get("device_host") or ""
    if host:
        return [Device(host=host, mode=data.get("mode", "gif80"))]
    return []


def load(path: Optional[pathlib.Path] = None) -> Config:
    p = path or config_path()
    if not p.exists():
        return Config.defaults()
    try:
        data = json.loads(p.read_text())
    except Exception as e:
        raise RuntimeError(f"{p}: {e}") from e

    cfg = Config.defaults()
    cfg.devices = _devices_from_data(data)
    for k in ("transport", "push_interval_sec", "force_push_sec"):
        if k in data:
            setattr(cfg, k, data[k])
    return cfg


def save(cfg: Config, path: Optional[pathlib.Path] = None) -> pathlib.Path:
    p = path or config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(cfg), indent=2) + "\n")
    return p
