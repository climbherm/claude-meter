"""Push loop: fetch usage, render, push, dedup, sleep."""
from __future__ import annotations

import datetime
import json
import sys
import time

from claude_meter import renderers, transports
from claude_meter.config import Config
from claude_meter.usage import RateLimited, extract, fetch_usage


def run(cfg: Config) -> None:
    if not cfg.device_host:
        raise SystemExit(
            "device_host is not set. Run `claude-meter configure "
            "--device-host <ip>` first."
        )
    renderer  = renderers.get(cfg.mode)
    transport = transports.get(cfg.transport, host=cfg.device_host, mode=cfg.mode)

    logged_once  = False
    last_key:   tuple | None = None
    last_push_ts = 0.0
    fail_streak  = 0
    alert_shown  = False

    while True:
        sleep_for = cfg.push_interval_sec
        try:
            data = fetch_usage()
            if not logged_once:
                print("API response:", json.dumps(data, indent=2), flush=True)
                logged_once = True

            five_pct, five_reset, week_pct, week_reset = extract(data)
            key = (int(round(five_pct)), int(round(week_pct)))
            now = time.time()

            if last_key == key and (now - last_push_ts) < cfg.force_push_sec:
                print(f"{_ts()} 5h {five_pct:.0f}%  7d {week_pct:.0f}%  "
                      f"unchanged, skipped", flush=True)
            else:
                payload = renderer.render(five_pct, five_reset, week_pct, week_reset)
                n = transport.push(payload)
                last_key     = key
                last_push_ts = now
                print(f"{_ts()} 5h {five_pct:.0f}%  7d {week_pct:.0f}%  "
                      f"pushed {n}B ({cfg.mode})", flush=True)
            fail_streak = 0
            alert_shown = False
            open("/Users/abramherman/.local/state/clock-heartbeats/meter", "w").write(str(time.time()))
        except KeyboardInterrupt:
            print("bye", flush=True)
            sys.exit(0)
        except RateLimited as e:
            sleep_for = max(e.retry_after, cfg.push_interval_sec)
            print(f"{_ts()} [warn] 429 rate limited, sleeping {sleep_for}s",
                  flush=True)
        except Exception as e:
            fail_streak += 1
            sleep_for = min(cfg.push_interval_sec * (2 ** (fail_streak - 1)), 600)
            print(f"{_ts()} [warn] {type(e).__name__}: {e} "
                  f"(retry in {sleep_for}s)", flush=True)
            if (not alert_shown) and cfg.mode == "photo240" and "403" in str(e):
                try:
                    transport.push(_auth_alert_frame())
                    alert_shown = True
                    print(f"{_ts()} auth-alert frame pushed to device", flush=True)
                except Exception as pe:
                    print(f"{_ts()} [warn] could not push auth alert: {pe}",
                          flush=True)

        time.sleep(sleep_for)


def _ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


def _auth_alert_frame() -> bytes:
    """240x240 frame telling the human the meter needs a fresh login."""
    import io
    from PIL import Image, ImageDraw
    from claude_meter.renderers import (COLOR_BG, COLOR_DIM, COLOR_TEXT,
                                        COLOR_YELLOW, load_font)
    img = Image.new("RGB", (240, 240), COLOR_BG)
    d = ImageDraw.Draw(img)
    d.text((12, 8), "Claude usage", font=load_font(20), fill=COLOR_TEXT)
    d.text((12, 70), "AUTH NEEDED", font=load_font(30), fill=COLOR_YELLOW)
    f = load_font(17)
    d.text((12, 120), "Sign out & back in to", font=f, fill=COLOR_TEXT)
    d.text((12, 142), "Claude Desktop, then", font=f, fill=COLOR_TEXT)
    d.text((12, 164), "run: claude-meter check", font=f, fill=COLOR_DIM)
    d.text((12, 206), "data stalled " + _ts(), font=load_font(14), fill=COLOR_DIM)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()
