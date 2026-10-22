"""
converter.py — WWE2K26 -> WWE2K25 custom character backport core logic.

This module knows nothing about the GUI. It takes a source folder (a WWE2K26
custom character mod, as exported from the game / CAW tools) plus the target
WWE2K25 roster slot the character should be installed over, and produces a
new, restructured folder laid out the way WWE2K25 expects.

RULES ENCODED HERE were derived empirically by diffing 5 paired WWE2K25/WWE2K26
example mods (see the project doc "findings-2k26-to-2k25-format.md" for the
full writeup). They are a best-effort first pass, not confirmed against the
actual game — expect to refine these as more examples / in-game test results
come in.

Confirmed rules:
  * Top-level folder: WWE2K26 uses "<charname>_A0" (freeform custom slot).
    WWE2K25 uses "<slotID>_<RosterName>" (overwrites an existing roster slot).
  * Attire folder: the SOURCE folder name is not a fixed pattern -- the only
    signal is that "attire" appears somewhere in the folder name (case-
    insensitive; confirmed by detourne, 2026-09-20, after a real source used a
    name other than "_Alt_Attire" and was silently skipped -- see
    find_attire_folders). WWE2K26 examples seen so far mostly use
    "<attireID>_Alt_Attire", but that's just the common case, not a rule.
    The OUTPUT folder is always "<slotID>_Default_Attire" for the primary/
    first attire found. Additional attires (if present) keep the
    "_Alt_Attire" suffix with a user-assigned ID (WWE2K25 secondary-attire
    naming has not been confirmed from an example).
  * File naming inside an attire folder, all prefixed with the new ID:
      <id>_Attire.mcd   (capitalized "Attire"; contents are patched -- see the
                         "_mcd game-version tag" section below, this is NOT a
                         plain copy)
      <id>_attire.mtls  (LOWERCASE "attire" -- WWE2K25-only convention,
                         WWE2K26 uses capitalized "Attire.mtls")
      <id>_Attire.hpl   (capitalized; masks parts of the BaseModel mesh so
                         they don't clip through attire geometry -- NOT
                         boilerplate, despite what this comment used to say.
                         Two real WWE2K25 examples (Athena, Manami Toyota)
                         were found to have wildly different .hpl content
                         (32276 bytes vs. 12 bytes), disproving that. When
                         the WWE2K26 source has its own .hpl, we copy+rename
                         it as before. When it doesn't (common -- 2K26
                         sources don't appear to carry this data at all), we
                         now write a genuine, well-formed EMPTY mask (see
                         _EMPTY_HPL_BYTES) rather than the old bundled
                         templates/default_attire.hpl fallback, which was
                         CONFIRMED WRONG 2026-10-21: parsing it against the
                         Blender addon's own export code (masking_hpl.py)
                         showed it has the wrong LOD-level count (2, not the
                         5 the real exporter always writes) and its one MESH
                         entry is named "M_NULL" -- not a name the exporter
                         can ever produce, so it matches no real in-game
                         mesh and was never doing any real masking for
                         anyone who hit this fallback. A real, working .hpl
                         is built per-character by hand in Blender: import
                         both the BaseModel and Attire .mcd files, add
                         "HPL_Ring" and "HPL_Entrance" vertex groups to the
                         body:skinned (and head:skinned, if needed) objects,
                         assign the vertices that should be hidden under
                         attire in each state (Ring = stripped-down in-ring
                         look, Entrance = full entrance look -- areas worn
                         in both get assigned to both groups), then
                         File > Export > "WWE 2K HPL (.hpl)" with both
                         objects selected. Confirmed working in-game for
                         Kevin Knight 2026-10-21. Drop the resulting file
                         into the 2K26 source attire folder (named with
                         "attire" in it) before converting, and the existing
                         "found a real source .hpl" path above picks it up
                         automatically -- no code change needed for that
                         case. See the project doc's HPL findings for the
                         full writeup.)
      <id>_Attire.ycl   (capitalized; only present when the source has one --
                         dynamic hair/cloth attires only)
      Textures/*.dds    (copied through unchanged, filenames unchanged)
  * Materials/*.jmtl (WWE2K26-only folder, both under an attire and under
    BaseModel) is DROPPED. No WWE2K25 example ever has this folder.
  * BaseModel folder: baseModel.mcd / baseModel.mtls / Textures copied through
    (mcd and mtls contents are patched -- see version-tag sections below).
    NOTE: the apparent "missing eye_overlay slot" in WWE2K26 BaseModel.mtls
    files is NOT actually WWE2K26-specific -- see the ".mtls MTL! chunk
    version tag" section below. It turns out WWE2K26 still writes an
    eye-overlay MTL! record, just a near-empty "hollow" placeholder one when
    a character doesn't use the effect (confirmed via CakeView's own material
    editor internals, which have dedicated "hollow"/"slot removed" handling).
    This is expected to resolve itself once the real bug -- an unpatched
    per-material version tag, exactly analogous to the .mcd issue -- is fixed.

Everything here is deliberately written as small, separately-callable
functions so the rules can be corrected individually as we learn more.
"""

from __future__ import annotations

import dataclasses
import re
import shutil
import struct
import sys
from pathlib import Path

from mcd_fix import McdFixError, fix_mcd_file, fix_mcd_terminator

# See blender_runner.py's matching _APP_DIR comment (added 2026-10-13 for the
# standalone-.exe build) -- sys._MEIPASS is PyInstaller's extraction dir for
# --add-data files when frozen; falls back to this file's real directory
# when running from source, unchanged from before.
_APP_DIR = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent)
_TEMPLATES_DIR = _APP_DIR / "templates"

# Genuine "no mask" .hpl payload -- byte-for-byte what the Blender addon's
# own exporter (masking_hpl.py's getBinaryHPL) writes when no mesh in the
# selection contributes any masked vertices. Used whenever a converted
# attire has no real .hpl to work with (see the file_rules loop in
# _convert_attire). This REPLACES the old fallback of copying the bundled
# templates/default_attire.hpl -- confirmed 2026-10-21, by parsing that file
# against masking_hpl.py's own format, to be a malformed/bogus file (wrong
# LOD-level count, and its one mesh entry is named "M_NULL", which the real
# exporter never produces and which matches no real in-game mesh) that was
# never providing real masking for anyone. An honest empty mask is a known,
# correct, intentional "no masking" state -- not a fix for clipping, but no
# longer actively misleading either. See the file-rules .hpl comment above
# for the real fix (a hand-built .hpl dropped into the source attire
# folder).
_EMPTY_HPL_BYTES = b"HPL!" + b"END!" + b"\x00\x00\x00\x00"

# Bundled, verbatim copy of a real, unmodified WWE2K25 baseModel.mtls (from
# 312_CM_Punk / Blake Christian's WWE2K25 reference folder). Confirmed
# (detourne, 2026-09-19, "the basemodel materials never change, so we can use
# a generic [file]") that BaseModel materials are character-agnostic --
# every real WWE2K25 example references the same fixed set of generic
# texture names (head_color, body_color, etc.), so a single known-good real
# file works as the BaseModel .mtls for ANY converted character. This is
# used in preference to patching the source's own (2K26) baseModel.mtls,
# which has repeatedly turned out to carry real defects when regenerated
# by the addon (wrong shader type on eye/head2/mouthbag records) or when
# taken raw (hollow/near-empty placeholder records on at least one
# character seen so far).
_GENERIC_BASEMODEL_MTLS = _TEMPLATES_DIR / "generic_2k25_basemodel" / "baseModel.mtls"

# CORRECTED 2026-10-16 (see the project doc's 2026-10-16 entry, Hazuki/645
# testing): this bundled file's own "eyelash" chunk had regressed to shader
# subtype "Normal" (same subtype as "eye_overlay") instead of the correct,
# dedicated "Eyelash" subtype -- confirmed by direct comparison against
# detourne's own already-working, in-game-confirmed Yota Tsuji BaseModel
# (which still had "Eyelash"), and confirmed to be the cause of a real,
# in-game "eyelash renders bright green" bug on a freshly-converted
# character. Fixed in place by wholesale-cloning that known-good chunk
# (both are otherwise structurally equivalent -- same 5 properties, same
# empty texture table, just reordered -- so no texture-prefix rename or
# pointer relocation was needed, a plain chunk swap). How/when this file's
# own eyelash chunk regressed to the wrong subtype isn't known; nothing in
# this project intentionally edited this bundled template before now.

# --- .mcd game-version tag -------------------------------------------------
#
# Confirmed by inspecting the community WWE2K25 Blender importer/exporter
# addon's own source (Link/interface_save.py), which defines:
#   COMPILE_TARGETS_MAP = {"option_vcexport_25": 0x29, "option_vcexport_24": 0x28}
# ("WWE 2K25 MCD v2.9" / "WWE 2K24 MDL v2.8"). Every .mcd file whose chunk
# tag (at byte offset 0x0C) is "MDL!" has a little-endian 4-byte version int
# at byte offset 0x14, right after a second "MDL!" marker. Checked against
# multiple real paired examples:
#   WWE2K25 attire.mcd / baseModel.mcd -> 0x29
#   WWE2K26 attire.mcd / baseModel.mcd -> 0x2f
# 0x2f isn't a version this WWE2K25-era tooling (or, evidently, the game
# itself) recognizes -- copying the file through unpatched is the most
# likely reason a v1 backport crashed the game and failed to import in
# Blender. We patch this single field to WWE2K25's 0x29 on the way out.
#
# NOTE: some WWE2K26 BaseModel.mcd files use a *different* chunk tag,
# "CTG!" instead of "MDL!" (seen on more complex base models -- e.g. ones
# with more blendshape/morph categories). We have NOT yet located a version
# tag in that variant, so those are left unpatched and flagged in the log
# rather than guessed at.
_MCD_MDL_CHUNK_TAG = b"MDL!"
_MCD_VERSION_OFFSET = 0x14
_MCD_VERSION_2K25 = 0x29


def _patch_mcd_version_for_2k25(src: Path, dst: Path, log: list[str]) -> None:
    """
    Copy an .mcd file from src to dst, patching its embedded game-version tag
    to WWE2K25 (0x29) if it's the recognized "MDL!" chunk layout, and applying
    the terminator fix (see mcd_fix.py) unconditionally -- every real addon
    export, this raw-copy path included, has been found missing it. Falls
    back to a plain copy (with a log warning) for any other layout, since we
    don't yet know where/whether that layout carries an equivalent tag.

    This is the FALLBACK / byte-patch-only path: it does NOT rebind the mesh
    onto a real WWE2K25 armature, so it inherits every limitation that
    motivated the Blender-driven path in _place_blender_exported_mcd below
    (crashes/distortion on characters whose meshes are bound to the wrong-
    version skeleton). Used when no Blender export was produced for this
    file (Blender integration skipped or failed for this run).
    """
    data = bytearray(src.read_bytes())

    if len(data) < _MCD_VERSION_OFFSET + 4 or data[0:4] != b"MCD!":
        log.append(f"  WARNING: {src.name} doesn't look like an .mcd file (bad magic) -- copied unpatched")
        dst.write_bytes(bytes(data))
        return

    chunk_tag = bytes(data[0x0C:0x10])
    if chunk_tag != _MCD_MDL_CHUNK_TAG:
        log.append(
            f"  *** WARNING: {src.name} uses chunk type {chunk_tag!r}, not the known "
            f"{_MCD_MDL_CHUNK_TAG!r} layout. This byte-patch path has NO way to convert "
            "it, so it was copied 100% UNCHANGED -- still a raw WWE2K26 file, still "
            "tagged as WWE2K26 internally. If this is a BaseModel file, this is very "
            "likely enough on its own to CRASH THE GAME on load -- confirmed on a real "
            "test (Lio Rush, 2026-09-20). The Blender path (see the skeleton-conversion "
            "checkbox) goes through the addon's real model loader instead of raw byte "
            "patching and should handle this layout correctly -- if you're seeing this "
            "warning, check the Blender pass actually succeeded (look for "
            "'[blender] ... exported ->' lines above, not a 'Blender pass failed' "
            "warning) rather than trying to use this file as-is."
        )
        dst.write_bytes(bytes(data))
        return

    original = int.from_bytes(data[_MCD_VERSION_OFFSET:_MCD_VERSION_OFFSET + 4], "little")
    data[_MCD_VERSION_OFFSET:_MCD_VERSION_OFFSET + 4] = _MCD_VERSION_2K25.to_bytes(4, "little")

    version_note = (
        f"version tag already 0x{_MCD_VERSION_2K25:02x}"
        if original == _MCD_VERSION_2K25
        else f"patched version tag 0x{original:02x} -> 0x{_MCD_VERSION_2K25:02x}"
    )

    try:
        fixed = fix_mcd_terminator(bytes(data))
        dst.write_bytes(fixed)
        log.append(f"  {src.name} -> {dst.name} ({version_note}; terminator fixed)")
    except McdFixError as e:
        dst.write_bytes(bytes(data))
        log.append(f"  {src.name} -> {dst.name} ({version_note}; terminator fix skipped: {e})")


def _place_blender_exported_mcd(src: Path, dst: Path, log: list[str]) -> None:
    """
    Copy an .mcd file that was already produced by blender_headless_convert.py
    (already armature-redirected onto a real WWE2K25 donor skeleton, already
    exported with compile_target=2K25 so its version tag is correct) to dst,
    applying only the terminator fix -- the one bug that survives even a real
    Blender export.
    """
    fix_mcd_file(src, dst, log)


# --- .mtls per-material game-version tag ------------------------------------
#
# Found by static inspection (strings + hex diff) of CakeView v1.92's bundled
# Material Editor (an Avalonia/.NET tool with real 2K26 support, unlike the
# older standalone Material Editor). Its embedded UI strings spell out the
# format directly:
#   "MTLs V6 + MTL! V3 (2K25)"
#   "MTLs V6 + MTL! V4 (2K26)"
#   "Unsupported MTL! version: " / "Unsupported MTLs version: "
# So the outer container ("MTLs", version 6) is shared by both games, but
# each individual material record inside it -- its own little "MTL!" chunk,
# with a magic + 4-byte little-endian version + 4-byte size header -- carries
# its own version tag that DOES differ: 3 for WWE2K25, 4 for WWE2K26. Verified
# against all 10 real baseModel.mtls examples (5 characters x 2 games): every
# single "MTL!" record in every WWE2K25 baseModel.mtls is version 3; every one
# in WWE2K26 is version 4, with no exceptions. Attire .mtls files, by
# contrast, were version 3 in *both* games in every example -- WWE2K26
# attire materials just don't use the newer per-record format, so patching is
# a safe no-op there.
#
# This is almost certainly the actual cause of "couldn't open baseModel.mtls
# in the Material Editor" reported after the v1.1 .mcd-only fix: the tool
# hard-rejects unrecognized MTL! versions per the strings above. It also
# explains the "missing eye_overlay slot": WWE2K26 doesn't drop the slot, it
# writes a tiny near-empty "hollow" MTL! record for it when unused (CakeView's
# Material Editor has explicit "WriteHollowMtls"/"slot removed" handling for
# exactly this) -- once the version tag is fixed, WWE2K25 should be able to
# read that hollow record as a normal (if blank) eye_overlay material rather
# than choking on an unrecognized version before ever getting that far.
_MTLS_MAGIC = b"MTLs"
_MTL_CHUNK_TAG = b"MTL!"
_MTL_VERSION_2K26 = 4
_MTL_VERSION_2K25 = 3


def _patch_mtls_version_for_2k25(src: Path, dst: Path, log: list[str]) -> None:
    """
    Copy an .mtls file from src to dst, patching the version field of every
    embedded "MTL!" material record from WWE2K26's 4 to WWE2K25's 3. Records
    already at 3 (as seen in every attire.mtls example) are left untouched.
    The outer "MTLs" container header/version is not touched -- it's "V6" in
    every example from both games.
    """
    data = bytearray(src.read_bytes())

    if len(data) < 4 or data[0:4] != _MTLS_MAGIC:
        log.append(f"  WARNING: {src.name} doesn't look like an .mtls file (bad magic) -- copied unpatched")
        dst.write_bytes(bytes(data))
        return

    patched = 0
    already_ok = 0
    other_versions = set()
    record_offsets: list[int] = []
    idx = 0
    while True:
        i = data.find(_MTL_CHUNK_TAG, idx)
        if i == -1:
            break
        ver_off = i + 4
        if ver_off + 4 > len(data):
            break
        record_offsets.append(i)
        version = int.from_bytes(data[ver_off:ver_off + 4], "little")
        if version == _MTL_VERSION_2K26:
            data[ver_off:ver_off + 4] = _MTL_VERSION_2K25.to_bytes(4, "little")
            patched += 1
        elif version == _MTL_VERSION_2K25:
            already_ok += 1
        else:
            other_versions.add(version)
        idx = i + 4

    dst.write_bytes(bytes(data))

    if patched:
        log.append(
            f"  {src.name} -> {dst.name} (patched {patched} MTL! record(s) "
            f"v{_MTL_VERSION_2K26} -> v{_MTL_VERSION_2K25}"
            + (f", {already_ok} already v{_MTL_VERSION_2K25}" if already_ok else "")
            + ")"
        )
    else:
        log.append(f"  {src.name} -> {dst.name} ({already_ok} MTL! record(s), already v{_MTL_VERSION_2K25})")
    if other_versions:
        log.append(
            f"  WARNING: {src.name} has MTL! record(s) with unrecognized version(s) "
            f"{sorted(other_versions)} -- left unpatched, unknown risk"
        )

    if _mtls_looks_hollow(len(data), record_offsets):
        log.append(
            f"  WARNING: {src.name} looks like a HOLLOW/placeholder materials file "
            f"(avg. ~{len(data) // max(1, len(record_offsets))} bytes/record across "
            f"{len(record_offsets)} record(s) -- real attire .mtls files run "
            "hundreds to thousands of bytes per record). Confirmed real-world cause: "
            "one raw 2K26 source (Athena) shipped a near-empty attire.mtls with no "
            "actual texture data -- a real character (Lio Rush) with this exact defect "
            "loaded fine in-game but showed NO attire at all. This should have been "
            "auto-replaced by a Blender-generated .mtls (see 'Generate MTLs' above) -- "
            "seeing this warning means the Blender pass didn't run for this attire "
            "(check the log above for why) and this file was used as a last resort."
        )


# Real attire .mtls files run 6800-9900+ bytes for 7-10 records (hundreds to
# thousands of bytes each, carrying real shader parameters/texture refs).
# The one confirmed hollow/placeholder file seen so far (a raw 2K26 source,
# Athena) was 640 bytes for 10 near-empty records (~44 bytes each) -- still
# tagged version 4 like a normal file, so the version-tag check alone can't
# catch it. This threshold sits well below every real example and well
# above the one confirmed hollow example.
_MTLS_HOLLOW_BYTES_PER_RECORD_THRESHOLD = 150


def _mtls_looks_hollow(total_bytes: int, record_offsets: list[int]) -> bool:
    if not record_offsets:
        return False
    avg = total_bytes / len(record_offsets)
    return avg < _MTLS_HOLLOW_BYTES_PER_RECORD_THRESHOLD


def _warn_if_mtls_hollow(path: Path, log: list[str]) -> None:
    """Same hollow-file heuristic as _patch_mtls_version_for_2k25, applied to
    an already-written .mtls file (e.g. one generated by the Blender pass)
    as a safety check -- generation isn't guaranteed to always produce a
    fully populated file (e.g. if it turns out to depend on textures being
    loaded, which the headless import pass currently skips for speed)."""
    data = path.read_bytes()
    if len(data) < 4 or data[0:4] != _MTLS_MAGIC:
        return
    record_offsets: list[int] = []
    idx = 0
    while True:
        i = data.find(_MTL_CHUNK_TAG, idx)
        if i == -1:
            break
        record_offsets.append(i)
        idx = i + 4
    if _mtls_looks_hollow(len(data), record_offsets):
        log.append(
            f"  WARNING: {path.name} (Blender-generated) STILL looks like a "
            f"HOLLOW/placeholder materials file (avg. ~{len(data) // max(1, len(record_offsets))} "
            f"bytes/record across {len(record_offsets)} record(s)) -- the automatic "
            "'Generate MTLs' fix didn't produce a fully populated file either. "
            "This attire's materials may still not render correctly in-game."
        )


def _place_generic_basemodel_mtls(dst: Path, log: list[str]) -> None:
    """
    Copy the bundled, verbatim real WWE2K25 baseModel.mtls (see
    _GENERIC_BASEMODEL_MTLS above) to dst, unmodified. This is the preferred
    source for BaseModel materials -- see the comment on
    _GENERIC_BASEMODEL_MTLS for why.
    """
    if not _GENERIC_BASEMODEL_MTLS.is_file():
        log.append(
            f"  WARNING: bundled generic BaseModel .mtls template not found at "
            f"{_GENERIC_BASEMODEL_MTLS} -- falling back to patching the source's "
            "own baseModel.mtls, which is known to sometimes carry wrong-shader-"
            "type or hollow-record defects."
        )
        return
    shutil.copy2(_GENERIC_BASEMODEL_MTLS, dst)
    log.append(
        f"  -> {dst.name} (bundled generic real WWE2K25 baseModel.mtls template, "
        "verbatim -- BaseModel materials are character-agnostic)"
    )


_MTL_SCHEMA_TERMINATOR = b"\x00\x00"


def _split_mtls_slots(data: bytes) -> tuple[list[str], dict[str, bytes], bytes]:
    """
    Parses a whole .mtls file's outer container: magic + slot_count(u32) +
    name_table_len(u32) + name_table (null-terminated slot names,
    concatenated) + an extra, not-yet-understood per-slot binding/offset
    table of variable size (confirmed present 2026-10-02 -- e.g. 64 bytes
    on a real 6-slot file, containing what look like ascending uint16s;
    NOT simply "name_table_len more bytes", and NOT safe to assume absent)
    + one "MTL!" chunk per slot, back to back, in the SAME order as the
    name table. Slot identity is purely positional/outer-table-driven --
    an "MTL!" chunk itself carries no name of its own, so chunk bytes can
    be freely matched up with whichever slot name sits at that position in
    the name table.

    Returns (slot_names_in_order, {slot_name: chunk_bytes}, header_bytes)
    where header_bytes is EVERYTHING before the first "MTL!" chunk, taken
    verbatim (magic + slot_count + name table + that extra table, whatever
    it is) -- found empirically by locating the first real "MTL!" chunk
    rather than computed from the known-header fields, specifically so an
    unaccounted-for region like this can never be silently dropped. A
    whole-file reconstruction MUST use this header_bytes verbatim, never
    12 + name_table_len -- a first version of this function got that
    wrong, silently truncated 64 real bytes, and was only caught because
    the whole-file byte count was checked against the original before
    anything was shipped. Raises ConversionError on anything that doesn't
    look like a valid .mtls file.
    """
    if len(data) < 12 or data[0:4] != _MTLS_MAGIC:
        raise ConversionError("not a valid .mtls file (bad magic)")
    # NOTE: the header's slot_count field does NOT reliably equal the name
    # table's entry count -- confirmed on a real, working, shipped WWE2K25
    # file (1307_Athena's own baseModel.mtls: header says 6, but the name
    # table lists 9 real materials with 9 real MTL! chunks -- eye_caruncle/
    # eye_overlay/eye_r are apparently unused-but-present library entries).
    # So this parses purely from the name table + the actual "MTL!" chunks
    # found, and never trusts slot_count for anything.
    name_table_len = int.from_bytes(data[8:12], "little")
    name_table = data[12:12 + name_table_len]
    names = [n.decode("latin1") for n in name_table.split(b"\x00") if n]
    positions = []
    idx = 12 + name_table_len
    while True:
        i = data.find(_MTL_CHUNK_TAG, idx)
        if i == -1:
            break
        positions.append(i)
        idx = i + 4
    if len(positions) != len(names):
        raise ConversionError(
            f".mtls name table has {len(names)} slot name(s) but {len(positions)} "
            "'MTL!' chunk(s) were found -- refusing to guess which belongs to which"
        )
    header_bytes = data[:positions[0]] if positions else data
    chunks = {}
    for i, name in enumerate(names):
        start = positions[i]
        end = positions[i + 1] if i + 1 < len(positions) else len(data)
        chunks[name] = data[start:end]
    return names, chunks, header_bytes


def _parse_mtl_chunk_schema(chunk: bytes) -> tuple[str, str, list[str]]:
    """
    Parses one "MTL!" chunk's shader-type string, subtype string, and its
    property-name schema list (a flat, null-terminated list of property
    names -- order and count vary per character/material, never assume a
    fixed universal layout -- terminated by a double-null).
    """
    if chunk[:4] != _MTL_CHUNK_TAG:
        raise ConversionError("not a valid MTL! chunk (bad magic)")

    def read_str(o: int) -> tuple[str, int]:
        e = chunk.index(b"\x00", o)
        return chunk[o:e].decode("latin1"), e + 1

    off = 12  # "MTL!" + 2 unidentified uint32 header fields (version, length)
    shader, off = read_str(off)
    subtype, off = read_str(off)
    end = chunk.find(_MTL_SCHEMA_TERMINATOR, off)
    if end == -1:
        raise ConversionError("couldn't find MTL! chunk's schema name-list terminator")
    names = [n.decode("latin1") for n in chunk[off:end].split(b"\x00") if n]
    return shader, subtype, names


def _mtl_chunk_value_blob_start(chunk: bytes, schema_names: list[str]) -> int:
    """
    Per-property values are stored as a flat array of 4-byte slots, one per
    schema property, IN SCHEMA ORDER, occupying exactly the last
    4*len(schema_names) bytes of the chunk (texture-reference properties,
    named "<x>@ref", store a 4-byte in-chunk byte offset into a separate
    small per-character texture-name table rather than a real value -- see
    _patch_mtl_tone_values, which skips those on purpose).

    Confirmed 2026-10-02 by anchoring real, human-read values (g_AOcolorR/
    G/B, read directly off MaterialEditor's own UI) to their exact byte
    positions in two independent real files (a real donor's own correct
    material, and detourne's own MaterialEditor-edited one) -- in both files,
    byte_position - schema_index*4 came out to the exact same constant for
    all 3 properties checked, and that constant equals len(chunk) -
    4*len(schema_names) exactly. See the project doc's 2026-10-02 entries
    for the full derivation. Not yet confirmed for property types other
    than plain 4-byte floats/ints (bools, if this format has narrower ones,
    aren't ruled out) -- which is exactly why the patch function below
    only ever does a raw 4-byte-for-4-byte copy and never interprets or
    resizes a value.
    """
    start = len(chunk) - 4 * len(schema_names)
    if start < 0:
        raise ConversionError(
            f"MTL! chunk ({len(chunk)} bytes) is too short for its own "
            f"schema ({len(schema_names)} properties, needs >= {4 * len(schema_names)} bytes) "
            "-- the flat-array assumption doesn't hold here, refusing to patch"
        )
    return start


def patch_mtl_tone_values(
    dst_chunk: bytes,
    ref_chunk: bytes,
    log: list[str],
    slot_label: str = "",
) -> bytes:
    """
    Returns a modified copy of dst_chunk with its numeric (non-texture-
    reference) material properties overwritten by ref_chunk's values for
    every property name the two chunks have in common -- intended use:
    dst_chunk is a material detourne already switched to the right shader
    type/subtype in MaterialEditor.exe (so its schema, texture references,
    and overall structure are already correct), and ref_chunk is a real,
    known-correct donor's own material of the SAME shader type (e.g. a
    real WWE2K25 character's own "head"/yCh_head material) -- the fix for
    the 2026-10-02 "skin tone got worse after switching shader type"
    finding: MaterialEditor's shader-type switch adds the right property
    SCHEMA but fills new properties with generic/default values, not this
    character's real art-authored tone data.

    Deliberately conservative:
      * Shader type AND subtype must match exactly between dst and ref --
        raises ConversionError otherwise (never patches mismatched shaders).
      * Properties ending in "@ref" are NEVER touched -- those store an
        in-chunk byte offset into each file's own, differently-laid-out
        texture-name table, not a real value; copying one across files
        would point at garbage.
      * CONFIRMED THE HARD WAY (2026-10-02): a property's base name having
        no "@ref" suffix does NOT mean it's a safe plain scalar. Every
        property that has a "<name>@ref" sibling elsewhere in the SAME
        schema (e.g. "colorMap" alongside "colorMap@ref") is ALSO a
        texture-slot pointer, not a real value -- it stores its own
        in-chunk byte offset too, in the ~1300-1950 range for a ~3KB
        chunk, which happens to bit-decode as a denormalized float so
        close to zero that printing it with 4 decimals shows "0.0000" on
        both sides, making a corrupted patch look like a no-op in the log.
        A first version of this function didn't know this and blindly
        copied all 10 of head2's texture-slot-name properties from the
        donor's completely different byte layout -- result, confirmed
        in-game: the head rendered solid black with every texture value
        blank. Never repeat that: any property with an "@ref" sibling in
        EITHER schema is skipped, same as the "@ref" property itself.
      * Belt-and-suspenders beyond the naming rule above: any property
        whose reference-side raw bytes are nonzero but decode as a float
        with |value| < 1e-30 (i.e. a denormalized near-zero -- the exact
        signature of "this is actually a small integer/offset, not a
        tuned value") is also skipped and logged loudly, even if it has
        no detected "@ref" sibling. This is the safety net for a shader
        type with the same kind of hidden pointer field under some other
        naming convention we haven't seen yet.
      * Only properties present in BOTH chunks' own schema lists are
        touched, each independently located via the dst's own and ref's
        own schema (property order is not assumed to match between files).
      * Chunk length and schema/structure are never changed -- this is a
        bounded, same-length, in-place 4-byte-for-4-byte overwrite only.
      * Every property patched (with old/new value, interpreted as both
        float and raw hex for a human to sanity-check) is logged; so is
        every property skipped, and why.
    """
    dst_shader, dst_subtype, dst_schema = _parse_mtl_chunk_schema(dst_chunk)
    ref_shader, ref_subtype, ref_schema = _parse_mtl_chunk_schema(ref_chunk)
    if (dst_shader, dst_subtype) != (ref_shader, ref_subtype):
        raise ConversionError(
            f"{slot_label}: refusing to patch tone values -- shader/subtype "
            f"mismatch (destination is {dst_shader}/{dst_subtype}, reference "
            f"is {ref_shader}/{ref_subtype}). Fix the shader type in "
            "MaterialEditor.exe first, then re-run this patch."
        )

    dst_vbs = _mtl_chunk_value_blob_start(dst_chunk, dst_schema)
    ref_vbs = _mtl_chunk_value_blob_start(ref_chunk, ref_schema)
    out = bytearray(dst_chunk)

    # Any property with a "<name>@ref" sibling in either schema is a
    # texture-slot pointer pair, not a tunable value -- see docstring.
    texture_slot_names = {
        n[:-4] for n in (dst_schema + ref_schema) if n.endswith("@ref")
    }

    def _looks_like_hidden_pointer(raw: bytes) -> bool:
        if raw == b"\x00\x00\x00\x00":
            return False  # a real, legitimate "off"/zero value
        try:
            return abs(struct.unpack("<f", raw)[0]) < 1e-30
        except struct.error:
            return False

    common = sorted(n for n in dst_schema if n in ref_schema)
    patched, skipped_ref, skipped_pointer_like, dst_only, ref_only = [], [], [], [], []
    for name in common:
        if name.endswith("@ref") or name in texture_slot_names:
            skipped_ref.append(name)
            continue
        dst_idx = dst_schema.index(name)
        ref_idx = ref_schema.index(name)
        dst_off = dst_vbs + dst_idx * 4
        ref_off = ref_vbs + ref_idx * 4
        old_raw = bytes(out[dst_off:dst_off + 4])
        new_raw = ref_chunk[ref_off:ref_off + 4]
        if _looks_like_hidden_pointer(old_raw) or _looks_like_hidden_pointer(new_raw):
            skipped_pointer_like.append(f"{name} (old={old_raw.hex()}, new={new_raw.hex()})")
            continue
        if old_raw == new_raw:
            continue  # already identical -- nothing to log or change
        out[dst_off:dst_off + 4] = new_raw
        try:
            old_f = struct.unpack("<f", old_raw)[0]
            new_f = struct.unpack("<f", new_raw)[0]
            patched.append(f"{name}: {old_f:.4f} -> {new_f:.4f}")
        except struct.error:
            patched.append(f"{name}: {old_raw.hex()} -> {new_raw.hex()}")
    dst_only = sorted(n for n in dst_schema if n not in ref_schema)
    ref_only = sorted(n for n in ref_schema if n not in dst_schema)

    log.append(f"  {slot_label}: patched {len(patched)} tone/property value(s) from the reference material:")
    for line in patched:
        log.append(f"    {line}")
    if skipped_ref:
        log.append(f"    (left {len(skipped_ref)} texture-slot propert(y/ies) untouched -- '@ref' or paired with one: {skipped_ref})")
    if skipped_pointer_like:
        log.append(f"    WARNING: skipped {len(skipped_pointer_like)} unpaired propert(y/ies) that still looked like hidden pointers (denormalized near-zero float), not tuned values: {skipped_pointer_like}")
    if dst_only:
        log.append(f"    (destination-only, not touched: {dst_only})")
    if ref_only:
        log.append(f"    (reference-only, not applicable: {ref_only})")
    return bytes(out)


def patch_mtls_tone_values_for_slots(
    dst_path: Path,
    ref_path: Path,
    slot_map: dict[str, str],
    out_path: Path,
    log: list[str],
) -> None:
    """
    Whole-file wrapper around patch_mtl_tone_values: for each
    {dst_slot_name: ref_slot_name} pair in slot_map, patches dst_path's
    numeric material properties for dst_slot_name using ref_path's values
    for ref_slot_name (which may be named differently -- e.g. this
    project's "head2" vs. a real donor's "head" -- slot identity is purely
    positional in the outer name table, never tied to the name string
    itself). Every other slot in dst_path is copied through byte-for-byte,
    completely untouched. Writes the result to out_path (never overwrites
    dst_path in place, so a bad run never destroys the input).
    """
    original_bytes = dst_path.read_bytes()
    dst_names, dst_chunks, header = _split_mtls_slots(original_bytes)
    ref_names, ref_chunks, _ref_header = _split_mtls_slots(ref_path.read_bytes())

    original_len_by_slot = {n: len(dst_chunks[n]) for n in dst_names}
    for dst_slot, ref_slot in slot_map.items():
        if dst_slot not in dst_chunks:
            raise ConversionError(f"'{dst_slot}' is not a slot in {dst_path.name} (has: {dst_names})")
        if ref_slot not in ref_chunks:
            raise ConversionError(f"'{ref_slot}' is not a slot in {ref_path.name} (has: {ref_names})")
        dst_chunks[dst_slot] = patch_mtl_tone_values(
            dst_chunks[dst_slot], ref_chunks[ref_slot], log,
            slot_label=f"{dst_slot} (from reference's {ref_slot})" if dst_slot != ref_slot else dst_slot,
        )
        if len(dst_chunks[dst_slot]) != original_len_by_slot[dst_slot]:
            raise ConversionError(
                f"'{dst_slot}' chunk changed length ({original_len_by_slot[dst_slot]} -> "
                f"{len(dst_chunks[dst_slot])} bytes) while patching -- this should never "
                "happen for a same-length value overwrite; refusing to write a file that "
                "could desync every chunk after it"
            )

    out = bytearray(header)
    for name in dst_names:
        out += dst_chunks[name]
    # Hard safety net: for every slot NOT in slot_map, and for the overall
    # file length, the output must be byte-for-byte identical to the
    # input -- this patch function should only ever change a bounded set
    # of named properties inside the targeted slots, nothing else. A first
    # version of this function silently dropped 64 real header bytes and
    # this check is what would have caught it before anything was written
    # to a file meant for in-game testing.
    if len(out) != len(original_bytes):
        raise ConversionError(
            f"INTERNAL SAFETY CHECK FAILED: patched file would be {len(out)} bytes, "
            f"original was {len(original_bytes)} bytes -- refusing to write. This is a "
            "bug in the patcher, not something to work around; nothing was written."
        )
    untouched_slots = [n for n in dst_names if n not in slot_map]
    recheck_names, recheck_chunks, recheck_header = _split_mtls_slots(bytes(out))
    if recheck_header != header:
        raise ConversionError("INTERNAL SAFETY CHECK FAILED: header region changed unexpectedly -- refusing to write.")
    for name in untouched_slots:
        if recheck_chunks[name] != dst_chunks[name]:
            raise ConversionError(f"INTERNAL SAFETY CHECK FAILED: untouched slot '{name}' changed unexpectedly -- refusing to write.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(out))
    log.append(f"  wrote patched materials -> {out_path}")


# Every "hidden pointer" schema property's stored value is NOT a chunk-
# absolute byte offset -- it's (absolute byte position of the string it
# points at) minus this constant, i.e. relative to the start of the
# shader-type string itself (chunk byte 12, right after "MTL!" + the
# version u32 + the still-unidentified length u32). Confirmed 2026-10-02
# against FIVE independent properties on a real donor head/yCh_head chunk
# (detailzoneMap, translucencyMap, colorMap, g_nrmMap, g_facepaintNrm, each
# anchored to its own real texture-name-table string's absolute position).
# Do NOT substitute "offset after the shader+subtype strings" here -- for
# a chunk whose shader/subtype strings aren't exactly 16 bytes together,
# that is a DIFFERENT number from this constant and silently points every
# recomputed property at the wrong string (caught by self-test before
# shipping: it happened to coincide for one string, due to the "head" +
# "Normal" + null terminators strings in a test chunk being exactly 16
# bytes together, and only the self-test comparing recomputed offsets to
# the known-good real second example caught that it was not, in fact,
# the right constant for this purpose).
_MTL_HIDDEN_POINTER_BASE = 12


def _locate_mtl_texture_table(chunk: bytes) -> tuple[int, int, int]:
    """
    Returns (shader_strings_end, table_start, table_end) for one "MTL!"
    chunk:
      * shader_strings_end -- the offset right after the shader-type and
        subtype strings (where the schema name list begins). NOT the same
        as _MTL_HIDDEN_POINTER_BASE (always 12) -- see that constant's own
        comment; do not use this return value for hidden-pointer math.
      * table_start -- the first byte of the per-character texture-name
        table, i.e. right after the schema name list's own double-null
        terminator.
      * table_end -- the position of the texture-name table's OWN
        double-null terminator (so chunk[table_start:table_end] is the
        table's entries, each single-null-terminated; chunk[table_end:
        table_end+2] is that closing double-null).

    Confirmed 2026-10-02 against a real donor head/yCh_head chunk: the
    table holds one null-terminated entry per texture reference this
    material COULD have (not just the ones actually used), e.g.
    "head_detailzone", "head_translucency", "head_color", "head_nrm",
    plus a stray bare "_nrm" entry with no prefix at all (left unexplained,
    but harmless -- never renamed, never pointed at by a live property in
    the one real file checked).
    """

    def read_str(o: int) -> int:
        return chunk.index(b"\x00", o) + 1

    off = 12
    off = read_str(off)  # shader string
    off = read_str(off)  # subtype string
    shader_strings_end = off
    term_pos = chunk.find(_MTL_SCHEMA_TERMINATOR, off)
    if term_pos == -1:
        raise ConversionError("couldn't find MTL! chunk's schema name-list terminator")
    table_start = term_pos + 2
    table_end = chunk.find(_MTL_SCHEMA_TERMINATOR, table_start)
    if table_end == -1:
        raise ConversionError("couldn't find MTL! chunk's texture-name table terminator")
    return shader_strings_end, table_start, table_end


def rename_mtl_chunk_texture_prefix(
    chunk: bytes,
    old_prefix: str,
    new_prefix: str,
    log: list[str],
    slot_label: str = "",
) -> bytes:
    """
    Returns a modified copy of chunk with every texture-name-table entry
    that is exactly old_prefix, or starts with old_prefix + "_", rewritten
    to use new_prefix instead (e.g. "head_color" -> "head2_color" when
    old_prefix="head", new_prefix="head2") -- and every "hidden pointer"
    schema property (a property with no "@ref" suffix of its own, but
    which has a "<name>@ref" sibling elsewhere in the schema -- see
    patch_mtl_tone_values) whose current value points at a renamed entry
    recomputed to point at that entry's new position. Nothing else in the
    chunk -- not one schema value, not any literal "@ref"-suffixed field,
    not any entry that doesn't match old_prefix -- is touched.

    This is the mechanism detourne discovered by hand on 2026-10-02 (copy a
    real donor's own BaseModel material wholesale, rename it to match the
    destination mesh's real object name, in MaterialEditor.exe) worked out
    byte-for-byte from his own "perfect in game" result, reconstructed
    here as a mechanical, automatable operation:

      1. Every schema value this material actually has -- including the
         ~30 "exotic" skin properties (blood tint, SSS tint, cut-mask,
         oiliness, etc.) that MaterialEditor's shader-type-switch leaves
         at generic defaults with no real per-character data -- comes
         through 100% untouched, because the WHOLE chunk starts as the
         donor's own real, art-authored material. This is why it looks
         right in-game where the old shader-switch-then-patch-10-
         properties approach never could: that approach could only ever
         fix the handful of properties it had a trustworthy donor anchor
         for.
      2. The texture-name-table entries are the ONLY thing that has to
         change, because they're literal text naming this file's own
         texture assets, and the destination mesh's real texture files
         are named after the destination object (e.g. "head2_color.dds"),
         not the donor's original object name ("head_color.dds").
      3. A rename can grow or shrink the table by a few bytes (here,
         exactly 1 byte per occurrence of a 1-character-longer new
         prefix). Two things depend on that table's exact byte layout and
         MUST move with it, confirmed against the real donor-vs-working
         byte diff:
           a. Every "hidden pointer" property's stored value, which is
              (absolute byte offset of the string it points at) - 12 --
              i.e. relative to the start of the shader-type string, not
              to the start of the file or the table. A property pointing
              BEFORE table_start (there are always a few -- this
              material's declared-but-unused texture slots, which point
              at the schema terminator's own trailing null byte, i.e. an
              empty string) is never touched; one pointing at a renamed
              entry is shifted by exactly that entry's and every prior
              renamed entry's cumulative length delta.
           b. The second, otherwise-unidentified uint32 chunk header field
              at chunk bytes 8:12 (previously logged only as "an
              unidentified length field" with no established meaning) --
              confirmed empirically to track this chunk's variable-length
              prefix region and need the exact same net delta added.
         Nothing else in the chunk is a byte offset into this table: the
         literal "<name>@ref"-suffixed properties are a separate, constant
         per-shader-type integer sequence (confirmed unchanged across
         completely different files using the same shader/subtype) and
         are never touched here, matching patch_mtl_tone_values' existing
         rule that those are not real values.
      4. The chunk's total length changes by the same net delta (it is
         NOT assumed to stay constant -- detourne's real result happened to
         have enough trailing null padding in the table to absorb a net
         +2 bytes without changing the overall chunk size, but that's a
         lucky special case of this general operation, not a rule to rely
         on). Callers that need the whole .mtls file's total length
         unchanged (there usually isn't a reason to) must account for
         this when reassembling the file; see
         rename_mtl_chunk_texture_prefix's sibling, which does not exist
         yet -- splice the returned chunk into the slot and let the
         file's overall length simply change, same as inserting or
         removing any other variable-length run of bytes.
    """
    shader_strings_end, table_start, table_end = _locate_mtl_texture_table(chunk)
    old_table = chunk[table_start:table_end]
    entries = old_table.split(b"\x00")

    # Safety net added 2026-10-16, after detourne's hand-rebuilt, confirmed-
    # working hair2_ALPHA chunk on Hazuki's 645_attire.mtls proved this
    # function's earlier use on the exact same clone-and-retarget case had
    # silently done NOTHING: _locate_mtl_texture_table found an empty
    # region (table_start == table_end) for that native/Blender-exported
    # yCh_hair chunk, because its texture names ("hair_flow"/"hair_rai")
    # are bare entries in the SCHEMA NAME LIST itself, not in a separate
    # table at all -- confirmed true for yCh_gear chunks in the same file
    # too (their "_color"/"_msk"/"_nrm"/"_rough" names are also bare
    # schema-list entries; the "table" this function edits held only a
    # stray literal tab character, pure garbage). The donor-clone's own
    # original names ("hair_flow"/"hair_rai") were left in place on the
    # retargeted hair2_ALPHA slot, so it ended up declaring the SAME
    # texture files as the slot it was cloned from -- the real, concrete
    # bug behind the in-game crash, not anything about the yCh_hair shader
    # or slot count being unsafe in general (see the project doc's
    # 2026-10-16 correction entry).
    #
    # This function does not yet know how to rename a bare schema-list
    # texture entry -- doing that correctly means resizing the schema name
    # list and re-deriving at least 3 confirmed unpaired, non-"@ref"
    # hidden-pointer-style property values whose stored offsets shift with
    # it (g_debugmenu_h1, g_mPixMsk, g_noiseUScale on yCh_hair; not yet
    # characterized for yCh_gear), which isn't reverse-engineered with
    # enough confidence yet to automate safely. So: refuse loudly here
    # instead of silently renaming nothing in an irrelevant table.
    shader_for_check, _subtype_for_check, schema_for_check = _parse_mtl_chunk_schema(chunk)
    ref_paired_for_check = {n[:-4] for n in schema_for_check if n.endswith("@ref")}
    bare_schema_matches = [
        n for n in schema_for_check
        if not n.endswith("@ref")
        and n not in ref_paired_for_check
        and (n == old_prefix or n.startswith(old_prefix + "_"))
    ]
    if bare_schema_matches:
        raise ConversionError(
            f"{slot_label}: refusing to rename texture prefix '{old_prefix}' -> "
            f"'{new_prefix}' -- found {len(bare_schema_matches)} bare schema-name "
            f"propert(y/ies) matching this prefix ({bare_schema_matches}) that live "
            f"directly in the {shader_for_check} chunk's schema name list, not in the "
            "separate texture-name table this function edits (which found "
            f"{len([e for e in entries if e == old_prefix.encode('latin1') or e.startswith(old_prefix.encode('latin1') + b'_')])} "
            "matching entries there). Renaming only the table would silently leave "
            "these real texture names unchanged -- exactly the bug that crashed "
            "Hazuki's hair2_ALPHA clone-and-retarget fix on 2026-10-16. Not safe to "
            "proceed automatically; fix this chunk by hand in MaterialEditor.exe instead."
        )

    old_entry_starts = []
    pos = table_start
    for e in entries:
        old_entry_starts.append(pos)
        pos += len(e) + 1

    renamed_prefix = old_prefix.encode("latin1")
    renamed_new = new_prefix.encode("latin1")
    new_entries = []
    renamed_indices = []
    for i, e in enumerate(entries):
        if e == renamed_prefix or e.startswith(renamed_prefix + b"_"):
            new_entries.append(renamed_new + e[len(renamed_prefix):])
            renamed_indices.append(i)
        else:
            new_entries.append(e)

    new_entry_starts = []
    pos = table_start
    for e in new_entries:
        new_entry_starts.append(pos)
        pos += len(e) + 1
    new_table = b"\x00".join(new_entries)
    delta = len(new_table) - len(old_table)

    shader, subtype, schema = _parse_mtl_chunk_schema(chunk)
    vbs = _mtl_chunk_value_blob_start(chunk, schema)
    out = bytearray(chunk)

    # 2026-10-16 (Miu Watanabe, 467 build): fixed a real, confirmed
    # in-game-crashing bug here. This loop used to only check the BARE
    # name of each "<name>@ref" pair (texture_slot_names, computed by
    # stripping "@ref" off schema entries that have it) for a pointer
    # value, on the assumption (true for the original head/head2 rename
    # this function was first built for) that the bare name holds the
    # live offset and "<name>@ref" is the decorative/constant sibling.
    # That assumption is BACKWARDS for yCh_head's/yCh_caruncle's real
    # texture-mapping properties (translucencyMap@ref, flushMap@ref,
    # colorMap@ref, g_nrmMap@ref, g_facepaintNrm@ref): confirmed by
    # scanning every property's raw value against the table's real old
    # positions that the live pointer sits on the "@ref"-SUFFIXED
    # property itself, and the bare name's value doesn't resolve to the
    # table at all. The old code silently left those 5 properties' stale
    # offsets untouched after the rename shifted the table, so they ended
    # up pointing at garbage bytes mid-chunk -- the real cause of the
    # WWE2K25.exe crash-to-desktop seen testing this fix on Miu's 467
    # build. Now scans EVERY schema property (regardless of "@ref"
    # naming) with the same general "denormalized near-zero float" hidden
    # -pointer heuristic build_default_mtl_chunk already uses, so it
    # catches a live pointer on either side of an "@ref" pair, or with no
    # "@ref" sibling at all.
    moved = []
    for idx, name in enumerate(schema):
        off = vbs + idx * 4
        raw = bytes(out[off:off + 4])
        if not _mtl_looks_like_hidden_pointer(raw):
            continue
        old_val = struct.unpack("<I", raw)[0]
        pointed_abs = old_val + _MTL_HIDDEN_POINTER_BASE
        # Check EVERY entry's position, not just the renamed ones: an
        # entry that wasn't itself renamed (e.g. the trailing stray "_nrm")
        # still shifts if anything earlier in the table grew or shrank, so
        # anything pointing at it needs updating too. Caught 2026-10-02 by
        # the self-test below -- checking only renamed_indices silently
        # left g_facepaintNrm pointing at the wrong (now-shifted) string.
        for i in range(len(entries)):
            if pointed_abs == old_entry_starts[i]:
                if new_entry_starts[i] == old_entry_starts[i]:
                    break  # didn't actually move
                new_val = new_entry_starts[i] - _MTL_HIDDEN_POINTER_BASE
                struct.pack_into("<I", out, off, new_val)
                moved.append(f"{name}: offset {old_val} -> {new_val} (points at '{entries[i].decode('latin1')}' -> '{new_entries[i].decode('latin1')}')")
                break

    length_field = struct.unpack("<I", bytes(out[8:12]))[0]
    struct.pack_into("<I", out, 8, length_field + delta)

    out = bytes(out[:table_start]) + new_table + bytes(out[table_end:])

    log.append(f"  {slot_label}: renamed texture prefix '{old_prefix}' -> '{new_prefix}' in material's own texture-name table:")
    for i in renamed_indices:
        log.append(f"    '{entries[i].decode('latin1')}' -> '{new_entries[i].decode('latin1')}'")
    if not renamed_indices:
        log.append(f"    (no entries in the table started with '{old_prefix}' -- nothing renamed)")
    for line in moved:
        log.append(f"    {line}")
    log.append(f"    chunk length field (offset 8) {length_field} -> {length_field + delta}; chunk total length {len(chunk)} -> {len(out)}")
    return out


def rebuild_mtl_texture_table(
    chunk: bytes,
    new_entries: list[str],
    log: list[str],
    slot_label: str = "",
) -> bytes:
    """
    Returns a modified copy of chunk with its ENTIRE texture-name table
    replaced by new_entries (each a bare string, no manual null bytes --
    joined and terminated the same way _locate_mtl_texture_table expects to
    find them: null-separated, no trailing null before the table's own
    closing double-null).

    Built 2026-10-15 for a newly-found corruption distinct from the rename
    case rename_mtl_chunk_texture_prefix already handles: on detourne's own
    735_attire.mtls, after manually switching 3 attire-part materials
    (hair2_alpha, hair4_ALPHA, combinations2_alpha) to yCh_hair/
    NewHair_ColorChanging in MaterialEditor.exe, those 3 chunks' texture-
    name tables came out as two garbage entries -- literally [b'', b'\\t']
    (an empty string plus a bare tab character) -- instead of the single
    tidy "<slot>_root" entry the 2 OTHER materials on the same file that
    converted cleanly through the same shader switch have (hair_ALPHA ->
    "hair_root", combinations_ALPHA -> "combinations_root"). Not a missing-
    files problem (the original "generate placeholder PBR textures" plan
    assumed) -- the table itself is garbled, structurally, regardless of
    what's on disk.

    Confirmed safe to rebuild as a plain table replacement, NOT a rename,
    for this specific shader/subtype (yCh_hair / NewHair_ColorChanging):
    exhaustively checked every schema property's value (not just the
    "<name>@ref"-paired ones) on all 5 yCh_hair chunks in this file --
    both the 2 clean ones and the 3 broken ones -- against
    _MTL_HIDDEN_POINTER_BASE-style resolution, and NOT ONE property on ANY
    of the 5 chunks resolves to a position inside that chunk's own texture
    table. This is unlike yCh_head/yCh_gear, where specific "@ref"-paired
    properties ARE live pointers into the table (see
    rename_mtl_chunk_texture_prefix). The conclusion: for yCh_hair /
    NewHair_ColorChanging, the single texture-table entry is resolved by
    the engine via the entry's OWN naming convention alone (it looks up
    "<root>_flow.dds" / "<root>_rai.dds" on disk using whatever string sits
    in the table), not via any chunk-stored offset -- confirmed by the 2
    clean materials working perfectly in-game with no property pointing at
    their table entry either. So this function only ever touches the
    table's own bytes and the chunk length field; it does NOT attempt to
    relocate any property value, unlike the rename function.

    As a safety net for a shader/subtype where that confirmed-false-for-
    this-file assumption might not hold, this still scans every schema
    property exactly as rename_mtl_chunk_texture_prefix does and LOGS A
    LOUD WARNING (does not silently continue) if anything is found pointing
    into the OLD table region -- that would mean this function is unsafe
    for that particular chunk and the caller should not trust the result
    without further investigation.
    """
    shader_strings_end, table_start, table_end = _locate_mtl_texture_table(chunk)
    old_table = chunk[table_start:table_end]

    new_table = b"\x00".join(e.encode("latin1") for e in new_entries)
    delta = len(new_table) - len(old_table)

    shader, subtype, schema = _parse_mtl_chunk_schema(chunk)
    vbs = _mtl_chunk_value_blob_start(chunk, schema)

    # Safety net: confirm nothing currently points into the OLD table
    # region before we destroy it. See docstring -- confirmed empty for
    # every case tested so far, but never assumed silently.
    suspect = []
    for i, name in enumerate(schema):
        off = vbs + i * 4
        raw = chunk[off:off + 4]
        val = int.from_bytes(raw, "little")
        pointed_abs = val + _MTL_HIDDEN_POINTER_BASE
        if table_start - 2 <= pointed_abs <= table_end + 2:
            suspect.append(f"{name} (idx {i}, raw={raw.hex()}, pointed_abs={pointed_abs})")
    if suspect:
        log.append(
            f"  {slot_label}: WARNING -- {len(suspect)} schema propert(y/ies) appear to "
            f"point INTO the texture table this rebuild is about to overwrite: {suspect}. "
            "This shader/subtype may need pointer fixups this function doesn't do -- "
            "verify in-game carefully, this may not be safe."
        )

    length_field = struct.unpack("<I", bytes(chunk[8:12]))[0]
    out = bytearray(chunk)
    struct.pack_into("<I", out, 8, length_field + delta)
    out = bytes(out[:table_start]) + new_table + bytes(out[table_end:])

    log.append(
        f"  {slot_label}: rebuilt texture-name table: {old_table!r} -> {new_entries} "
        f"(chunk length field {length_field} -> {length_field + delta}; "
        f"chunk total length {len(chunk)} -> {len(out)})"
    )
    return out


def rebuild_mtls_texture_tables_for_slots(
    mtls_path: Path,
    slot_entries: dict[str, list[str]],
    out_path: Path,
    log: list[str],
) -> None:
    """
    Whole-file wrapper around rebuild_mtl_texture_table: for each
    {slot_name: [new table entries]} pair in slot_entries, rebuilds that
    slot's texture-name table and splices the result back into the whole
    .mtls file. Every other slot is carried through byte-for-byte,
    untouched. Writes to out_path (never overwrites mtls_path in place).

    Because each rebuilt chunk can change length (unlike
    patch_mtls_tone_values_for_slots' bounded in-place overwrite), the
    whole-file length is expected to change too -- this function does NOT
    assert the output is the same length as the input (that would be wrong
    here), but it DOES re-parse the result with _split_mtls_slots and
    diff every untouched slot's chunk bytes against the original before
    writing, so a bug here can never silently corrupt a slot that wasn't
    supposed to change.
    """
    original_bytes = mtls_path.read_bytes()
    slot_names, chunks, header = _split_mtls_slots(original_bytes)

    for slot, new_entries in slot_entries.items():
        if slot not in chunks:
            raise ConversionError(f"'{slot}' is not a slot in {mtls_path.name} (has: {slot_names})")
        chunks[slot] = rebuild_mtl_texture_table(
            chunks[slot], new_entries, log, slot_label=slot,
        )

    out = bytearray(header)
    for name in slot_names:
        out += chunks[name]

    # Re-parse the freshly-assembled bytes from scratch and verify every
    # untouched slot is byte-for-byte identical to the original, and every
    # rebuilt slot still parses as a structurally valid MTL! chunk with the
    # table we meant to write. A rebuilt chunk's length changing means
    # every chunk after it in the file shifts position, which is exactly
    # why this re-parse (positional, not offset-arithmetic) is the real
    # safety net here, not a byte-count assertion like the tone-value
    # patcher uses.
    recheck_names, recheck_chunks, recheck_header = _split_mtls_slots(bytes(out))
    if recheck_header != header:
        raise ConversionError("INTERNAL SAFETY CHECK FAILED: header region changed unexpectedly -- refusing to write.")
    if recheck_names != slot_names:
        raise ConversionError("INTERNAL SAFETY CHECK FAILED: slot name/order changed unexpectedly -- refusing to write.")
    for name in slot_names:
        if name not in slot_entries and recheck_chunks[name] != chunks[name]:
            raise ConversionError(f"INTERNAL SAFETY CHECK FAILED: untouched slot '{name}' changed unexpectedly -- refusing to write.")
    for name in slot_entries:
        shader_strings_end, table_start, table_end = _locate_mtl_texture_table(recheck_chunks[name])
        got_entries = recheck_chunks[name][table_start:table_end].split(b"\x00")
        want_entries = [e.encode("latin1") for e in slot_entries[name]]
        if got_entries != want_entries:
            raise ConversionError(
                f"INTERNAL SAFETY CHECK FAILED: '{name}' texture table re-read as "
                f"{got_entries}, expected {want_entries} -- refusing to write."
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(out))
    log.append(f"  wrote rebuilt texture tables -> {out_path}")


def _rename_mtls_outer_slot_name(
    header: bytes,
    old_name: str,
    new_name: str,
    log: list[str],
) -> bytes:
    """
    Returns a modified copy of a whole .mtls file's outer header (the
    bytes _split_mtls_slots returns as header_bytes) with one slot's name
    changed in the outer name table -- e.g. donor's own "head" -> "head2"
    to match the destination mesh's real object name -- WITHOUT changing
    the number of slots. Does not touch any "MTL!" chunk; pair this with
    rename_mtl_chunk_texture_prefix on that slot's own chunk to fix its
    internal texture-name-table references too.

    Reverse-engineered 2026-10-02 from two real, known-good files (a
    donor's own 9-slot baseModel.mtls and detourne's working, in-game-
    confirmed 8-slot rename-and-drop of it) that disagree in total size
    and in exactly how many trailing bytes follow the real per-slot offset
    table, which means the full semantics of this header region (an
    unidentified ~6-byte preamble containing what looks like a second,
    this time apparently ACCURATE, slot count, a large reserved run of
    zero bytes of uncertain purpose, and a list of uint16 cumulative byte
    offsets -- one per slot name after the first -- into the name table)
    are NOT fully pinned down for the add/remove-a-slot case. Deliberately
    scoped to the one transformation that doesn't require touching any of
    that uncertain structure: a same-slot-count rename never changes the
    preamble, the reserved region, or whether/how many trailing zero
    entries follow the real offsets, so all of that is carried over
    completely untouched, byte-for-byte, and only two things are ever
    rewritten:
      1. The name table itself (the renamed slot's text, which may grow
         or shrink the table by a few bytes) and the outer
         name_table_len field, plus the one alignment pad byte right
         after the table that's present iff the new table length is odd
         (confirmed: WWE2K25 keeps this region 2-byte aligned).
      2. Each later slot's own stored cumulative-offset-into-the-name-
         table value, by exactly the net byte delta the rename
         introduced -- located not by position (deliberately, since the
         exact boundary/padding rules past the real offset list aren't
         fully pinned down) but by searching for each slot's own
         CURRENTLY-KNOWN, independently-computed offset value and
         rewriting that specific occurrence; collisions are not a
         concern since these are strictly increasing cumulative
         positions, each unique within one file.
    This is lower-risk than reconstructing the add/remove-a-slot case from
    only two examples would be: this project has already shipped two
    binary-surgery mistakes this session from acting on an incompletely
    understood region, so this function deliberately never drops or adds
    a slot. For dropping an unused donor slot (e.g. a second eye material
    the destination mesh doesn't have), see build_basemodel_mtls_from_donor
    -- it just leaves the unused slot's chunk in place rather than
    removing it, which is safe because the real donor file already ships
    with exactly this kind of unused-but-present library slot (confirmed:
    eye_caruncle/eye_overlay on this exact donor) and works fine in-game,
    so an extra, never-referenced-by-any-mesh-object slot is evidently
    harmless.
    """
    name_table_len = struct.unpack("<I", header[8:12])[0]
    name_table = header[12:12 + name_table_len]
    names = [n.decode("latin1") for n in name_table.split(b"\x00") if n]
    if names.count(old_name) != 1:
        raise ConversionError(
            f"expected exactly one '{old_name}' slot in the .mtls outer name "
            f"table, found {names.count(old_name)} (table: {names})"
        )
    idx = names.index(old_name)

    def starts_of(ns: list[str]) -> list[int]:
        starts, pos = [], 0
        for n in ns:
            starts.append(pos)
            pos += len(n.encode("latin1")) + 1
        return starts

    old_starts = starts_of(names)
    new_names = list(names)
    new_names[idx] = new_name
    new_starts = starts_of(new_names)
    new_name_table = b"\x00".join(n.encode("latin1") for n in new_names) + b"\x00"

    # FIXED 2026-10-22 (Athena, custom-donor BaseModel run): the old 2-byte
    # "is name_table_len odd" heuristic for old_pad/new_pad was WRONG, and
    # corrupted every rename that crossed a mod-4 boundary -- confirmed by
    # decompiling MaterialEditor.dll's real MtlFileService.ParseFromStream
    # (same ikdasm/dotnet harness as the 2026-10-21 Ricochet overflow fix):
    # the real reader does `ReadBytes(name_table_len); AlignTo(stream, 4)`
    # -- a plain align-to-4 of the ABSOLUTE stream position (12 + table
    # length), nothing parity-based at all. The old heuristic's old_pad=0
    # for this rename's 70-byte (even) original table silently left the
    # table's real 2 true align-4 bytes sitting as the first 2 bytes of
    # `rest` (since align-to-4 from absolute position 82 needs 2 bytes, not
    # the 0 the odd/even heuristic predicted) -- harmless on its own, since
    # `rest` is copied through opaquely either way. The bug was additive:
    # new_pad=1 (new table length 71 is odd) then STACKED one more pad byte
    # in front of `rest`'s already-baked-in-but-uncounted 2 real bytes,
    # landing DeclaredMaterialCount 2 bytes late (at 86, not the real
    # reader's expected 84) -- read as whatever 4 bytes actually sat at 84
    # (two stray zero bytes from this bug + the first half of the real
    # count), which is how a 9 became 589824 and crashed the whole file
    # with an EndOfStreamException trying to read that many 8-byte TOC
    # hashes. Fixed by computing true align-4 padding from the absolute
    # position on both sides, and explicitly STRIPPING the old file's real
    # padding bytes out of `rest` before splicing in the new file's own
    # (possibly different-sized) padding -- rather than treating `rest` as
    # if it started exactly where the old heuristic's pad guess said it did.
    old_table_end_abs = 12 + name_table_len
    old_align = (-old_table_end_abs) % 4
    new_name_table_len = len(new_name_table)
    new_table_end_abs = 12 + new_name_table_len
    new_align = (-new_table_end_abs) % 4
    rest = bytearray(header[old_table_end_abs + old_align:])
    new_pad_bytes = b"\x00" * new_align

    changed = []
    for i in range(1, len(names)):  # the first name's start (always 0) is never stored
        if old_starts[i] == new_starts[i]:
            continue
        old_bytes = struct.pack("<H", old_starts[i])
        new_bytes = struct.pack("<H", new_starts[i])
        pos_found = rest.rfind(old_bytes)
        if pos_found == -1:
            raise ConversionError(
                f"couldn't find the stored name-table offset for '{names[i]}' "
                f"({old_starts[i]}) in the .mtls outer header -- refusing to "
                "rename blind rather than risk writing a corrupt header"
            )
        rest[pos_found:pos_found + 2] = new_bytes
        changed.append(f"{names[i]}: {old_starts[i]} -> {new_starts[i]}")

    new_header = (
        header[0:4] + header[4:8] + struct.pack("<I", new_name_table_len)
        + new_name_table + new_pad_bytes + bytes(rest)
    )
    log.append(
        f"  outer slot name table: '{old_name}' -> '{new_name}' "
        f"(and {len(changed)} later slot's stored name-table offset(s) shifted to match)"
    )
    for line in changed:
        log.append(f"    {line}")
    return new_header


def build_basemodel_mtls_from_donor(
    donor_path: Path,
    rename_map: dict[str, str],
    out_path: Path,
    log: list[str],
) -> None:
    """
    Builds a complete baseModel.mtls by taking a real, known-good WWE2K25
    donor character's own baseModel.mtls WHOLESALE -- every slot, every
    property, completely unmodified -- except for the slots named in
    rename_map (e.g. {"head": "head2"}), which are renamed, both in the
    outer slot name table (_rename_mtls_outer_slot_name) and in that
    slot's own internal texture-name-table references
    (rename_mtl_chunk_texture_prefix), to match the destination mesh's
    real object names.

    This is the automated version of the fix detourne found by hand on
    2026-10-02 (copy a real donor's own materials wholesale in
    MaterialEditor.exe, rename the slot that doesn't match the
    destination mesh, leave literally everything else alone), confirmed
    "perfect in game" -- reconstructed byte-for-byte from his own working
    file. It supersedes the MaterialEditor-shader-switch-plus-property-
    patch approach (patch_mtl_tone_values) for BaseModel materials:
    that approach could only ever fix the handful of properties it had a
    trustworthy donor anchor for and left ~30 "exotic" skin properties
    (blood tint, SSS tint, cut-mask, oiliness) at generic defaults, which
    is the real reason every previous attempt looked wrong in a different
    way. Bringing over the donor's ENTIRE material leaves nothing to
    guess.

    Deliberately does NOT drop any donor slot that isn't in rename_map
    and has no equivalent on the destination mesh (e.g. a donor's second,
    unused eye material) -- see _rename_mtls_outer_slot_name's docstring
    for why that's left in place rather than removed. An extra, never-
    referenced slot is already how the real donor file itself ships.
    """
    donor_names, donor_chunks, donor_header = _split_mtls_slots(donor_path.read_bytes())
    for old_name in rename_map:
        if old_name not in donor_chunks:
            raise ConversionError(
                f"'{old_name}' (from rename_map) is not a slot in {donor_path.name} "
                f"(has: {donor_names})"
            )

    header = donor_header
    names = list(donor_names)
    chunks = dict(donor_chunks)
    for old_name, new_name in rename_map.items():
        header = _rename_mtls_outer_slot_name(header, old_name, new_name, log)
        chunks[new_name] = rename_mtl_chunk_texture_prefix(
            chunks.pop(old_name), old_name, new_name, log, slot_label=new_name,
        )
        names = [new_name if n == old_name else n for n in names]

    out = bytearray(header)
    for name in names:
        out += chunks[name]

    # Hard safety net, same discipline as patch_mtls_tone_values_for_slots:
    # re-parse what we're about to write and confirm it comes back with
    # exactly the expected slot roster and byte-identical chunk content
    # for every slot before anything touches disk.
    recheck_names, recheck_chunks, _recheck_header = _split_mtls_slots(bytes(out))
    if recheck_names != names:
        raise ConversionError(
            f"INTERNAL SAFETY CHECK FAILED: rebuilt .mtls re-parses with slot "
            f"order/names {recheck_names}, expected {names} -- refusing to write."
        )
    for name in names:
        if recheck_chunks[name] != chunks[name]:
            raise ConversionError(
                f"INTERNAL SAFETY CHECK FAILED: slot '{name}' did not round-trip "
                "byte-identical after rebuilding the file -- refusing to write."
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(out))
    log.append(
        f"  built baseModel.mtls wholesale from donor {donor_path} -> {out_path} "
        f"(slots: {names})"
    )


def auto_detect_basemodel_slot_renames(
    dst_names: list[str],
    donor_names: list[str],
    log: list[str],
) -> tuple[dict[str, str], list[str]]:
    """
    Given the destination mesh's own real material/slot names (read
    straight off this character's own, as-exported, WWE2K26-format
    baseModel.mtls -- confirmed 2026-10-02 across 6 real example
    characters that this file's slot names are always this character's
    real mesh object names, with NO conversion/Blender step required to
    get them right; and confirmed on the SAME real examples that whether
    a rename is even needed at all varies per character -- Athena's own
    2K26 mesh really does name its head object "head2", where El
    Desperado, Yuki Arai, Momo Kohgo, Blake Christian and Steven Borden's
    all say plain "head" already, matching a donor directly with no
    rename needed) and a real WWE2K25 donor character's own
    baseModel.mtls slot names, returns ({donor_slot_name:
    destination_slot_name}, [unmatched destination slot name, ...]) --
    the rename map for build_basemodel_mtls_from_donor, and any
    destination slot this donor simply has no material for at all.

    Matching rule, deliberately conservative -- never guesses when more
    than one candidate is equally plausible (this file format has already
    cost this project two real in-game regressions from acting on a
    guess that looked right and wasn't -- see the project doc's
    2026-10-02 entries):
      1. Any name present verbatim in both lists is assumed to already be
         the same body part and needs no rename -- true for most slots on
         every real example seen so far (body, eyelash, mouthbag, teeth,
         eye_caruncle, eye_overlay, and often eye_l/eye_r/head too).
      2. Everything left over is paired up by prefix relationship ONLY
         (one name starting with the other, e.g. "head2" starts with
         "head") -- the only relationship confirmed on a real example. If
         a destination name has exactly one such candidate among the
         not-yet-matched donor names, it's paired. Zero or multiple
         candidates means it's left unmatched and logged loudly rather
         than guessed.
      3. A donor slot never claimed by any destination name (e.g. a
         donor's second, unused eye material when the destination mesh
         only has one) is left out of the rename map entirely -- it ends
         up copied through by build_basemodel_mtls_from_donor unrenamed
         and unused, matching how the real donor file itself already
         ships this kind of harmless extra slot.
      4. A destination slot matching no donor slot at all (no exact match,
         no unique prefix match) is returned in the second list --
         there's no real donor data for it, and the caller decides what
         to do (as of 2026-10-02, _convert_base_model treats this as "we
         don't know enough to safely rebuild this file" and falls back to
         the plain donor-copy behavior rather than guessing or silently
         dropping a body part).
    """
    donor_remaining = [n for n in donor_names if n not in dst_names]
    dst_remaining = [n for n in dst_names if n not in donor_names]
    rename_map: dict[str, str] = {}
    unmatched_dst: list[str] = []
    for dst_name in dst_remaining:
        candidates = [
            d for d in donor_remaining
            if dst_name.startswith(d) or d.startswith(dst_name)
        ]
        if len(candidates) == 1:
            rename_map[candidates[0]] = dst_name
            donor_remaining.remove(candidates[0])
        elif len(candidates) == 0:
            unmatched_dst.append(dst_name)
        else:
            unmatched_dst.append(dst_name)
            log.append(
                f"  WARNING: destination material '{dst_name}' matches {len(candidates)} "
                f"possible donor materials by name ({candidates}) -- ambiguous, not "
                "auto-renaming any of them"
            )
    if rename_map:
        log.append(f"  auto-detected BaseModel material rename(s): {rename_map}")
    else:
        log.append("  auto-detected BaseModel material renames: none needed (all names already match)")
    if unmatched_dst:
        log.append(
            f"  WARNING: destination material(s) {unmatched_dst} have no matching "
            "donor material by exact name or unique name-prefix -- no real donor "
            "data available for these"
        )
    if donor_remaining:
        log.append(
            f"  (donor material(s) {donor_remaining} have no destination mesh "
            "counterpart -- will be left in the output file unused and unrenamed, "
            "same as how the donor's own real, shipped file already carries unused "
            "library materials like this)"
        )
    return rename_map, unmatched_dst


_TEXTURE_FILE_EXTENSIONS = (".dds",)


def copy_donor_textures_with_rename(
    donor_textures_dir: Path,
    dst_textures_dir: Path,
    rename_map: dict[str, str],
    log: list[str],
) -> None:
    """
    For every {old_prefix: new_prefix} rename applied to a BaseModel
    material (see build_basemodel_mtls_from_donor/
    auto_detect_basemodel_slot_renames), copies that donor material's own
    texture files -- e.g. "head_color.dds", "head_nrm.dds" -- into the
    destination Textures folder under the NEW prefix ("head2_color.dds",
    "head2_nrm.dds"), matching what the renamed .mtls's own texture-name
    table now points at (see rename_mtl_chunk_texture_prefix -- the game
    resolves a material's textures by this filename convention, not by
    any byte offset). Matches "<old_prefix>.<ext>" and
    "<old_prefix>_<anything>.<ext>" case-insensitively; does not touch
    any file that doesn't start with one of the renamed prefixes.

    GAP-FILLING ONLY: _copy_textures() runs before this and already copies
    the destination character's OWN textures (by name) into dst_textures_dir.
    If the destination already has a file under the renamed name, it's the
    character's own, correctly-authored texture (e.g. Athena's own real
    "head2_color.dds") and must NOT be overwritten by the donor's
    same-destination-name texture (e.g. Liv Morgan's "head_color.dds"
    renamed to "head2_color.dds") -- that produced a silent skin-tone/seam
    mismatch between the donor-sourced slot and the rest of the character.
    This only fills in a renamed texture when the destination doesn't
    already have one under that name, matching the sibling function
    _fill_missing_donor_textures's gap-filling-only behavior.
    """
    if not donor_textures_dir.is_dir():
        return
    dst_textures_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    skipped_existing = []
    for old_prefix, new_prefix in rename_map.items():
        old_lower = old_prefix.lower()
        for f in donor_textures_dir.iterdir():
            if not f.is_file() or f.suffix.lower() not in _TEXTURE_FILE_EXTENSIONS:
                continue
            stem_lower = f.stem.lower()
            if stem_lower == old_lower:
                new_stem = new_prefix
            elif stem_lower.startswith(old_lower + "_"):
                new_stem = new_prefix + f.stem[len(old_prefix):]
            else:
                continue
            dst_name = new_stem + f.suffix
            dst_path = dst_textures_dir / dst_name
            if dst_path.exists():
                # Destination already has its own texture under this name
                # (placed there by _copy_textures from the character's own
                # Textures folder) -- keep it, don't clobber with the
                # donor's version.
                skipped_existing.append(f"{f.name} -> {dst_name} (kept existing)")
                continue
            shutil.copy2(f, dst_path)
            copied.append(f"{f.name} -> {dst_name}")
    if copied:
        log.append(f"  copied donor texture(s) under the renamed material's new name: {copied}")
    if skipped_existing:
        log.append(
            f"  kept the source's own existing texture(s) instead of the donor's renamed version: {skipped_existing}"
        )


_ATTIRE_HAIR_SLOT_RE = re.compile(r"^(hair\d*)(_ALPHA)?$", re.IGNORECASE)
_HAIR_SHADER_NAME = "ych_hair"

# Suffix vocabulary used to classify a slot's REQUIRED shader straight from
# the real texture files shipped for it, instead of guessing from the
# slot/object's own name. Added 2026-10-20 at detourne's suggestion: a slot
# named "beard" (or anything else) still ships hair_flow/hair_rai-style
# textures when it's a hair-shader mesh, and a gear mesh always ships a
# *_color + *_nrm pair -- that pairing is the real, reliable signal, not
# whether the slot's name happens to contain the substring "hair".
_HAIR_TEXTURE_SUFFIXES = ("_flow", "_rai")
_GEAR_REQUIRED_SUFFIXES = ("_color", "_nrm")


def _scan_attire_texture_groups(
    textures_dir: Path | None, log: list[str],
) -> dict[str, tuple[str, str]]:
    """
    Groups an attire's real shipped texture files by shared name prefix and
    classifies each group as a "hair" or "gear" shader group, purely from
    which suffixes are actually present on disk:
      - any "*_flow.dds" or "*_rai.dds" file -> hair group
      - a "*_color.dds" file AND a "*_nrm.dds" file (both) -> gear group

    This is the ground truth build_attire_mtls_from_defaults now builds
    from (per detourne, 2026-10-20: "work backwards" from the Textures
    folder) instead of pattern-matching the slot's own name with
    _ATTIRE_HAIR_SLOT_RE -- which only ever recognized "hair"-named slots
    and would misclassify e.g. a "beard" slot (also yCh_hair in detourne's
    own confirmed-working manual process) as gear.

    A gear group only requires a real "*_color.dds" file -- "*_nrm.dds"
    is treated as a (near-always-present but not guaranteed) supporting
    signal, not a requirement. Found 2026-10-20 testing Manami Toyota for
    real: her "shoes" slot ships "shoes_color.dds" with NO "shoes_nrm.dds"
    at all, which an earlier require-both version of this function
    wrongly treated as "no real texture group" even though the slot is
    unambiguously real gear. (That earlier version still happened to
    build correctly for "shoes" specifically, by falling through to the
    old name-guess path and the slot's own name already being its real
    prefix verbatim -- but it was pure coincidence, and would have been
    silently wrong for any differently-decorated slot name with the same
    missing-nrm situation.)

    Returns {lowercase_prefix: (shader, real_prefix)}, where real_prefix
    is the prefix exactly as cased in the real files -- the same
    scene-authoring-decoration problem _resolve_attire_texture_prefix's
    docstring describes means this real, on-disk spelling is often NOT
    the same string as the slot/object's own name, so it must come from
    here, not be assumed.
    """
    if textures_dir is None or not textures_dir.is_dir():
        return {}

    by_prefix: dict[str, dict[str, str]] = {}  # lower prefix -> {suffix: real_prefix}
    all_suffixes = (*_HAIR_TEXTURE_SUFFIXES, *_GEAR_REQUIRED_SUFFIXES)
    for f in textures_dir.iterdir():
        if not f.is_file() or f.suffix.lower() not in _TEXTURE_FILE_EXTENSIONS:
            continue
        stem_lower = f.stem.lower()
        for suf in all_suffixes:
            if stem_lower.endswith(suf):
                real_prefix = f.stem[: -len(suf)]
                by_prefix.setdefault(real_prefix.lower(), {})[suf] = real_prefix
                break

    groups: dict[str, tuple[str, str]] = {}
    for prefix_lower, suffixes in by_prefix.items():
        real_prefix = next(iter(suffixes.values()))
        if any(suf in suffixes for suf in _HAIR_TEXTURE_SUFFIXES):
            groups[prefix_lower] = ("hair", real_prefix)
        elif "_color" in suffixes:
            groups[prefix_lower] = ("gear", real_prefix)
        # else: a stray "*_nrm.dds" with no "*_color.dds" at all -- not
        # enough evidence on its own to decide a shader, so it's left
        # unclassified rather than guessed at.
    return groups


def _match_slot_to_texture_group(
    slot_name: str, groups: dict[str, tuple[str, str]],
) -> tuple[str, str] | None:
    """
    Finds which _scan_attire_texture_groups() group a slot/object name's
    real textures live under, tolerating the same scene-authoring
    decorations (a leading underscore, a trailing Maya-style "_A"/"_B"
    disambiguation suffix) _resolve_attire_texture_prefix tolerates --
    e.g. slot "_l_kneepad_A" matches the "l_kneepad" group.

    Also tolerates a bare trailing digit with no underscore separator
    (e.g. "hair2") as a LOWEST-priority fallback candidate, tried only
    after every other candidate has failed to match -- confirmed real
    2026-10-20 via Kevin Knight's own working, hand-rebuilt reference
    file: his "hair2_ALPHA" slot ships no dedicated hair2_flow/hair2_rai
    textures of its own at all, and its real table entries are literally
    ['hair_msk', 'hair_opacity', 'hair_ID', 'hair_root'] -- i.e. it
    reuses the "hair" group verbatim, not a "hair2" group. This is tried
    last (not promoted ahead of an exact or underscore-decorated match)
    so a slot like "tights2", which DOES ship its own tights2_color.dds/
    tights2_nrm.dds real texture group, keeps matching THAT group first
    and never falls through to "tights".
    """
    if not groups:
        return None
    candidates = [slot_name]
    stripped_underscore = slot_name[1:] if slot_name.startswith("_") else slot_name
    candidates.append(stripped_underscore)
    m = re.match(r"^(.*)_[A-Za-z0-9]+$", stripped_underscore)
    if m:
        candidates.append(m.group(1))
        m2 = re.match(r"^(.*)_[A-Za-z0-9]+$", m.group(1))
        if m2:
            candidates.append(m2.group(1))
    for cand in candidates:
        hit = groups.get(cand.lower())
        if hit:
            return hit
    # Lowest-priority fallback: strip a bare trailing digit (no
    # underscore) from each candidate already tried above.
    digit_candidates = []
    for cand in candidates:
        dm = re.match(r"^(.*[^\d])\d+$", cand)
        if dm and dm.group(1):
            digit_candidates.append(dm.group(1))
    for cand in digit_candidates:
        hit = groups.get(cand.lower())
        if hit:
            return hit
    return None


def _resolve_attire_texture_prefix(
    slot_name: str, texture_suffix: str, textures_dir: Path | None, log: list[str],
) -> str:
    """
    Picks the texture-name PREFIX build_default_mtl_chunk should rename a
    slot's materials to -- which is NOT always the same string as the
    slot's own name in the outer slot-name table.

    Added 2026-10-20 after a real, confirmed in-game bug (Cutie Suzuki,
    WILDJUMBO2K source mod): the mesh/material slot was named
    "_l_kneepad_A" (a leading underscore plus a Maya/Blender-style "_A"
    disambiguation suffix -- both purely scene-authoring artifacts), but
    the mod's actual shipped texture files are "l_kneepad_color.dds" /
    "l_kneepad_nrm.dds" / "l_kneepad_mask1.dds" -- no leading underscore,
    no "_A". build_attire_mtls_from_defaults previously used the slot name
    verbatim as the new texture-name prefix, producing a material that
    declared "_l_kneepad_A_color" etc. -- a name that matches NO real file
    on disk. Confirmed (by diffing this exact build against detourne's own
    independently hand-rebuilt, confirmed-working .mtls in MaterialEditor)
    that this was the ONLY structural difference between the broken and
    working files; every other slot's schema and content were identical
    bar harmless per-material random-seed bytes. The in-game symptom was
    not a missing/blank kneepad texture but the WHOLE character showing
    another character's (Miu Watanabe's) textures -- so an engine that
    can't resolve one slot's texture reference apparently doesn't just
    fail that one part, it seems to fall back hard for the whole asset.

    Strategy: trust the slot name as-is UNLESS a real file for it is
    missing from textures_dir AND a normalized candidate (stripping a
    leading underscore and/or a trailing Maya-style "_XXX" disambiguation
    suffix) matches a real file instead -- then use that real, confirmed
    name instead of guessing. If textures_dir isn't given, or NOTHING
    matches even after normalizing, falls back to the slot name verbatim
    (old behavior) but logs a warning so a silent mismatch like this one
    doesn't go unnoticed again.
    """
    if textures_dir is None or not textures_dir.is_dir():
        return slot_name

    real_prefixes: dict[str, str] = {}  # lowercase prefix -> real-cased prefix
    suffix_lower = texture_suffix.lower()
    for f in textures_dir.iterdir():
        if not f.is_file():
            continue
        stem_lower = f.stem.lower()
        if stem_lower.endswith(suffix_lower):
            real_prefixes[stem_lower[: -len(suffix_lower)]] = f.stem[: -len(suffix_lower)]

    if not real_prefixes:
        return slot_name

    if slot_name.lower() in real_prefixes:
        return slot_name

    candidates = [slot_name]
    stripped_underscore = slot_name[1:] if slot_name.startswith("_") else slot_name
    candidates.append(stripped_underscore)
    m = re.match(r"^(.*)_[A-Za-z0-9]+$", stripped_underscore)
    if m:
        candidates.append(m.group(1))
        m2 = re.match(r"^(.*)_[A-Za-z0-9]+$", m.group(1))
        if m2:
            candidates.append(m2.group(1))

    for cand in candidates[1:]:
        if cand.lower() in real_prefixes:
            real = real_prefixes[cand.lower()]
            log.append(
                f"  NOTE: slot '{slot_name}' has no matching "
                f"'{slot_name}{texture_suffix}.*' texture file -- found a real one "
                f"under '{real}{texture_suffix}.*' instead (stripped scene-authoring "
                f"decoration from the slot's own name) -- building this slot's "
                f"materials with texture name prefix '{real}', not '{slot_name}'"
            )
            return real

    log.append(
        f"  WARNING: slot '{slot_name}' has no matching '{slot_name}{texture_suffix}.*' "
        f"texture file in {textures_dir}, and no normalized variant matched one either "
        f"(real prefixes found there: {sorted(real_prefixes.values())}) -- building this "
        f"slot's materials with the slot's own name anyway, which may leave its texture "
        "reference(s) dangling in-game"
    )
    return slot_name


def fix_attire_hair_shader_misassignment(mtls_path: Path, log: list[str]) -> bool:
    """
    Fixes a real, confirmed bug in the Blender addon's own "Generate MTLs"
    attire export: it only assigns the real Hair shader to ONE hair-named
    slot and silently defaults every OTHER hair-pattern-named slot to the
    generic Gear shader instead. Confirmed 2026-10-02 on a real in-game
    test (Saya Kamitani, v6 build): source 2K26 has `hair_ALPHA`,
    `hair2_ALPHA`, `hair4_ALPHA` all on shader `yCh_Hair`; the addon's
    generated output had `hair_ALPHA` correctly on `yCh_hair` but
    `hair2_ALPHA`/`hair4_ALPHA` both silently defaulted to `yCh_gear` --
    exactly why they rendered static and slightly discolored in-game
    (Gear shader carries none of Hair's rendering/animation parameters).
    This is the SAME bug class already documented and manually worked
    around for BaseModel (`head2`/`eye_l` wrongly defaulting to
    `yCh_gear` there too) -- this is its attire-side counterpart, and the
    first one actually automated rather than fixed by hand in
    MaterialEditor.exe each time.

    Fix: find one slot matching the `hair*_ALPHA` naming pattern that
    already has the real Hair shader (the addon DOES get at least one
    right, every example seen so far) and use its own chunk, bytes and
    all, as a template. For every OTHER `hair*_ALPHA`-pattern slot that
    does NOT have the Hair shader, clone the good slot's chunk and
    retarget its internal texture-name-table references from the good
    slot's own texture prefix (e.g. "hair") to the broken slot's prefix
    (e.g. "hair2") via rename_mtl_chunk_texture_prefix -- the exact same
    splice-a-known-good-chunk-and-rename-its-textures mechanism already
    shipped for BaseModel materials (build_basemodel_mtls_from_donor),
    just reused within a single file instead of across a donor/
    destination pair. The destination's own texture files (e.g.
    "hair2_flow.dds", "hair2_rai.dds") already exist in the Textures
    folder either way -- the broken chunk just wasn't pointing at them
    with the right shader to use them.

    The outer slot name table is never touched: object names
    (`hair2_ALPHA` etc.) were always correct, only the chunk CONTENT was
    wrong, and _split_mtls_slots finds chunks by a sequential "MTL!" scan
    matched positionally to the name table -- not through any stored
    per-chunk byte-offset/length table -- so a differently-sized
    replacement chunk (Hair's parameter schema is much larger than
    Gear's) needs no header surgery at all, just placing it at the same
    position in slot order.

    Returns True and overwrites mtls_path in place if any fix was
    applied; returns False (leaving the file untouched) if every
    `hair*_ALPHA`-pattern slot already has the Hair shader or none exist
    at all -- so this is always safe to call unconditionally after an
    attire .mtls is generated, on every character, hair-quirks or not.
    """
    data = mtls_path.read_bytes()
    names, chunks, header = _split_mtls_slots(data)

    good_name = None
    good_prefix = None
    broken = []  # list of (name, prefix)
    for name in names:
        m = _ATTIRE_HAIR_SLOT_RE.match(name)
        if not m:
            continue
        prefix = m.group(1)  # "hair", "hair2", "hair4", ...
        shader, _subtype, _schema = _parse_mtl_chunk_schema(chunks[name])
        if shader.lower() == _HAIR_SHADER_NAME:
            if good_name is None:
                good_name, good_prefix = name, prefix
        else:
            broken.append((name, prefix))

    if good_name is None or not broken:
        return False

    log.append(
        f"  attire hair-shader fix: using '{good_name}' (real '{_HAIR_SHADER_NAME}' "
        f"shader) as a template for {len(broken)} hair slot(s) the addon's Generate "
        f"MTLs left on the wrong (Gear) shader: {[n for n, _ in broken]}"
    )
    good_chunk = chunks[good_name]
    for name, prefix in broken:
        new_chunk = rename_mtl_chunk_texture_prefix(
            good_chunk, good_prefix, prefix, log, slot_label=name,
        )
        chunks[name] = new_chunk

    out = bytearray(header)
    for name in names:
        out += chunks[name]

    # Same hard safety net as build_basemodel_mtls_from_donor: re-parse
    # before writing anything, confirm slot roster is unchanged and every
    # untouched chunk really is still byte-identical.
    recheck_names, recheck_chunks, _recheck_header = _split_mtls_slots(bytes(out))
    if recheck_names != names:
        raise ConversionError(
            f"INTERNAL SAFETY CHECK FAILED: hair-shader-fixed .mtls re-parses with "
            f"slot order/names {recheck_names}, expected {names} -- refusing to write."
        )
    for name in names:
        if name == good_name or any(n == name for n, _ in broken):
            continue
        if recheck_chunks[name] != chunks[name]:
            raise ConversionError(
                f"INTERNAL SAFETY CHECK FAILED: untouched slot '{name}' changed "
                "during the attire hair-shader fix -- refusing to write."
            )
    for name, _prefix in broken:
        fixed_shader, _subtype, _schema = _parse_mtl_chunk_schema(recheck_chunks[name])
        if fixed_shader.lower() != _HAIR_SHADER_NAME:
            raise ConversionError(
                f"INTERNAL SAFETY CHECK FAILED: '{name}' still reads back as shader "
                f"'{fixed_shader}' after the fix, expected '{_HAIR_SHADER_NAME}' -- "
                "refusing to write."
            )

    mtls_path.write_bytes(bytes(out))
    return True


def _mtl_looks_like_hidden_pointer(raw: bytes) -> bool:
    """Shared with patch_mtl_tone_values' own nested copy -- a 4-byte value
    that decodes as a denormalized near-zero float is actually a small
    integer (a byte offset into this chunk), not a real tuned value. See
    patch_mtl_tone_values' docstring for how this was established."""
    if raw == b"\x00\x00\x00\x00":
        return False
    try:
        return abs(struct.unpack("<f", raw)[0]) < 1e-30
    except struct.error:
        return False


def _mtl_collect_name_positions(chunk: bytes) -> tuple[int, list[str], list[str], int, int, dict]:
    """
    Returns (after_strings_offset, schema_names, table_entries, table_start,
    table_end, positions) where `positions` maps every bare schema name to
    its own chunk-relative start offset, and maps every non-empty real
    texture-table entry to the same, keyed as ("TABLE", entry_string) to
    keep the two namespaces apart (a bare schema name and a table entry can
    legitimately be spelled the same).
    """
    shader, subtype, schema = _parse_mtl_chunk_schema(chunk)
    off = 12

    def read_str(o: int) -> int:
        return chunk.index(b"\x00", o) + 1

    o = read_str(off)
    o = read_str(o)
    positions: dict = {}
    p = o
    for n in schema:
        positions[n] = p
        p = chunk.index(b"\x00", p) + 1
    _sse, table_start, table_end = _locate_mtl_texture_table(chunk)
    table_entries = [e.decode("latin1") for e in chunk[table_start:table_end].split(b"\x00")]
    p2 = table_start
    for e in table_entries:
        if e:
            positions[("TABLE", e)] = p2
        p2 += len(e.encode("latin1")) + 1
    return o, schema, table_entries, table_start, table_end, positions


def build_default_mtl_chunk(
    template_chunk: bytes,
    old_prefix: str,
    new_prefix: str,
    log: list[str],
    slot_label: str = "",
    fixed_name_buffer: bool = False,
    never_rename_suffixes: tuple[str, ...] = (),
) -> bytes:
    """
    Returns a new MTL! chunk for a DIFFERENT slot, built from a real,
    known-good "default settings" chunk of the SAME shader/subtype (one
    detourne created directly in MaterialEditor.exe by clearing an attire's
    materials and re-adding each one at its defaults -- yCh_gear::Normal
    for clothing, yCh_hair::NewHair_ColorChanging for hair) by renaming
    every texture-name reference from old_prefix to new_prefix, wherever
    it lives: a bare schema-list entry (e.g. yCh_gear's "<slot>_color",
    "<slot>_msk", "<slot>_nrm", "<slot>_rough" -- "tights_metal" is a
    fixed, never-renamed shared name, confirmed identical across every
    real yCh_gear slot checked) or the separate real texture table (e.g.
    yCh_hair's optional "<root>_msk" entry, present only when the material
    actually has a mask texture authored).

    Built 2026-10-16 after TWO real, confirmed in-game failures of a
    different, narrower approach (rename_mtl_chunk_texture_prefix alone,
    which only ever touches the separate real table and silently does
    nothing when a shader's texture names live in the bare schema list --
    see that function's own 2026-10-16 safety-net comment) crashed Hazuki's
    hair2_ALPHA fix, and after detourne independently built a correct,
    working attire.mtls for Miu Watanabe entirely by hand in
    MaterialEditor.exe and asked for the tool to do the same thing. This
    function is reverse-engineered directly from diffing detourne's own real,
    confirmed-working output: 16 real yCh_gear/Normal slots of 4 different
    name lengths, and 4 real yCh_hair/NewHair_ColorChanging slots of 2
    different name lengths, all independently created in MaterialEditor
    at defaults.

    Both shaders use the SAME exact-fit layout: the whole chunk grows or
    shrinks by exactly the net rename delta. Confirmed exact-byte-length-
    correct against detourne's own real "shoes"/"kneepad"/"ruffle_darkpink"
    yCh_gear chunks (4 different name lengths, all built from a "top"-
    shaped donor), AND -- corrected 2026-10-20 -- against two real
    yCh_hair chunks of DIFFERENT lengths (Kevin Knight's "hair_ALPHA" and
    "combinations_ALPHA", 4 vs 12 characters).

    CORRECTED 2026-10-21 (Ricochet "Arithmetic operation resulted in an
    overflow" bug, root-caused by decompiling the real MaterialEditor.dll):
    "exact-fit" above was *almost* right but glossed over one detail --
    the template's own trailing padding between the true end of the
    name/table blob and the fixed "flags" struct that follows it was
    being copied into the renamed output at a FIXED byte count, rather
    than recomputed for the new blob's own length. That's invisible
    whenever the net rename delta happens to leave that count unchanged
    (every case sampled through 2026-10-20, including Kevin Knight's
    same-residue "hair"->"combinations_ALPHA" rename), but corrupts the
    file whenever it doesn't (Ricochet's hair-default-template rename to
    "combinations2_ALPHA", net content delta +45, a different residue mod
    4 than Kevin Knight's +24): the property-count field gets read from
    the wrong byte offset, and MaterialEditor.exe's own parser doesn't
    catch the resulting garbage count -- it throws an uncaught
    OverflowException that aborts loading the WHOLE .mtls file, not just
    the one bad material. Fixed by trimming the template's baked-in
    padding back off the renamed blob and re-appending the correctly
    computed padding for the new length (a plain align-to-4 of the true
    new content boundary, plus a constant +2 bytes -- see the inline
    comments below for why), while keeping the declared_len field's own
    delta based on true content growth only, never the padding-byte-
    count change.

    `fixed_name_buffer` is now a VESTIGIAL parameter kept only so call
    sites can still say which shader they're building (used for logging);
    it no longer changes the growth behavior. The original reverse-
    engineering (2026-10-16) only ever had same-length real yCh_hair
    samples to compare (hair_ALPHA/hair2_ALPHA/hair3_ALPHA/hair4_ALPHA --
    all effectively 4-character prefixes), so "the reserved buffer never
    moves" looked like a real, fixed property of the shader. detourne's
    Kevin Knight conversion exposed the gap: "combinations_ALPHA" (a real,
    12-character hair-type slot name) needs a rename far longer than that
    sample range ever tested, and this function's old ConversionError-on-
    overflow refused to build it at all, silently falling back to the
    Blender addon's own broken raw output for that whole slot. detourne's own
    from-scratch "combinations_ALPHA" rebuild in MaterialEditor.exe proved
    the chunk (and its internal marker position) genuinely grows by
    exactly the expected rename delta, same as gear -- there was no real
    fixed limit, just an under-sampled template.

    never_rename_suffixes: table/schema entries ending in one of these
    suffixes are left COMPLETELY untouched even though they match
    old_prefix's own naming pattern. Found the same way, from the same
    Kevin Knight file: yCh_hair's real texture table always has FOUR
    entries (e.g. "hair_msk", "hair_opacity", "hair_ID", "hair_root"), but
    only the first (the material's own "_msk" mask) is ever renamed to
    match a new slot's prefix -- "hair_opacity"/"hair_ID"/"hair_root" stay
    literally "hair_"-prefixed in EVERY real yCh_hair material checked so
    far, including "combinations_ALPHA" (whose own flow/rai/msk names DO
    correctly read "combinations_*"). They're evidently fixed, shared
    identifiers, not per-character texture names -- the caller passes
    ("_opacity", "_ID", "_root") for yCh_hair and leaves this empty for
    yCh_gear (whose own fixed shared name, "tights_metal", never matches
    old_prefix's pattern in the first place, so it needed no special
    case).

    Every "hidden pointer" property (not just "@ref"-suffixed ones --
    see patch_mtl_tone_values' docstring for why that naming convention
    alone isn't reliable) whose current value resolves, via
    _MTL_HIDDEN_POINTER_BASE, to the EXACT chunk-relative start of a name
    this function is renaming is recomputed to the renamed name's new
    position. One confirmed real quirk: some such properties (e.g.
    yCh_hair's g_mPixMsk) store a value exactly ONE LESS than the name
    they reference, not the name's own exact start -- handled as a
    fallback, never as the primary match, so a coincidental off-by-one
    elsewhere can't be silently misattributed.

    Every property this function cannot positively identify as pointing
    at a specific renamed name is left byte-for-byte untouched -- this
    includes a small (worst case, roughly a dozen bytes), consistently
    non-deterministic region observed even between two of detourne's own
    independently-created same-shader materials with NO rename between
    them at all (e.g. his own "top" vs "shoes", both built fresh at
    defaults) -- almost certainly a per-material random seed or similar,
    not something tied to this chunk's identity, and not something this
    function should try to fabricate a "matching" value for.
    """
    # Hoisted above the old_prefix == new_prefix early return below (2026-
    # 10-22 fix, see that branch's own comment for why) -- these used to
    # be defined only ~140 lines further down, right next to the one place
    # they were used.
    _GEAR_HASH_PROPS_IN_ORDER = [
        "colorMap@ref", "g_mPixMsk@ref", "g_nrmMap@ref",
        "metalMap@ref", "normalMap@ref", "roughnessMap@ref",
    ]
    _GEAR_REF_HASH_REGION_OFFSET_IN_REST = 318  # == 816 - marker(498) for this exact template file
    _GEAR_REF_FIELDS_TO_ZERO = {"colorMap@ref", "g_nrmMap@ref", "normalMap@ref"}

    def _zero_stale_gear_ref_hashes(chunk: bytes, marker_pos: int, chunk_schema: list[str]) -> tuple[bytes, list[str]]:
        """
        Shared by both the no-rename-needed early return just below and the
        main rebuild path further down -- factored out 2026-10-22 after
        confirming (Saya Kamitani attire, "resaving isn't generating a new
        @ref") that the early return was silently skipping this fix
        entirely for any slot whose real name already equals the default
        template's own baked prefix (e.g. a slot literally named "top",
        same as _ATTIRE_GEAR_DEFAULT_TEMPLATE's own "top_color"-derived
        prefix): old_prefix == new_prefix made build_default_mtl_chunk
        return template_chunk completely untouched, including its stale,
        non-zero colorMap@ref/g_nrmMap@ref/normalMap@ref hash values (baked
        in from whatever donor character MaterialEditor.exe originally
        built this bundled template from). Since MaterialEditor's own
        AutoSyncRefs only resolves fields that are ALREADY zero (see the
        2026-10-21 comment below), opening and re-saving a .mtls with this
        slot in MaterialEditor.exe did nothing for it -- exactly that
        symptom.
        """
        hash_props = [n for n in chunk_schema if n.endswith("@ref")]
        zeroed_log: list[str] = []
        if hash_props != _GEAR_HASH_PROPS_IN_ORDER:
            return chunk, zeroed_log
        region_off = _GEAR_REF_HASH_REGION_OFFSET_IN_REST
        region_len = 8 * len(hash_props)
        abs_start = marker_pos + region_off
        if not (0 <= abs_start and abs_start + region_len <= len(chunk)):
            return chunk, zeroed_log
        out_bytes = bytearray(chunk)
        for i, name in enumerate(hash_props):
            slot_off = abs_start + i * 8
            slot = bytes(out_bytes[slot_off:slot_off + 8])
            if name in _GEAR_REF_FIELDS_TO_ZERO and slot != b"\x00" * 8:
                out_bytes[slot_off:slot_off + 8] = b"\x00" * 8
                zeroed_log.append(f"{name}: {slot.hex()} -> zeroed (was the stale template default)")
        return bytes(out_bytes), zeroed_log

    if old_prefix == new_prefix:
        _o_np, schema_np, _table_entries_np, _table_start_np, table_end_np, _old_positions_np = _mtl_collect_name_positions(template_chunk)
        marker_np = table_end_np + 2
        while template_chunk[marker_np] == 0:
            marker_np += 1
        out_np, ref_zeroed_np = _zero_stale_gear_ref_hashes(template_chunk, marker_np, schema_np)
        if ref_zeroed_np:
            log.append(
                f"  {slot_label}: zeroed {len(ref_zeroed_np)} stale @ref asset-binding "
                "hash(es) on the no-rename-needed path (this slot's real name already "
                "matches the default template's own prefix, so the usual rename/splice "
                "below is skipped, but the stale-hash fix still needs to run -- see "
                "2026-10-22 comment above):"
            )
            for line in ref_zeroed_np:
                log.append(f"    {line}")
        return out_np

    o, schema, table_entries, table_start, table_end, old_positions = _mtl_collect_name_positions(template_chunk)
    old_lower = old_prefix.lower()
    never_rename_lower = tuple(s.lower() for s in never_rename_suffixes)

    def rename(n: str) -> str:
        n_lower = n.lower()
        if any(n_lower.endswith(suf) for suf in never_rename_lower):
            return n
        if n_lower == old_lower:
            return new_prefix
        if n_lower.startswith(old_lower + "_"):
            return new_prefix + n[len(old_prefix):]
        return n

    marker = table_end + 2
    while template_chunk[marker] == 0:
        marker += 1
    region = template_chunk[o:marker]

    renames = [(n, rename(n)) for n in schema if rename(n) != n]
    renames += [(e, rename(e)) for e in table_entries if e and rename(e) != e]
    renames.sort(key=lambda pair: -len(pair[0]))  # longest-first: avoid a short name matching inside a longer one

    region_bytes = region
    for old_n, new_n in renames:
        region_bytes = region_bytes.replace(old_n.encode("latin1") + b"\x00", new_n.encode("latin1") + b"\x00")
    # The chunk grows or shrinks by the net rename delta -- BUT `region`
    # (template_chunk[o:marker]) includes the TEMPLATE's own baked-in
    # trailing padding between the true end of the name/table blob and
    # `marker`. Plain substring replacement never touches those bytes, so
    # it preserves their COUNT verbatim -- which is only coincidentally
    # correct when the renaming's net byte-length change is itself a
    # multiple of 4. Confirmed via decompiled MaterialEditor IL and
    # reproduced against the real MaterialEditor.dll: whenever net_delta
    # % 4 != 0 (e.g. Ricochet's hair-default-template rename to
    # "combinations2_ALPHA", net delta +45), the template's padding-byte-
    # count is wrong for the new blob length, shifting every subsequent
    # field read in the chunk by the discrepancy and corrupting the
    # property-count field -- which crashes MaterialEditor.exe with
    # "Arithmetic operation resulted in an overflow" (an uncaught
    # OverflowException that aborts the whole file load, not just this
    # material).
    #
    # The real parser's own alignment call (MtlFileService.AlignTo) is a
    # plain minimal align-to-4 of the true content boundary -- confirmed
    # directly against the decompiled IL AND against a faithful C#
    # reimplementation run side-by-side on real, known-working files. But
    # `marker` here (found by scanning forward from the name table for the
    # first non-zero byte) lands 2 bytes PAST that true align-to-4 point in
    # every real sample checked (yCh_hair and yCh_gear alike, multiple
    # characters/attires) -- because the two bytes right after the true
    # align point are themselves a flags field ("u1") that is always zero
    # in every default/freshly-created material sampled, so the scan
    # mistakes it for more name-table padding. That 2-byte offset between
    # `marker` and the true align point is constant (verified against 20+
    # real chunks spanning residues 0/1/3 mod 4) and must be preserved
    # exactly, because `rest` below is sliced at `marker`, unchanged, and
    # already assumes those 2 bytes were consumed before it starts. Fix:
    # recompute the trailing byte count as [minimal align-to-4 of the new
    # content boundary] + [that same constant 2-byte offset], instead of
    # copying the template's padding-byte-count verbatim.
    true_blob_end_template = table_end + 1  # last table entry's NUL terminator, +1
    pad_len_template = marker - true_blob_end_template
    pure_bytes = region_bytes[:len(region_bytes) - pad_len_template] if pad_len_template else region_bytes
    new_blob_end_abs = o + len(pure_bytes)
    correct_pad_len = ((-new_blob_end_abs) % 4) + 2
    region_bytes = pure_bytes + b"\x00" * correct_pad_len
    # IMPORTANT: declared_len (the chunk[8:12] field) must track only the
    # TRUE content growth -- never the padding-byte-count change -- because
    # MaterialEditor's own parser derives the post-padding position itself
    # via AlignTo(stream,4) *after* consuming exactly declared_len bytes;
    # declared_len always equals the true blob end (true_blob_end_template
    # for the template, confirmed empirically: base==true_blob_end for every
    # real file checked), excluding any trailing padding. Patching it with
    # the padding-inclusive byte delta would shift the parser's aligned
    # landing spot away from where `rest` (the flags struct) actually
    # starts in the spliced output below -- reintroducing the exact bug
    # this fix exists to close, just in the opposite direction.
    pure_content_delta = len(pure_bytes) - (true_blob_end_template - o)
    net_delta = pure_content_delta

    new_positions: dict = {}
    p = o
    pp = 0
    for n in schema:
        new_positions[rename(n)] = o + pp
        pp = region_bytes.index(b"\x00", pp) + 1
    pp += 1  # schema list's own double-null terminator (one null already consumed above)
    for e in table_entries:
        if e:
            new_positions[("TABLE", rename(e))] = o + pp
            pp = region_bytes.index(b"\x00", pp) + 1

    rest = bytearray(template_chunk[marker:])

    # 2026-10-21 (Megumi Kudo pink-attire bug, confirmed root cause):
    # yCh_gear's "@ref" properties (colorMap@ref, g_mPixMsk@ref,
    # g_nrmMap@ref, metalMap@ref, normalMap@ref, roughnessMap@ref) do
    # NOT live in the per-schema flat value blob at all -- that blob's
    # own slot for each of them (at _mtl_chunk_value_blob_start-derived
    # offsets) is a decoy, always 00000000 in every real file checked,
    # broken or working. Their REAL value is an 8-byte asset-binding
    # hash, stored in its own separate, contiguous region: exactly
    # 8*len(hash_props) bytes, one slot per "@ref" schema entry IN
    # SCHEMA ORDER, sitting immediately before that flat blob. Found by
    # brute-force byte search for detourne's own real, screenshotted hash
    # values (both his working Cutie Suzuki/Kevin Knight files and this
    # template), confirmed via MaterialEditor.exe's own "Audit @ref
    # Texture Bindings" tool, and confirmed via a live in-game test
    # (2026-10-21): this default template has 2 of its own 6 hash slots
    # (colorMap@ref, and g_nrmMap@ref/normalMap@ref sharing one stale
    # value) baked in from WHATEVER real character MaterialEditor.exe
    # built this template from -- build_default_mtl_chunk used to copy
    # them byte-for-byte unmodified into every rebuilt slot, which is
    # the actual root cause of attires showing another character's (or
    # no) texture in-game: MaterialEditor's Audit tool flags these as
    # "NO LOCAL MATCH" since they don't resolve to any file in the
    # rebuilt mod's own folder.
    #
    # The fix is NOT to compute a correct hash ourselves -- the real
    # algorithm is unknown (tried FNV1a/FNV1/MurmurHash64A/MurmurHash3/
    # CRC64/xxHash64/truncated MD5-SHA1-SHA256 against texture name,
    # raw file bytes, and path strings, in many variants; none matched
    # known real (name/content -> hash) pairs -- it's most likely an
    # internal MaterialEditor.exe asset-registry lookup, not a pure
    # function of the name or file). Instead: zero these 3 fields, same
    # as a BRAND NEW material MaterialEditor.exe itself creates (its own
    # UI shows a freshly-added material's @ref fields as
    # 0000000000000000 until the file is next saved). MaterialEditor has
    # "AutoSyncRefs" enabled by default (its own appsettings.json) --
    # detourne confirmed live, in-game, that opening a file with these 3
    # fields zeroed and doing ONE Save pass in MaterialEditor.exe
    # resolves them to real, working, non-identical hashes and fixes the
    # texture. That resolve-on-save step is NOT reproduced here (no
    # batch/CLI mode exists in MaterialEditor.exe) -- every .mtls this
    # function helps build still needs one manual Open+Save pass in
    # MaterialEditor.exe before this fix actually takes effect in game.
    # Zeroing here just stops us from writing a KNOWN-WRONG non-zero
    # value that MaterialEditor's own save-time AutoSyncRefs won't touch
    # (it only resolves fields that are already zero).
    hash_props = [n for n in schema if n.endswith("@ref")]
    _GEAR_HASH_PROPS_IN_ORDER = [
        "colorMap@ref", "g_mPixMsk@ref", "g_nrmMap@ref",
        "metalMap@ref", "normalMap@ref", "roughnessMap@ref",
    ]
    _GEAR_REF_HASH_REGION_OFFSET_IN_REST = 318  # == 816 - marker(498) for this exact template file
    _GEAR_REF_FIELDS_TO_ZERO = {"colorMap@ref", "g_nrmMap@ref", "normalMap@ref"}
    ref_zeroed = []
    if hash_props == _GEAR_HASH_PROPS_IN_ORDER:
        region_off = _GEAR_REF_HASH_REGION_OFFSET_IN_REST
        region_len = 8 * len(hash_props)
        if not (0 <= region_off and region_off + region_len <= len(rest)):
            raise ConversionError(
                f"INTERNAL SAFETY CHECK FAILED: '{slot_label}' -- expected "
                f"gear template's @ref hash region at rest[{region_off}:"
                f"{region_off + region_len}] but rest is only {len(rest)} "
                "bytes. Template file changed shape; fix "
                "_GEAR_REF_HASH_REGION_OFFSET_IN_REST by hand rather than guessing."
            )
        for i, name in enumerate(hash_props):
            slot_off = region_off + i * 8
            slot = bytes(rest[slot_off:slot_off + 8])
            if name in _GEAR_REF_FIELDS_TO_ZERO and slot != b"\x00" * 8:
                rest[slot_off:slot_off + 8] = b"\x00" * 8
                ref_zeroed.append(f"{name}: {slot.hex()} -> zeroed (was the stale template default)")

    vbs = _mtl_chunk_value_blob_start(template_chunk, schema)
    fixups, unresolved = [], []
    for i, name in enumerate(schema):
        off_v = vbs + i * 4
        raw = template_chunk[off_v:off_v + 4]
        if not _mtl_looks_like_hidden_pointer(raw):
            continue
        val = struct.unpack("<I", raw)[0]
        pointed_abs = val + _MTL_HIDDEN_POINTER_BASE
        target_key = next((k for k, pos in old_positions.items() if pos == pointed_abs), None)
        off_by_one = False
        if target_key is None:
            target_key = next((k for k, pos in old_positions.items() if pos == pointed_abs + 1), None)
            off_by_one = True
        if target_key is None:
            unresolved.append(name)
            continue
        new_key = ("TABLE", rename(target_key[1])) if isinstance(target_key, tuple) else rename(target_key)
        new_pos = new_positions.get(new_key)
        if new_pos is None:
            unresolved.append(f"{name} (target '{target_key}' had no new position)")
            continue
        if off_by_one:
            new_pos -= 1
        new_val = new_pos - _MTL_HIDDEN_POINTER_BASE
        if new_val != val:
            struct.pack_into("<I", rest, off_v - marker, new_val)
            fixups.append(f"{name}: {val}->{new_val} (follows '{target_key}')")

    out = template_chunk[:o] + region_bytes + bytes(rest)
    length_field = struct.unpack("<I", bytes(template_chunk[8:12]))[0]
    out_b = bytearray(out)
    struct.pack_into("<I", out_b, 8, length_field + net_delta)
    out = bytes(out_b)

    # Safety net: re-parse what we just built and confirm it comes back
    # with exactly the slot's own intended texture names and a length
    # that matches the chunk's own internal length field expectations --
    # never trust the byte-splicing math silently.
    r_shader, r_subtype, r_schema = _parse_mtl_chunk_schema(out)
    if [rename(n) for n in schema] != r_schema:
        raise ConversionError(
            f"INTERNAL SAFETY CHECK FAILED: '{slot_label}' built chunk's schema "
            f"doesn't match the expected renamed list -- refusing to use it."
        )

    log.append(
        f"  {slot_label}: built from '{old_prefix}'-prefixed default template "
        f"({len(renames)} name(s) renamed, {len(fixups)} pointer fixup(s)"
        + (f", {len(unresolved)} unresolved value(s) left untouched (expected -- "
           "see function docstring)" if unresolved else "") + f"), {len(out)} bytes "
        f"(template was {len(template_chunk)})"
    )
    if ref_zeroed:
        log.append(
            f"  {slot_label}: zeroed {len(ref_zeroed)} stale @ref asset-binding "
            "hash(es) instead of copying the template's own stale value (see "
            "2026-10-21 comment above) -- REMINDER: this file still needs ONE "
            "manual Open+Save pass in MaterialEditor.exe (AutoSyncRefs on) "
            "before these fields resolve to real values and the fix takes "
            "effect in game:"
        )
        for line in ref_zeroed:
            log.append(f"    {line}")
    return out


_ATTIRE_GEAR_DEFAULT_TEMPLATE = _TEMPLATES_DIR / "generic_2k25_attire_defaults" / "gear_default.mtl_chunk"
_ATTIRE_HAIR_DEFAULT_TEMPLATE = _TEMPLATES_DIR / "generic_2k25_attire_defaults" / "hair_default.mtl_chunk"


def build_attire_mtls_from_defaults(
    source_mtls_path: Path,
    out_path: Path,
    log: list[str],
    textures_dir: Path | None = None,
) -> None:
    """
    Builds a complete attire .mtls using build_default_mtl_chunk and the
    two bundled template chunks for every slot -- the tool-automated
    equivalent of what detourne does by hand in MaterialEditor.exe: clear
    every material, then re-add each one at its shader's default settings
    (yCh_gear::Normal for every slot whose real textures are a *_color +
    *_nrm pair, yCh_hair::NewHair_ColorChanging for every slot whose real
    textures include a *_flow or *_rai file).

    This exists because the Blender addon's own "Generate MTLs" attire
    export has been found, on a real character (Miu Watanabe,
    2026-10-16), to produce structurally broken yCh_gear materials: real
    pointers wired to texture-table entries (_metal/_normal/_roughness)
    this project's source mods never actually ship, while the slot's own
    real color texture reference isn't wired as a pointer at all --
    "none of the textures... work" was the exact, confirmed real-world
    symptom. Building every material fresh from a known-good default,
    the same way detourne's own fix does, sidesteps that addon bug entirely
    rather than trying to repair its output.

    source_mtls_path is the addon's own "Generate MTLs" output -- its
    per-chunk CONTENT is what this function replaces entirely, but its
    header and outer slot-name table (order included) are trustworthy
    and reused as-is, same principle already established for BaseModel
    (see _convert_base_model's dst_name_source priority) and for
    fix_attire_hair_shader_misassignment.

    textures_dir: the source attire's own Textures folder. detourne's
    proposal, 2026-10-20: "work backwards" from what's actually shipped --
    rather than guessing a slot's shader from its own name (the old
    _ATTIRE_HAIR_SLOT_RE approach only ever recognized "hair"-pattern
    names, so a "beard" slot -- also yCh_hair per detourne's own confirmed
    manual process -- would have been misbuilt as yCh_gear), each slot is
    matched (via _match_slot_to_texture_group, tolerating scene-authoring
    decorations like a leading underscore or trailing "_A"/"_B") against
    the real texture groups _scan_attire_texture_groups finds on disk, and
    BOTH the shader choice and the texture-name prefix come from that real
    match, not from the slot's own name. This also covers the confirmed
    real in-game bug this same mechanism replaced
    (_resolve_attire_texture_prefix's docstring has the Cutie Suzuki
    kneepad case history). If textures_dir is omitted, or a slot has no
    matching texture group at all, falls back to the old name-pattern
    guess (_ATTIRE_HAIR_SLOT_RE) and logs a warning, since building
    *something* with a best-effort guess beats refusing to convert.

    Per-slot failure isolation (added 2026-10-20 after a real, confirmed
    case -- Kevin Knight's "combinations_ALPHA" hair slot needed a rename
    too long for yCh_hair's fixed reserved name buffer, see
    build_default_mtl_chunk's ConversionError): if building ONE slot from
    its default template raises ConversionError, ONLY that slot falls
    back to the addon's own raw "Generate MTLs" chunk for THAT slot (the
    same known-fragile content described above, logged loudly). Every
    OTHER slot still gets the full default-template rebuild. The caller
    previously fell back to copying the addon's ENTIRE raw file the
    moment ANY one slot failed this way -- silently discarding every
    other slot's correct rebuild along with the one bad slot. That's been
    moved in here precisely so a single oversized rename can't take the
    whole attire down with it.
    """
    if not _ATTIRE_GEAR_DEFAULT_TEMPLATE.is_file() or not _ATTIRE_HAIR_DEFAULT_TEMPLATE.is_file():
        raise ConversionError(
            "missing bundled attire default-material template(s) -- expected "
            f"{_ATTIRE_GEAR_DEFAULT_TEMPLATE} and {_ATTIRE_HAIR_DEFAULT_TEMPLATE}"
        )
    slot_names, src_chunks, header = _split_mtls_slots(source_mtls_path.read_bytes())

    gear_template = _ATTIRE_GEAR_DEFAULT_TEMPLATE.read_bytes()
    hair_template = _ATTIRE_HAIR_DEFAULT_TEMPLATE.read_bytes()
    _gshader, _gsubtype, gschema = _parse_mtl_chunk_schema(gear_template)
    _hshader, _hsubtype, hschema = _parse_mtl_chunk_schema(hair_template)
    gear_prefix = next(n for n in gschema if n.lower().endswith("_color"))[:-len("_color")]
    hair_prefix = next(n for n in hschema if n.lower().endswith("_flow"))[:-len("_flow")]

    texture_groups = _scan_attire_texture_groups(textures_dir, log)

    def _build_one_slot(name: str) -> bytes:
        match = _match_slot_to_texture_group(name, texture_groups)
        if match is not None:
            shader, real_prefix = match
            if shader == "hair":
                if real_prefix != name:
                    log.append(
                        f"  NOTE: slot '{name}' matched real texture group "
                        f"'{real_prefix}' ({'/'.join(s + '.dds' for s in _HAIR_TEXTURE_SUFFIXES)}) -- "
                        f"building as yCh_hair with texture name prefix '{real_prefix}', not '{name}'"
                    )
                else:
                    log.append(f"  slot '{name}' matched real *_flow/*_rai textures -- building as yCh_hair")
                return build_default_mtl_chunk(
                    hair_template, hair_prefix, real_prefix, log, slot_label=name, fixed_name_buffer=True,
                    never_rename_suffixes=("_opacity", "_ID", "_root"),
                )
            else:
                if real_prefix != name:
                    log.append(
                        f"  NOTE: slot '{name}' matched real texture group "
                        f"'{real_prefix}' (*_color.dds) -- building as yCh_gear with "
                        f"texture name prefix '{real_prefix}', not '{name}'"
                    )
                else:
                    log.append(f"  slot '{name}' matched a real *_color.dds texture -- building as yCh_gear")
                return build_default_mtl_chunk(
                    gear_template, gear_prefix, real_prefix, log, slot_label=name, fixed_name_buffer=False,
                )

        # No real texture group matched this slot at all (no textures_dir
        # given, or this slot's real files don't form a recognizable
        # *_flow/*_rai or *_color+*_nrm group) -- fall back to the old
        # name-pattern guess rather than refusing to convert, but say so.
        m = _ATTIRE_HAIR_SLOT_RE.match(name)
        if m:
            log.append(
                f"  WARNING: slot '{name}' has no matching real texture group in "
                f"{textures_dir} -- guessing yCh_hair from its own name alone "
                "(UNVERIFIED against real texture files, may be wrong)"
            )
            new_prefix = _resolve_attire_texture_prefix(m.group(1), "_flow", textures_dir, log)
            return build_default_mtl_chunk(
                hair_template, hair_prefix, new_prefix, log, slot_label=name, fixed_name_buffer=True,
                never_rename_suffixes=("_opacity", "_ID", "_root"),
            )
        else:
            log.append(
                f"  WARNING: slot '{name}' has no matching real texture group in "
                f"{textures_dir} -- guessing yCh_gear from its own name alone "
                "(UNVERIFIED against real texture files, may be wrong)"
            )
            new_prefix = _resolve_attire_texture_prefix(name, "_color", textures_dir, log)
            return build_default_mtl_chunk(
                gear_template, gear_prefix, new_prefix, log, slot_label=name, fixed_name_buffer=False,
            )

    chunks: dict[str, bytes] = {}
    for name in slot_names:
        try:
            chunks[name] = _build_one_slot(name)
        except ConversionError as exc:
            log.append(
                f"  WARNING: building '{name}' from its default template failed "
                f"({exc}) -- falling back to the addon's own 'Generate MTLs' "
                f"content for THIS SLOT ONLY (may have broken/dangling textures, "
                "see the 2026-10-16 Miu Watanabe finding above); every other slot "
                "in this attire is unaffected and still gets the full rebuild"
            )
            chunks[name] = src_chunks[name]

    out = bytearray(header)
    for name in slot_names:
        out += chunks[name]

    recheck_names, recheck_chunks, recheck_header = _split_mtls_slots(bytes(out))
    if recheck_names != slot_names:
        raise ConversionError(
            f"INTERNAL SAFETY CHECK FAILED: built attire .mtls re-parses with slot "
            f"roster {recheck_names}, expected {slot_names} -- refusing to write."
        )
    if recheck_header != header:
        raise ConversionError("INTERNAL SAFETY CHECK FAILED: header region changed unexpectedly -- refusing to write.")
    for name in slot_names:
        if recheck_chunks[name] != chunks[name]:
            raise ConversionError(
                f"INTERNAL SAFETY CHECK FAILED: slot '{name}' doesn't read back "
                "identically to what was built -- refusing to write."
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(out))
    log.append(f"  built attire .mtls from scratch (default-settings templates, header/slot order from {source_mtls_path.name}) -> {out_path} ({len(slot_names)} slot(s))")


class ConversionError(Exception):
    """Raised when the source folder doesn't look like a WWE2K26 character mod."""


@dataclasses.dataclass
class AttireTarget:
    """User-assigned mapping for one source attire folder."""
    source_folder_name: str        # e.g. "178_Alt_Attire"
    target_id: str                 # e.g. "312"
    is_primary: bool                # True -> "_Default_Attire", False -> "_Alt_Attire"


@dataclasses.dataclass
class ConversionPlan:
    source_root: Path
    output_parent: Path
    target_slot_id: str
    target_roster_name: str        # human label used in the output top folder name
    attires: list[AttireTarget]
    drop_materials: bool = True    # per user decision: drop Materials/*.jmtl

    # -- Blender-driven skeleton conversion (see blender_runner.py) --------
    # When set, these are used INSTEAD of the raw 2K26 source .mcd files:
    # already armature-redirected onto a real WWE2K25 donor skeleton (fixes
    # crashes/distortion from meshes bound to the wrong-version armature --
    # see the project doc's "armature redirect" findings). When absent
    # (Blender integration wasn't run, e.g. blender.exe wasn't found), the
    # tool falls back to the older byte-patch-only path (version tag +
    # terminator fix on the raw source file), which carries the known
    # armature-binding risk that motivated the Blender path in the first
    # place -- always prefer running Blender when it's available.
    blender_basemodel_mcd: Path | None = None
    blender_attire_mcds: dict[str, Path] = dataclasses.field(default_factory=dict)  # keyed by AttireTarget.source_folder_name

    # BaseModel materials generated by the addon's own "Generate MTLs"
    # export option during the Blender pass (added 2026-10-02, mirroring
    # blender_attire_mtls below -- see blender_headless_convert.py's
    # export_args_basemodel comment for why: the game binds a material to
    # a mesh by matching the material SLOT name to the mesh OBJECT name
    # exactly, and this is the only BaseModel materials source whose slot
    # names are GUARANTEED to match the real source mesh's object names
    # (e.g. "head2", not "head"), since the addon derives them directly
    # from the scene rather than from a template or donor file authored
    # for a different mesh. Used INSTEAD of custom_donor_basemodel_mtls /
    # the generic template / the source's own file when present -- see
    # _convert_base_model's priority order.
    blender_basemodel_mtls: Path | None = None

    # Attire materials generated by the addon's own "Generate MTLs" export
    # option during the Blender pass (see blender_headless_convert.py),
    # keyed by AttireTarget.source_folder_name -- used INSTEAD of patching
    # the source's own attire .mtls when present. Confirmed necessary
    # (2026-09-20, Lio Rush): a source attire .mtls can be a hollow/
    # placeholder file (detected by _mtls_looks_hollow below) with no real
    # texture data, and the game silently renders NO attire at all rather
    # than erroring -- the addon's own "Generate MTLs" checkbox is its
    # documented fix for this, now run automatically instead of requiring
    # a manual re-export.
    blender_attire_mtls: dict[str, Path] = dataclasses.field(default_factory=dict)

    # BaseModel materials: use the bundled generic real WWE2K25 template
    # (recommended -- see _GENERIC_BASEMODEL_MTLS) instead of patching the
    # source's own baseModel.mtls.
    use_generic_basemodel_mtls: bool = True

    # -- Custom per-character donor (see app.py's "Donor character folder"
    # field), EXPERIMENTAL, added 2026-09-23 -------------------------------
    # Instead of the bundled generic (Blake Christian) donor, use a specific
    # real WWE2K25 character's own folder as the donor for BOTH the Blender
    # armature-redirect pass (wired in app.py -- run_blender_conversion's
    # donor_basemodel_mcd param just points at this donor's baseModel.mcd
    # instead of the bundled one, no new plumbing needed there) AND these
    # two fields here. Rationale (detourne, 2026-09-23, Saya Kamitani): the
    # bundled generic donor's materials aren't fully character-agnostic
    # after all (teeth/eyelash color mismatches seen on two different
    # characters) -- using a donor that's either the SAME character's own
    # real WWE2K25 release, or a different attire of a similar-looking
    # character, should fix that. Also lets a WWE2K26 source with dynamic
    # hair (.ycl) borrow a real, known-working WWE2K25 hair setup from the
    # donor -- hair conversion is otherwise completely unsolved (see the
    # project doc's Athena/.ywa investigation).
    #
    # When None / empty, behavior is 100% unchanged from every character
    # converted before this field existed.
    custom_donor_basemodel_mtls: Path | None = None

    # Donor's own real WWE2K25 hair companion files, keyed by extension
    # (".ycl", ".hair_ywa", ".hair_corr_ywa", ".other_ywa",
    # ".other_corr_ywa" -- whichever the donor's attire actually has).
    # Applied to EVERY attire in this plan (a single donor hairstyle is
    # assumed to apply to the whole character, per detourne's "different
    # attires, similar hairstyle" framing) -- overrides copying the
    # source's own .ycl (2K26-format, likely incompatible) and adds the
    # _ywa files this tool otherwise never touches at all.
    donor_hair_files: dict[str, Path] = dataclasses.field(default_factory=dict)

    # Donor's own attire Textures folder (added 2026-09-25, alongside the
    # donor hair MESH transplant in blender_headless_convert.py). The
    # transplanted donor hair mesh's material/texture references are the
    # DONOR's own texture file names, which never lived in the source's
    # own Textures folder -- without this, the transplanted mesh would
    # render untextured/black in-game even though the geometry and
    # weights are correct. Filled in ONLY (never overrides anything the
    # source itself provides -- see _fill_missing_donor_textures), same
    # merge-safe approach already used for the BaseModel materials case.
    donor_attire_textures_dir: Path | None = None

    # Skip copying the source's own .ycl file ("Option 4" rigid-hair-to-head,
    # added 2026-09-25). Rationale: the raw source .ycl is copied VERBATIM
    # (still WWE2K26-format bytes -- never run through YclEditor.exe here),
    # and for a bone-chain hair rig (Hair_bNN/Hair_bNN_MM physics entries,
    # e.g. Shota Umino) that file describes physics for bones that, under
    # Option 4, deliberately no longer exist anywhere in the exported mesh's
    # weighting (every hair vertex is pinned rigidly to J_Head instead).
    # A first real in-game test of Option 4 (2026-09-25) showed the EXACT
    # same catastrophic stretched-pillar artifact as every earlier failed
    # Option 3 attempt, despite the Blender-side log confirming the weight
    # pinning itself ran correctly (traced through the addon's own export
    # code in blender_utils.py -- getVertexWeights/getBoneIndex -- and
    # confirmed a single-bone, weight-1.0 vertex would export cleanly). That
    # makes the untouched, unconverted .ycl the leading suspect: the 2K25
    # engine may still be loading its raw 2K26-format bone-chain physics
    # setup and producing garbage transforms, entirely independent of the
    # mesh's own weights. When this flag is set, the .ycl file rule below is
    # skipped for every attire in the plan (a donor .ycl, if one is set via
    # donor_hair_files, still takes priority and is unaffected by this flag
    # -- that's a real, known-working WWE2K25 file, not the source's raw
    # one). UNCONFIRMED as of writing -- see README's Option 4 section.
    skip_source_ycl: bool = False

    log: list[str] = dataclasses.field(default_factory=list)


def _sanitize_name_fragment(name: str) -> str:
    """Turn a free-text roster name into a filesystem-safe folder-name fragment."""
    keep = []
    for ch in name.strip():
        if ch.isalnum() or ch in ("_",):
            keep.append(ch)
        elif ch in (" ", "-"):
            keep.append("_")
        # else: drop the character
    frag = "".join(keep).strip("_")
    return frag or "Character"


def find_character_root(source_root: Path) -> Path:
    """
    Locate the actual "<charname>_A0"-style folder inside the selected source
    folder. Accepts either that folder directly, or a parent folder that
    contains exactly one such folder (as in the training examples, where
    source_root was e.g. ".../WWE2k26/blakechristian_A0").
    """
    if not source_root.is_dir():
        raise ConversionError(f"Source folder does not exist: {source_root}")

    # Case 1: user pointed straight at the "<name>_A0" folder.
    if (source_root / "BaseModel").is_dir():
        return source_root

    # Case 2: user pointed at a parent folder (e.g. a "WWE2k26" folder, or the
    # character's own top folder) -- look one level down for a folder that
    # contains a BaseModel subfolder.
    candidates = [
        p for p in source_root.iterdir()
        if p.is_dir() and (p / "BaseModel").is_dir()
    ]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise ConversionError(
            "Found more than one candidate character folder under "
            f"{source_root} (each containing a BaseModel folder): "
            f"{[c.name for c in candidates]}. Point the app directly at the "
            "specific character folder (the one that itself contains "
            "BaseModel and a folder with 'attire' in its name)."
        )
    raise ConversionError(
        f"Couldn't find a WWE2K26 character folder (one containing a "
        f"BaseModel subfolder) at or under {source_root}."
    )


def find_attire_folders(character_root: Path) -> list[Path]:
    """
    An attire folder can be named almost anything WWE2K26 or a modder
    chooses -- "_Alt_Attire" is just the most common pattern in the training
    examples, not a rule the game enforces. The one actual signal is that
    the word "attire" appears in the folder name somewhere (confirmed by
    detourne, 2026-09-20, after a real source folder used a different name and
    was silently skipped). Matching is case-insensitive and substring-based
    for the same reason.
    """
    attires = sorted(
        p for p in character_root.iterdir()
        if p.is_dir() and p.name != "BaseModel" and "attire" in p.name.lower()
    )
    if not attires:
        raise ConversionError(
            f"No folder with 'attire' in its name found under {character_root}. "
            "This doesn't look like a WWE2K26 custom character export."
        )
    return attires


def find_donor_attire_folders(character_root: Path) -> list[Path]:
    """
    Locate attire folder(s) inside a real WWE2K25 DONOR character's folder,
    for the Blender pass's donor-hair features (the "carry over donor's
    hair" checkbox and the "donor attire as base" mode).

    Added 2026-10-02 after a real test (Athena's donor, 1307_Athena) showed
    find_attire_folders's "'attire' substring in the FOLDER name" heuristic
    doesn't hold here: a real, game-shipped WWE2K25 character's attire
    folder can be named after an in-game event/theme instead (confirmed on
    this example: "1307_Super_Showdown_2020", no "attire" anywhere in it)
    -- that "_Default_Attire"/"_Alt_Attire"-style convention is a WWE2K26
    MODDER convention (confirmed by detourne, 2026-09-20, for SOURCE folders
    specifically), not something the base game itself follows for every
    real character's own folder. The FILES inside the folder still
    reliably have "Attire" in their own names regardless (confirmed on
    this same example: "1307_Attire.mcd", "1307_Attire.ycl", etc.) -- so
    this falls back to that instead: any subfolder (other than BaseModel)
    that contains at least one *attire*.mcd file, whatever the subfolder
    itself happens to be named.

    Tries find_attire_folders's normal naming convention FIRST (so a donor
    that happens to use the modder convention -- e.g. another backported/
    custom-made donor -- is still matched the same simple, fast way) and
    only falls back to this broader per-file scan if that finds nothing.
    """
    try:
        return find_attire_folders(character_root)
    except ConversionError:
        pass
    candidates = sorted(
        p for p in character_root.iterdir()
        if p.is_dir() and p.name != "BaseModel"
        and any(
            f.is_file() and f.suffix.lower() == ".mcd" and "attire" in f.name.lower()
            for f in p.iterdir()
        )
    )
    if not candidates:
        raise ConversionError(
            f"No attire folder found under {character_root} -- checked both "
            "the folder-NAME convention (substring 'attire') and, as a "
            "fallback for real WWE2K25 donor characters whose attire folder "
            "may be named after an in-game event/theme instead, any "
            "subfolder containing an *attire*.mcd file. Neither matched "
            "anything here."
        )
    return candidates


_DONOR_HAIR_EXTENSIONS = ("hair_ywa", "hair_corr_ywa", "other_ywa", "other_corr_ywa", "ycl")


def find_donor_hair_files(donor_attire_folder: Path) -> dict[str, Path]:
    """
    Scan a real WWE2K25 donor character's attire folder for hair-related
    companion files. These live alongside the donor's other attire files
    (not under BaseModel), matched by extension + "attire" in the filename,
    same convention as the rest of this tool's file matching (see
    find_attire_folders). Extensions are compound (e.g. "*.hair_ywa"), not
    simple, so this checks the full lowercase name rather than Path.suffix.

    Returns a dict keyed by extension (without leading dot) -> source Path.
    Missing files are simply absent from the dict -- the caller decides
    whether that's fatal (currently it isn't; this feature is experimental
    and best-effort per detourne's 2026-09-23 request to try it).
    """
    found: dict[str, Path] = {}
    if not donor_attire_folder.is_dir():
        return found
    for f in donor_attire_folder.iterdir():
        if not f.is_file():
            continue
        lower = f.name.lower()
        if "attire" not in lower:
            continue
        for ext in _DONOR_HAIR_EXTENSIONS:
            if lower.endswith("." + ext):
                found[ext] = f
                break
    return found


def default_plan(
    source_root: Path,
    output_parent: Path,
    target_slot_id: str,
    target_roster_name: str,
) -> ConversionPlan:
    """
    Build a straightforward plan: the first attire folder (alphabetically) is
    treated as the primary/default attire and takes the target slot ID
    directly. Any additional attire folders are left for the caller (the GUI)
    to assign IDs to -- default_plan gives them a placeholder equal to the
    source's own attire ID, which the GUI should let the user override.
    """
    character_root = find_character_root(source_root)
    attire_folders = find_attire_folders(character_root)

    attires: list[AttireTarget] = []
    for i, folder in enumerate(attire_folders):
        is_primary = i == 0
        if is_primary:
            tid = target_slot_id
        else:
            # best-effort placeholder -- source's own numeric prefix
            tid = folder.name.split("_", 1)[0]
        attires.append(AttireTarget(folder.name, tid, is_primary))

    return ConversionPlan(
        source_root=character_root,
        output_parent=output_parent,
        target_slot_id=target_slot_id,
        target_roster_name=target_roster_name,
        attires=attires,
    )


def run_conversion(plan: ConversionPlan) -> Path:
    """
    Execute the plan. Returns the path to the newly created output folder.
    Raises ConversionError on any structural problem. Never modifies the
    source folder.
    """
    character_root = plan.source_root
    name_fragment = _sanitize_name_fragment(plan.target_roster_name)
    out_top = plan.output_parent / f"{plan.target_slot_id}_{name_fragment}"

    if out_top.exists():
        raise ConversionError(
            f"Output folder already exists, refusing to overwrite: {out_top}"
        )

    plan.log.append(f"Source character folder: {character_root}")
    plan.log.append(f"Output folder: {out_top}")
    out_top.mkdir(parents=True)

    for attire in plan.attires:
        _convert_attire(character_root, out_top, attire, plan)

    _convert_base_model(character_root, out_top, plan)

    plan.log.append("Done.")
    return out_top


def _copy_textures(src_dir: Path, dst_dir: Path, plan: ConversionPlan) -> None:
    src_tex = src_dir / "Textures"
    if not src_tex.is_dir():
        plan.log.append(f"  (no Textures folder in {src_dir}, skipping)")
        return
    dst_tex = dst_dir / "Textures"
    dst_tex.mkdir(parents=True, exist_ok=True)
    count = 0
    skipped = []
    for f in src_tex.iterdir():
        if not f.is_file():
            continue
        # Only carry over recognized texture files -- every example seen is
        # .dds. Anything else (OS cruft like Thumbs.db/.DS_Store, editor temp
        # files, partial downloads) is skipped rather than blindly copied.
        if f.suffix.lower() == ".dds":
            shutil.copy2(f, dst_tex / f.name)
            count += 1
        else:
            skipped.append(f.name)
    plan.log.append(f"  copied {count} texture file(s) -> {dst_tex}")
    if skipped:
        plan.log.append(f"  skipped {len(skipped)} non-.dds file(s) in {src_tex}: {skipped}")


def _fill_missing_donor_textures(donor_tex_dir: Path, dst_dir: Path, plan: ConversionPlan) -> None:
    """
    Fallback-only texture fill for the custom-donor-materials case (added
    2026-09-23 after a real, confirmed bug on Saya Kamitani: her real
    WWE2K25 baseModel.mtls references 'head_color'/'head_nrm' by name, but
    her WWE2K26 source mod's own BaseModel/Textures folder only contains
    'head2_color'/'head2_nrm' -- a texture-naming variant that mod's own
    author used -- so the game found no 'head_color.dds' at all and
    rendered a completely untextured/black face. Confirmed via byte-level
    string extraction from the donor .mtls vs. a listing of both texture
    folders, not guessed.

    Copies any texture file from the donor's own Textures folder into
    dst_dir/Textures ONLY if a file with that exact name doesn't already
    exist there (case-insensitive) -- this never overrides anything the
    source itself provided, it only fills gaps the donor's materials file
    needs but the source's own texture set doesn't have. Safe specifically
    because this only runs for a real, same-character custom donor (never
    the generic bundled template, which belongs to a different character
    entirely and whose own textures should never be painted onto someone
    else's face).
    """
    if not donor_tex_dir.is_dir():
        return
    dst_tex = dst_dir / "Textures"
    dst_tex.mkdir(parents=True, exist_ok=True)
    existing = {f.name.lower() for f in dst_tex.iterdir() if f.is_file()}
    filled = []
    for f in donor_tex_dir.iterdir():
        if not f.is_file() or f.suffix.lower() != ".dds":
            continue
        if f.name.lower() in existing:
            continue
        shutil.copy2(f, dst_tex / f.name)
        filled.append(f.name)
    if filled:
        plan.log.append(
            f"  EXPERIMENTAL: filled {len(filled)} texture name(s) missing from the "
            f"source's own Textures folder using the custom donor's own Textures "
            f"({donor_tex_dir}) -- the donor's materials file references these names "
            f"and they weren't provided any other way: {filled}"
        )


_DONOR_HAIR_TEXTURE_NAME_PREFIX = "hair"


def _override_donor_hair_textures(donor_tex_dir: Path, dst_dir: Path, plan: ConversionPlan) -> None:
    """
    Donor hair textures ALWAYS override a same-named source texture (added
    2026-10-02, after a real in-game test on Athena: detourne reported "the
    hair is barely visible" and suspected "the texture files from the 2k25
    need to be bundled with the output, instead of the hair texture files
    from the 2k26 mesh"). Confirmed directly by inspecting both real
    Textures folders and the generated attire .mtls: Athena's SOURCE
    (702_Athena) Attire/Textures folder and her donor's (1307_Athena)
    attire Textures folder both happen to contain files named exactly
    "hair_flow.dds" and "hair_rai.dds" -- the SAME names the addon's own
    generated hair material (shader "yCh_hair", subtype
    "NewHair_ColorChanging") actually references via its flowMap/g_...
    properties. `_copy_textures` unconditionally mirrors the ENTIRE source
    Textures folder first (it has no concept of "needed" vs "not needed"),
    so the source's own WWE2K26-authored hair_flow.dds/hair_rai.dds (never
    meant for this donor mesh or this shader) land in the output, and
    `_fill_missing_donor_textures` -- gap-fill ONLY, by design, since
    overriding was never safe for ordinary clothing textures -- sees those
    names already present and silently skips the donor's real versions.
    Net effect: the donor's own real hair mesh (which this pipeline
    deliberately keeps untouched -- see `_convert_attire_donor_base` and
    the donor hair mesh transplant) ends up textured with flow-map/
    anisotropic-highlight data authored for the wrong game's hair shader
    entirely, a very plausible explanation for hair rendering as "barely
    visible."

    Unlike `_fill_missing_donor_textures` (correctly left as gap-fill-only
    for everything else -- ordinary clothing texture names like
    "top_color.dds" ALSO collide between this exact source and donor, see
    the real folder listing, and there the SOURCE's version must always
    win, since the source's own clothing is what's actually kept and
    exported), hair texture names are different: whenever this pipeline
    keeps the donor's own hair mesh, the donor's own hair textures are the
    only textures that make sense for it by construction -- there is no
    scenario where a same-named SOURCE hair texture is the right one. So
    this OVERRIDES (copies and replaces) rather than only filling gaps,
    scoped narrowly to texture file names starting with "hair" (case-
    insensitive) -- matching the actual naming convention confirmed on
    this real example -- and never touches any other (clothing) texture
    name. Called for both donor-hair pipelines (the "donor attire as
    base" mode and the older donor hair mesh transplant), since both
    generate a hair material via the same addon Generate MTLs mechanism
    and are equally exposed to this same-name collision.
    """
    if not donor_tex_dir.is_dir():
        return
    dst_tex = dst_dir / "Textures"
    dst_tex.mkdir(parents=True, exist_ok=True)
    existing_by_lower = {f.name.lower(): f for f in dst_tex.iterdir() if f.is_file()}
    overridden = []
    for f in donor_tex_dir.iterdir():
        if not f.is_file() or f.suffix.lower() != ".dds":
            continue
        if not f.name.lower().startswith(_DONOR_HAIR_TEXTURE_NAME_PREFIX):
            continue
        # Match the destination's existing casing if a same-named (case-
        # insensitive) file is already there, so this replaces it in place
        # rather than leaving two differently-cased copies side by side.
        existing = existing_by_lower.get(f.name.lower())
        target = existing if existing is not None else dst_tex / f.name
        shutil.copy2(f, target)
        overridden.append((f.name, "replaced source file" if existing is not None else "added"))
    if overridden:
        plan.log.append(
            f"  donor hair texture(s) override any same-named source file "
            f"(the donor's own hair textures are always correct for the "
            f"donor's own kept hair mesh -- see the 2026-10-02 'hair barely "
            f"visible' finding in the project doc): {overridden}"
        )


def _convert_attire(
    character_root: Path,
    out_top: Path,
    attire: AttireTarget,
    plan: ConversionPlan,
) -> None:
    src_dir = character_root / attire.source_folder_name
    suffix = "Default_Attire" if attire.is_primary else "Alt_Attire"
    dst_dir = out_top / f"{attire.target_id}_{suffix}"
    dst_dir.mkdir(parents=True)
    plan.log.append(f"Attire '{attire.source_folder_name}' -> '{dst_dir.name}'")

    # Map of (extension, dest filename template, required). Matching is by
    # extension + "attire" appearing somewhere in the filename (case-
    # insensitive), NOT a fixed "_Attire.mcd"-style suffix -- like the
    # attire FOLDER name (see find_attire_folders), the actual file names
    # inside it aren't guaranteed to follow one fixed capitalization/prefix
    # pattern either. Dest casing rules confirmed from paired examples:
    # mcd/hpl/ycl keep capitalized "Attire", .mtls is lowercase "attire" in
    # WWE2K25.
    file_rules = [
        (".mcd", "{id}_Attire.mcd", True),
        (".hpl", "{id}_Attire.hpl", False),
        (".ycl", "{id}_Attire.ycl", False),
    ]

    blender_mtls = plan.blender_attire_mtls.get(attire.source_folder_name)
    if blender_mtls is not None and blender_mtls.is_file():
        dst_name = f"{attire.target_id}_attire.mtls"
        dst_path = dst_dir / dst_name
        plan.log.append(
            f"  {blender_mtls.name} -> {dst_name} (header/slot-name table taken "
            "from the addon's own 'Generate MTLs' export during the Blender pass; "
            "see below for why its per-slot material CONTENT is not used as-is)"
        )
        # 2026-10-21: the "Arithmetic operation resulted in an overflow"
        # MaterialEditor crash seen on at least one real conversion
        # (Ricochet) has been root-caused (see build_default_mtl_chunk's
        # 2026-10-21 docstring entry and its inline comments) and fixed --
        # it was a padding-byte-count bug in this tool's own rename logic,
        # not anything in the addon's raw "Generate MTLs" output. The
        # TEMPORARY ".mtls.raw_blender_debug" copy used to compare the raw
        # pre-rebuild file against the broken output while that was still
        # under investigation has been removed now that the root cause is
        # confirmed.
        try:
            build_attire_mtls_from_defaults(blender_mtls, dst_path, plan.log, textures_dir=src_dir / "Textures")
            plan.log.append(
                "  (2026-10-16, Miu Watanabe: confirmed the addon's own 'Generate "
                "MTLs' yCh_gear output wires real pointers to _metal/_normal/"
                "_roughness texture-table entries this project's source mods never "
                "ship, while the slot's real _color texture isn't wired as a pointer "
                "at all -- 'none of the textures work'. Building every material "
                "fresh from known-good yCh_gear::Normal / yCh_hair::NewHair_"
                "ColorChanging default templates -- the same fix detourne applies by "
                "hand in MaterialEditor.exe -- sidesteps that bug entirely, so this "
                "replaces the old direct copy-of-addon-output-then-patch approach)"
            )
        except ConversionError as exc:
            plan.log.append(
                f"  WARNING: build_attire_mtls_from_defaults failed ({exc}) -- "
                "falling back to the addon's own 'Generate MTLs' output as-is "
                "(may have broken/dangling textures, see above)"
            )
            shutil.copy2(blender_mtls, dst_path)
        _warn_if_mtls_hollow(dst_path, plan.log)
        # DISABLED 2026-10-02 (same day it was added) -- detourne's first real
        # test of a build carrying this fix (v10, Saya Kamitani) crashed BOTH
        # WWE2K25 itself (crash to desktop) AND MaterialEditor.exe (3 crash
        # dumps, all EXCEPTION_ACCESS_VIOLATION reading a dangling/garbage
        # heap address, fault inside MaterialEditor.exe's own code, not a
        # system DLL -- confirmed from the real .dmp). Re-parsing the fixed
        # 660_attire.mtls with this project's own parser finds nothing
        # structurally wrong (header/name-table/chunk-boundary/hidden-
        # pointer checks all pass), which points at a different, nastier
        # problem than a byte-layout mistake: the "@ref"-paired schema
        # properties on the yCh_hair chunk (flowMap, g_rootMap, etc.) do NOT
        # resolve as hidden pointers into the texture-name table the way
        # they do on yCh_gear/yCh_head chunks -- they read back as ordinary
        # floats (1.0, ~0.3, etc.), so rename_mtl_chunk_texture_prefix's
        # pointer-fixup silently did nothing for them (no exception, no
        # corruption, just a no-op). That means yCh_hair's real texture-
        # reference mechanism is still not understood, AND -- separately --
        # going from one yCh_hair-shaded slot to three may simply not be
        # something the engine/tool supports at all, independent of byte-
        # level correctness. Until one of those is actually confirmed safe
        # with a real in-game + MaterialEditor test, do not auto-apply this:
        # a static/discolored hair2/hair4 is a cosmetic bug, a crash to
        # desktop is not an acceptable trade for fixing it. The function
        # itself is left in place below for manual/experimental use.
        # if fix_attire_hair_shader_misassignment(dst_path, plan.log):
        #     plan.log.append(
        #         f"  (see 'attire hair-shader fix' above -- corrected the addon's own "
        #         f"Generate MTLs bug in {dst_name})"
        #     )
    else:
        for f in src_dir.iterdir():
            if not f.is_file():
                continue
            lower = f.name.lower()
            if lower.endswith(".mtls"):
                dst_name = f"{attire.target_id}_attire.mtls"
                _patch_mtls_version_for_2k25(f, dst_dir / dst_name, plan.log)

    for ext, dst_template, required in file_rules:
        dst_name = dst_template.format(id=attire.target_id)
        if ext == ".ycl" and plan.skip_source_ycl:
            plan.log.append(
                f"  (skipping source .ycl -- \"Option 4\" rigid-hair-to-head is on, "
                "so no vertex is weighted to the Hair_bNN/Hair_bNN_MM bones this raw, "
                "unconverted WWE2K26-format file's physics describes; harmless to "
                "skip either way. NOTE: the real cause of the stretched-hair "
                "artifact turned out to be unrelated to this file -- see README's "
                "Option 4 section for the confirmed fix)"
            )
            continue
        matches = [
            f for f in src_dir.iterdir()
            if f.is_file() and f.suffix.lower() == ext and "attire" in f.name.lower()
        ]
        if not matches:
            if ext == ".hpl":
                (dst_dir / dst_name).write_bytes(_EMPTY_HPL_BYTES)
                plan.log.append(
                    f"  (no .hpl in source -- wrote a genuine empty-mask placeholder -> "
                    f"{dst_name}. This character will have NO body/attire clipping "
                    "masking (e.g. legs may clip through tights) until a real .hpl is "
                    "hand-built in Blender -- see the file-rules .hpl comment at the top "
                    "of this file -- and dropped into this attire's source folder "
                    "(named with 'attire' in it) before converting."
                )
                continue
            if required:
                raise ConversionError(
                    f"Expected a file with 'attire' in its name and a *{ext} extension "
                    f"in {src_dir} but found none."
                )
            continue
        if len(matches) > 1:
            plan.log.append(
                f"  WARNING: found {len(matches)} '*attire*{ext}' files in {src_dir}, "
                f"using '{matches[0].name}' -- rename/remove the others if this is wrong: "
                f"{[m.name for m in matches]}"
            )
        src_file = matches[0]
        if ext == ".mcd":
            blender_mcd = plan.blender_attire_mcds.get(attire.source_folder_name)
            if blender_mcd is not None and blender_mcd.is_file():
                plan.log.append(f"  (using Blender-exported, armature-redirected .mcd for {attire.source_folder_name})")
                _place_blender_exported_mcd(blender_mcd, dst_dir / dst_name, plan.log)
            else:
                if plan.blender_attire_mcds:
                    plan.log.append(
                        f"  WARNING: no Blender export found for '{attire.source_folder_name}' -- "
                        "falling back to raw byte-patch path (no armature redirect) for this attire"
                    )
                _patch_mcd_version_for_2k25(src_file, dst_dir / dst_name, plan.log)
        else:
            shutil.copy2(src_file, dst_dir / dst_name)
            plan.log.append(f"  {src_file.name} -> {dst_name}")

    if plan.donor_hair_files:
        plan.log.append(
            "  EXPERIMENTAL: overriding hair file(s) with a real, user-specified "
            "WWE2K25 donor character's own hair -- requested 2026-09-23 to attempt "
            "dynamic hair conversion for the first time (hair has always been out of "
            "scope for this tool otherwise) by borrowing a known-working WWE2K25 "
            "hairstyle from a similar-looking donor character; untested, may not work"
        )
        for ext, donor_path in plan.donor_hair_files.items():
            dst_name = f"{attire.target_id}_Attire.{ext}"
            shutil.copy2(donor_path, dst_dir / dst_name)
            plan.log.append(f"    {donor_path.name} -> {dst_name} (donor hair, replaces source-converted file if any)")

        if ".ycl" in plan.donor_hair_files:
            # Added 2026-10-03 after a real in-game test (Saya Kamitani,
            # Option 3/Donor Hair): the donor's own .ycl is correct for the
            # donor's own hair physics, but it knows nothing about this
            # SOURCE attire's own tassel/cloth dynamics nodes (e.g.
            # Armband_Tassels, Belt_Tassels, Shoes_Tassels, Tights_Tassels)
            # -- those nodes simply don't exist in the donor's file, so they
            # get dropped on the floor by this wholesale copy, and the
            # tassels render with no dynamics (don't animate) in-game.
            # detourne's own first real test confirmed this exact symptom, and
            # confirmed the fix: manually opening the output .ycl in
            # YclEditor.exe and adding the source attire's own tassel nodes
            # back in (keeping the donor's hair nodes untouched) made both
            # the tassels and the donor hair animate correctly.
            #
            # This is NOT automated by this tool, and -- as of 2026-10-03 --
            # isn't expected to be anytime soon: two independent
            # investigations (one via exe string-mining, one via byte-level
            # diffing a real hand-merged file against this one) both found
            # that ~99.7% of a .ycl is an unmapped bulk region of physics
            # data whose relationship to the small name tables at the file's
            # tail isn't understood, and that YclEditor.exe (the one tool
            # that can safely write this format) has no command-line, batch,
            # or scripting interface to drive instead. See the project's
            # findings doc for the full investigation. So: manual, every
            # time, for now.
            plan.log.append(
                "    NOTE: this donor .ycl has the donor's own hair physics, but none of "
                f"{attire.target_id}_Attire's own cloth/tassel dynamics nodes (confirmed "
                "missing in-game 2026-10-03: tassels didn't animate) -- before testing "
                "in-game, open this output's .ycl in YclEditor.exe and manually add back "
                "the source attire's own tassel/cloth dynamics nodes (e.g. any "
                "*_Tassels nodes) from the original 2K26 source .ycl, leaving the donor's "
                "hair nodes untouched. This manual step is required for every Donor Hair "
                "conversion for now -- see the project's findings doc for why it can't be "
                "automated yet."
            )

    _copy_textures(src_dir, dst_dir, plan)

    if plan.donor_attire_textures_dir is not None:
        _override_donor_hair_textures(plan.donor_attire_textures_dir, dst_dir, plan)
        _fill_missing_donor_textures(plan.donor_attire_textures_dir, dst_dir, plan)

    materials_dir = src_dir / "Materials"
    if materials_dir.is_dir():
        if plan.drop_materials:
            plan.log.append(
                f"  (dropping {materials_dir} -- {len(list(materials_dir.iterdir()))} "
                "file(s) not carried over, per current conversion rules)"
            )
        else:
            dst_mat = dst_dir / "Materials"
            shutil.copytree(materials_dir, dst_mat)
            plan.log.append(f"  copied Materials folder -> {dst_mat}")


def _convert_base_model(character_root: Path, out_top: Path, plan: ConversionPlan) -> None:
    src_dir = character_root / "BaseModel"
    dst_dir = out_top / "BaseModel"
    dst_dir.mkdir(parents=True)
    plan.log.append("BaseModel")

    if plan.blender_basemodel_mcd is not None and plan.blender_basemodel_mcd.is_file():
        plan.log.append("  (using Blender-exported, armature-redirected baseModel.mcd)")
        _place_blender_exported_mcd(plan.blender_basemodel_mcd, dst_dir / "baseModel.mcd", plan.log)
    else:
        mcd_src = src_dir / "baseModel.mcd"
        if mcd_src.is_file():
            if plan.blender_basemodel_mcd is not None:
                plan.log.append(
                    "  WARNING: Blender export for BaseModel was requested but not found -- "
                    "falling back to raw byte-patch path (no armature redirect)"
                )
            _patch_mcd_version_for_2k25(mcd_src, dst_dir / "baseModel.mcd", plan.log)
        else:
            plan.log.append(f"  WARNING: expected baseModel.mcd not found in {src_dir}")

    basemodel_rename_map: dict[str, str] = {}
    built_from_donor = False
    is_real_custom_donor = (
        plan.custom_donor_basemodel_mtls is not None
        and plan.custom_donor_basemodel_mtls.is_file()
    )

    # donor_mtls_path: whose materials this run builds baseModel.mtls from.
    # A real, user-specified custom donor (if one was given) always wins;
    # otherwise -- changed 2026-10-13 at detourne's suggestion -- fall
    # through to the bundled generic template and run IT through the
    # exact same auto-rename pipeline below, instead of the old dumb
    # verbatim copy (_place_generic_basemodel_mtls). This is what fixes
    # the head2/eye_l/mouthbag wrong-shader-type bug BY DEFAULT, with no
    # donor folder required: the generic template's own shader types are
    # already correct (it's a real, working WWE2K25 file, unchanged
    # bytes), and the auto-rename step below gives it this character's own
    # real mesh slot names -- which is also what fixes the separate
    # "invisible head" slot-name-mismatch bug that Blender's auto-
    # generate-mtls path (export_args_basemodel in
    # blender_headless_convert.py) was added for. Only when the template
    # file is missing, or use_generic_basemodel_mtls is explicitly off,
    # does this fall further down the elif chain below.
    donor_mtls_path: Path | None = None
    if is_real_custom_donor:
        donor_mtls_path = plan.custom_donor_basemodel_mtls
    elif plan.use_generic_basemodel_mtls and _GENERIC_BASEMODEL_MTLS.is_file():
        donor_mtls_path = _GENERIC_BASEMODEL_MTLS

    if donor_mtls_path is not None:
        # This character's own real mesh slot names -- the ground truth we
        # need here is specifically "whatever material names the SHIPPED
        # baseModel.mcd actually looks up", so the auto-rename below gets
        # the real texture files right.
        #
        # CORRECTED 2026-10-15 (see the project doc's 2026-10-15 "wrong
        # head texture, corrected again" entry -- a live, in-game-confirmed
        # bug on 735_Yota_Tsuji): this used to try the source WWE2K26 mod's
        # OWN, self-declared baseModel.mtls FIRST and only fall back to
        # Blender's export. That was backwards. The source mod's own
        # baseModel.mtls names are an upstream author's declared names --
        # confirmed 2026-10-02 on 6 examples to usually match the real
        # mesh, which is why that bug survived this long, but "usually"
        # isn't "guaranteed", and nothing re-derives them after whatever
        # the Blender pass actually does to the mesh (armature redirect,
        # hair-bone grafting, etc.). plan.blender_basemodel_mtls, by
        # contrast, is generated by the SAME Blender export call, from the
        # SAME mesh data, that writes out_basemodel.mcd -- the exact file
        # that becomes the shipped baseModel.mcd (see app.py, where both
        # are produced together and the .mtls is explicitly commented as
        # "guaranteed to match this character's own real mesh object
        # names"). On 735_Yota_Tsuji, the source's own baseModel.mtls said
        # "head" (so no rename was auto-detected) while the actual exported
        # mesh's real object name was "head2" -- exactly the file/texture
        # mismatch that showed up in-game as a wrong/fallback head texture.
        # Blender's own export is the one source that can never disagree
        # with the .mcd it was generated alongside, so it's tried first now;
        # the source mod's own file is kept only as a fallback for when no
        # Blender BaseModel pass ran at all (e.g. Blender failed, or this
        # run never needed one).
        dst_name_source = plan.blender_basemodel_mtls
        dst_names = None
        if dst_name_source is not None and dst_name_source.is_file():
            try:
                dst_names, _dn_chunks, _dn_header = _split_mtls_slots(dst_name_source.read_bytes())
            except ConversionError as e:
                plan.log.append(f"  WARNING: couldn't read destination mesh's own material names from {dst_name_source}: {e}")
        if dst_names is None:
            fallback_name_source = src_dir / "baseModel.mtls"
            if fallback_name_source.is_file():
                plan.log.append(
                    f"  (no Blender-exported baseModel.mtls available -- falling back to "
                    f"{fallback_name_source}'s own declared material names, which are NOT "
                    "guaranteed to match the actual shipped baseModel.mcd -- see the "
                    "2026-10-15 project doc entry)"
                )
                try:
                    dst_names, _dn_chunks, _dn_header = _split_mtls_slots(fallback_name_source.read_bytes())
                except ConversionError as e:
                    plan.log.append(f"  WARNING: couldn't read destination mesh's own material names from {fallback_name_source}: {e}")

        unmatched_dst: list[str] = []
        if dst_names is not None:
            donor_names, _donor_chunks, _donor_header = _split_mtls_slots(donor_mtls_path.read_bytes())
            basemodel_rename_map, unmatched_dst = auto_detect_basemodel_slot_renames(
                dst_names, donor_names, plan.log
            )

        if dst_names is not None and not unmatched_dst:
            build_basemodel_mtls_from_donor(
                donor_mtls_path, basemodel_rename_map,
                dst_dir / "baseModel.mtls", plan.log,
            )
            plan.log.append(
                f"  baseModel.mtls built wholesale from "
                f"{'the custom donor' if is_real_custom_donor else 'the bundled generic template'} "
                f"{donor_mtls_path} (every real material value comes from it, completely "
                "unmodified, except for the texture-name references of any auto-renamed "
                "slot -- see the 2026-10-02 wholesale-copy-and-rename findings in the "
                "project doc)"
            )
            built_from_donor = True
        else:
            if dst_names is None:
                plan.log.append(
                    "  WARNING: couldn't determine this character's own real mesh material "
                    "names -- falling back to copying the donor's BaseModel materials as-is"
                )
            shutil.copy2(donor_mtls_path, dst_dir / "baseModel.mtls")
            plan.log.append(
                f"  {donor_mtls_path} -> baseModel.mtls "
                f"(copied as-is from {'a real, user-specified WWE2K25 donor character' if is_real_custom_donor else 'the bundled generic template'}'s "
                "own BaseModel materials. NOTE: this "
                "file's material slot names come from the DONOR's own mesh -- any slot name "
                f"this run couldn't safely auto-rename ({unmatched_dst if unmatched_dst else 'n/a'}) "
                "may render invisible in-game if it doesn't match this character's real mesh "
                "object names)"
            )
    elif plan.blender_basemodel_mtls is not None and plan.blender_basemodel_mtls.is_file():
        # Only reachable when neither a custom donor nor the bundled
        # generic template was usable this run (e.g.
        # use_generic_basemodel_mtls was explicitly turned off, or the
        # bundled template file is missing). Kept as a safety-net fallback
        # -- see the 2026-10-02 invisible-head finding for why this was
        # ever preferred -- but it's known to carry the head2/eye_l/
        # mouthbag wrong-shader-type bug, which is exactly what the
        # generic-template-as-donor path above now avoids by default.
        shutil.copy2(plan.blender_basemodel_mtls, dst_dir / "baseModel.mtls")
        plan.log.append(
            f"  {plan.blender_basemodel_mtls.name} -> baseModel.mtls (generated by "
            "the addon's own 'Generate MTLs' export option during the Blender pass -- "
            "used only because neither a custom donor nor the bundled generic template "
            "was available this run. NOTE: this is known to carry the head2/eye_l/"
            "mouthbag wrong-shader-type bug -- see the project doc)"
        )
    else:
        mtls_src = src_dir / "baseModel.mtls"
        if mtls_src.is_file():
            _patch_mtls_version_for_2k25(mtls_src, dst_dir / "baseModel.mtls", plan.log)
        else:
            plan.log.append(f"  WARNING: expected baseModel.mtls not found in {src_dir}")

    _copy_textures(src_dir, dst_dir, plan)

    # Texture carryover from the donor's OWN Textures folder: deliberately
    # restricted to a real, user-specified custom donor -- never the
    # bundled generic template, whose textures (what little might ever be
    # bundled alongside it) belong to a different person entirely and must
    # never be painted onto someone else's face. See
    # _fill_missing_donor_textures's docstring. (In practice the bundled
    # template ships with no Textures folder at all, so this would safely
    # no-op either way -- this guard just keeps that invariant explicit
    # rather than relying on an always-absent folder.)
    if is_real_custom_donor:
        donor_textures_dir = plan.custom_donor_basemodel_mtls.parent / "Textures"
        if built_from_donor and basemodel_rename_map:
            copy_donor_textures_with_rename(donor_textures_dir, dst_dir / "Textures", basemodel_rename_map, plan.log)
        _fill_missing_donor_textures(donor_textures_dir, dst_dir, plan)

    materials_dir = src_dir / "Materials"
    if materials_dir.is_dir():
        if plan.drop_materials:
            plan.log.append(
                f"  (dropping BaseModel/Materials -- "
                f"{len(list(materials_dir.iterdir()))} file(s) not carried over)"
            )
        else:
            dst_mat = dst_dir / "Materials"
            shutil.copytree(materials_dir, dst_mat)
            plan.log.append(f"  copied BaseModel/Materials folder -> {dst_mat}")
