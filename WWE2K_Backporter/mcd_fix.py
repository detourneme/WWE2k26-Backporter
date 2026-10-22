"""
mcd_fix.py -- the one binary-level bug that survives every export path.

Confirmed (see project doc "findings-2k26-to-2k25-format.md", 100% reproduction
rate across every addon export tested, both hand-patched and real Blender
exports): whenever the WWE2K_MDL addon (any version, including the real
Blender export path driven by blender_headless_convert.py) writes an .mcd
file, its outer "MDL!" substream's `size` field (byte offset 8, 4 bytes after
the 4-byte "MDL!" tag) is wrong -- it does not correctly predict where the
payload ends / the trailing "END!" terminator substream begins. A real,
correct WWE2K25 file (confirmed byte-for-byte against a known-good donor)
always satisfies `declared_size == total_len - 28` and ends with the exact
12-byte substream `[ver=4][size=0]["END!"]`.

Two different ways this shows up in practice (both confirmed on real files,
2026-09-20):
  1. **No terminator at all** (every pre-Blender addon export tested):
     declared_size == total_len (the file claims to be exactly its own
     current length, with no room left over for a terminator), and there is
     no "END!" chunk anywhere near the end. Fix: append the 12-byte END!
     chunk, then set declared_size = new_total_len - 28.
  2. **Terminator present, size field still wrong** (seen for the first time
     on a real Blender-headless export, Lio Rush 2026-09-20): the file
     already ends with the correct 12-byte END! substream, but
     declared_size == total_len (of the file *with* that END! chunk already
     included) instead of total_len - 28 -- i.e. the exact same underlying
     size-field bug, just co-occurring with a terminator that happens to
     already be there instead of missing. This was NOT caught by the
     original version of this function (which required END! to be *absent*
     before touching anything, on the assumption the two symptoms always
     came together) -- it shipped at least one real converted build with
     this undetected, still carrying the same wrong-size-field defect that
     was root-caused (via a real crash dump) to an
     EXCEPTION_ACCESS_VIOLATION from the game's model loader dereferencing a
     garbage pointer computed from this field. Fix: leave the existing END!
     chunk alone, just correct declared_size in place.

This is a pure post-process: run it on the .mcd file AFTER Blender (or any
other tool) has written it, regardless of how it got there.
"""

from __future__ import annotations

import struct
from pathlib import Path

_END_CHUNK = struct.pack("<II4s", 4, 0, b"END!")  # ver=4, size=0, tag="END!" -- 12 bytes


class McdFixError(Exception):
    pass


def fix_mcd_terminator(data: bytes) -> bytes:
    """
    Return a corrected copy of an .mcd file's bytes: fixes the leading
    "MDL!" substream's size field (and appends the "END!" terminator if it's
    missing entirely) -- but ONLY when the file matches one of the two known
    bug signatures described in the module docstring. Any file that matches
    neither is returned UNCHANGED, not "corrected" -- deliberately
    conservative (verified against a real, untouched WWE2K25 baseModel.mcd:
    must come back byte-identical).

    Raises McdFixError if `data` doesn't even look like an .mcd/MDL! file, or
    doesn't match a known signature (caller should fall back to leaving the
    file untouched and warning).
    """
    data = bytearray(data)

    if len(data) < 16 or data[0:4] != b"MCD!":
        raise McdFixError("bad MCD! magic")

    # Outer container: [MCD!][u32 ver][u32 size][MDL! or CTG! tag]...
    chunk_tag = bytes(data[0x0C:0x10])
    if chunk_tag != b"MDL!":
        raise McdFixError(f"unrecognized chunk layout (tag={chunk_tag!r}, expected MDL!)")

    total_len = len(data)
    declared_size = struct.unpack_from("<I", data, 0x08)[0]
    ends_with_end_chunk = bytes(data[-12:]) == _END_CHUNK

    if ends_with_end_chunk:
        correct_declared_size = total_len - 28
        if declared_size == correct_declared_size:
            return bytes(data)  # already fully correct
        # Terminator already present (signature 2 above) -- just fix the
        # size field in place, don't append a second END! chunk.
        struct.pack_into("<I", data, 0x08, correct_declared_size)
        return bytes(data)

    # No END! chunk present at all -- only touch it if it matches the
    # original, most-common signature exactly (signature 1 above).
    if declared_size != total_len:
        raise McdFixError(
            f"no END! terminator and declared size ({declared_size}) doesn't match "
            f"the known bug pattern (expected {total_len}) -- not touching it"
        )

    new_total_len = total_len + len(_END_CHUNK)
    struct.pack_into("<I", data, 0x08, new_total_len - 28)
    data += _END_CHUNK

    return bytes(data)


def fix_mcd_file(src: Path, dst: Path, log: list[str] | None = None) -> None:
    """Read src, apply fix_mcd_terminator, write to dst. Falls back to a
    plain copy (with a warning appended to `log`) if the file doesn't match
    the known layout -- never raises, so one odd file doesn't kill a whole
    conversion run."""
    raw = src.read_bytes()
    try:
        fixed = fix_mcd_terminator(raw)
        if log is not None:
            if fixed == raw:
                log.append(f"  {src.name}: terminator already correct")
            else:
                log.append(f"  {src.name}: patched MDL! size field + appended END! terminator")
        dst.write_bytes(fixed)
    except McdFixError as e:
        if log is not None:
            log.append(
                f"  WARNING: {src.name}: terminator fix skipped ({e}) -- copied "
                "as-is, known open risk area (e.g. a CTG!-layout BaseModel)"
            )
        dst.write_bytes(raw)
