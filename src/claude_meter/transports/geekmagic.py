"""HTTP upload to a GeeKmagic SmallTV clock.

The stock firmware accepts POST /upload with multipart field "imageFile".
The filename picks which slot to overwrite:
  - "gif.jpg"              -> main-screen Customization GIF slot. The body
                              must be the firmware's custom animated-GIF
                              container: [frame0 JPEG][2400-byte index
                              block][frame1]...[frameN-1]. Index layout
                              per 12-byte record: <u16 0x01ff> <u16 id>
                              <u32 offset> <u32 size>. Record 0's `id`
                              holds the total frame count; records 1..N-1
                              hold absolute offsets. We declare exactly as
                              many frames as we render (1 for a static card,
                              N for an N-frame loop) and the device plays
                              those slots in order. The index block itself
                              is a fixed 2400 bytes regardless of the count.
  - "file1.jpg".."file5.jpg" -> Photo-mode full-screen slots (plain JPEG).
Max 1 MB per the device's JS check.
"""
from __future__ import annotations

import struct

import requests

GIF_INDEX_SIZE = 2400


class GeekmagicTransport:
    def __init__(self, host: str, mode: str):
        """
        host: "192.168.1.50" or "http://192.168.1.50" (your clock's IP)
        mode: "gif80"/"fan80" -> writes gif.jpg with container wrap;
              "photo240" -> writes file1.jpg as-is
        """
        if not host.startswith("http"):
            host = f"http://{host}"
        self._url  = f"{host.rstrip('/')}/upload"
        self._mode = mode

    def push(self, frames: list[bytes]) -> int:
        """Send rendered frames. `frames` has one entry for a static card or
        several for an animation. Returns bytes-on-wire for logging."""
        if self._mode in ("gif80", "fan80"):
            body = _build_gif_container(frames)
            filename = "gif.jpg"
        elif self._mode == "photo240":
            body = frames[0]
            filename = "file1.jpg"
        else:
            raise ValueError(f"unsupported mode for geekmagic: {self._mode!r}")

        # The firmware often sends a truncated HTTP response after a
        # successful write — status line + headers, then it closes the
        # socket mid-body. Stream the response so we read only the
        # status and headers and never the body; otherwise a perfectly
        # good upload surfaces as a ChunkedEncodingError. timeout is
        # (connect, read-headers): the device can be slow to reply
        # while it commits the image to flash.
        resp = requests.post(
            self._url,
            files={"imageFile": (filename, body, "image/jpeg")},
            timeout=(5, 15),
            stream=True,
        )
        try:
            resp.raise_for_status()
        finally:
            resp.close()
        return len(body)


def _build_gif_container(frames: list[bytes], count: int | None = None) -> bytes:
    """Wrap rendered frames in the firmware's container format.

    `count` is how many frame slots the device is told to play; it defaults
    to one slot per rendered frame (1 for a static card, N for an N-frame
    loop). It can be set larger to repeat the frames into more slots, useful
    when experimenting with the device's playback. Byte-identical frames are
    laid down once and aliased by multiple index records, so a static card
    ships a single physical frame however many slots it declares.

    Layout: unique[0] | 2400-byte index | unique[1] | unique[2] | ...
    Every record carries the absolute offset+size of the frame its slot
    shows.
    """
    if not frames:
        raise ValueError("no frames to push")

    if count is None:
        count = len(frames)
    slots = [frames[k % len(frames)] for k in range(count)]

    # Deduplicate by bytes so repeated frames cost nothing on the wire.
    unique: list[bytes] = []
    index_of: dict[bytes, int] = {}
    slot_to_unique: list[int] = []
    for fr in slots:
        u = index_of.get(fr)
        if u is None:
            u = len(unique)
            index_of[fr] = u
            unique.append(fr)
        slot_to_unique.append(u)

    # Absolute offsets: unique[0] sits before the index block, the rest after.
    offsets = [0] * len(unique)
    pos = len(unique[0]) + GIF_INDEX_SIZE
    for i in range(1, len(unique)):
        offsets[i] = pos
        pos += len(unique[i])

    idx = bytearray(GIF_INDEX_SIZE)
    for k in range(count):
        u = slot_to_unique[k]
        # Record 0's id field carries the total frame count; later records
        # carry the slot id. Offset/size always point at the slot's frame.
        ident = count if k == 0 else k
        struct.pack_into("<HHII", idx, k * 12, 0x01ff, ident,
                         offsets[u], len(unique[u]))

    body = bytearray()
    body += unique[0]
    body += bytes(idx)
    for i in range(1, len(unique)):
        body += unique[i]
    return bytes(body)
