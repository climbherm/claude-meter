"""claude-meter command-line interface."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from claude_meter import __version__, config, loop, service
from claude_meter.auth import AuthError, get_access_token
from claude_meter.config import VALID_MODES
from claude_meter.transports import VALID_TRANSPORTS
from claude_meter.usage import fetch_usage


def _cmd_run(_args) -> int:
    cfg = config.load()
    loop.run(cfg)
    return 0


def _cmd_configure(args) -> int:
    cfg = config.load()
    # --device-host / --mode operate on the first device (created if none) so
    # existing single-display setups and install.sh keep working. Use the
    # `device` subcommands to manage additional displays.
    if args.device_host or args.mode:
        if not cfg.devices:
            cfg.devices.append(config.Device())
        dev = cfg.devices[0]
        if args.device_host:
            dev.host = args.device_host
        if args.mode:
            dev.mode = args.mode
    if args.transport:
        cfg.transport = args.transport
    if args.push_interval is not None:
        cfg.push_interval_sec = args.push_interval
    if args.force_push is not None:
        cfg.force_push_sec = args.force_push
    p = config.save(cfg)
    print(f"wrote {p}")
    print(json.dumps(asdict(cfg), indent=2))
    return 0


def _cmd_device_add(args) -> int:
    cfg = config.load()
    transport = args.transport or ""
    for d in cfg.devices:
        if d.host == args.host:
            d.mode = args.mode
            if transport:
                d.transport = transport
            config.save(cfg)
            print(f"updated {args.host} -> mode={args.mode}"
                  + (f" transport={d.transport}" if d.transport else ""))
            return 0
    cfg.devices.append(config.Device(host=args.host, mode=args.mode, transport=transport))
    config.save(cfg)
    print(f"added {args.host} (mode={args.mode}"
          + (f", transport={transport}" if transport else "") + ")")
    return 0


def _cmd_device_list(_args) -> int:
    cfg = config.load()
    if not cfg.devices:
        print("(no devices configured)")
        return 0
    for i, d in enumerate(cfg.devices):
        tr = d.transport or f"{cfg.transport} (inherited)"
        print(f"{i}: {d.host}  mode={d.mode}  transport={tr}")
    return 0


def _cmd_device_remove(args) -> int:
    cfg = config.load()
    kept = [d for d in cfg.devices if d.host != args.host]
    if len(kept) == len(cfg.devices):
        print(f"no device with host {args.host}", file=sys.stderr)
        return 1
    cfg.devices = kept
    config.save(cfg)
    print(f"removed {args.host}")
    return 0


def _cmd_show(_args) -> int:
    cfg = config.load()
    print(f"# {config.config_path()}")
    print(json.dumps(asdict(cfg), indent=2))
    return 0


def _cmd_check(_args) -> int:
    """Verify auth + API + configured devices without looping."""
    try:
        _, org = get_access_token()
        print(f"auth:   ok (org={org})")
    except AuthError as e:
        print(f"auth:   FAIL — {e}", file=sys.stderr)
        return 2

    try:
        data = fetch_usage()
        five = (data.get("five_hour") or {}).get("utilization")
        week = (data.get("seven_day") or {}).get("utilization")
        print(f"usage:  ok (5h={five}%, 7d={week}%)")
    except Exception as e:
        print(f"usage:  FAIL — {e}", file=sys.stderr)
        return 2

    cfg = config.load()
    print(f"config: {config.config_path()}")
    if not cfg.devices:
        print("        (no devices configured — `claude-meter device add <ip>`)")
    for d in cfg.devices:
        print(f"        device={d.host} mode={d.mode} "
              f"transport={d.transport or cfg.transport}")
    print(f"        interval={cfg.push_interval_sec}s")
    return 0


def _cmd_install_service(_args) -> int:
    path = service.install()
    print(f"installed {path}")
    return 0


def _cmd_uninstall_service(_args) -> int:
    path = service.uninstall()
    if path is None:
        print("no service installed")
    else:
        print(f"removed {path}")
    return 0


def _cmd_status(_args) -> int:
    print(service.status())
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-meter",
        description="Push Claude Code usage (or fan speed) to tiny screens.",
    )
    p.add_argument("--version", action="version", version=f"claude-meter {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("run",   help="Run the push loop in the foreground").set_defaults(
        func=_cmd_run)
    sub.add_parser("check", help="Verify auth + API + config").set_defaults(
        func=_cmd_check)
    sub.add_parser("show",  help="Print the current config").set_defaults(
        func=_cmd_show)

    pc = sub.add_parser("configure", help="Update global settings (and the first device)")
    pc.add_argument("--device-host",   help="IP or hostname of the (first) clock")
    pc.add_argument("--mode",          choices=list(VALID_MODES))
    pc.add_argument("--transport",     choices=["geekmagic"])
    pc.add_argument("--push-interval", type=int, dest="push_interval",
                    help="seconds between pushes (default 60)")
    pc.add_argument("--force-push",    type=int, dest="force_push",
                    help="seconds between re-pushes of unchanged values (default 600)")
    pc.set_defaults(func=_cmd_configure)

    pd = sub.add_parser("device", help="Manage the list of displays")
    dsub = pd.add_subparsers(dest="device_cmd", required=True)
    da = dsub.add_parser("add", help="Add or update a display")
    da.add_argument("host", help="IP or hostname of the clock, e.g. 192.168.1.50")
    da.add_argument("--mode", choices=list(VALID_MODES), default="gif80")
    da.add_argument("--transport", choices=list(VALID_TRANSPORTS),
                    help="device protocol: 'geekmagic' (SmallTV) or "
                         "'geekmagic-ultra' (SmallTV-Ultra). Default: inherit global.")
    da.set_defaults(func=_cmd_device_add)
    dsub.add_parser("list", help="List configured displays").set_defaults(
        func=_cmd_device_list)
    dr = dsub.add_parser("remove", help="Remove a display by host")
    dr.add_argument("host", help="IP or hostname to remove")
    dr.set_defaults(func=_cmd_device_remove)

    sub.add_parser("install-service",
                   help="Install as launchd/systemd user service").set_defaults(
        func=_cmd_install_service)
    sub.add_parser("uninstall-service",
                   help="Remove the installed service").set_defaults(
        func=_cmd_uninstall_service)
    sub.add_parser("service-status",
                   help="Show status of the installed service").set_defaults(
        func=_cmd_status)

    return p


def main() -> None:
    args = build_parser().parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
