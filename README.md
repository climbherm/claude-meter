# claude-meter

At-a-glance Claude Code usage on a tiny screen. Pulls live 5-hour and
weekly percentages from Anthropic's OAuth usage endpoint and pushes
them to a [GeeKmagic SmallTV](http://www.geekmagic.cc/) clock over
your LAN.

Numbers match the Claude app's **Settings → Usage** exactly — both
come from the same `/api/oauth/usage` endpoint the desktop app uses.

Supported on macOS and Linux. Runs as a user-level background service
(launchd on macOS, systemd user unit on Linux).

---

## How it works

```
       ┌────────────────────┐
       │  Claude Code CLI   │   (you're already signed in)
       │  tokens in Keychain│   macOS: Keychain "Claude Code-credentials"
       │  or ~/.claude/…    │   Linux: ~/.claude/.credentials.json
       └─────────┬──────────┘
                 │ reused (no separate login)
                 ▼
       ┌────────────────────┐           ┌─────────────────────────┐
       │    claude-meter    │──GET──────▶ /api/oauth/usage        │
       │    push loop       │◀─────────── {five_hour, seven_day}  │
       └─────────┬──────────┘           └─────────────────────────┘
                 │ render to JPEG
                 ▼
       ┌────────────────────┐
       │   Renderer         │   gif80 (80×80)  or  photo240 (240×240)
       └─────────┬──────────┘
                 │ POST /upload
                 ▼
       ┌────────────────────┐
       │  GeeKmagic clock   │   on your Wi-Fi
       └────────────────────┘
```

Token refresh is automatic. When the access token is within 60 seconds
of expiry, claude-meter exchanges the refresh token against
`/v1/oauth/token` and writes the new pair back to the same store
(Keychain on macOS, JSON file on Linux), so the `claude` CLI stays in
sync.

---

## Requirements

- **macOS or Linux.** Windows is not supported.
- **Python 3.9+** available as `python3` on your PATH.
- **[Claude Code](https://claude.com/claude-code) CLI installed and
  signed in.** claude-meter reuses its OAuth tokens — there's no
  separate login.
- **A GeeKmagic SmallTV clock** on the same Wi-Fi network. Tested
  against v2 firmware.

Python runtime dependencies (`pillow`, `requests`) install
automatically.

---

## Install

claude-meter is not published on PyPI yet — install it from source.

```bash
git clone https://github.com/shavindraSN/claude-meter.git
cd claude-meter
./install.sh
```

[`install.sh`](install.sh) is the end-to-end setup:

1. Removes any previous install (old venv in the repo, stale service,
   old symlink).
2. Creates a clean venv at `~/.venvs/claude-meter` — outside macOS's
   TCC-protected folders, so the background service can actually read
   it.
3. Installs claude-meter into that venv (non-editable) and symlinks
   `~/.local/bin/claude-meter` to it.
4. Adds `~/.local/bin` to your PATH in `~/.zshrc` if it isn't already.
5. Prompts for the clock's IP address and writes it to the config.
6. Runs `claude-meter check` to verify auth and the API.
7. Registers the launchd / systemd service and prints its status.

Non-interactive form — pass the clock's IP as the first argument:

```bash
./install.sh 192.168.1.50
```

Override the install location or Python version with env vars:

```bash
CLAUDE_METER_VENV=~/apps/claude-meter PYTHON=python3.12 ./install.sh
```

### Why not `pip install -e .` in the source tree?

On macOS, launchd agents cannot read files under `~/Documents`,
`~/Desktop`, or `~/Downloads` without Full Disk Access. An editable
install leaves the package in the source tree, so the service fails
to start with `PermissionError: … pyvenv.cfg` in the error log.
[`install.sh`](install.sh) sidesteps this by installing into a venv
under `~/.venvs/`.

If you're doing active development and your repo lives outside those
protected folders, a standard `pip install -e .` inside a venv works
fine.

---

## Configure

The installer will prompt for the clock's IP, but you can change any
value later:

```bash
claude-meter configure \
  --device-host 192.168.1.50 \
  --mode gif80 \
  --push-interval 60 \
  --force-push 600
```

`configure` sets the global options and the *first* display. To run more
than one screen, see **Multiple displays** below.

| Flag              | Default      | Meaning |
| ----------------- | ------------ | ------- |
| `--device-host`   | *(required)* | IP or hostname of the (first) clock. |
| `--mode`          | `gif80`      | `gif80`, `photo240`, or `fan80`. See "Display modes" below. |
| `--transport`     | `geekmagic`  | `geekmagic` (SmallTV) or `geekmagic-ultra` (SmallTV-Ultra). |
| `--push-interval` | `60`         | Seconds between fetches. Below ~30s tends to trip Anthropic's rate limiter; claude-meter honors `Retry-After` automatically but lighter polling is cleaner. |
| `--force-push`    | `600`        | Re-push even when numbers are unchanged after this many seconds (keeps the display from looking stuck). |

### Multiple displays

One service can drive several clocks at once, each with its own mode and
transport. Manage the list with `device`:

```bash
# A SmallTV showing the spinning fan, and a SmallTV-Ultra showing usage:
claude-meter device add 192.168.1.125 --mode fan80
claude-meter device add 192.168.1.128 --mode photo240 --transport geekmagic-ultra
claude-meter device list
claude-meter device remove 192.168.1.128
```

Each device de-dupes independently, and usage-mode displays share a single
API fetch per cycle (so adding screens never multiplies API calls). Restart
the service after changing the device list.

Config is stored as JSON. Discovery order:

1. `$CLAUDE_METER_CONFIG`
2. `$XDG_CONFIG_HOME/claude-meter/config.json`
3. `~/.config/claude-meter/config.json` *(macOS and Linux default)*

Inspect the current values:

```bash
claude-meter show
```

### Display modes

- **`gif80`** — 80×80 JPEG that lives in the device's Customization-GIF
  slot. Shown alongside the stock clock + weather. Good for
  "ambient" display.
- **`photo240`** — full-screen 240×240 usage card with reset
  countdowns. On the original SmallTV (`geekmagic` transport), requires
  Photo mode: *Settings → Photo*, `photo-switch` **ON**, `file1-switch`
  **ON**, `file2-switch`…`file5-switch` **OFF**. On the SmallTV-Ultra
  (`geekmagic-ultra` transport), claude-meter uploads the image and
  switches the device to Photo Album mode automatically.
- **`fan80`** — 80×80 animated card showing the Mac's fan speed: a fan
  that genuinely spins on-device (spin rate scales with RPM) above the
  current RPM. Reads the SMC directly (Apple Silicon Mac required); no
  helper process. Uses the `geekmagic` transport's 33-frame container.

`gif80`/`fan80` use the firmware's custom multi-frame container;
`photo240` pushes a plain JPEG.

### Transports

- **`geekmagic`** — original SmallTV / SmallTV-Pro (`POST /upload`).
- **`geekmagic-ultra`** — SmallTV-Ultra, which uses a different
  firmware (`POST /doUpload` + Photo Album theme). Pair it with
  `photo240`.

---

## Verify

```bash
claude-meter check
```

Prints three lines:

- `auth: ok (org=…)` — Claude Code token works.
- `usage: ok (5h=N%, 7d=N%)` — API responded.
- `config: … device=… mode=… interval=…s` — loaded config.

Exit code is non-zero if any step fails.

---

## Run

As a background service (recommended, installed by `install.sh`):

```bash
claude-meter install-service      # launchd on macOS, systemd user unit on Linux
claude-meter service-status
claude-meter uninstall-service
```

In the foreground (for debugging):

```bash
claude-meter run
```

Service layout:

| Platform | Unit file | Logs |
| -------- | --------- | ---- |
| macOS    | `~/Library/LaunchAgents/com.claude-meter.plist` | `~/Library/Logs/claude-meter/claude-meter.{out,err}.log` |
| Linux    | `~/.config/systemd/user/claude-meter.service`    | `journalctl --user -u claude-meter -f` |

On macOS the agent restarts automatically (`KeepAlive=true`,
throttled to 10s); on Linux systemd is `Restart=on-failure` with a
10s backoff.
### Restart the service

If the service gets stuck (e.g. extended upstream errors) and you
want to kick it without re-registering the unit, restart it in place:

**macOS** (launchd):

```bash
launchctl kickstart -k gui/$(id -u)/com.claude-meter
```

`-k` sends SIGTERM to the running process first, then launchd
respawns it. Confirm a fresh PID with:

```bash
launchctl list | grep claude-meter
```

**Linux** (systemd user unit):

```bash
systemctl --user restart claude-meter
systemctl --user status claude-meter
```

Heavier alternative on either platform — fully re-register the unit
file (use this after editing the plist/service template):

```bash
claude-meter uninstall-service && claude-meter install-service
claude-meter service-status
```
### Watch the logs

**macOS:**

```bash
tail -f ~/Library/Logs/claude-meter/claude-meter.out.log
tail -f ~/Library/Logs/claude-meter/claude-meter.err.log
```

**Linux:**

```bash
journalctl --user -u claude-meter -f
```

---

## CLI reference

```
claude-meter [-h] [--version]
             {run,check,show,configure,device,install-service,uninstall-service,service-status}
```

| Command              | Purpose |
| -------------------- | ------- |
| `run`                | Run the push loop in the foreground. |
| `check`              | Verify auth + API + config (one-shot). |
| `show`               | Print the config file path and contents. |
| `configure`          | Update global settings + the first device (see flags above). |
| `device add/list/remove` | Manage displays for multi-screen setups. |
| `install-service`    | Install as a launchd / systemd user service. |
| `uninstall-service`  | Remove the installed service. |
| `service-status`     | Print `launchctl list` / `systemctl status` output. |
| `--version`          | Print version. |

---

## Docker

Runs inside a container if you'd rather not install on the host. You
must mount your host's Claude Code credentials in, since claude-meter
has no login of its own.

**Linux host** — credentials live in a file:

```bash
docker run --rm \
  -v ~/.claude:/root/.claude:rw \
  -v ~/.config/claude-meter:/root/.config/claude-meter \
  -w /src -v "$PWD":/src \
  python:3.12-slim bash -c "pip install . && claude-meter run"
```

The `-rw` mount is required: claude-meter writes refreshed tokens back
so the `claude` CLI on the host stays signed in.

**macOS host** — credentials live in the Keychain, which isn't
mountable into a container. Run claude-meter directly on the host
(via [`install.sh`](install.sh)), not in Docker.

---

## Supported displays

| Device | Mode(s) | Status |
| --- | --- | --- |
| GeeKmagic SmallTV (v2 firmware) | `gif80`, `photo240` | supported |

Adding another display is two files and a registration line:

- A **transport** under
  [`src/claude_meter/transports/`](src/claude_meter/transports/)
  that implements `push(payload: bytes) -> int`.
- Optionally a new **renderer** under
  [`src/claude_meter/renderers/`](src/claude_meter/renderers/) if the
  existing JPEG sizes don't fit.
- Register the names in the `get(...)` factory of the respective
  `__init__.py`.

---

## Privacy

claude-meter talks to exactly two places:

- `api.anthropic.com` — for the usage endpoint and token refresh, using
  *your* Claude Code OAuth tokens.
- The clock's IP on your LAN — for the JPEG upload.

No telemetry, no analytics, no third-party services, no phone-home.
Tokens never leave your machine except to Anthropic.

---

## Troubleshooting

**`auth: FAIL — Claude Code credentials not found`**
Run `claude` and sign in, then retry `claude-meter check`.

**`[warn] 429 rate limited, sleeping Ns` in logs**
Anthropic rate-limited the usage endpoint. claude-meter honors the
`Retry-After` header automatically, so occasional 429s are harmless.
If they're frequent, raise `--push-interval` (default is 60s).

**Service fails to start on macOS with `PermissionError: … pyvenv.cfg`**
Your install lives under `~/Documents`, `~/Desktop`, or `~/Downloads`.
macOS TCC blocks launchd agents from those folders. Re-run
[`install.sh`](install.sh) — it creates the venv under `~/.venvs/`
and symlinks the CLI into `~/.local/bin`, both outside TCC control.

**`LastExitStatus = 256` in `service-status`**
The service crashed and launchd is waiting to restart it. Check the
error log:
```bash
tail -n 50 ~/Library/Logs/claude-meter/claude-meter.err.log
```
Most common cause is `device_host is not set` — run
`claude-meter configure --device-host <IP>` and reinstall the
service.

**Clock shows the old image, byte count looks right**
GeeKmagic's firmware silently rejects malformed uploads with HTTP 200
but keeps the previous content on screen. Make sure Photo mode is
configured correctly for `photo240` (see *Display modes* above).

**Clock IP changed / I moved networks**
```bash
claude-meter configure --device-host <new IP>
claude-meter uninstall-service && claude-meter install-service
```
The restart is needed because the loop loads config once at startup.

---

## Development

The source tree:

```
src/claude_meter/
├── __main__.py       # `python -m claude_meter`
├── cli.py            # argparse + subcommand dispatch
├── config.py         # Config dataclass + JSON load/save
├── auth.py           # Keychain/file read, OAuth refresh
├── usage.py          # /api/oauth/usage + RateLimited handling
├── loop.py           # fetch → render → push → dedup → sleep
├── service.py        # install/uninstall/status for launchd & systemd
├── renderers/
│   ├── gif80.py      # 80×80 JPEG with firmware-specific qtables
│   └── photo240.py   # 240×240 JPEG
├── transports/
│   └── geekmagic.py  # multipart POST /upload, gif-container wrap
└── services/
    ├── launchd.plist.template
    └── systemd.service.template
```

For local iteration (repo checked out somewhere not under
`~/Documents` on macOS):

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
claude-meter check
```

After code changes, re-run `./install.sh` to reinstall the
background service from the updated source.

---

## License

MIT. See [LICENSE](LICENSE).
