"""HTTP upload to a GeeKmagic SmallTV-Ultra clock.

The Ultra runs different firmware from the original SmallTV (which uses
/upload + the animated-GIF container). Its 240x240 web UI stores an image
on the on-device filesystem, switches the display to "Photo Album" mode,
then selects the image:

  1. POST /doUpload?dir=/image/   multipart field "file" -> stores /image/<name>
  2. GET  /set?theme=3            -> switch display to Photo Album mode
  3. GET  /set?img=/image/<name>  -> show it (returns "OK")

Pairs with the photo240 renderer (a 240x240 JPEG). We always write the same
filename so repeated pushes don't fill the device's flash.

The firmware's HTTP responses are non-compliant — /doUpload returns two
conflicting Content-Length headers, which trips requests/urllib3's strict
parser. We use stdlib http.client (which takes the first Content-Length and
doesn't raise) and read only what we need.
"""
from __future__ import annotations

import http.client
import uuid
from urllib.parse import urlsplit

_DIR = "/image/"
_FILENAME = "claude.jpg"
_PHOTO_ALBUM_THEME = 3


class GeekmagicUltraTransport:
    def __init__(self, host: str, mode: str):
        if "://" not in host:
            host = f"http://{host}"
        parts = urlsplit(host)
        self._host = parts.hostname or host
        self._port = parts.port or 80
        self._mode = mode

    def _conn(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection(self._host, self._port, timeout=15)

    def _get(self, path: str) -> str:
        conn = self._conn()
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            body = resp.read()
            if resp.status != 200:
                raise RuntimeError(f"GET {path} -> HTTP {resp.status}")
            return body.decode("utf-8", "replace").strip()
        finally:
            conn.close()

    def push(self, frames: list[bytes]) -> int:
        body = frames[0]  # 240x240 JPEG from photo240
        payload = _multipart(_FILENAME, body)

        conn = self._conn()
        try:
            conn.request(
                "POST", f"/doUpload?dir={_DIR}", body=payload,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={_BOUNDARY}",
                    "Content-Length": str(len(payload)),
                },
            )
            resp = conn.getresponse()
            resp.read()  # drain (and tolerate the firmware's odd length headers)
            if resp.status != 200:
                raise RuntimeError(f"doUpload -> HTTP {resp.status}")
        finally:
            conn.close()

        # Make sure the display is in Photo Album mode, then show our image.
        self._get(f"/set?theme={_PHOTO_ALBUM_THEME}")
        sel = self._get(f"/set?img={_DIR}{_FILENAME}")
        if sel != "OK":
            raise RuntimeError(f"device rejected display: {sel[:80]!r}")
        return len(body)


_BOUNDARY = uuid.uuid4().hex


def _multipart(filename: str, content: bytes) -> bytes:
    return b"".join([
        f"--{_BOUNDARY}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
        b"Content-Type: image/jpeg\r\n\r\n",
        content,
        f"\r\n--{_BOUNDARY}--\r\n".encode(),
    ])
