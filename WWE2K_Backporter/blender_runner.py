"""
blender_runner.py -- launches Blender in the background to run
blender_headless_convert.py, and reports back what happened.

Kept deliberately separate from converter.py: this module's only job is
process orchestration (finding blender.exe, writing the job file, running
the subprocess, reading the result). All the actual WWE2K26->WWE2K25
conversion knowledge stays in converter.py (byte-level fixes, folder/file
naming) and blender_headless_convert.py (the Blender-side skeleton work).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# Added 2026-10-13 for the standalone-.exe build: when frozen by PyInstaller,
# this module's __file__ points into the frozen bundle, not a loose .py file
# next to a real blender_headless_convert.py -- but this path still needs to
# be a REAL file on disk, since it's handed to Blender's own Python as a
# --python script argument (a separate process, not something our frozen
# exe's own import machinery can satisfy). sys._MEIPASS is PyInstaller's
# per-run temp extraction directory, where --add-data files actually land as
# loose files; when not frozen (running from source), fall back to this
# file's own real directory, same as before.
_APP_DIR = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent)
_HEADLESS_SCRIPT = _APP_DIR / "blender_headless_convert.py"

_COMMON_WINDOWS_BLENDER_PATHS = [
    r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 3.6\blender.exe",
]


def guess_blender_exe() -> str | None:
    """Best-effort autodetect for the GUI's default field. Returns None if
    nothing obvious is found -- the user can always browse to it manually."""
    import shutil as _shutil

    found = _shutil.which("blender")
    if found:
        return found
    for p in _COMMON_WINDOWS_BLENDER_PATHS:
        if Path(p).is_file():
            return p
    return None


@dataclass
class AttireJob:
    source_mcd: Path
    out_mcd: Path


@dataclass
class BlenderJobResult:
    ok: bool
    log: list[str] = field(default_factory=list)
    error: str | None = None
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""


def run_blender_conversion(
    *,
    blender_exe: Path,
    donor_basemodel_mcd: Path,
    source_basemodel_mcd: Path,
    out_basemodel_mcd: Path,
    addon_parent_dir: Path | None = None,
    attires: list[AttireJob],
    addon_package_name: str = "WWE2K_MDL",
    timeout_seconds: int = 600,
    fix_hair_bone_weights: bool = False,  # "Option 2" -- confirmed broken, stripped from UI 2026-10-02
    graft_hair_bones: bool = False,  # "Option 3" -- superseded by rigid_hair_to_head, stripped from UI 2026-10-02
    rigid_hair_to_head: bool = False,
    donor_attire_mcd: Path | None = None,
    use_donor_attire_as_base: bool = False,
    use_heat_seek: bool = False,
    graft_real_hair_bones: bool = False,
) -> BlenderJobResult:
    """
    Runs Blender headlessly to produce armature-redirected, 2K25-targeted
    .mcd exports for the BaseModel and every attire in `attires`. Does NOT
    do any of the byte-level post-processing (terminator fix, .mtls
    handling, folder naming) -- that's converter.py's job, run on the files
    this function writes.

    use_donor_attire_as_base (added 2026-10-02): when True (and
    `donor_attire_mcd` is set), every attire is built with the donor's own
    attire .mcd as the base scene -- its native hair + native skeleton kept
    as-is -- with the 2K26 source mod's clothing grafted onto it, instead
    of the default of keeping the source attire as the base scene and
    transplanting the donor's hair mesh into it. See
    blender_headless_convert.py's `_convert_attire_donor_base` for the full
    rationale. Only affects attires; the BaseModel conversion is unchanged.

    use_heat_seek (added 2026-10-15): when True, BOTH the BaseModel and
    every attire are converted via blender_headless_convert.py's
    `_convert_one_heat_seek` instead -- a different pipeline entirely,
    reverse-engineered from a real community Blender workflow (hair-bone-
    chain grafting from the source's own embedded skeleton, plus
    DATA_TRANSFER-based "heat seek" weight recomputation for everything
    else, sourced from the donor BaseModel's own body mesh). Takes
    precedence over fix_hair_bone_weights/graft_hair_bones/
    rigid_hair_to_head (those are Options 2/3/4, which heat seek mode
    doesn't use at all); if `use_donor_attire_as_base` is ALSO set, that
    one wins for attires (BaseModel still uses heat seek) -- see
    blender_headless_convert.py's `_run_job` dispatch order. UNTESTED
    IN-GAME as of writing -- see that function's docstring.

    graft_real_hair_bones (added 2026-10-15, mode "5. Hair Graft Only"):
    isolates just Heat Seek's hair-bone-chain-graft step (CONFIRMED WORKING
    in-game, 2026-10-15, Yota Tsuji) WITHOUT any of Heat Seek's other,
    still-unconfirmed DATA_TRANSFER weight recompute for body/head/
    clothing -- requested by detourne specifically to validate the hair fix
    across more characters while that other half is still being worked
    out. Applies to both the BaseModel and every attire. If `use_heat_seek`
    is ALSO on, that takes priority (this flag is ignored for any part
    heat seek handles) -- see blender_headless_convert.py's `_convert_one`
    docstring for exactly what this does differently from mode 1.
    """
    for label, p in [
        ("Blender executable", blender_exe),
        ("Donor BaseModel .mcd", donor_basemodel_mcd),
        ("Source BaseModel .mcd", source_basemodel_mcd),
    ]:
        if not p.exists():
            return BlenderJobResult(ok=False, error=f"{label} not found: {p}")
    if addon_parent_dir is not None and not addon_parent_dir.exists():
        return BlenderJobResult(ok=False, error=f"Addon parent dir not found: {addon_parent_dir}")
    if donor_attire_mcd is not None and not donor_attire_mcd.exists():
        return BlenderJobResult(ok=False, error=f"Donor attire .mcd (for hair-mesh transplant) not found: {donor_attire_mcd}")

    with tempfile.TemporaryDirectory(prefix="wwe2k_blender_job_") as tmpdir:
        tmp = Path(tmpdir)
        job = {
            "addon_parent_dir": str(addon_parent_dir) if addon_parent_dir is not None else "",
            "addon_package_name": addon_package_name,
            "donor_basemodel_mcd": str(donor_basemodel_mcd),
            "source_basemodel_mcd": str(source_basemodel_mcd),
            "out_basemodel_mcd": str(out_basemodel_mcd),
            "attires": [
                {"source_mcd": str(a.source_mcd), "out_mcd": str(a.out_mcd)}
                for a in attires
            ],
            "fix_hair_bone_weights": fix_hair_bone_weights,
            "graft_hair_bones": graft_hair_bones,
            "rigid_hair_to_head": rigid_hair_to_head,
            "donor_attire_mcd": str(donor_attire_mcd) if donor_attire_mcd is not None else "",
            "use_donor_attire_as_base": use_donor_attire_as_base,
            "use_heat_seek": use_heat_seek,
            "graft_real_hair_bones": graft_real_hair_bones,
            "result_path": str(tmp / "result.json"),
        }
        job_path = tmp / "job.json"
        job_path.write_text(json.dumps(job, indent=2))

        cmd = [
            str(blender_exe),
            "--background",
            "--factory-startup",
            "--python", str(_HEADLESS_SCRIPT),
            "--",
            str(job_path),
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as e:
            return BlenderJobResult(
                ok=False,
                error=f"Blender did not finish within {timeout_seconds}s (possibly hung / waiting on a dialog -- --background --factory-startup should prevent that, but check for a stray Blender process)",
                stdout=e.stdout or "",
                stderr=e.stderr or "",
            )
        except OSError as e:
            return BlenderJobResult(ok=False, error=f"Could not launch Blender ({blender_exe}): {e}")

        result_path = tmp / "result.json"
        if not result_path.is_file():
            return BlenderJobResult(
                ok=False,
                error=(
                    f"Blender exited (code {proc.returncode}) without writing a result "
                    "file -- it likely crashed before reaching our script, or the "
                    "--python script itself failed to even start. Check stderr below."
                ),
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
            )

        result = json.loads(result_path.read_text())
        return BlenderJobResult(
            ok=bool(result.get("ok")),
            log=result.get("log", []),
            error=result.get("error"),
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )


if __name__ == "__main__":
    # Tiny manual smoke test / CLI, e.g.:
    #   python blender_runner.py "C:\...\blender.exe" "C:\...\addon_parent" donor.mcd source.mcd out.mcd
    if len(sys.argv) < 6:
        print("Usage: blender_runner.py <blender.exe> <addon_parent_dir> <donor.mcd> <source.mcd> <out.mcd>")
        sys.exit(1)
    res = run_blender_conversion(
        blender_exe=Path(sys.argv[1]),
        addon_parent_dir=Path(sys.argv[2]),
        donor_basemodel_mcd=Path(sys.argv[3]),
        source_basemodel_mcd=Path(sys.argv[4]),
        out_basemodel_mcd=Path(sys.argv[5]),
        attires=[],
    )
    print(json.dumps(res.__dict__, indent=2))
