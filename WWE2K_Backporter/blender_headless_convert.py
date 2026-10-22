"""
blender_headless_convert.py -- runs INSIDE Blender, in background mode:

    blender --background --factory-startup --python blender_headless_convert.py -- <job.json>

Does the Blender-side half of a no-hair male WWE2K26 -> WWE2K25 conversion:
for the BaseModel and each attire .mcd, imports a real WWE2K25 donor
armature (bundled Blake-Christian-verified "generic" baseModel) into the
scene alongside the raw 2K26 source, lets the WWE2K_MDL addon's own
cross-version auto-redirect rebind the 2K26 mesh onto the real 2K25
armature, applies the one small known-safe vertex-group rename (the 4
bones that map 1:1 between the two games' bone tables), and exports
targeting WWE2K25.

This mirrors, step for step, the manual Blender workflow already validated
in-game on Blake Christian (see the project doc's "armature redirect"
section) -- it does NOT attempt the harder, never-validated Pec/Lat/Delt
bone-subdivision weight redistribution. That known limitation is left
exactly as it stood after manual testing: arms/shoulders weighting may
still show minor issues for those muscle groups. Everything else in the
validated pipeline (donor-armature redirect, terminator fix applied
afterward by the calling Python process, generic BaseModel materials) is
now fully automatic.

WHY THIS RUNS INSIDE BLENDER AND NOT AS PLAIN PYTHON: the WWE2K_MDL addon
loads/saves .mcd files through the game's own native model library (see
Link/wrapper.py) and does mesh/armature/weight work through Blender's own
data model (bpy) -- there is no supported way to do a *correct* cross-
skeleton conversion (as opposed to raw byte patching) without both of
those. See the "App architecture" decision in the project history: this
was chosen deliberately over a from-scratch byte-level reimplementation
because the addon's own disabled auto-remap code confirms real bone-
topology differences need actual weight recomputation, not string
renaming.

NOT YET TESTED AGAINST A REAL BLENDER SESSION (this was written by reading
the WWE2K_MDL addon's source, not by running it -- I don't have Blender or
the addon available where I write code). Expect the first run to need at
least small fixes -- read every error in the log JSON this script writes,
they're designed to be specific enough to fix from.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

# Set once in _run_job() from the job config; read by _ensure_attach_points_generated()
# so it doesn't need a new parameter threaded through every _export_mesh_objs call site.
_ADDON_PACKAGE_NAME = "WWE2K_MDL"


def _parse_job_path() -> Path:
    # Blender strips its own args; everything after a lone "--" is ours.
    argv = sys.argv
    if "--" not in argv:
        raise SystemExit(
            "Usage: blender --background --python blender_headless_convert.py -- <job.json>"
        )
    rest = argv[argv.index("--") + 1:]
    if not rest:
        raise SystemExit("Missing <job.json> path after '--'")
    return Path(rest[0])


def main() -> None:
    job_path = _parse_job_path()
    job = json.loads(job_path.read_text())

    log: list[str] = []
    result = {"ok": False, "log": log, "error": None}
    result_path = Path(job["result_path"])

    try:
        _run_job(job, log)
        result["ok"] = True
    except Exception as e:  # noqa: BLE001 -- always want a result file, even on crash
        log.append(f"FATAL: {type(e).__name__}: {e}")
        log.append(traceback.format_exc())
        result["error"] = f"{type(e).__name__}: {e}"
    finally:
        result_path.write_text(json.dumps(result, indent=2))

    # Non-zero exit on failure so the calling process's subprocess check
    # notices even if it doesn't parse the result JSON.
    sys.exit(0 if result["ok"] else 1)


def _run_job(job: dict, log: list[str]) -> None:
    import bpy  # only available when actually running inside Blender

    addon_parent_dir = job.get("addon_parent_dir", "")
    addon_package_name = job.get("addon_package_name", "WWE2K_MDL")

    global _ADDON_PACKAGE_NAME
    _ADDON_PACKAGE_NAME = addon_package_name

    log.append(f"Enabling addon package '{addon_package_name}' ...")
    _enable_addon(bpy, addon_package_name, addon_parent_dir, log)

    try:
        vc_interface = _import(f"{addon_package_name}.Link.interface")
        vc_interface_save = _import(f"{addon_package_name}.Link.interface_save")
        skeleton_remap = _import(f"{addon_package_name}.Serialize.skeleton_remap")
    except ImportError as e:
        raise RuntimeError(
            f"Could not import the WWE2K_MDL addon as a package from "
            f"'{addon_parent_dir}' (package name '{addon_package_name}'). "
            f"Check --addon-dir points at the FOLDER THAT CONTAINS the "
            f"'{addon_package_name}' folder (not the folder itself). "
            f"Underlying error: {e}"
        ) from e
    log.append("Addon imported OK.")

    # The 4 bones with a clean 1:1 name mapping between the two games'
    # bone tables (see project doc). RENAME_25_TO_26 in the addon's own
    # skeleton_remap module maps 2K25 name -> 2K26 name; we want the
    # inverse, since we're renaming 2K26-sourced vertex groups back to
    # their real 2K25 names before the donor-armature redirect runs.
    rename_26_to_25 = {v: k for k, v in skeleton_remap.RENAME_25_TO_26.items()}
    log.append(f"4-bone rename map (2K26 -> 2K25): {rename_26_to_25}")

    _clear_scene(bpy)

    import_args = dict(
        import_shape_keys=True,
        split_material_groups=False,
        load_textures=False,  # headless export doesn't need textures loaded
        load_attachment_points=False,
        load_goto_points=True,
        auto_apply_hpl=False,
    )
    # TRIED AND REVERTED (2026-09-20, Ricochet retest): attire import with
    # load_textures=True, on the theory that generate_mtls's per-material
    # shader-TYPE mismatches (e.g. a jacket's alpha-cutout "feathers"
    # rendering as solid opaque geometry) might trace back to the imported
    # material's shader node graph never getting a real texture hookup.
    # Confirmed NOT the mechanism -- and confirmed why the test wasn't even
    # a fair one: Blender's own image loader failed to decode every single
    # source .dds file regardless (`IMB_ibImageFromMemory: unknown
    # file-format`, all textures, both attires), a separate, unrelated
    # limitation of Blender's stock DDS support for whatever compression
    # variant these use. So real pixel data never actually got loaded
    # either way, and the shader-type result was unchanged -- consistent
    # with (not proof of) shader-type being decided independently of
    # whether an image datablock successfully decoded (texture PATH/NAME
    # strings already appeared correctly in the generated .mtls even
    # without this, so that part was never gated on it). Reverted to
    # load_textures=False for attire imports too, matching the donor/
    # BaseModel import -- no observed benefit, and it's the addon's own
    # legacy DLL writer making the shader-type call either way, not
    # something this script's import settings appear to influence.
    export_args_base = dict(
        use_calculate_tangents=True,
        export_shape_keys=True,
        compile_target="option_vcexport_25",
        export_hpl=False,
        generate_mtls=False,  # overridden per-call below; see export_args_attire
        overwrite_existing_jmtls=True,
        material_context="AUTO",
        export_atpts=True,
        export_gtpts=True,
        preserve_normals=False,
        preserve_tangents=False,
        use_vertex_remap=False,
        generate_ctg=False,
        belt_cs_fix=False,
    )
    # BaseModel materials: generate_mtls is ON (changed 2026-10-02 -- was
    # OFF, with BaseModel materials coming only from the bundled generic
    # template or a copied-as-is custom donor file instead; see
    # run_conversion in converter.py). Root cause found by detourne on a real
    # in-game test (Athena, 2026-10-02): the game binds a material to a
    # mesh by matching the material SLOT's name to the mesh OBJECT's own
    # name exactly -- "the name of the material needs to perfectly reflect
    # the name of the object." Athena's own head mesh object is named
    # "head2" (confirmed in this export's own log), but every materials
    # source this tool used automatically (the bundled generic template,
    # and a copied-as-is real donor's baseModel.mtls) uses the slot name
    # "head" -- a mismatch invisible to every earlier diagnostic in this
    # project (slot COUNT was the focus, e.g. "Athena's own BaseModel mesh
    # has only 6 real material slots", 2026-09-20) but which independently
    # explains the long-running "invisible head" symptom regardless of slot
    # count or shader-property correctness. The addon's own native
    # "Generate MTLs" writer (same mechanism already used for attire, and
    # already proven by hand on 2026-09-20 to produce a real 6-slot table
    # matching Athena's own mesh) derives slot names DIRECTLY from the real
    # mesh object names in the scene, so turning it on for BaseModel too
    # fixes this by construction -- no byte-level renaming of an existing
    # file needed. The already-documented 3 wrong-shader-type bugs in the
    # addon's own Generate MTLs output (head2 -> yCh_head, eye_l -> yCh_eye,
    # mouthbag subtype -> Mouthbag) are NOT fixed by this change and still
    # need the same manual MaterialEditor.exe correction as before -- see
    # the project doc's 2026-09-20 BaseModel-materials section.
    export_args_basemodel = dict(export_args_base, generate_mtls=True)

    # Attire materials: turn generate_mtls ON. For a 2K25-target export (our
    # only target here) this routes to the addon's legacy DLL .mtls writer
    # (is_2k26 is False, so dll_generate_mtls == generate_mtls -- see
    # Link/interface_save.py's saveSkinModel), the same thing the addon's own
    # "Generate MTLs" export checkbox does. Confirmed necessary (2026-09-20,
    # Lio Rush): a source attire .mtls can be a hollow/placeholder file that
    # the game silently renders as NO attire at all, and this is the addon's
    # own documented fix for that, now run automatically every time instead
    # of only as a manual recovery step.
    export_args_attire = dict(export_args_base, generate_mtls=True)

    donor_path = job["donor_basemodel_mcd"]
    log.append(f"Importing donor (real WWE2K25) BaseModel: {donor_path}")
    donor_objs = _import_tracked(bpy, vc_interface, donor_path, import_args, log)
    if not donor_objs:
        raise RuntimeError(f"Donor BaseModel import produced no objects: {donor_path}")
    donor_armature = next((o for o in donor_objs if o.type == "ARMATURE"), None)
    if donor_armature is None:
        raise RuntimeError("Donor BaseModel import has no ARMATURE object -- can't redirect onto it.")
    log.append(f"Donor armature in scene: '{donor_armature.name}' ({len(donor_armature.data.bones)} bones)")

    # Donor hair MESH transplant (added 2026-09-25 at detourne's request, as a
    # further step beyond Option 4): import the donor's own real attire
    # .mcd once here (not per-attire) so its hair mesh object(s) can be
    # copied into every source attire that has hair, replacing the
    # source's own problematic hair mesh entirely with real, correctly-
    # authored donor geometry. See _transplant_donor_hair_mesh for the
    # actual replace logic (runs per-attire, inside _convert_one).
    donor_hair_source_objs: list = []
    donor_attire_mcd_path = job.get("donor_attire_mcd", "")
    # Added 2026-10-02 at detourne's request: a fundamentally different
    # (and, per real WWE2K25 modding practice, the CORRECT) attire
    # pipeline -- the donor's own attire .mcd becomes the base scene
    # (native hair + native skeleton, both already correct) and the 2K26
    # source mod's clothing is grafted onto it, instead of the previous
    # default of transplanting the donor's hair MESH into the source
    # attire's scene. See _convert_attire_donor_base's docstring. When
    # this is on, skip the old pre-import-once-for-transplant block below
    # entirely -- _convert_attire_donor_base does its own fresh donor
    # attire import per attire instead (it needs a fresh, untouched copy
    # to strip clothing from each time, not a shared one).
    use_donor_attire_as_base = bool(job.get("use_donor_attire_as_base", False))

    # HEAT SEEK mode (added 2026-10-15, at detourne's explicit request after
    # cross-referencing a community tutorial against this project's own
    # architecture -- see _convert_one_heat_seek's docstring for the full
    # story and _heat_seek_transfer_weights for the weight-recompute
    # mechanics). A NEW, alternative pipeline, added ALONGSIDE every
    # existing option (Options 2/3/4, donor-attire-as-base) -- none of
    # those are removed or disabled by this.
    use_heat_seek = bool(job.get("use_heat_seek", False))
    donor_body_mesh = None
    donor_head_mesh = None
    if use_heat_seek:
        donor_body_mesh = max(
            (o for o in donor_objs if o.type == "MESH"),
            key=lambda o: len(o.data.vertices),
            default=None,
        )
        if donor_body_mesh is not None:
            log.append(
                "HEAT SEEK: reference body mesh chosen (donor's own, largest "
                f"by vertex count): '{donor_body_mesh.name}' "
                f"({len(donor_body_mesh.data.vertices)} vertex/vertices, already "
                "correctly weighted to the donor armature)"
            )
        else:
            log.append(
                "WARNING: HEAT SEEK: donor BaseModel import produced no usable "
                "MESH object to use as the weight-transfer reference -- heat "
                "seek will have nothing to transfer from this run."
            )

        # FIX (2026-10-15, after the first real in-game test on Yota Tsuji,
        # v12: no crash this time, but the head was badly broken -- teeth
        # and eyeballs floating/out of sync, half the head bound to the
        # shoulder (collapsed when the arm raised, stretched sideways when
        # it lowered). Root cause: every heat-seeked mesh, including
        # head/eye/eyelash, was being DATA_TRANSFERed from the single
        # donor_body_mesh above -- the donor's largest-vertex-count mesh,
        # which is the TORSO, not the head (head:skinned is a SEPARATE
        # object in the donor's own BaseModel, same as in every source
        # BaseModel this project has ever imported). DATA_TRANSFER's
        # POLYINTERP_NEAREST maps each target vertex to the nearest point
        # on the chosen SOURCE mesh's own surface -- for a head/eye/eyelash
        # vertex, with no head geometry anywhere on that source mesh to
        # match against, the nearest point it CAN find is down at the
        # collar/shoulder, so those vertices inherited shoulder/arm bone
        # weights instead of real head/neck weights. That is exactly the
        # reported symptom. (The real addon's own heat_seek_rigging also
        # only takes a single reference mesh per call -- the tutorial this
        # was reverse-engineered from almost certainly runs it more than
        # once, with a different, region-appropriate reference mesh
        # selected each time, not once for the whole BaseModel.)
        #
        # Fix: pick a SECOND reference mesh, the donor's own head mesh (by
        # substring name match, same "match by substring, case-insensitive"
        # convention this project uses everywhere else for part names --
        # see e.g. find_attire_folders), and use IT instead of
        # donor_body_mesh for any heat-seek target whose name suggests it's
        # part of the head (head/eye/eyelash -- see
        # _HEAD_REGION_NAME_RE in _convert_one_heat_seek). Falls back to
        # donor_body_mesh if no separate head mesh is found, same (broken)
        # behavior as before, with a warning.
        donor_head_mesh = next(
            (o for o in donor_objs if o.type == "MESH" and "head" in o.name.lower()),
            None,
        )
        if donor_head_mesh is not None:
            log.append(
                "HEAT SEEK: reference HEAD mesh chosen (donor's own, name "
                f"match): '{donor_head_mesh.name}' "
                f"({len(donor_head_mesh.data.vertices)} vertex/vertices) -- used "
                "for head/eye/eyelash heat-seek targets instead of the body "
                "mesh above, so those vertices match against real head "
                "surface geometry instead of snapping to the nearest (very "
                "distant) point on the torso."
            )
        else:
            log.append(
                "WARNING: HEAT SEEK: no separate donor head mesh found by name "
                "match -- head/eye/eyelash targets will fall back to the body "
                "mesh reference, which is the exact mismatch suspected of "
                "causing the Yota Tsuji head/shoulder distortion (2026-10-15)."
            )

    if donor_attire_mcd_path and not use_donor_attire_as_base:
        log.append(f"Importing donor attire (for hair-mesh transplant): {donor_attire_mcd_path}")
        donor_attire_objs = _import_tracked(bpy, vc_interface, donor_attire_mcd_path, import_args, log)
        donor_hair_source_objs = [
            o for o in donor_attire_objs
            if o.type == "MESH" and "hair" in o.name.lower()
        ]
        if donor_hair_source_objs:
            log.append(
                f"  Found {len(donor_hair_source_objs)} donor hair mesh object(s) for "
                f"transplant: {[o.name for o in donor_hair_source_objs]}"
            )
        else:
            log.append(
                "  WARNING: donor attire imported but no mesh object with 'hair' in "
                "its name was found among "
                f"{[o.name for o in donor_attire_objs if o.type == 'MESH']} -- donor "
                "hair mesh transplant will have nothing to use for this run."
            )
    elif use_donor_attire_as_base and not donor_attire_mcd_path:
        log.append(
            "  WARNING: use_donor_attire_as_base is on, but no donor attire .mcd "
            "was resolved -- every attire will fall back to the normal "
            "source-as-base pipeline this run."
        )

    # ---- BaseModel ----
    if use_heat_seek:
        _convert_one_heat_seek(
            bpy, vc_interface, vc_interface_save,
            source_mcd=job["source_basemodel_mcd"],
            out_mcd=job["out_basemodel_mcd"],
            import_args=import_args,
            export_args=export_args_basemodel,
            rename_map=rename_26_to_25,
            donor_armature=donor_armature,
            donor_body_mesh=donor_body_mesh,
            donor_head_mesh=donor_head_mesh,
            label="BaseModel",
            log=log,
        )
    else:
        _convert_one(
            bpy, vc_interface, vc_interface_save,
            source_mcd=job["source_basemodel_mcd"],
            out_mcd=job["out_basemodel_mcd"],
            import_args=import_args,
            export_args=export_args_basemodel,
            rename_map=rename_26_to_25,
            donor_armature=donor_armature,
            fix_hair_bone_weights=job.get("fix_hair_bone_weights", False),
            graft_hair_bones=job.get("graft_hair_bones", False),
            rigid_hair_to_head=job.get("rigid_hair_to_head", False),
            graft_real_hair_bones=job.get("graft_real_hair_bones", False),
            label="BaseModel",
            log=log,
        )

    # ---- Attires ----
    for attire in job.get("attires", []):
        if use_donor_attire_as_base and donor_attire_mcd_path:
            _convert_attire_donor_base(
                bpy, vc_interface, vc_interface_save,
                source_mcd=attire["source_mcd"],
                out_mcd=attire["out_mcd"],
                donor_attire_mcd=donor_attire_mcd_path,
                import_args=import_args,
                export_args=export_args_attire,
                rename_map=rename_26_to_25,
                label=f"Attire ({Path(attire['source_mcd']).name})",
                log=log,
            )
            continue
        if use_heat_seek:
            _convert_one_heat_seek(
                bpy, vc_interface, vc_interface_save,
                source_mcd=attire["source_mcd"],
                out_mcd=attire["out_mcd"],
                import_args=import_args,
                export_args=export_args_attire,
                rename_map=rename_26_to_25,
                donor_armature=donor_armature,
                donor_body_mesh=donor_body_mesh,
                donor_head_mesh=donor_head_mesh,
                label=f"Attire ({Path(attire['source_mcd']).name}) [Heat Seek]",
                log=log,
            )
            continue
        _convert_one(
            bpy, vc_interface, vc_interface_save,
            source_mcd=attire["source_mcd"],
            out_mcd=attire["out_mcd"],
            import_args=import_args,
            export_args=export_args_attire,
            rename_map=rename_26_to_25,
            donor_armature=donor_armature,
            fix_hair_bone_weights=job.get("fix_hair_bone_weights", False),
            graft_hair_bones=job.get("graft_hair_bones", False),
            rigid_hair_to_head=job.get("rigid_hair_to_head", False),
            graft_real_hair_bones=job.get("graft_real_hair_bones", False),
            donor_hair_source_objs=donor_hair_source_objs,
            label=f"Attire ({Path(attire['source_mcd']).name})",
            log=log,
        )

    log.append("All conversions finished.")


def _import(module_name: str):
    import importlib
    return importlib.import_module(module_name)


def _enable_addon(bpy, addon_package_name: str, addon_parent_dir: str, log: list[str]) -> None:
    """
    Make the addon's classes AND its registered custom properties (e.g.
    `Object.mdl_properties`, a PointerProperty the addon registers on
    bpy.types.Object during its own register()) actually exist.

    FIRST REAL RUN (2026-09-20) crashed here: the original version of this
    script only `importlib.import_module()`'d the leaf modules it needed
    (Link.interface etc.) and never called the addon's own register()
    function, so bpy.types.Object never got the `mdl_properties` attribute
    the import path writes to (`Blender/blender_mesh.py`'s
    setMeshGameFlags: `obj.mdl_properties.all_scenes = True`) -- crashed
    with AttributeError on the very first import. Merely being able to
    `import` the Python modules is not the same as the addon being
    "enabled" in Blender's sense.

    Fix, in priority order:
      1. `addon_utils.enable()` -- the same mechanism Blender's own
         Preferences > Add-ons checkbox uses, and (confirmed from detourne's
         traceback: `...AppData\\Roaming\\Blender Foundation\\Blender\\4.2\\
         scripts\\addons\\WWE2K_MDL\\...`) matches where his addon is
         ACTUALLY installed -- Blender's own per-user addons folder, which
         addon_utils already knows how to find without being told a path
         at all. This is the primary path now; `addon_parent_dir` in the
         GUI becomes optional.
      2. Fallback, only if (1) doesn't find it (e.g. the addon lives
         somewhere addon_utils doesn't scan): sys.path-insert
         `addon_parent_dir` and import the package directly, then call its
         own `register()` function manually -- same end effect, for a
         non-standard install location.
    """
    import addon_utils

    mod = None
    try:
        mod = addon_utils.enable(addon_package_name, default_set=True, persistent=True)
    except Exception as e:  # noqa: BLE001 -- fall through to the manual path below
        log.append(f"addon_utils.enable('{addon_package_name}') raised {type(e).__name__}: {e}")

    if mod is not None:
        log.append(
            f"Enabled '{addon_package_name}' via Blender's own addon system "
            f"(found at {getattr(mod, '__file__', '?')})"
        )
        return

    log.append(
        f"addon_utils.enable('{addon_package_name}') did not find it as an installed "
        f"Blender addon -- falling back to manual import from '{addon_parent_dir}'"
    )
    if not addon_parent_dir:
        raise RuntimeError(
            f"'{addon_package_name}' is not enabled/installed as a Blender addon, and no "
            "addon folder path was provided as a fallback. Either enable it in Blender's "
            "Preferences > Add-ons normally once, or fill in the addon folder field in the app."
        )
    if addon_parent_dir not in sys.path:
        sys.path.insert(0, addon_parent_dir)
    addon_pkg = _import(addon_package_name)
    if not hasattr(addon_pkg, "register"):
        raise RuntimeError(
            f"Imported '{addon_package_name}' from '{addon_parent_dir}' but it has no "
            "register() function -- is this really the addon's top-level folder?"
        )
    addon_pkg.register()
    log.append(f"Manually imported + registered '{addon_package_name}' from {addon_parent_dir}")


def _clear_scene(bpy) -> None:
    """Background-mode Blender still starts with the factory default Cube /
    Camera / Light. Remove them so later 'select everything we just
    imported' logic never accidentally picks up scene furniture."""
    import bpy as _bpy  # noqa: F401 -- keep bpy import local & explicit here too
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def _import_tracked(bpy, vc_interface, path: str, import_args: dict, log: list[str]):
    """Import `path` and return the list of newly-created bpy objects (by
    before/after diff of bpy.data.objects -- same pattern the addon's own
    disabled skeleton-remap code uses for this)."""
    before = set(bpy.data.objects.keys())
    ok = vc_interface.loadModelFile(path, dict(import_args), operator=None)
    if ok is False:
        raise RuntimeError(f"loadModelFile reported failure for: {path}")
    after = set(bpy.data.objects.keys())
    new_names = after - before
    new_objs = [bpy.data.objects[n] for n in new_names]
    log.append(f"  imported {path}: {len(new_objs)} new object(s)")
    return new_objs


def _report_unresolved_vertex_groups(mesh_objs, donor_armature, log: list[str]) -> None:
    """
    DIAGNOSTIC (added 2026-09-26, after Kosei Fujita's in-game test: the
    donor hair transplant itself worked -- confirmed animating correctly --
    but the SOURCE's own goatee mesh came out static (not moving with mouth
    movements) and discolored. Neither of those is something this project's
    donor-hair-transplant or rigid-hair-to-head code touches (both only
    ever act on meshes with a Hair_bNN-style vertex group -- see
    _find_hair_mesh_objs -- and a goatee is ordinarily rigged to a regular
    body/face bone, not a dynamic hair-bone chain). So this generalizes the
    same "does this vertex group's name resolve to a real bone on the
    target armature" check the hair-specific options already do (see
    Blender/blender_utils.py's getBoneIndex/getVertexWeights, traced
    earlier in this project) to EVERY mesh being exported, not just hair --
    a dropped weight on, say, a jaw-adjacent secondary bone would produce
    exactly a static/non-animating mesh, the same failure signature as
    every other "unmapped bone name" bug found in this project so far.

    Deliberately run LAST in _convert_one, right before export -- after
    every hair-specific option has already had its chance to add bones or
    otherwise change the mesh set -- so this reflects the actual final
    state being written to the .mcd, not an intermediate one.

    Hair_bNN-style names are excluded from what gets flagged here: those
    are ALREADY a known, separately-diagnosed issue (Options 2/3/4 above,
    or the donor hair transplant's own check) -- repeating them here would
    just be noise on top of an already-understood problem. This is only
    trying to surface something NEW: an unresolved name that ISN'T part of
    that already-understood hair-chain story.

    This says nothing about MATERIALS/color -- vertex-group-to-bone
    resolution and the addon's own "Generate MTLs" material export are two
    completely separate systems (the latter lives inside the addon's
    compiled cmodellib, not anything traceable in this project's own
    Python), so a clean result here would not explain the goatee's
    discoloration on its own.
    """
    if donor_armature is None:
        return
    target_bone_names = {b.name for b in donor_armature.data.bones}
    for obj in mesh_objs:
        if obj.type != "MESH":
            continue
        vgroup_names = [vg.name for vg in obj.vertex_groups]
        if not vgroup_names:
            continue
        unresolved = [
            n for n in vgroup_names
            if n not in target_bone_names and not _HAIR_BONE_RE.match(n)
        ]
        if unresolved:
            log.append(
                f"    WARNING: '{obj.name}' has {len(unresolved)} non-hair vertex "
                f"group(s) that DON'T match any real bone on the target armature "
                f"({unresolved[:10]}"
                + (" ...(truncated)" if len(unresolved) > 10 else "")
                + ") -- these will silently export with dropped weight, which "
                "shows up in-game as that part of the mesh not moving/animating "
                "(it stays rigid at its bind-pose position)."
            )


def _apply_rename_map(mesh_objs, rename_map: dict, log: list[str]) -> None:
    renamed = 0
    for obj in mesh_objs:
        if obj.type != "MESH":
            continue
        for old_name, new_name in rename_map.items():
            vg = obj.vertex_groups.get(old_name)
            if vg is not None and obj.vertex_groups.get(new_name) is None:
                vg.name = new_name
                renamed += 1
    if renamed:
        log.append(f"  renamed {renamed} vertex group(s) toward real 2K25 bone names")


import re

# Dynamic-hair chain bones follow "Hair_bNN" (chain root) / "Hair_bNN_MM"
# (link MM of chain NN) naming, confirmed by direct byte-level inspection of
# real .mcd files (2026-09-24, Saya Kamitani): source (WWE2K26) uses 16
# uniform 8-link chains (144 bone names total); a real donor's own WWE2K25
# hair rig uses a DIFFERENT, non-uniform chain layout (fewer/more chains,
# different link counts per chain) -- same mismatch exists against the
# bundled generic (no-hair) donor, which simply has none of these bones at
# all. Either way, over half the source's hair bone names commonly have no
# match in the target skeleton at all.
#
# WIDENED 2026-10-15 (Yota Tsuji, v13 in-game re-test): this was
# "^Hair_b(\d+)...$" -- only the "b" chain letter -- until detourne reported
# the beard/sideburn meshes floating and unattached after the BaseModel
# head fix (v13) otherwise worked. Root cause, confirmed by reading the
# same run's hair-bone-chain-graft log line by line: this character's hair
# rig has THREE separate chain letters -- Hair_b* (main hair, correctly
# detected), Hair_c* and Hair_d* (sideburns/beard, NOT detected at all by
# the old "b"-only pattern). _find_hair_mesh_objs uses this regex to decide
# which mesh objects count as "hair" for the bone-chain-graft treatment
# (both Heat Seek and "5. Hair Graft Only" mode) -- a mesh whose vertex
# groups are entirely Hair_c*/Hair_d* was silently falling through to
# Heat Seek's generic DATA_TRANSFER recompute (from the donor's BODY mesh,
# which has no hair/beard geometry anywhere near it -- nearest-surface
# mapping bound it to whatever body bone happened to be closest, producing
# exactly the reported "floating and unattached" look) instead of getting
# the real hair-bone graft like the main hair mesh did. The root J_Hair
# bone-hierarchy walk in _graft_hair_only_subtree was never affected by
# this (it grafts by real parent/child structure, not by this regex) --
# this only affects CLASSIFICATION of which MESH OBJECTS get treated as
# hair. Now matches any single-letter chain (Hair_b/c/d/... NN or NN_MM),
# not just "b" -- the letter itself is not captured (group 1 is still the
# chain number, group 2 the link number) since every live call site only
# ever needs those two numbers, never the letter.
_HAIR_BONE_RE = re.compile(r"^Hair_[a-zA-Z](\d+)(?:_(\d+))?$")

# Universal fallback when a hair bone's own chain has NO surviving link at
# all in the target skeleton (confirmed present in both the real WWE2K25
# donor's and the WWE2K26 source's BaseModel skeletons via string
# extraction, alongside the rest of the standard "J_"-prefixed humanoid
# rig -- J_Hips, J_Spine1/2, J_Neck, J_Head, J_Jaw, etc. -- so this is not a
# guess). Hair simply becomes rigid relative to the head instead of
# dynamically simulated for whatever links land here.
_HAIR_FALLBACK_BONE = "J_Head"


def _hair_bone_fallback_chain(name: str) -> list[str]:
    """
    NOTE (2026-10-15): only ever called by Option 2 (_remap_hair_bone_weights),
    which is hardcoded off and unreachable from the UI (see app.py). This
    function (and _hair_bone_parent_name/_hair_bone_child_name below) still
    hardcode the "b" chain letter when reconstructing a name from
    (chain, link) -- now that _HAIR_BONE_RE matches any chain letter, those
    reconstructed names would be WRONG for a Hair_c*/Hair_d*/etc. bone (they'd
    build "Hair_bNN" instead of "Hair_cNN"). Left as-is since this code path
    can't currently run; fix properly (thread the matched letter through
    too) before ever re-enabling Option 2/3.

    Candidate replacement bone names for a missing hair bone, nearest-first:
    walk down the SAME chain toward its root (Hair_bNN_MM -> Hair_bNN_(MM-1)
    -> ... -> Hair_bNN_00 -> Hair_bNN), then fall back to the universal
    anchor bone. This is a naming-convention walk, not a real armature
    parent-chain walk -- the source .mcd's own bone hierarchy objects don't
    exist as real Blender bones in this scene (only the target/donor
    armature does), so the chain's own numbering is the only hierarchy
    information available, and it reliably reflects it (link MM's real
    parent is always link MM-1, confirmed by the uniform naming pattern in
    every hair rig inspected so far).
    """
    m = _HAIR_BONE_RE.match(name)
    if not m:
        return [_HAIR_FALLBACK_BONE]
    chain, link = m.group(1), m.group(2)
    candidates = []
    if link is not None:
        for l in range(int(link) - 1, -1, -1):
            candidates.append(f"Hair_b{chain}_{l:02d}")
        candidates.append(f"Hair_b{chain}")
    candidates.append(_HAIR_FALLBACK_BONE)
    return candidates


def _remap_hair_bone_weights(bpy, mesh_objs, target_bone_names: set[str], log: list[str]) -> None:
    """
    OPTION 2 fix (added 2026-09-24, at detourne's request, after confirming via
    byte-level bone-name comparison that dynamic-hair chain bones on the
    WWE2K26 source commonly don't exist by name in the target/donor 2K25
    skeleton at all -- over half, on the one real test case measured).

    Read directly from the addon's own export code (Blender/blender_utils.py
    getVertexWeights/getBoneIndex): a vertex's weight slot is written out
    ONLY if its vertex group's name is found in the target armature's bone
    list (`skin.bones`, itself literally `obj.parent.data.bones` at export
    time -- i.e. the donor armature after the redirect). If not found, that
    weight slot is silently left at its prepopulated default (blend index 0,
    weight 0.0) -- never reassigned, never warned about per-vertex. This is
    the CONFIRMED, exact mechanism behind the "odd stretching in the long
    [hair] parts" detourne reported: vertices whose hair-bone weights all
    vanish this way end up with no real influence from where they should
    be, while neighboring boundary vertices (with some surviving non-hair
    weight) get pulled elsewhere as the body animates.

    This function runs BEFORE export (before reconcile_armature_binding /
    saveModelFile), while we still have direct access to every vertex
    group's real weight data via Blender's vertex_groups/vertex.groups API.
    For every hair-chain vertex group whose name isn't in the target
    skeleton, every vertex's weight in that group is moved (summed, not
    overwritten) onto the nearest surviving bone along the same chain, or
    onto the universal head-bone fallback if the whole chain is gone -- see
    _hair_bone_fallback_chain(). The original (missing-bone) vertex group is
    then removed so nothing references a nonexistent bone at export time.

    Trade-off, stated plainly for the log: bones collapsed this way lose
    whatever dynamic/simulated motion they'd have had at their real link
    position -- they become rigid relative to wherever they landed. This
    does not touch the .ycl dynamics file at all (out of scope for this
    fix, and doing so safely would need a currently-unimplemented, riskier
    partial merge -- see the project doc); the .ycl's own per-bone physics
    entries for links that got collapsed away will reference bones that no
    longer exist in the exported skeleton. Based on the same
    graceful-degradation pattern already confirmed for mesh weights (unknown
    name -> silently skipped, not a crash), this is EXPECTED to be similarly
    harmless, but this has NOT been confirmed against the game -- flagged
    here and to detourne rather than assumed.
    """
    total_groups_remapped = 0
    total_vertices_touched = 0
    fallback_targets_used: dict[str, int] = {}

    for obj in mesh_objs:
        if obj.type != "MESH":
            continue
        hair_groups = [vg for vg in obj.vertex_groups if _HAIR_BONE_RE.match(vg.name)]
        missing = [vg for vg in hair_groups if vg.name not in target_bone_names]
        if not missing:
            continue

        # Resolve each missing group's fallback destination up front.
        resolved: dict[str, str] = {}
        for vg in missing:
            for candidate in _hair_bone_fallback_chain(vg.name):
                if candidate in target_bone_names:
                    resolved[vg.name] = candidate
                    break
            else:
                # _HAIR_FALLBACK_BONE itself wasn't in the target skeleton --
                # shouldn't happen (it's a standard humanoid bone), but don't
                # silently drop the weight if it does: leave the group alone
                # rather than guess at something else.
                log.append(
                    f"  WARNING: hair bone '{vg.name}' has no fallback -- even "
                    f"'{_HAIR_FALLBACK_BONE}' is missing from the target skeleton. "
                    "Leaving this vertex group as-is (its weights will still be "
                    "dropped at export, same as before this fix)."
                )

        if not resolved:
            continue

        # Ensure every fallback destination group exists on this object.
        dest_groups = {}
        for dest_name in set(resolved.values()):
            vg = obj.vertex_groups.get(dest_name)
            if vg is None:
                vg = obj.vertex_groups.new(name=dest_name)
            dest_groups[dest_name] = vg

        group_index_to_name = {vg.index: vg.name for vg in obj.vertex_groups}
        touched_this_obj = 0
        for vertex in obj.data.vertices:
            for ge in list(vertex.groups):
                src_name = group_index_to_name.get(ge.group)
                dest_name = resolved.get(src_name) if src_name else None
                if dest_name is None or ge.weight <= 0.0:
                    continue
                dest_vg = dest_groups[dest_name]
                existing = 0.0
                try:
                    existing = dest_vg.weight(vertex.index)
                except RuntimeError:
                    existing = 0.0
                dest_vg.add([vertex.index], existing + ge.weight, "REPLACE")
                touched_this_obj += 1

        for src_name in resolved:
            vg = obj.vertex_groups.get(src_name)
            if vg is not None:
                obj.vertex_groups.remove(vg)

        total_groups_remapped += len(resolved)
        total_vertices_touched += touched_this_obj
        for dest_name in resolved.values():
            fallback_targets_used[dest_name] = fallback_targets_used.get(dest_name, 0) + 1
        log.append(
            f"  '{obj.name}': collapsed {len(resolved)} orphaned hair bone vertex "
            f"group(s) onto {len(set(resolved.values()))} surviving bone(s) "
            f"({touched_this_obj} weight-slot(s) moved)"
        )

    if total_groups_remapped:
        log.append(
            f"  OPTION 2 hair-bone fix: {total_groups_remapped} hair bone(s) total "
            f"collapsed across all meshes, {total_vertices_touched} vertex weight "
            f"slot(s) moved, {fallback_targets_used.get(_HAIR_FALLBACK_BONE, 0)} "
            f"landed on the universal '{_HAIR_FALLBACK_BONE}' fallback (whole chain "
            "missing from target skeleton) -- these are now rigid instead of "
            "dynamically simulated. NOT yet confirmed in-game."
        )


def _find_hair_mesh_objs(mesh_objs):
    """Mesh objects with at least one Hair_bNN/Hair_bNN_MM-named vertex
    group -- the same heuristic Option 4's _pin_hair_to_head uses to
    identify "the hair mesh(es)" within an attire's mesh set."""
    return [
        obj for obj in mesh_objs
        if obj.type == "MESH" and any(_HAIR_BONE_RE.match(vg.name) for vg in obj.vertex_groups)
    ]


def _graft_donor_hair_bones(bpy, donor_armature, source_armature, bone_names: list[str], log: list[str]) -> set[str]:
    """
    REVISION (2026-09-25, v2 of the donor hair mesh transplant): the first
    version of the transplant reparented the copied donor hair mesh straight
    onto `donor_armature` (the donor's main 207-bone BaseModel skeleton) and
    just hoped its vertex groups would resolve. First log-only diagnostic
    run (Kosei Fujita) proved that wrong: 26 of the mesh's 27 vertex groups
    (Hair_b01/02/05 chains, plus a differently-named 'J_Hair_b' root) don't
    exist as real bones on the BaseModel skeleton at all -- per
    Blender/blender_utils.py's getBoneIndex/getVertexWeights (traced
    earlier in this project), a vertex's weight is only written if its
    vertex group's name matches a REAL bone on whatever armature the mesh
    is parented to at export time, so ~96% of this mesh's weight data would
    have silently exported as zero -- the same "stretched pillar" failure
    Option 4 hit on its first (narrow) attempt, except here there's no
    rigid-pin fallback, since the whole point of transplanting real geometry
    is to keep real per-strand motion.

    Root cause: the donor's real dynamic hair bones aren't part of the
    donor's main BaseModel skeleton at all -- they live on a SEPARATE,
    attire-embedded armature that Blender creates fresh every time an
    attire .mcd with embedded skeleton data is imported (confirmed by the
    log: the donor hair mesh was "originally parented to 'Skeleton.001'",
    not 'Skeleton'/donor_armature). That's a real, working WWE2K25 rig for
    this exact hairstyle -- unlike Option 3's situation (the SOURCE's own
    hair bones, which don't exist as real data ANYWHERE in this pipeline,
    forcing Option 3 to synthesize positions from mesh vertex averages),
    we have the donor's actual rest-pose bone transforms sitting right
    there in `source_armature`. This function copies them (head, tail, and
    roll -- not just position, learned the hard way across Option 3's 5
    revisions) into `donor_armature`, so the mesh can then be safely
    reparented there and its vertex groups resolve against real bone data
    instead of nothing.

    Walks each name's REAL parent chain in `source_armature` (via
    `Bone.parent`, not the Hair_bNN naming convention Option 3 has to
    assume -- these donor bones don't all follow it) until it reaches a
    bone that already exists by name in `donor_armature`. That's expected
    to normally be a real shared body bone like J_Head, since the donor's
    BaseModel skeleton and the donor's own attire-embedded hair skeleton
    are two separate exports of the SAME real WWE2K25 character and should
    reference their shared anchor bone(s) by the same name.

    Coordinates are converted through each armature object's own world
    matrix (`source_armature.matrix_world` / `donor_armature.matrix_world`)
    rather than assumed to already share one local space -- cheap
    correctness insurance in case the two armature objects don't happen to
    sit at the same transform, even though every armature this pipeline has
    imported so far has been at the origin.

    Returns the subset of `bone_names` that are now resolvable in
    `donor_armature` (already present, or newly grafted here) so the caller
    can report which vertex groups will actually carry real weight.

    UNTESTED IN-GAME as of writing -- log-only verification first, same
    discipline as every other hair fix in this project.
    """
    import mathutils

    existing = {b.name for b in donor_armature.data.bones}
    to_graft = [n for n in bone_names if n not in existing]
    if not to_graft:
        return {n for n in bone_names if n in existing}

    source_bones = source_armature.data.bones
    graftable = [n for n in to_graft if source_bones.get(n) is not None]
    not_real_bones = [n for n in to_graft if source_bones.get(n) is None]
    if not_real_bones:
        log.append(
            f"    WARNING: donor hair bone graft: {len(not_real_bones)} vertex "
            f"group name(s) aren't real bones on the donor's own hair rig "
            f"('{source_armature.name}') either ({not_real_bones[:10]}"
            + (" ...(truncated)" if len(not_real_bones) > 10 else "")
            + ") -- can't graft these, will still export with dropped weight."
        )

    if not graftable:
        return {n for n in bone_names if n in existing}

    prev_active = bpy.context.view_layer.objects.active
    prev_mode = prev_active.mode if prev_active is not None else "OBJECT"
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.objects.active = donor_armature
    bpy.ops.object.mode_set(mode="EDIT")

    edit_bones = donor_armature.data.edit_bones
    src_world = source_armature.matrix_world
    src_world_rot = src_world.to_3x3()
    dst_world_inv = donor_armature.matrix_world.inverted()
    dst_world_inv_rot = dst_world_inv.to_3x3()

    grafted: set[str] = set()
    cycle_guard_tripped: list[str] = []

    def ensure(name: str, seen: tuple):
        eb_existing = edit_bones.get(name)
        if eb_existing is not None:
            return eb_existing
        if name in seen:
            cycle_guard_tripped.append(name)
            return None
        sb = source_bones.get(name)
        if sb is None:
            return None

        parent_eb = None
        if sb.parent is not None:
            parent_eb = ensure(sb.parent.name, seen + (name,))

        head_world = src_world @ sb.head_local
        tail_world = src_world @ sb.tail_local
        z_local_src = sb.matrix_local.to_3x3() @ mathutils.Vector((0.0, 0.0, 1.0))
        z_world = src_world_rot @ z_local_src

        eb = edit_bones.new(name)
        eb.head = dst_world_inv @ head_world
        eb.tail = dst_world_inv @ tail_world
        if parent_eb is not None:
            eb.parent = parent_eb
            eb.use_connect = False
        z_dst = dst_world_inv_rot @ z_world
        if z_dst.length > 1e-6:
            eb.align_roll(z_dst.normalized())

        grafted.add(name)
        return eb

    try:
        for name in graftable:
            ensure(name, ())
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
        if prev_active is not None:
            bpy.context.view_layer.objects.active = prev_active
            try:
                bpy.ops.object.mode_set(mode=prev_mode)
            except RuntimeError:
                pass

    if cycle_guard_tripped:
        log.append(
            f"    WARNING: donor hair bone graft: parent-chain cycle guard "
            f"tripped on {sorted(set(cycle_guard_tripped))} -- these bones "
            "were skipped (shouldn't happen on a real rig; flagging rather "
            "than infinite-looping)"
        )

    log.append(
        f"    donor hair bone graft: copied {len(grafted)} real bone(s) "
        f"(head/tail/roll, from the actual donor rig) from "
        f"'{source_armature.name}' into the donor armature "
        f"'{donor_armature.name}': {sorted(grafted)}"
    )

    return {n for n in bone_names if n in existing or n in grafted}


def _transplant_donor_hair_mesh(bpy, mesh_objs: list, donor_hair_source_objs: list, donor_armature, log: list[str]) -> None:
    """
    Donor hair MESH transplant (added 2026-09-25 at detourne's request, as a
    further option beyond Option 4). Option 4 (confirmed working
    in-game the same day) trades away all dynamic hair motion by pinning
    every hair vertex rigidly to J_Head. This is a different, more
    ambitious approach for when a chosen custom donor character's real
    hairstyle is a reasonable stand-in: instead of guessing at bone
    positions (Option 3) or giving up on motion (Option 4), REPLACE the
    source's own hair mesh with the donor's -- real geometry, real
    vertex weights, authored for and by a real WWE2K25 character, needing
    no POSITION-guessing at all (see _graft_donor_hair_bones: its real
    rest-pose bone data is copied wholesale, not synthesized) -- so it can
    keep genuine per-strand dynamics (paired with the donor's own real
    `.ycl`/`.hair_ywa` companion files, already carried over separately
    by the existing "carry over donor hair" checkbox this piggybacks on).

    Runs per-attire (called from _convert_one, right after
    _apply_rename_map and BEFORE Options 2/3/4).

    CORRECTED (2026-09-25, after the first log-only test on Kosei Fujita):
    the comment here used to claim Options 2/3/4 would naturally leave the
    donor hair alone because it "uses the donor's own bone-chain layout,
    not the source's uniform Hair_bNN naming" -- that assumption was WRONG.
    The real donor hairstyle tested (Kosei Fujita's donor, Kosei_Fujita's
    own WWE2K25 hair) uses vertex groups named 'Hair_b01'/'Hair_b02'/
    'Hair_b05' plus a 'J_Hair_b' root -- i.e. it DOES match _HAIR_BONE_RE,
    the exact same pattern Options 2/3/4 key off of. So if any of those are
    also enabled, they WILL find and touch the transplanted donor mesh too
    -- Option 4 in particular would rigidly pin it to J_Head, throwing away
    the real per-strand motion this feature exists to preserve. Leave
    Options 2/3/4 off when using this feature (matches the OFF/OFF/OFF
    settings in the first real test).

    Only touches an attire that actually HAS a source hair mesh (per
    _find_hair_mesh_objs) -- an attire with no hair to begin with is left
    completely untouched, so this safely runs on every attire in a plan
    without needing to know in advance which one has hair.

    donor_hair_source_objs are the ORIGINAL objects imported ONCE from
    the donor's own attire .mcd (in _run_job, before the per-attire
    loop) -- the same donor hair mesh may be needed across multiple
    attires, so this always works on independent .copy()'s of them,
    never the originals (which stay untouched in the scene throughout).

    UNTESTED IN-GAME as of writing -- log-only verification first, same
    discipline as every other hair fix in this project.
    """
    source_hair_objs = _find_hair_mesh_objs(mesh_objs)
    if not source_hair_objs:
        log.append(
            "  DONOR HAIR TRANSPLANT: no source hair mesh found on this attire -- "
            "nothing to replace, leaving it untouched"
        )
        return

    if not donor_hair_source_objs:
        log.append(
            "  WARNING: DONOR HAIR TRANSPLANT: this attire has a source hair mesh "
            f"({[o.name for o in source_hair_objs]}), but no donor hair mesh "
            "object(s) were found/imported to replace it with -- leaving the "
            "source's own (known-problematic) hair mesh in place rather than "
            "deleting it with nothing to put back."
        )
        return

    removed_names = [o.name for o in source_hair_objs]
    removed_vertex_total = sum(len(o.data.vertices) for o in source_hair_objs)
    for obj in source_hair_objs:
        mesh_objs.remove(obj)
        bpy.data.objects.remove(obj, do_unlink=True)

    dest_collection = None
    if mesh_objs:
        cols = mesh_objs[0].users_collection
        if cols:
            dest_collection = cols[0]
    if dest_collection is None:
        dest_collection = bpy.context.scene.collection

    transplanted = []
    for src in donor_hair_source_objs:
        # Independent copy -- .data.copy() too, so this mesh's own
        # vertex-group edits (Options 2/3/4, if enabled, WILL also match
        # and touch this mesh -- see the CORRECTED note in this function's
        # docstring above -- so leave them off) never touch the original
        # donor object, which may be reused for another attire in this
        # same job.
        new_obj = src.copy()
        new_obj.data = src.data.copy()
        dest_collection.objects.link(new_obj)

        # ORDER MATTERS (v2, 2026-09-25): graft the donor's own REAL hair
        # bones into donor_armature BEFORE reparenting the mesh onto it --
        # see _graft_donor_hair_bones for why the v1 blind-reparent
        # (further down, now removed) silently dropped ~96% of this mesh's
        # weight data on the first log-only test. `src.parent` here is the
        # ORIGINAL donor hair object's own armature -- the one Blender
        # created fresh when the donor's attire .mcd was imported in
        # _run_job (e.g. 'Skeleton.001'), which carries the donor's real,
        # working rest-pose bone data for this exact hairstyle.
        original_parent = src.parent.name if src.parent else None
        vgroup_names = [vg.name for vg in new_obj.vertex_groups]
        source_armature = src.parent if (src.parent is not None and src.parent.type == "ARMATURE") else None
        if source_armature is not None:
            resolvable = _graft_donor_hair_bones(bpy, donor_armature, source_armature, vgroup_names, log)
        else:
            log.append(
                f"    WARNING: donor hair mesh '{src.name}' has no armature parent "
                "of its own ('Skeleton.001'-style) to graft real bone data from -- "
                "falling back to reparenting onto the donor's main armature as-is, "
                "which is very likely to drop most of this mesh's weight (see "
                "_graft_donor_hair_bones for why)."
            )
            resolvable = {n for n in vgroup_names if n in {b.name for b in donor_armature.data.bones}}

        # Now that (hopefully) real bones exist for its vertex groups,
        # reparent the mesh copy onto donor_armature -- the one object this
        # whole pipeline treats as the single shared skeleton every mesh in
        # the output attire is ultimately bound to for export.
        new_obj.parent = donor_armature
        arm_mod = next((m for m in new_obj.modifiers if m.type == "ARMATURE"), None)
        if arm_mod is None:
            arm_mod = new_obj.modifiers.new(type="ARMATURE", name="Armature")
        arm_mod.object = donor_armature

        unresolved = [n for n in vgroup_names if n not in resolvable]
        if unresolved:
            log.append(
                f"    WARNING: donor hair mesh '{new_obj.name}' STILL has "
                f"{len(unresolved)} vertex group(s) that don't match any real bone "
                f"in the donor armature even after grafting ({unresolved[:10]}"
                + (" ...(truncated)" if len(unresolved) > 10 else "")
                + ") -- these will silently export with dropped weight."
            )
        else:
            log.append(
                f"    donor hair mesh '{new_obj.name}': all {len(vgroup_names)} "
                "vertex group(s) now resolve to a real bone in the donor armature."
            )

        mesh_objs.append(new_obj)
        transplanted.append(new_obj)
        log.append(
            f"    donor hair object '{src.name}' (originally parented to "
            f"'{original_parent}') copied as '{new_obj.name}', reparented to "
            f"donor armature '{donor_armature.name}', {len(new_obj.data.vertices)} "
            f"vertex/vertices, {len(vgroup_names)} vertex group(s)"
        )

    transplanted_vertex_total = sum(len(o.data.vertices) for o in transplanted)
    log.append(
        f"  DONOR HAIR TRANSPLANT: removed source hair mesh {removed_names} "
        f"({removed_vertex_total} vertex/vertices total), replaced with donor "
        f"hair mesh {[o.name for o in transplanted]} ({transplanted_vertex_total} "
        "vertex/vertices total). UNTESTED IN-GAME."
    )


def _pin_hair_to_head(bpy, mesh_objs, target_bone_names: set[str], log: list[str]) -> None:
    """
    OPTION 4 fix (added 2026-09-25, at detourne's request, after Options 2 and 3
    both failed real in-game tests on Shota Umino's bone-chain hair (Option
    2: "odd stretching"; Option 3, through 5 revisions -- v1 fixed
    placeholder, v2 mesh-derived positions, v3 axis-swap correction, v4
    chain-tip length fix, v5 outward roll -- each measurably closer, but v5
    STILL showed stretching localized to the top/crown/back of the head).
    Option 3's whole premise is a synthesized bone chain standing in for
    real rig data that was never available in any .mcd examined in this
    project; after 5 iterations of refining that synthesis and still not
    reaching a clean result, detourne asked for a fundamentally different,
    much blunter approach instead of a 6th refinement: give up on dynamic
    hair motion entirely and pin every hair vertex rigidly to J_Head.

    Unlike Option 2 (which only touches vertex groups NAMED after a hair
    bone that's missing from the target skeleton, and re-targets each to
    the nearest surviving link in ITS OWN chain before falling back to
    J_Head) and Option 3 (which fabricates the missing bones instead of
    removing the need for them), this is unconditional: for every vertex
    that has ANY nonzero weight in ANY Hair_bNN/Hair_bNN_MM-named group --
    regardless of whether that exact bone happens to exist in the target
    skeleton -- ALL of that vertex's existing group memberships (hair AND
    any blended non-hair weight) are removed, and it is reassigned 100% to
    J_Head. The hair mesh becomes a single rigid piece that moves only
    with the head bone, like any other static attachment (comparable to
    how glasses or a static prop would be rigged) -- no per-strand
    simulation, no per-vertex ycl. There is nothing left for a hair `.ycl`
    to drive once this runs -- worth testing WITHOUT bothering to run the
    source `.ycl` through YclEditor.exe at all, since no vertex will carry
    any Hair_bNN weight for its physics data to act on either way.

    This trades away all dynamic hair motion, but it is a fundamentally
    simpler, much more constrained operation than Options 2/3 -- no
    synthesized geometry, no bone creation, no coordinate-space or roll
    assumptions to get wrong, just "attach this mesh to a bone we already
    know is correct, the same way the rest of the head/face already is."

    BROADENED (2026-09-25, v2, same day as the first version above): a
    first real in-game test still showed the same catastrophic stretched-
    hair artifact -- WITH the .ycl removed entirely, ruling that out as a
    factor, and with the pre-export log confirming the original, narrower
    version of this function DID correctly pin every vertex it looked at.
    A round-trip export-verification diagnostic (added the same day, see
    _verify_rigid_hair_export) then found the actual cause: 2024 of
    Shota Umino's 10218 "hair:skinned" mesh vertices came out of export
    with literally ZERO weight. Those are vertices that were never
    weighted to ANY Hair_bNN group to begin with -- so the original
    per-vertex check below never touched them -- but whatever bone(s)
    they WERE weighted to also doesn't exist in the target skeleton, so
    the addon's exporter silently dropped them too, the same "unknown
    bone name -> dropped" behavior this whole project has been working
    around since Option 1, just never caught for this specific mesh
    before. An unweighted vertex commonly snaps/stretches toward the
    world origin in-game -- a very plausible match for the reported
    artifact, and one entirely untouched by the original narrower fix.

    Rather than only patch the specific vertices already known to be
    Hair_bNN-weighted, this now pins EVERY vertex belonging to any mesh
    OBJECT that has at least one Hair_bNN-named vertex group at all --
    the whole object is treated as a "hair mesh", full stop, once any
    part of it is. This is also a more literal match for detourne's original
    request ("all hair is just assigned to J_Head") than the narrower
    per-vertex version was.

    CONFIRMED WORKING IN-GAME (2026-09-25, Shota Umino, this v2/broadened
    revision): the round-trip export diagnostic came back clean (all
    10218 hair-mesh vertices weighted only to J_Head, zero unweighted),
    and detourne confirmed the in-game result: "This version worked." This is
    the recommended fix for a bone-chain hair rig that Options 1/2/3 can't
    handle correctly -- see README for the full investigation.
    """
    if _HAIR_FALLBACK_BONE not in target_bone_names:
        log.append(
            f"  WARNING: OPTION 4 rigid-hair-to-head: universal anchor bone "
            f"'{_HAIR_FALLBACK_BONE}' not found in the target skeleton -- "
            "skipping this fix entirely rather than pinning hair to a bone "
            "that won't exist at export time."
        )
        return

    total_vertices_touched = 0
    total_groups_removed = 0

    for obj in mesh_objs:
        if obj.type != "MESH":
            continue

        hair_group_indices = {
            vg.index for vg in obj.vertex_groups if _HAIR_BONE_RE.match(vg.name)
        }
        if not hair_group_indices:
            continue

        # BROADENED 2026-09-25 (v2, see docstring): once an object has been
        # identified as a "hair mesh" (has at least one Hair_bNN-named
        # group anywhere on it), EVERY vertex on that object is pinned --
        # not just the subset that happened to already carry weight in a
        # Hair_bNN group. This is what actually catches the vertices that
        # were weighted to some OTHER, also-missing bone name and would
        # otherwise silently export with zero weight (confirmed via a
        # round-trip export diagnostic: 2024 of 10218 vertices on Shota
        # Umino's "hair:skinned" mesh fell into exactly this gap under the
        # narrower v1 check).
        hair_vertex_indices = [v.index for v in obj.data.vertices]
        narrowly_matched = sum(
            1 for v in obj.data.vertices
            if any(ge.group in hair_group_indices and ge.weight > 0.0 for ge in v.groups)
        )
        if not hair_vertex_indices:
            continue

        head_vg = obj.vertex_groups.get(_HAIR_FALLBACK_BONE)
        if head_vg is None:
            head_vg = obj.vertex_groups.new(name=_HAIR_FALLBACK_BONE)

        group_index_to_name = {vg.index: vg.name for vg in obj.vertex_groups}
        for vidx in hair_vertex_indices:
            vertex = obj.data.vertices[vidx]
            # Remove membership in every group this vertex currently has --
            # not just the hair-named ones -- so nothing but J_Head has any
            # say in this vertex's final position. Snapshot group indices
            # first since we're mutating membership while iterating.
            current_groups = [ge.group for ge in vertex.groups]
            for gidx in current_groups:
                name = group_index_to_name.get(gidx)
                if name is None:
                    continue
                vg = obj.vertex_groups.get(name)
                if vg is not None:
                    vg.remove([vidx])
            head_vg.add([vidx], 1.0, "REPLACE")

        total_vertices_touched += len(hair_vertex_indices)

        # Clean up: the hair-named groups should now be empty (every vertex
        # that belonged to one was just stripped of all its memberships
        # above) -- remove them outright so nothing references a bone name
        # that may not exist in the target skeleton at export time.
        removed_this_obj = 0
        for vg in list(obj.vertex_groups):
            if _HAIR_BONE_RE.match(vg.name):
                obj.vertex_groups.remove(vg)
                removed_this_obj += 1
        total_groups_removed += removed_this_obj

        log.append(
            f"  '{obj.name}': pinned {len(hair_vertex_indices)} vertex/vertices (whole "
            f"object, since it has Hair_bNN group(s) present) 100% to "
            f"'{_HAIR_FALLBACK_BONE}' -- {narrowly_matched} of those already carried "
            f"explicit Hair_bNN weight, {len(hair_vertex_indices) - narrowly_matched} "
            "did not (these are the ones the narrower v1 check would have missed and "
            "left exporting with zero weight); removed "
            f"{removed_this_obj} now-empty hair vertex group(s)"
        )

    if total_vertices_touched:
        log.append(
            f"  OPTION 4 rigid-hair-to-head: {total_vertices_touched} vertex/vertices "
            f"total pinned rigidly to '{_HAIR_FALLBACK_BONE}' across all meshes, "
            f"{total_groups_removed} hair vertex group(s) removed. All dynamic hair "
            "motion is gone -- hair now moves only as part of the head. A hair "
            "`.ycl` has nothing left to act on either way. CONFIRMED WORKING "
            "IN-GAME 2026-09-25 (Shota Umino)."
        )
    else:
        log.append("  OPTION 4 rigid-hair-to-head: no hair vertices found on this mesh (nothing to pin)")


# Placeholder bone length (meters) for newly-grafted hair-chain links that
# have no derivable geometry at all -- see _graft_missing_hair_bones. Only
# used as a last-resort fallback now (see _HAIR_GRAFT_LINK_LENGTH_FALLBACK
# below); most grafted bones get a data-driven length instead.
_HAIR_GRAFT_LINK_LENGTH_FALLBACK = 0.04


def _hair_bone_chain_link(name: str):
    """('12', None) for 'Hair_b12', ('12', 3) for 'Hair_b12_03'. Caller must
    have already matched name against _HAIR_BONE_RE."""
    m = _HAIR_BONE_RE.match(name)
    chain, link = m.group(1), m.group(2)
    return chain, (None if link is None else int(link))


def _hair_bone_parent_name(name: str) -> str:
    chain, link = _hair_bone_chain_link(name)
    if link is None:
        return _HAIR_FALLBACK_BONE
    return f"Hair_b{chain}" if link == 0 else f"Hair_b{chain}_{link - 1:02d}"


def _hair_bone_child_name(name: str) -> str:
    chain, link = _hair_bone_chain_link(name)
    return f"Hair_b{chain}_00" if link is None else f"Hair_b{chain}_{link + 1:02d}"


def _compute_hair_bone_head_positions(mathutils, mesh_objs, donor_armature, names, log: list[str]):
    """
    For each name in `names`, computes a rest-pose HEAD position derived from
    the actual mesh: the weight-averaged world-space position of every vertex
    (across mesh_objs) that carries a nonzero weight in a vertex group of
    that same name, converted into the donor armature's local (object)
    space.

    This is plain OBJECT-mode data access (vertex.co, vertex.groups) -- no
    bpy.ops, safe to call before entering EDIT mode on the armature.

    Returns (head_positions, names_without_geometry). names_without_geometry
    covers a vertex group that exists on some mesh but has zero total weight
    everywhere (rare) -- the caller falls back to a parent-relative
    placeholder for those.
    """
    names_set = set(names)
    armature_world_inv = donor_armature.matrix_world.inverted()
    # name -> [weighted_sum: Vector, weight_total: float]
    accum: dict[str, list] = {}

    for obj in mesh_objs:
        if obj.type != "MESH":
            continue
        relevant = {vg.index: vg.name for vg in obj.vertex_groups if vg.name in names_set}
        if not relevant:
            continue
        mw = obj.matrix_world
        for vertex in obj.data.vertices:
            for ge in vertex.groups:
                name = relevant.get(ge.group)
                if name is None or ge.weight <= 0.0:
                    continue
                slot = accum.get(name)
                if slot is None:
                    slot = [mathutils.Vector((0.0, 0.0, 0.0)), 0.0]
                    accum[name] = slot
                slot[0] += (mw @ vertex.co) * ge.weight
                slot[1] += ge.weight

    head_positions: dict[str, "mathutils.Vector"] = {}
    names_without_geometry: list[str] = []
    for name in names:
        slot = accum.get(name)
        if slot is None or slot[1] <= 0.0:
            names_without_geometry.append(name)
            continue
        centroid_world = slot[0] / slot[1]
        centroid_local = armature_world_inv @ centroid_world

        # AXIS-SWAP CORRECTION (added 2026-09-25, v3, empirically derived):
        # v2 fed `centroid_local` straight through and got a bone ~269cm
        # from J_Head in-game-confirmed-broken (detourne's V8 diagnostic log).
        # Comparing the raw numbers against the donor armature's own real
        # bones told a clear story: this armature's "vertical" axis is Y
        # (J_Hips=-108.7, J_Spine1=-115.2, J_Spine2=-128.7, J_Neck=-164.7,
        # J_Head=-177.6 -- monotonically more negative going up the body),
        # with X and Z both small/near-zero for on-the-midline bones. Our
        # UNCORRECTED centroids instead had their large, head-height-shaped
        # values (-170 to -196) sitting in the Z slot, with Y small -- i.e.
        # local space Y and Z are swapped relative to how the armature's own
        # bones express verticality. Re-checked against J_Head with the swap
        # applied: distance drops from 269cm to ~21cm for the chain-00 root
        # (a plausible real distance for a hair root near the head), and
        # ~17.5cm for that chain's outer link -- consistent across multiple
        # points, not a one-off coincidence. No sign flip needed on either
        # swapped component, only X stays as computed.
        #
        # Root cause not fully nailed down (would need to read the addon's
        # own mesh-vs-armature import code to know for certain), but the
        # working theory: the donor armature's bones are expressed directly
        # in the game's native axis convention, while mesh vertex data goes
        # through a different/generic import path that doesn't preserve that
        # same convention -- so `mesh_obj.matrix_world @ vertex.co` and
        # `donor_armature.matrix_world` don't actually agree on which axis
        # is "up" the way a single consistent world space would imply.
        # UNTESTED IN-GAME as of writing -- the plan is to verify this
        # against another log-only diagnostic run (checking dist_from_J_Head
        # drops as predicted) before spending another in-game test cycle.
        centroid_local = mathutils.Vector((centroid_local.x, centroid_local.z, centroid_local.y))

        head_positions[name] = centroid_local

    if names_without_geometry:
        shown = ", ".join(names_without_geometry[:8])
        more = "..." if len(names_without_geometry) > 8 else ""
        log.append(
            f"  OPTION 3 hair-bone graft: {len(names_without_geometry)} vertex "
            f"group(s) had no nonzero-weight vertices to derive a position from "
            f"({shown}{more}) -- these fall back to a parent-relative placeholder"
        )

    return head_positions, names_without_geometry


def _graft_missing_hair_bones(bpy, mesh_objs, donor_armature, log: list[str]) -> None:
    """
    OPTION 3 fix (added 2026-09-25, at detourne's request, after confirming
    (Shota Umino's source .ycl, via string extraction) that at least some
    characters' dynamic hair is a BONE-CHAIN physics system -- per-bone
    .ycl entries literally named Hair_b00, Hair_b00_00, Hair_b00_01, ...,
    the exact same names as the "orphaned hair bone" vertex groups this
    project has been working around since Option 1/2 -- rather than the
    large per-vertex cloth system confirmed working for Yuki Arai via
    YclEditor.exe (see project doc). For a bone-chain rig, neither Option 1
    (leave the vertices rigid) nor Option 2 (collapse their weights onto a
    single fallback bone) gives a correctly-converted .ycl's per-bone
    physics data any real bone to act on -- there's nothing there to move.

    This function creates the MISSING Hair_bNN / Hair_bNN_MM bones directly
    in the donor armature so that:
      (a) the addon's own exporter -- which (per _remap_hair_bone_weights's
          docstring, read from Blender/blender_utils.py) only writes a
          vertex's weight slot if its vertex group's name matches a REAL
          bone in the target armature at export time -- finds them and
          writes real weight data instead of silently dropping it, and
      (b) a correctly re-targeted .ycl (YclEditor.exe's "Export As WWE
          2K25", confirmed working for the cloth-sim case) has actual
          bones by these exact names to reference for its own per-bone
          physics.

    REVISION (2026-09-25, v2): the first version placed every new bone as a
    short straight segment extending in -Z from its parent's tail, anchored
    near J_Head -- a fixed, mesh-independent placeholder. That version was
    tested in-game on Shota Umino and produced catastrophic hair distortion
    (a stretched, erratic vertical pillar); detourne's own diagnostic (removing
    the .ycl entirely and still seeing the same stretching) proved the
    defect was in the grafted bones' bind-pose geometry, not the .ycl.

    This version instead derives each bone's HEAD position from the actual
    mesh: the weight-averaged position of the vertices already assigned to
    that bone's vertex group (see _compute_hair_bone_head_positions), so the
    bind pose roughly matches where the hair geometry actually sits instead
    of an arbitrary stub near the head. Each bone's TAIL is pointed at its
    chain child's derived head position when known (so a multi-link chain
    follows the mesh's own shape instead of a rigid straight rod); a chain's
    last link, or any bone with no derivable position, falls back to
    continuing the parent->head direction (or a short -Z segment if no
    direction is available at all).

    REMAINING CAVEAT, stated plainly for the log and for detourne: these are
    still not reconstructed real rig bones -- no .mcd examined anywhere in
    this project (Shota's real WWE2K26 BaseModel, or his real WWE2K25
    donor's BaseModel) contains any Hair_bNN bone at all, so real rest-pose
    transforms for these bones may not exist anywhere accessible to this
    tool; the game may generate their actual runtime position/motion
    procedurally from the .ycl's own chain parameters (working theory, not
    confirmed). What changed is only that the placeholder is now anchored to
    the real mesh instead of floating near J_Head. UNTESTED IN-GAME as of
    writing this revision.
    """
    import mathutils

    hair_names: set[str] = set()
    for obj in mesh_objs:
        if obj.type != "MESH":
            continue
        for vg in obj.vertex_groups:
            if _HAIR_BONE_RE.match(vg.name):
                hair_names.add(vg.name)

    existing = {b.name for b in donor_armature.data.bones}
    missing_names = [n for n in hair_names if n not in existing]
    if not missing_names:
        log.append("  OPTION 3 hair-bone graft: no missing hair bones found on this mesh (nothing to graft)")
        return

    def _sort_key(name: str):
        chain, link = _hair_bone_chain_link(name)
        return (int(chain), -1 if link is None else link)

    missing_names.sort(key=_sort_key)

    if _HAIR_FALLBACK_BONE not in existing:
        log.append(
            f"  WARNING: OPTION 3 hair-bone graft: universal anchor bone "
            f"'{_HAIR_FALLBACK_BONE}' not found in donor armature -- new "
            "chain-root bones will be created with no parent (armature root) instead"
        )

    head_positions, names_without_geometry = _compute_hair_bone_head_positions(
        mathutils, mesh_objs, donor_armature, missing_names, log
    )
    derived_count = len(head_positions)

    # DIAGNOSTIC reference points, captured now (Object-mode `data.bones` is
    # valid at any time, unlike `data.edit_bones` which only exists while
    # actually in Edit Mode -- grabbing these AFTER the edit-mode block below
    # would silently return nothing). `head_local` is a bone's rest HEAD
    # position in the armature's own local/object space -- the same space
    # head_positions above is already expressed in -- so these are directly
    # comparable to the grafted bones' coordinates for a scale/sanity check.
    _diag_ref_names = [_HAIR_FALLBACK_BONE, "J_Hips", "J_Spine1", "J_Spine2", "J_Neck"]
    diag_reference_bones: dict[str, "mathutils.Vector"] = {}
    for _rn in _diag_ref_names:
        _rb = donor_armature.data.bones.get(_rn)
        if _rb is not None:
            diag_reference_bones[_rn] = _rb.head_local.copy()

    prev_active = bpy.context.view_layer.objects.active
    prev_mode = prev_active.mode if prev_active is not None else "OBJECT"
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.objects.active = donor_armature
    bpy.ops.object.mode_set(mode="EDIT")

    edit_bones = donor_armature.data.edit_bones
    created = 0
    fallback_used = 0
    placed: dict[str, tuple] = {}  # name -> (head Vector, tail Vector), for the diagnostic dump below
    try:
        for name in missing_names:
            if edit_bones.get(name) is not None:
                continue  # already created earlier in this same pass

            parent_name = _hair_bone_parent_name(name)
            parent_eb = edit_bones.get(parent_name)

            head = head_positions.get(name)
            if head is None:
                fallback_used += 1
                if parent_eb is not None:
                    head = parent_eb.tail.copy()
                else:
                    # No usable parent found (anchor bone missing, or --
                    # shouldn't happen given the sort order -- this chain's
                    # own root/prior link wasn't created first). Don't
                    # crash; anchor at the real J_Head position captured
                    # above (diag_reference_bones), which is a far better
                    # fallback than a guessed constant now that we know it.
                    # The hardcoded Vector is only a last-resort safety net
                    # for the near-impossible case that J_Head itself isn't
                    # in the donor armature -- (0, -175, 0) matches this
                    # armature's real observed convention (Y vertical, more
                    # negative = up; see the axis-swap correction above),
                    # not the old, wrong (0, 0, 1.6) Z-up assumption.
                    head = diag_reference_bones.get(
                        _HAIR_FALLBACK_BONE, mathutils.Vector((0.0, -175.0, 0.0))
                    ).copy()
            else:
                head = head.copy()

            child_name = _hair_bone_child_name(name)
            child_head = head_positions.get(child_name)
            if child_head is not None:
                tail = child_head.copy()
            elif parent_eb is not None and (parent_eb.tail - parent_eb.head).length > 1e-6:
                # No known child position (this is the last link we can
                # place, or the child itself has no geometry) -- continue
                # the direction the chain was already heading in from its
                # parent, rather than always dropping straight down.
                #
                # BUGFIX (2026-09-25, v4): this used to scale by the fixed
                # _HAIR_GRAFT_LINK_LENGTH_FALLBACK constant (0.04) -- a
                # leftover from v1, when every bone was that same tiny
                # placeholder length. Now that real chains are mesh-derived
                # (median link length ~2.57 per the V9 diagnostic), a fixed
                # 0.04 tail made every chain's LAST link (its visible tip --
                # the leaf, hit by this branch on essentially every one of
                # the 30 chains) a near-zero-length bone roughly 60x shorter
                # than its neighbors. detourne's V9 in-game test (2026-09-25)
                # showed exactly this signature: most of the hair now reads
                # as real hair, but a specific, localized chunk still goes
                # rigid/blocky -- consistent with a degenerate terminal bone
                # at each strand's tip rather than a global positioning bug.
                # Fix: continue at the SAME length as the previous link,
                # not an arbitrary constant, so the tip stays data-driven
                # and in scale with the rest of its own chain.
                prev_vec = parent_eb.tail - parent_eb.head
                prev_length = prev_vec.length
                direction = prev_vec.normalized()
                tail = head + direction * prev_length
            else:
                # Default "continue roughly downward" direction. This
                # armature's real vertical axis is Y, with more-NEGATIVE Y
                # meaning further UP (see the axis-swap correction above) --
                # so "down" here is +Y, not -Z like the old Z-up assumption.
                tail = head + mathutils.Vector((0.0, _HAIR_GRAFT_LINK_LENGTH_FALLBACK, 0.0))

            eb = edit_bones.new(name)
            eb.head = head
            eb.tail = tail
            if parent_eb is not None:
                eb.parent = parent_eb
                # Not using use_connect: a data-driven head won't generally
                # equal the parent's tail exactly (it usually will be close,
                # since the parent's own tail was pointed at this bone's
                # head above, but we don't want Blender silently overriding
                # our explicit head with the parent's tail in edge cases --
                # e.g. a fallback-positioned parent with a derived child).
                eb.use_connect = False

            # ROLL FIX (2026-09-25, v5): every prior version left roll at
            # Blender's default for a freshly created bone (effectively
            # arbitrary, not derived from anything). Position/length are now
            # confirmed good (V10/V11 diagnostics: no per-chain outliers,
            # J_Head distances all sane) -- yet detourne's V11 in-game test
            # still showed stretching, now specifically on the TOP, CROWN,
            # and BACK of the head, while the FRONT (bangs hanging mostly
            # straight down under gravity) looks correct. A strand that
            # mostly just hangs is much less sensitive to its bone's local
            # twist than one that has to curve around the head's own
            # curvature -- exactly the pattern we're seeing. That points at
            # roll, not position, for this remaining defect.
            #
            # Fix: align each bone's roll so its local Z axis points
            # radially OUTWARD from J_Head through the bone's own head
            # position -- i.e. away from the skull, like a surface normal.
            # This is a principled default for a hair-root bone (hair grows
            # outward from the scalp) even though it's still synthesized,
            # not real rig data -- UNTESTED IN-GAME as of writing.
            anchor_pos = diag_reference_bones.get(_HAIR_FALLBACK_BONE)
            if anchor_pos is not None:
                outward = head - anchor_pos
                if outward.length > 1e-6:
                    eb.align_roll(outward.normalized())

            created += 1
            placed[name] = (head.copy(), tail.copy())
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
        if prev_active is not None:
            bpy.context.view_layer.objects.active = prev_active
            try:
                bpy.ops.object.mode_set(mode=prev_mode)
            except RuntimeError:
                pass

    log.append(
        f"  OPTION 3 hair-bone graft: created {created} bone(s) "
        f"({len(missing_names)} vertex-group name(s) were missing from the "
        f"donor armature) -- {derived_count} placed from real weighted-mesh "
        f"centroids (Y/Z axis-swap correction, v3), {fallback_used} used a "
        f"parent-relative placeholder (no weighted geometry found for that "
        "name). These are now real bones the exporter will write valid "
        "vertex weights for instead of dropping them. PARTIALLY CONFIRMED "
        "IN-GAME (v3: axis-corrected positions; v4: chain-tip length fix, "
        "confirmed working -- min link length now sane, no more degenerate "
        "tips; still-remaining defect (V11 test) is stretching specific to "
        "top/crown/back, not front, with no per-chain position outlier -- "
        "v5 adds an outward-facing roll for every grafted bone, since a "
        "front-only-fine / curved-area-broken pattern points at bone "
        "orientation rather than position). UNTESTED IN-GAME (v5, roll)."
    )

    # DIAGNOSTIC (added 2026-09-25, after v2's mesh-derived positions still
    # produced the same stretched/erratic in-game result as v1's fixed
    # placeholder -- that result rules out "the bones are in the wrong
    # neighborhood" as the sole explanation, since v2's positions are real
    # mesh centroids, not guesses. Dumping the actual placed geometry here
    # so the next data point doesn't require another in-game round trip: if
    # link lengths/spans below are wildly inconsistent (near-zero, huge, or
    # non-monotonic within a chain), that points at degenerate/overlapping
    # vertex-group weight painting (broad soft weight falloff across many
    # hair bones pulling every centroid toward a similar average point)
    # rather than a coordinate-space bug; if they look geometrically sane,
    # the defect is more likely downstream (e.g. how the exporter's own
    # bind-matrix / .ycl runtime interpretation actually uses these bones).
    if diag_reference_bones:
        log.append(
            "  OPTION 3 diagnostic: real donor-skeleton reference bone positions "
            "(same armature-local space as the grafted-bone coordinates below -- "
            "use these to sanity-check scale/sign, e.g. is the hair chain's "
            "coordinate range even remotely close to J_Head's?):"
        )
        for _rn, _rv in diag_reference_bones.items():
            log.append(f"    '{_rn}': head=({_rv.x:.4f}, {_rv.y:.4f}, {_rv.z:.4f})")

    if placed:
        import statistics

        lengths = [(t - h).length for h, t in placed.values()]
        log.append(
            f"  OPTION 3 diagnostic: grafted bone link lengths (head-to-tail, "
            f"armature-local units) across all {len(lengths)} placed bones -- "
            f"min={min(lengths):.4f}, max={max(lengths):.4f}, "
            f"mean={statistics.fmean(lengths):.4f}, "
            f"median={statistics.median(lengths):.4f}"
        )

        all_heads = [h for h, _t in placed.values()]
        _xs, _ys, _zs = [v.x for v in all_heads], [v.y for v in all_heads], [v.z for v in all_heads]
        log.append(
            "  OPTION 3 diagnostic: bounding box of ALL grafted bone head "
            f"positions -- X=[{min(_xs):.4f}, {max(_xs):.4f}], "
            f"Y=[{min(_ys):.4f}, {max(_ys):.4f}], Z=[{min(_zs):.4f}, {max(_zs):.4f}]"
        )

        by_chain: dict[str, list[str]] = {}
        for name in placed:
            chain, _link = _hair_bone_chain_link(name)
            by_chain.setdefault(chain, []).append(name)

        anchor_head = diag_reference_bones.get(_HAIR_FALLBACK_BONE)

        # DIAGNOSTIC (added 2026-09-25, v4b, after detourne's V10 in-game test:
        # the chain-tip length fix (v4) confirmed via this same log's own
        # min-length stat rising from the old degenerate 0.0400 to a normal
        # 1.3953 -- so that specific bug is fixed -- but he still reports
        # "the same section of hair is weighted improperly." That means the
        # remaining defect is CHAIN-SPECIFIC (only part of the hair), not a
        # global rule everything is still hitting. Previously we only ever
        # dumped chain 00 as a worked example -- not enough to spot an
        # outlier elsewhere among the ~30 chains. This logs EVERY chain's
        # summary stats (root distance from J_Head, total unrolled strand
        # length, root-to-tip straight-line reach) so an anomalous chain
        # (e.g. one wildly longer/shorter or farther from J_Head than the
        # rest) is visible directly in the log, without another in-game
        # round trip just to narrow down WHICH chain is still broken.
        chain_summaries = []
        for chain_id, names_in_chain in by_chain.items():
            names_in_chain = sorted(names_in_chain, key=lambda n: _hair_bone_chain_link(n)[1] or -1)
            root_head = placed[names_in_chain[0]][0]
            total_span = sum((placed[n][1] - placed[n][0]).length for n in names_in_chain)
            tip_tail = placed[names_in_chain[-1]][1]
            reach = (tip_tail - root_head).length
            root_dist = (root_head - anchor_head).length if anchor_head is not None else None
            chain_summaries.append((chain_id, len(names_in_chain), root_dist, total_span, reach))

        chain_summaries.sort(key=lambda row: int(row[0]))
        spans = [row[3] for row in chain_summaries]
        reaches = [row[4] for row in chain_summaries]
        if spans:
            import statistics as _stats

            log.append(
                f"  OPTION 3 diagnostic: per-chain summary, all {len(chain_summaries)} "
                f"chain(s) -- total unrolled strand length: min={min(spans):.2f}, "
                f"max={max(spans):.2f}, median={_stats.median(spans):.2f}; "
                f"root-to-tip straight-line reach: min={min(reaches):.2f}, "
                f"max={max(reaches):.2f}, median={_stats.median(reaches):.2f}"
            )
            span_median = _stats.median(spans)
            for chain_id, n_links, root_dist, total_span, reach in chain_summaries:
                flag = ""
                if span_median > 0 and (total_span > span_median * 1.8 or total_span < span_median * 0.4):
                    flag = "  <-- OUTLIER (span far from median)"
                root_dist_str = f"{root_dist:.2f}" if root_dist is not None else "n/a"
                log.append(
                    f"    chain {chain_id}: {n_links} link(s), root dist_from_"
                    f"{_HAIR_FALLBACK_BONE}={root_dist_str}, total_span={total_span:.2f}, "
                    f"reach={reach:.2f}{flag}"
                )

        # Worked example: the first chain (lowest chain number) we actually
        # placed, showing each link's head position and the spacing between
        # consecutive links -- and its span back to the anchor bone.
        first_chain = chain_summaries[0][0] if chain_summaries else sorted(by_chain, key=lambda c: int(c))[0]
        chain_names = by_chain[first_chain]
        chain_names.sort(key=lambda n: _hair_bone_chain_link(n)[1] or -1)

        log.append(f"  OPTION 3 diagnostic: worked example, chain {first_chain} ({len(chain_names)} link(s)):")
        prev_head = None
        for name in chain_names:
            h, t = placed[name]
            dist_from_prev = (h - prev_head).length if prev_head is not None else None
            dist_from_anchor = (h - anchor_head).length if anchor_head is not None else None
            parts = [f"head=({h.x:.4f}, {h.y:.4f}, {h.z:.4f})"]
            if dist_from_prev is not None:
                parts.append(f"dist_from_prev_link={dist_from_prev:.4f}")
            if dist_from_anchor is not None:
                parts.append(f"dist_from_{_HAIR_FALLBACK_BONE}={dist_from_anchor:.4f}")
            log.append(f"    '{name}': " + ", ".join(parts))
            prev_head = h


def _verify_rigid_hair_export(bpy, vc_interface, out_mcd: str, import_args: dict, log: list[str]) -> None:
    """
    DIAGNOSTIC (added 2026-09-25, Option 4 round-trip verification).

    Context: a real in-game test on Shota Umino (2026-09-25) -- WITH the
    source .ycl removed entirely, ruling that out as a factor -- still
    showed the exact same catastrophic stretched-hair artifact as every
    earlier failed attempt, even though the pre-export log for that same
    run confirmed 8194 hair vertices were pinned 100% to J_Head in
    Blender's own mesh data before saveModelFile() ran. Hand-tracing the
    addon's export code (Blender/blender_utils.py's
    getVertexWeights/getBoneIndex) suggested a single-bone, weight-1.0
    vertex should export cleanly -- but that was never actually confirmed
    against real output bytes. This function closes that gap: it
    re-imports the file *we just wrote* into a scratch scene and reports
    what weight data is REALLY in it, rather than trusting what we set in
    Blender right before export.

    Specifically flags any vertex with literally ZERO total weight in the
    reimported file -- in most game engines (untested here, but a common
    convention worth ruling in or out) a vertex with no bone influence at
    all binds to a default/identity transform, which commonly renders as
    that vertex snapping toward the world origin -- exactly the kind of
    "thin stretched pillar reaching off to one side" look reported.

    Every object this function imports is removed again immediately after
    inspection -- this must NEVER leave scene state behind for a later
    _convert_one() call in the same headless Blender session to
    accidentally pick up (e.g. get selected and exported by mistake).
    """
    log.append(f"  DIAGNOSTIC: re-importing just-exported file to verify weight data actually survived export: {out_mcd}")
    try:
        reimported = _import_tracked(bpy, vc_interface, out_mcd, import_args, log)
    except Exception as e:
        log.append(f"  DIAGNOSTIC: re-import FAILED ({type(e).__name__}: {e}) -- can't verify the round-trip this way")
        return

    try:
        for obj in reimported:
            if obj.type != "MESH":
                continue
            group_names = [vg.name for vg in obj.vertex_groups]
            hair_groups_present = [n for n in group_names if _HAIR_BONE_RE.match(n)]
            group_index_to_name = {vg.index: vg.name for vg in obj.vertex_groups}

            total_verts = len(obj.data.vertices)
            unweighted = 0
            hair_weighted = 0
            head_only = 0
            other_weighted = 0

            for v in obj.data.vertices:
                positive = [ge for ge in v.groups if ge.weight > 0.0]
                if not positive:
                    unweighted += 1
                    continue
                names_here = {group_index_to_name.get(ge.group) for ge in positive}
                if any(n and _HAIR_BONE_RE.match(n) for n in names_here):
                    hair_weighted += 1
                elif names_here == {_HAIR_FALLBACK_BONE}:
                    head_only += 1
                else:
                    other_weighted += 1

            log.append(
                f"    RE-IMPORTED '{obj.name}': {total_verts} vertex/vertices -- "
                f"{unweighted} with ZERO total weight (a common cause of a vertex "
                f"snapping/stretching toward the world origin in-game), "
                f"{hair_weighted} STILL weighted to a Hair_bNN group "
                f"({hair_groups_present if hair_groups_present else 'none present in file'}), "
                f"{head_only} weighted ONLY to '{_HAIR_FALLBACK_BONE}' (expected/correct "
                f"for a pinned vertex), {other_weighted} weighted to something else "
                f"entirely. Vertex groups present after round-trip "
                f"({len(group_names)} total): {group_names[:20]}"
                + (" ...(truncated)" if len(group_names) > 20 else "")
            )
    finally:
        for obj in reimported:
            bpy.data.objects.remove(obj, do_unlink=True)


def _convert_one(
    bpy, vc_interface, vc_interface_save,
    source_mcd: str, out_mcd: str,
    import_args: dict, export_args: dict, rename_map: dict,
    label: str, log: list[str],
    donor_armature=None, fix_hair_bone_weights: bool = False,  # "Option 2" -- confirmed broken, stripped from UI 2026-10-02
    graft_hair_bones: bool = False,  # "Option 3" -- superseded by rigid_hair_to_head, stripped from UI 2026-10-02
    rigid_hair_to_head: bool = False,
    donor_hair_source_objs: list | None = None,
    graft_real_hair_bones: bool = False,
) -> None:
    """
    graft_real_hair_bones (added 2026-10-15, mode "5. Hair Graft Only"):
    isolates just the hair half of Heat Seek -- grafting the real J_Hair
    bone-hierarchy subtree from this part's OWN embedded armature into
    `donor_armature` via _graft_hair_only_subtree (CONFIRMED WORKING
    in-game, 2026-10-15, Yota Tsuji: "There was a success with the hair...
    it animated properly") -- WITHOUT any of Heat Seek's other, still-
    unconfirmed behavior (the DATA_TRANSFER weight recompute for body/
    clothing/head, which broke head/eye/shoulder weighting on that same
    test; see _convert_one_heat_seek). Everything else in this function
    runs exactly as it does for mode "1. Straight conversion" -- the
    mesh's existing vertex groups are left alone (not cleared, not
    recomputed), and the addon's own import-time auto-redirect is what
    already parented it onto `donor_armature`; grafting the missing hair
    bones is the ONLY change needed for its existing Hair_bNN-style
    vertex groups to actually resolve against something real at export
    time, instead of silently dropping that weight the way every
    hair-less donor skeleton has always forced until now. Requested by
    detourne specifically to let the hair fix be tested/trusted across more
    characters while the Heat Seek body/head weighting issue is still
    being worked out, without bundling the two together.
    """
    log.append(f"--- {label} ---")
    new_objs = _import_tracked(bpy, vc_interface, source_mcd, import_args, log)
    mesh_objs = [o for o in new_objs if o.type == "MESH"]
    if not mesh_objs:
        raise RuntimeError(f"{label}: import produced no MESH objects: {source_mcd}")
    source_armature = next((o for o in new_objs if o.type == "ARMATURE"), None) if graft_real_hair_bones else None

    # DIAGNOSTIC (added 2026-09-25): log exactly which mesh objects made it
    # into the Blender scene and are about to be selected for export. If a
    # mesh present in the source .mcd is missing from the final exported
    # .mcd, this line tells us whether it was already missing here (import
    # or vertex-group/rename step dropped it -- visible to us, fixable in
    # Python) or only vanished during saveModelFile() itself (the addon's
    # compiled C library -- invisible to us here, would need Blender's own
    # console/DLL-side diagnostics instead). See README "Known limitations"
    # -- BaseModel mesh-object count mismatch (Shota Umino, 2026-09-25).
    log.append(f"  mesh objects about to be exported ({len(mesh_objs)}): {[o.name for o in mesh_objs]}")

    # DIAGNOSTIC (added 2026-09-25, for the .ycl hair-conversion investigation):
    # log each mesh object's vertex count, plus a hypothetical cumulative
    # vertex-index offset (running total across mesh_objs, in this export
    # order) -- IF the exported .mcd's vertex buffer is laid out as one
    # mesh's vertices after another in this same order (unconfirmed; a
    # reasonable guess for this kind of format, not verified against the
    # addon's export code), this is what a global vertex index N would need
    # to fall into to land in a given mesh object. A dynamic-hair .ycl's
    # vertex references are tied to some such global numbering from the
    # ORIGINAL 2K26 source export -- if this project's own Blender pass
    # changes any mesh's vertex COUNT (not just order) relative to the
    # source, every offset after that mesh shifts, invalidating any hair
    # .ycl's vertex indices for meshes that come after it in the list,
    # regardless of whether the .ycl's own byte-level format conversion
    # (see YclEditor.exe findings) is otherwise correct. This does not by
    # itself prove or disprove that -- it just gives real numbers to compare
    # against next time a source's own vertex counts (e.g. from a hex/format
    # inspection of the source .mcd) are available.
    running_offset = 0
    for o in mesh_objs:
        vcount = len(o.data.vertices)
        log.append(f"    '{o.name}': {vcount} vertex/vertices (hypothetical global index range {running_offset}..{running_offset + vcount - 1})")
        running_offset += vcount

    _apply_rename_map(mesh_objs, rename_map, log)

    if graft_real_hair_bones:
        hair_objs_here = _find_hair_mesh_objs(mesh_objs)
        if not hair_objs_here:
            log.append("  GRAFT REAL HAIR BONES: no hair mesh found on this part (nothing to graft)")
        elif donor_armature is None:
            log.append("  WARNING: GRAFT REAL HAIR BONES: no donor armature available -- skipping.")
        elif source_armature is None:
            log.append(
                "  WARNING: GRAFT REAL HAIR BONES: this part has no embedded "
                "ARMATURE object of its own to graft real hair bone data from "
                "-- hair mesh's Hair_bNN-style vertex groups will still export "
                "with dropped weight, same as mode 1 without this option."
            )
        else:
            _graft_hair_only_subtree(bpy, donor_armature, source_armature, log)
            log.append(
                f"  GRAFT REAL HAIR BONES: grafted the real 'J_Hair' subtree from "
                f"'{source_armature.name}' into the donor armature -- hair mesh "
                f"{[o.name for o in hair_objs_here]}'s EXISTING vertex groups "
                "(left untouched, not recomputed) should now resolve against "
                "real bones instead of silently dropping that weight."
            )

    if donor_hair_source_objs and donor_armature is not None:
        # Runs BEFORE Options 2/3/4 deliberately -- see
        # _transplant_donor_hair_mesh's docstring for why the ordering
        # matters (those options then naturally leave the transplanted
        # donor hair alone, since it doesn't match Hair_bNN naming).
        _transplant_donor_hair_mesh(bpy, mesh_objs, donor_hair_source_objs, donor_armature, log)

    if rigid_hair_to_head and donor_armature is not None:
        # OPTION 4 takes precedence over Options 2/3 -- it unconditionally
        # strips ALL hair weight and pins it to J_Head, so grafting bones
        # (3) or collapsing missing ones onto a per-chain fallback (2)
        # first would just be wasted work this immediately undoes. Warn
        # rather than silently ignoring the other checkboxes, since leaving
        # them checked alongside this one is probably not what was meant.
        if graft_hair_bones or fix_hair_bone_weights:
            log.append(
                "  NOTE: OPTION 4 (rigid hair to head) is on, which takes "
                "precedence -- skipping Option 3 (graft) and/or Option 2 "
                "(per-chain fallback) this run, since Option 4 immediately "
                "overwrites all hair weight anyway."
            )
        target_bone_names = {b.name for b in donor_armature.data.bones}
        _pin_hair_to_head(bpy, mesh_objs, target_bone_names, log)
    else:
        if graft_hair_bones and donor_armature is not None:
            _graft_missing_hair_bones(bpy, mesh_objs, donor_armature, log)

        if fix_hair_bone_weights and donor_armature is not None:
            # Runs AFTER the graft above -- target_bone_names is recomputed
            # here, so any hair bone the graft just created is no longer
            # "missing" and Option 2 will correctly leave it alone (only
            # bones the graft didn't create, e.g. because graft_hair_bones
            # was off, still get collapsed).
            target_bone_names = {b.name for b in donor_armature.data.bones}
            _remap_hair_bone_weights(bpy, mesh_objs, target_bone_names, log)

    _report_unresolved_vertex_groups(mesh_objs, donor_armature, log)

    _export_mesh_objs(bpy, vc_interface_save, mesh_objs, out_mcd, export_args, label, log, donor_armature=donor_armature)

    if rigid_hair_to_head:
        _verify_rigid_hair_export(bpy, vc_interface, out_mcd, import_args, log)


def _ensure_attach_points_generated(bpy, log: list[str], donor_armature=None) -> None:
    """
    Make sure the armature this export will actually end up bound to has a
    "Model APs" attach-point proxy rig, built from the addon's own bundled
    default_char_APs.json template (191 points; the addon's own code comment
    confirms this is universal -- byte-identical between vanilla 2K25 and
    2K26, not per-character data).

    WHY THIS EXISTS (found 2026-10-22, see project doc
    2026-10-22-atpt-investigation.md): without it, every export this script
    produces ships with ZERO attach points, full stop. export_atpts=True
    only ever makes Link/interface_save.py call the addon's
    getSceneAttachPoints(), which reads back a "Model APs" scene collection
    -- and this script never creates one (every import here runs with
    load_attachment_points=False, and nothing else populates it). With no
    points, Serialize/model_serialize.py's set_attach_points() is a silent
    no-op, and there is no raw-passthrough fallback the way GtPt has
    (transferGotoPoints) -- confirmed by grepping the whole addon for any
    transferAttachPoints-style DLL call; none exists. This is the confirmed
    root cause of "spinning" championship belts (and anything else
    AtPt-anchored): with no attach point at all, nothing pins the object to
    a bone.

    SECOND BUG, found 2026-10-22 after a real in-game retest (Saya Kamitani,
    _convert_one / "Hair Graft Only" mode, no Heat Seek, no donor-attire-as-
    base): the first fix above shipped correctly (log confirmed 191/191
    points generated on both the BaseModel and Attire armatures) but the
    belt STILL spun wildly in-game. Root cause: for _convert_one's plain
    path, the mesh objects are never explicitly reparented onto
    `donor_armature` in this script at all -- this file's own header comment
    says the addon's own cross-version auto-redirect handles that
    automatically, INSIDE saveModelFile, by detecting that the freshly-
    imported source armature (e.g. 'Skeleton.001', a 2K26-origin skeleton)
    doesn't match the 2K25 export target and rebinding the mesh onto
    whatever real 2K25 armature is already in the scene (confirmed by the
    project doc's 2026-09-19 entry: the addon logs "...were bound to
    'Skeleton.001' (2K26) but you are exporting as 2K25. Rebound to
    'Skeleton' (2K25)..." when this fires). That rebind happens LATER than
    this function's call site (which runs in Python, before saveModelFile's
    own internal/compiled save path) -- so building the "Model APs" rig
    against whatever armature the mesh's `obj.parent` points at RIGHT NOW
    (via model_points_gen.get_scene_armature_object(), which is exactly what
    the previous version of this function did) targets the STALE, about-to-
    be-abandoned source armature, not the one the mesh will actually be
    bound to by the time the addon reads attach points back out for
    serialization. Either the "Model APs" collection ends up orphaned (if
    the redirect also moves obj.parent, our collection is a child of the
    OLD armature's now-irrelevant top-level collection and is never found
    again -- net result: zero points again, the original bug) or the points
    resolve bone names/indices against the wrong armature's bone list
    entirely (if the redirect only retargets the skin modifier) -- either
    failure mode is consistent with "generated fine in the log, still wrong
    in-game," and the second explains "spins wildly" specifically (attached
    to a real but WRONG bone on a differently-ordered skeleton) better than
    "floats freely" would.

    _convert_one_heat_seek and _convert_attire_donor_base don't have this
    problem -- both explicitly set `.parent` AND the armature modifier's
    `.object` to their own local donor armature themselves, before export,
    rather than relying on the addon's internal redirect -- so `obj.parent`
    at call time already IS the real final armature for those two paths.
    Only _convert_one's plain path depends on the addon's later, invisible-
    to-this-script rebind.

    Fix: when the caller already knows the real target armature (every
    _convert_one/_convert_one_heat_seek/_convert_attire_donor_base call site
    has one -- it's the same `donor_armature` imported once in _run_job and
    threaded through this whole file), use it directly instead of
    rediscovering "the armature" from the current mesh selection. This also
    now matches what this function's own idempotency check always assumed:
    "a run with one BaseModel export + N attire exports all sharing one
    donor armature doesn't regenerate" -- true by construction once every
    call targets the same shared Object, rather than accidentally hitting
    whichever fresh, not-yet-redirected armature each separate import
    happened to create (which is why the real run's own log showed AP
    generation firing TWICE, once each on 'Skeleton.001' and 'Skeleton.002'
    -- two different, both-wrong armatures -- instead of once on the shared
    donor armature as intended).

    Falls back to the old scene-selection-based discovery
    (model_points_gen.get_scene_armature_object()) only when no
    `donor_armature` is supplied at all -- kept as a safety net, not because
    any current call site is expected to hit it.

    export_args["export_atpts"] still gates whether this runs at all (see
    call site in _export_mesh_objs) -- if a future job ever turns that off
    on purpose, this stays a no-op, matching the addon's own flag semantics.
    """
    try:
        points_gen = _import(f"{_ADDON_PACKAGE_NAME}.Blender.Extras.model_points_gen")
        blender_skeleton = _import(f"{_ADDON_PACKAGE_NAME}.Blender.blender_skeleton")
        blender_scene = _import(f"{_ADDON_PACKAGE_NAME}.Blender.blender_scene")
    except ImportError as e:
        log.append(
            f"  [AP Gen] WARNING: could not import addon AP modules ({e}) -- "
            f"skipping attach-point generation. This export will ship with 0 "
            f"attach points, same as before this fix."
        )
        return

    if donor_armature is not None:
        # Known-correct: the same real donor armature this whole conversion
        # is targeting, not whatever the currently-selected mesh's `.parent`
        # happens to be at this point in time (see the SECOND BUG note
        # above -- that can still be a stale, about-to-be-abandoned import-
        # time armature for _convert_one's plain path).
        armature_obj = donor_armature
    else:
        # NOTE (fixed 2026-10-22, first real-run crash -- Athena):
        # blender_save.getSceneArmature() is the WRONG helper here -- it
        # returns a lightweight vCSkeleton proxy (built for the DLL writer's
        # set_skeleton() call: id/bone-name/matrix data only) that has no
        # .users_collection, no .name, no real identity as a scene Object at
        # all. The addon's own "Generate Character APs" button uses a
        # DIFFERENT function for exactly this purpose -- model_points_gen.py's
        # own get_scene_armature_object(), which returns the actual Blender
        # Object (via obj.parent on a selected mesh with an armature
        # modifier). That's the one to use here when no explicit
        # donor_armature is available at all.
        armature_obj = points_gen.get_scene_armature_object()
    if armature_obj is None:
        log.append(
            "  [AP Gen] WARNING: no armature found on the current export "
            "selection -- skipping attach-point generation (export will "
            "have 0 attach points)."
        )
        return

    if not armature_obj.users_collection:
        log.append(
            f"  [AP Gen] WARNING: armature '{armature_obj.name}' has no "
            f"top-level collection -- skipping attach-point generation."
        )
        return

    top_collection = armature_obj.users_collection[0]
    if any(child.name.startswith("Model APs") for child in top_collection.children):
        return  # already generated for this armature instance this run

    default_points = points_gen.get_default_aps()
    if not default_points:
        log.append(
            "  [AP Gen] WARNING: could not load default_char_APs.json -- "
            "skipping attach-point generation."
        )
        return

    ap_col = blender_scene.getNewSceneCollection("Model APs", False)
    top_collection.children.link(ap_col)

    root = blender_skeleton.build_sphere("ap_root", ap_col)
    root.hide_set(True)

    # Drops any point whose named bone (e.g. "J_Hips") doesn't exist on this
    # armature -- robust to the skeleton actually present, unlike a raw
    # numeric joint index would be.
    filtered_points = points_gen.filter_points(armature_obj, default_points)
    blender_skeleton.load_attachment_points(ap_col, root, armature_obj, filtered_points)

    log.append(
        f"  [AP Gen] Generated {len(filtered_points)}/{len(default_points)} "
        f"attach points on '{armature_obj.name}'."
    )


def _export_mesh_objs(bpy, vc_interface_save, mesh_objs: list, out_mcd: str, export_args: dict, label: str, log: list[str], donor_armature=None) -> None:
    """
    Shared tail of a conversion: select exactly `mesh_objs`, export via the
    addon's saveModelFile, and verify a requested generate_mtls companion
    file actually landed.

    Factored out of _convert_one (2026-10-02) so the new donor-attire-as-
    base path (_convert_attire_donor_base, added the same day at detourne's
    request to invert the hair-swap architecture -- donor attire as the
    base scene instead of the source attire) can share this exact export
    bookkeeping instead of duplicating it.

    Also now the single chokepoint (2026-10-22) where attach points get
    generated before every save -- see _ensure_attach_points_generated.

    donor_armature (2026-10-22, second AtPt fix): the caller's own real
    target armature, passed straight through to _ensure_attach_points_
    generated so it can build the attach-point rig against the armature this
    export will ACTUALLY end up bound to, instead of rediscovering "the
    armature" from the current mesh selection (which can be stale for
    _convert_one's plain path -- see that function's docstring).
    """
    bpy.ops.object.select_all(action="DESELECT")
    for o in mesh_objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = mesh_objs[0]

    if export_args.get("export_atpts", False):
        _ensure_attach_points_generated(bpy, log, donor_armature=donor_armature)

    Path(out_mcd).parent.mkdir(parents=True, exist_ok=True)
    ok = vc_interface_save.saveModelFile(out_mcd, dict(export_args), operator=None)
    if ok is False:
        raise RuntimeError(f"{label}: saveModelFile reported failure writing: {out_mcd}")
    log.append(f"  exported -> {out_mcd}")

    if export_args.get("generate_mtls"):
        # For a 2K25-target export the addon's legacy DLL material writer
        # follows the same convention as a manual "Generate MTLs" export:
        # a companion file next to the .mcd, same stem, ".mtls" extension.
        # Verify it actually landed there rather than assuming -- this is
        # new/unconfirmed behavior (2026-09-20) for the headless path.
        expected_mtls = Path(out_mcd).with_suffix(".mtls")
        if expected_mtls.is_file():
            log.append(f"  generate_mtls: found generated materials file -> {expected_mtls}")
        else:
            log.append(
                f"  WARNING: generate_mtls was requested but no companion "
                f".mtls file was found at the expected path ({expected_mtls}) "
                "after export -- the DLL's legacy materials writer may use a "
                "different naming convention than assumed, or may have silently "
                "failed. Falling back to the source's own .mtls (which may be "
                "hollow/placeholder -- see converter.py's hollow-.mtls check)."
            )


# Added 2026-10-02 after a real in-game test of _convert_attire_donor_base
# (Athena): the donor attire's "lambert1"-named mesh object turned out to
# actually be part of the donor's hairstyle, not real clothing -- "lambert1"
# (and "blinn1"/"phong1"/"standardSurface1" etc.) is Maya's own default,
# never-renamed material-derived object name, and per detourne's own
# inspection, in this donor it denotes hair-related geometry rather than a
# clothing piece. _find_hair_mesh_objs's vertex-group heuristic (Hair_bNN
# names) doesn't catch it -- this object is presumably rigidly bound to an
# ordinary bone rather than the dynamic hair-chain bones -- so by that
# heuristic alone it looked like clothing: stripped from the donor (losing
# real hair geometry) and kept from the source (importing a mismatched
# replacement that mostly fails to bind -- see the Pec/Lat/Delt/Neck
# unresolved-vertex-group warnings this produces). That mismatch is the
# leading suspect for the chaotic "hair never at rest, jagged black mass"
# artifact detourne saw in-game.
#
# Per detourne's explicit instruction: "the lambert tied to the hair in the
# donor attire.mcd should stay, and do not transfer over the lambert
# connected to the hair from the 2k26 mod." This is a per-character, NOT
# universally safe assumption -- "lambert1" is generic enough that it could
# just as easily be a real small clothing accessory on a different mod.
# Scoped narrowly to this naming pattern and used ONLY by
# _convert_attire_donor_base (never the general Option 2/3/4 hair-fix
# pipeline, which has a much looser tolerance for misclassifying something
# as "hair") -- logged loudly either way so it's easy to spot-check on
# future characters.
#
# RESTRICTED TO THE DONOR SIDE ONLY as of 2026-10-02 (see the long comment
# at _convert_attire_donor_base's source-side hair-detection call for the
# full story): this widened heuristic exists ONLY because a donor's own
# hair piece can be rigidly bound (no Hair_bNN vertex groups) AND left
# with Maya's never-renamed default material name, making it otherwise
# undetectable -- a real, confirmed situation on Athena's donor. The
# SOURCE side never had that problem (every source hair piece seen in this
# project is clearly named -- hair_ALPHA, hair2_ALPHA, hair4_ALPHA,
# goatee_ALPHA -- or carries real Hair_bNN groups), so applying this same
# widened sweep to source as well was pure extra risk with no corresponding
# benefit, and on a character whose attire has MULTIPLE placeholder-named
# objects for different, unrelated purposes (Saya Kamitani: one tied to
# hair, several tied to individual tassel sections -- each apparently
# load-bearing for that piece's own .ycl-driven animation), that extra
# risk turned into a real bug: it discarded every one of them as "hair-
# adjacent," including the tassels' own, confirmed (2026-10-02, in-game)
# to be why her tassels went static in the donor-base-swap pipeline.
_PLACEHOLDER_MATERIAL_NAME_RE = re.compile(r"^(lambert|blinn|phong|standardsurface)\d*(:|$)", re.IGNORECASE)


def _find_hair_and_placeholder_mesh_objs(mesh_objs, log: list[str], context_label: str):
    """Like _find_hair_mesh_objs, but ALSO includes any mesh object whose
    name matches a Maya default/unrenamed material name (lambert1, blinn1,
    ...) -- see _PLACEHOLDER_MATERIAL_NAME_RE's comment above. Used only by
    _convert_attire_donor_base's DONOR-side hair detection -- the
    source-side call uses the narrower _find_hair_mesh_objs instead (see
    that call site for why)."""
    hair_objs = _find_hair_mesh_objs(mesh_objs)
    placeholder_objs = [
        obj for obj in mesh_objs
        if obj not in hair_objs and obj.type == "MESH"
        and _PLACEHOLDER_MATERIAL_NAME_RE.match(obj.name)
    ]
    if placeholder_objs:
        log.append(
            f"  {context_label}: also treating {[o.name for o in placeholder_objs]} "
            "as hair-adjacent (name matches a Maya default/unrenamed material name "
            "like 'lambert1' -- per detourne's 2026-10-02 finding on Athena, this is "
            "often leftover hair geometry an artist never renamed, not real "
            "clothing -- VERIFY visually if this looks wrong on a future character)"
        )
    return hair_objs + placeholder_objs


def _convert_attire_donor_base(
    bpy, vc_interface, vc_interface_save,
    source_mcd: str, out_mcd: str,
    donor_attire_mcd: str,
    import_args: dict, export_args: dict, rename_map: dict,
    label: str, log: list[str],
) -> None:
    """
    INVERTED hair-swap architecture (added 2026-10-02, at detourne's explicit
    request). Per real WWE2K25 modding community practice (which detourne
    learned and relayed after the previous approach kept causing errors): a
    correct hair swap starts from the DONOR's own attire .mcd -- which
    embeds a complete, self-contained, ALREADY-correctly-bound skeleton
    (body bones and the donor's native dynamic-hair-chain bones together,
    as one single rig) -- strips the donor's own clothing out of it, and
    grafts the 2K26 SOURCE mod's clothing mesh onto that same donor-native
    skeleton.

    This is the INVERSE of this tool's previous/default attire pipeline
    (_convert_one + _transplant_donor_hair_mesh): that approach kept the
    SOURCE attire as the base scene and transplanted the donor's hair MESH
    into it, which required _graft_donor_hair_bones to reconcile the
    donor's separately-imported, attire-embedded hair armature (e.g.
    'Skeleton.001') with the single shared donor_armature every other mesh
    in the output was being bound to -- i.e. it tried to make the DONOR's
    hair rig fit into a skeleton object it wasn't natively part of. This
    function instead leaves the donor's hair exactly where it already
    works (on its own native armature, imported fresh, untouched) and
    brings the SOURCE's clothing to it instead.

    Deliberately self-contained and explicit rather than relying on the
    addon's own "auto-redirect onto whatever armature is already in the
    scene" import behavior (that behavior is what produces a SEPARATE new
    armature object for a second attire-embedded import in the first
    place -- see _graft_donor_hair_bones's docstring): every mesh object
    this function ends up exporting has its `.parent` and Armature
    modifier `.object` set explicitly, the same proven technique
    `_transplant_donor_hair_mesh` already uses successfully.

    Steps:
      1. Import the donor's own attire .mcd fresh -- a REAL, already-
         working WWE2K25 attire export, so its embedded armature (captured
         by reference; never assumed to be named 'Skeleton') already has
         every bone, body and hair, correctly authored, and its own hair
         mesh is already correctly bound to it. No bone data is
         synthesized or grafted here at all.
      2. Remove the donor attire's own CLOTHING mesh objects (every mesh
         object that does NOT match the _find_hair_mesh_objs hair
         heuristic) -- keep only its hair mesh object(s), natively bound.
      3. Import the 2K26 SOURCE attire .mcd fresh, apply the same 4-bone
         rename map every other conversion in this tool applies, then
         discard the SOURCE's own hair mesh entirely (the donor's native
         hair from step 2 replaces it) -- keep only the source's CLOTHING
         mesh objects.
      4. Reparent every surviving source clothing mesh object onto the
         donor attire armature from step 1. Ordinary body-bone vertex
         groups are expected to resolve directly against it (2K26 and
         2K25 share bone names outside the 4-bone rename map -- the same
         assumption the rest of this pipeline already relies on);
         _report_unresolved_vertex_groups flags anything that doesn't.
      5. Export the combined set (donor hair + source clothing), all
         parented to the donor attire's own native armature. No Option
         2/3/4 hair-bone fix applies here -- the hair being exported is
         the donor's own real, already-correctly-bound hair, never the
         source's, so there is nothing broken left to fix.

    UNTESTED IN-GAME as of writing -- log-only verification first, same
    discipline as every other hair fix in this project.
    """
    log.append(f"--- {label} (DONOR-ATTIRE-AS-BASE mode) ---")

    log.append(f"  Importing donor attire (base scene): {donor_attire_mcd}")
    donor_objs = _import_tracked(bpy, vc_interface, donor_attire_mcd, import_args, log)
    donor_mesh_objs = [o for o in donor_objs if o.type == "MESH"]
    donor_armature = next((o for o in donor_objs if o.type == "ARMATURE"), None)
    if donor_armature is None:
        raise RuntimeError(f"{label}: donor attire import has no ARMATURE object: {donor_attire_mcd}")
    log.append(
        f"  Donor attire armature in scene: '{donor_armature.name}' "
        f"({len(donor_armature.data.bones)} bones)"
    )

    donor_hair_objs = _find_hair_and_placeholder_mesh_objs(donor_mesh_objs, log, "donor attire")
    donor_clothing_objs = [o for o in donor_mesh_objs if o not in donor_hair_objs]
    if not donor_hair_objs:
        log.append(
            "  WARNING: donor attire has no mesh object matching the hair "
            f"heuristic ({[o.name for o in donor_mesh_objs]}) -- this mode is "
            "meant for a donor attire that actually has hair; continuing "
            "with no hair in the output."
        )
    removed_donor_clothing = [o.name for o in donor_clothing_objs]
    for obj in donor_clothing_objs:
        bpy.data.objects.remove(obj, do_unlink=True)
    log.append(
        f"  Stripped donor's own clothing mesh {removed_donor_clothing} -- "
        f"kept donor hair mesh {[o.name for o in donor_hair_objs]}, natively "
        f"bound to '{donor_armature.name}'"
    )

    log.append(f"  Importing source (2K26) attire clothing: {source_mcd}")
    source_objs = _import_tracked(bpy, vc_interface, source_mcd, import_args, log)
    source_mesh_objs = [o for o in source_objs if o.type == "MESH"]
    if not source_mesh_objs:
        raise RuntimeError(f"{label}: source import produced no MESH objects: {source_mcd}")
    log.append(
        f"  source mesh objects imported ({len(source_mesh_objs)}): "
        f"{[o.name for o in source_mesh_objs]}"
    )

    _apply_rename_map(source_mesh_objs, rename_map, log)

    # NARROW heuristic on purpose (_find_hair_mesh_objs, NOT
    # _find_hair_and_placeholder_mesh_objs) -- fixed 2026-10-02 after a
    # real in-game test (Saya Kamitani, v6) confirmed a real bug here. The
    # widened donor-side heuristic (which also sweeps up "lambert1"/
    # "blinn1"/etc-named objects -- see _PLACEHOLDER_MATERIAL_NAME_RE) was
    # being applied to the SOURCE side too, on the theory that the
    # source's hair-adjacent placeholder-named object should be discarded
    # as a matched pair with the source's own hair mesh (same logic as
    # keeping the donor's hair+placeholder pair together). That's too
    # broad for an attire with MULTIPLE placeholder-named objects: detourne
    # confirmed Saya's source attire has more than one Maya-default-named
    # object (NOT all of them hair-related -- her tassel pieces have their
    # own "accompanying lambert" that the .ycl-driven animation apparently
    # depends on; per detourne: "each item that gets animated by the .ycl
    # file has an accompanying lambert... without the lambert object...
    # won't animate properly"), and the widened heuristic was silently
    # discarding ALL of them as "hair-adjacent," not just the one actually
    # tied to source's hair -- the leading suspect for the v6 test's
    # static tassels (materials were separately confirmed correct for
    # tassels, see the 2026-10-02 shader-fix finding, ruling that out).
    # detourne's own follow-up test (2026-10-02, no hair swap this run, so
    # this code path wasn't even in play and every original lambert object
    # survived untouched) confirms tassels DO animate correctly once their
    # lambert survives -- consistent with this being the real cause.
    # Source hair objects we actually DO need to find are already reliably
    # named (hair_ALPHA, hair2_ALPHA, hair4_ALPHA, goatee_ALPHA, etc. --
    # every real example seen in this project) or carry real Hair_bNN
    # vertex groups, so unlike the donor side, source never needed the
    # placeholder-name widening to find its hair in the first place. This
    # is a strictly safer change in the failure direction too: the worst
    # case of under-matching here is an extra unwanted object surviving as
    # "clothing" (visible, fixable by eye), never a silently-dropped
    # animation-critical object (invisible until an in-game test).
    source_hair_objs = _find_hair_mesh_objs(source_mesh_objs)
    source_clothing_objs = [o for o in source_mesh_objs if o not in source_hair_objs]
    if source_hair_objs:
        removed_source_hair = [o.name for o in source_hair_objs]
        for obj in source_hair_objs:
            bpy.data.objects.remove(obj, do_unlink=True)
        log.append(
            f"  Discarded source's own hair mesh {removed_source_hair} -- "
            "the donor's native hair (kept above) replaces it."
        )
    if not source_clothing_objs:
        raise RuntimeError(
            f"{label}: source attire had no non-hair (clothing) mesh object "
            "left after removing its hair mesh -- nothing to graft onto the "
            "donor base."
        )

    target_bone_names = {b.name for b in donor_armature.data.bones}
    dest_collection = None
    if donor_hair_objs:
        cols = donor_hair_objs[0].users_collection
        if cols:
            dest_collection = cols[0]
    if dest_collection is None:
        dest_collection = bpy.context.scene.collection

    for obj in source_clothing_objs:
        obj.parent = donor_armature
        arm_mod = next((m for m in obj.modifiers if m.type == "ARMATURE"), None)
        if arm_mod is None:
            arm_mod = obj.modifiers.new(type="ARMATURE", name="Armature")
        arm_mod.object = donor_armature
        if obj.users_collection and dest_collection not in obj.users_collection:
            for c in list(obj.users_collection):
                c.objects.unlink(obj)
            dest_collection.objects.link(obj)

        vgroup_names = [vg.name for vg in obj.vertex_groups]
        unresolved = [
            n for n in vgroup_names
            if n not in target_bone_names and not _HAIR_BONE_RE.match(n)
        ]
        if unresolved:
            log.append(
                f"  '{obj.name}': reparented onto donor attire armature "
                f"'{donor_armature.name}' -- {len(unresolved)} vertex group(s) "
                f"don't match any real bone on it ({unresolved[:10]}"
                + (" ...(truncated)" if len(unresolved) > 10 else "")
                + ")"
            )
        else:
            log.append(
                f"  '{obj.name}': reparented onto donor attire armature "
                f"'{donor_armature.name}' -- all {len(vgroup_names)} vertex "
                "group(s) resolve to a real bone on it"
            )

    mesh_objs = donor_hair_objs + source_clothing_objs

    _report_unresolved_vertex_groups(mesh_objs, donor_armature, log)

    _export_mesh_objs(bpy, vc_interface_save, mesh_objs, out_mcd, export_args, label, log, donor_armature=donor_armature)


# ---------------------------------------------------------------------------
# HEAT SEEK mode (added 2026-10-15)
#
# detourne shared a screenshot of a community tutorial for manually backporting
# WWE2K26 mods to WWE2K25 in Blender, using "pwm" (= this project's own
# WWE2K_MDL addon dependency) and a third-party UI wrapper addon, "vynx
# tools". A first pass cross-referencing it against this project's
# architecture was done from the screenshot alone and got two mechanisms
# wrong by guesswork (logged and corrected in the project doc the same day).
# detourne then found the actual vynx tools addon
# (Quick-Port_Vynx.dev.zip) locally, which was inspected directly
# (vynx_2k26_quick_port.py, read in full in an isolated scratch directory
# per this sandbox's untrusted-download rule, then deleted) -- it turned out
# to be a thin UI wrapper whose real work is done by calling into the SAME
# WWE2K_MDL addon this project already depends on
# (Blender/Extras/skeleton_alignment.py and
# Blender/Extras/streamline4_1.py). detourne's own installed copy of that addon
# (Blender Addon_v2.10.3) was staged from his computer and those two files
# read in full, which is what this section is built from -- CONFIRMED
# mechanisms, not guesses:
#
#   - "Prep Skeleton" (hair-only) = skeleton_alignment.py's
#     alignArmature(target, base, hair_only=True): walks the 'J_Hair'
#     subtree of one selected armature (BFS, so parents land before
#     children) and creates any bone missing-by-name on the other,
#     copying real head/tail/matrix data across. check_parenting() then
#     makes sure J_Hips parents under J_Root if both exist.
#   - "Heat Seek" (body parts / attire) = streamline4_1.py's
#     heat_seek_rigging's 'AUTO' path: transfer_weights_blender_native
#     (Blender's own DATA_TRANSFER modifier, VGROUP_WEIGHTS,
#     POLYINTERP_NEAREST -- nearest-surface-point projection from a
#     reference mesh that's already correctly weighted) +
#     cleanup_vertex_weights (zero sub-threshold weights, renormalize per
#     vertex) + reparent onto the reference mesh's own armature. Only ever
#     run on meshes with NO existing vertex groups (the tutorial's own
#     separate "Delete All Vertex Groups" step clears them first).
#
# Reimplemented below using only documented core Blender API -- not by
# importing the addon's own private Extras modules -- for portability
# across addon versions (the tutorial referenced "2.2.0"; detourne's installed
# copy is "2.10.3", and this project doesn't want a hard dependency on
# either addon's internal module layout staying stable). The hair-bone-graft
# half doesn't even need a new function: this project's own
# _graft_donor_hair_bones (written 2026-09-25 for a different purpose --
# reconciling the DONOR's own attire-embedded hair armature with
# donor_armature) is already fully generic over "any source armature, any
# donor armature, any bone name list," and is reused as-is here with the
# SOURCE attire's own freshly-imported embedded armature as the input
# instead -- a capability this project's code never exercised before, since
# nothing previously captured that object at all (see _convert_one, which
# discards everything from _import_tracked except the MESH objects).
#
# Added ALONGSIDE every existing pipeline (Options 2/3/4 in _convert_one,
# and _convert_attire_donor_base) -- none of those are removed or disabled
# by this. Per detourne's own framing ("If this step works well, it may just
# be the ultimate method, and we would not need the other options in the
# tool"), this is a candidate to eventually replace them, but per this
# project's standing discipline that only happens after a real in-game
# test confirms it -- never preemptively.
#
# UNTESTED IN-GAME as of writing -- log-only verification first, same
# discipline as every other fix in this project. There is no Blender (or
# the addon) available where this code is written, so treat the first real
# run's log output as the primary debugging tool if something's wrong.
# ---------------------------------------------------------------------------

# Parts the real tutorial reparents directly (vynx's "Prepare Body"/"Prep
# Mouth Parts" steps both call the SAME plain-reparent function,
# prep_parts_to_skeleton -- confirmed NOT a heat-seek call by reading
# vynx_2k26_quick_port.py) rather than heat-seeking. No equivalent regex
# existed anywhere in this file before -- this project's other mesh
# classification (_find_hair_mesh_objs, _PLACEHOLDER_MATERIAL_NAME_RE) is
# vertex-group- or material-name-based, not this. Deliberately narrow (teeth/
# tongue/mouthbag only, matching what the tutorial's own separate "Quick
# Port Mouth" step implies) -- everything NOT matched here and NOT hair
# falls through to heat-seek instead, which is the safer default direction
# (heat-seek recomputes real geometry-based weight rather than silently
# keeping a possibly name-mismatched vertex group untouched).
_SIMPLE_REPARENT_NAME_RE = re.compile(r"(teeth|tooth|tongue|mouthbag)", re.IGNORECASE)


def _heat_seek_transfer_weights(
    bpy, source_mesh, target_objs: list, donor_armature, log: list[str],
    weight_threshold: float = 0.01,
) -> None:
    """
    Reimplementation of streamline4_1.py's heat_seek_rigging('AUTO') path
    (transfer_weights_blender_native + cleanup_vertex_weights + reparent),
    using only documented core Blender API -- see this section's header
    comment for the confirmed source this is built from.

    For each target object: existing vertex groups are deleted FIRST
    (mirrors the real addon's own tutorial workflow, which has a separate
    explicit "Delete All Vertex Groups" step before Heat Seek -- confirmed
    necessary because the real addon's own native-transfer function only
    CREATES group names that don't already exist, so a target's stale
    groups from its own, differently-named source skeleton would otherwise
    survive alongside the new ones instead of being replaced by them). A
    DATA_TRANSFER modifier then pulls VGROUP_WEIGHTS from `source_mesh` by
    nearest surface point (POLYINTERP_NEAREST, matching the real addon's
    own 'AUTO' search method) and is applied immediately. A cleanup pass
    zeros any weight under `weight_threshold` (DATA_TRANSFER can leave tiny
    near-zero ghost influences across many groups) and renormalizes each
    vertex's remaining weights to sum to 1.0 -- a plain per-vertex Python
    loop rather than the real addon's numpy-vectorized version, since
    nothing else in this file uses numpy and this project has generally
    avoided adding dependencies the headless Blender environment might not
    have. Finally the target is reparented onto `donor_armature` (parent +
    Armature modifier), the same pattern _transplant_donor_hair_mesh and
    _convert_attire_donor_base already use successfully elsewhere in this
    file.

    `source_mesh` itself (if it ever turned up in `target_objs` by
    accident) is always skipped.
    """
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    for obj in target_objs:
        if obj.type != "MESH" or obj is source_mesh:
            continue

        existing_group_count = len(obj.vertex_groups)
        for vg in list(obj.vertex_groups):
            obj.vertex_groups.remove(vg)

        # BUG FOUND 2026-10-15, first real test run (Yota Tsuji, v10): every
        # single heat-seeked mesh came back from the log reporting "produced
        # 0 new group(s)" -- confirmed from detourne's own real log, not a
        # guess. Root cause: with layers_vgroup_select_dst="NAME",
        # DATA_TRANSFER only writes into a DESTINATION vertex group that
        # ALREADY EXISTS under that name -- it never creates new groups to
        # match the source, no matter what layers_vgroup_select_src is set
        # to. The real addon's own transfer_weights_blender_native (see this
        # section's header comment) does exactly this -- creates the
        # missing destination group names BEFORE running the modifier -- and
        # this reimplementation had dropped that step entirely, clearing
        # every old group above and then giving DATA_TRANSFER nothing to
        # write into at all. Every heat-seeked mesh in that test run (the
        # entire non-hair, non-mouth mesh set of both the BaseModel and the
        # attire) therefore exported with ZERO vertex groups -- fully
        # dropped weight, which would show up in-game as those parts
        # collapsing/snapping to the world origin. Fixed by creating one
        # new (initially empty) vertex group per name on `source_mesh`
        # before the modifier runs, same as the real addon does.
        for vg in source_mesh.vertex_groups:
            obj.vertex_groups.new(name=vg.name)

        mod = obj.modifiers.new(name="_heat_seek_wt", type="DATA_TRANSFER")
        mod.object = source_mesh
        mod.use_vert_data = True
        mod.data_types_verts = {"VGROUP_WEIGHTS"}
        mod.vert_mapping = "POLYINTERP_NEAREST"
        mod.layers_vgroup_select_src = "ALL"
        mod.layers_vgroup_select_dst = "NAME"
        mod.use_object_transform = True

        bpy.context.view_layer.objects.active = obj
        try:
            bpy.ops.object.modifier_apply(modifier=mod.name)
        except RuntimeError as e:
            log.append(
                f"    WARNING: HEAT SEEK: DATA_TRANSFER failed on '{obj.name}' "
                f"({type(e).__name__}: {e}) -- removing the modifier and leaving "
                f"this mesh with NO vertex groups at all (it had "
                f"{existing_group_count} before this ran); it will export with "
                "fully dropped weight."
            )
            leftover = obj.modifiers.get(mod.name)
            if leftover is not None:
                obj.modifiers.remove(leftover)
            continue

        new_group_count = len(obj.vertex_groups)
        group_index_to_name = {vg.index: vg.name for vg in obj.vertex_groups}
        zeroed = 0
        for vertex in obj.data.vertices:
            entries = [(ge.group, ge.weight) for ge in vertex.groups if ge.weight > 0.0]
            kept = [(gi, w) for gi, w in entries if w >= weight_threshold]
            dropped = [gi for gi, w in entries if w < weight_threshold]
            for gi in dropped:
                name = group_index_to_name.get(gi)
                vg = obj.vertex_groups.get(name) if name else None
                if vg is not None:
                    vg.remove([vertex.index])
                    zeroed += 1
            total = sum(w for _, w in kept)
            if total <= 0.0 or abs(total - 1.0) < 1e-6:
                continue
            for gi, w in kept:
                name = group_index_to_name.get(gi)
                vg = obj.vertex_groups.get(name) if name else None
                if vg is not None:
                    vg.add([vertex.index], w / total, "REPLACE")

        obj.parent = donor_armature
        arm_mod = next((m for m in obj.modifiers if m.type == "ARMATURE"), None)
        if arm_mod is None:
            arm_mod = obj.modifiers.new(type="ARMATURE", name="Armature")
        arm_mod.object = donor_armature

        log.append(
            f"    HEAT SEEK: '{obj.name}' -- cleared {existing_group_count} old "
            f"vertex group(s), DATA_TRANSFER from '{source_mesh.name}' produced "
            f"{new_group_count} new group(s), zeroed {zeroed} sub-threshold "
            f"weight slot(s), reparented onto '{donor_armature.name}'"
        )


def _graft_hair_only_subtree(bpy, donor_armature, source_armature, log: list[str]) -> set[str]:
    """
    FIX (2026-10-15, after a real in-game test on Yota Tsuji, v11, crashed
    the game to desktop while loading the match). Faithful reimplementation
    of the real addon's skeleton_alignment.py:alignArmature(..., hair_only=
    True) + check_parenting -- walks ONLY the real bone-HIERARCHY subtree
    rooted at a bone literally named 'J_Hair' on `source_armature` (BFS via
    Bone.children, so parents always land before children, exactly
    matching the real addon's own _collect_subtree_bfs), and grafts any
    bone in that subtree missing-by-name on `donor_armature`.

    REPLACES the v10/v11 version of this step, which instead grafted every
    name found in the hair mesh object(s)' own vertex groups, via the
    existing (and still otherwise-valid) _graft_donor_hair_bones -- a
    shortcut that seemed safe (that function is generic over "any bone
    name list") but turned out NOT to be: a real source attire's hair mesh
    can have a few vertices blended onto body/muscle-subdivision bones near
    the hairline for a smoother join (CONFIRMED in Yota Tsuji's own v11
    log: 'H_Delt_L_2', 'H_Delt_R_2', 'H_Lat_L_2', 'H_Lat_L_3', 'H_Lat_R_1',
    'H_Lat_R_2', 'H_Lat_R_3', 'H_Neck_L_1', 'H_Neck_L_2', 'H_Neck_R_1',
    'H_Neck_R_2', 'H_Pec_L_1', 'H_Pec_L_2', 'H_Pec_L_3', 'H_Pec_R_1',
    'H_Pec_R_2', 'H_Pec_R_3' all got grafted alongside the real Hair_bNN
    chain). Those are CORE BODY ANIMATION bones, not part of the optional
    hair-physics system, and at least the '_3'-suffixed ones don't exist in
    the real WWE2K25 skeleton this donor represents at all (2K26 appears to
    have finer per-muscle subdivision than 2K25). Mixing extra, non-
    canonical bones into what the game treats as this character's base
    animation skeleton is the leading suspect for the crash -- a much
    harsher failure mode than the graceful "silently drops weight"
    degradation this project relies on everywhere else for a genuinely
    unresolvable vertex group name.

    Walking the real bone HIERARCHY from a literal 'J_Hair' root (as the
    real addon does) instead of harvesting arbitrary vertex-group names
    rules this out structurally: a muscle-subdivision bone is never a
    descendant of 'J_Hair' in the skeleton's own parent/child tree, no
    matter what a mesh's vertex groups happen to reference. Note this
    project's own Option 3 (_graft_missing_hair_bones) was already safe in
    this respect -- it only ever pattern-matches Hair_bNN-style names, never
    harvests a mesh's full vertex-group list -- this bug was specific to
    this Heat Seek feature's reuse of _graft_donor_hair_bones.

    Delegates the actual bone-creation (world-matrix-correct head/tail/
    roll copy, parent-chain walk, cycle guard) to the existing
    _graft_donor_hair_bones, now called with the real subtree's bone names
    instead of a mesh's vertex-group names -- that function's own
    mechanics were never the problem, only what this caller fed it.

    Also reimplements check_parenting: if the donor armature has both
    'J_Root' and 'J_Hips', makes sure 'J_Hips' is parented under 'J_Root'
    (a no-op if it already is).

    Returns the resolvable bone-name set from _graft_donor_hair_bones
    (names in the real J_Hair subtree that now exist on `donor_armature`,
    whether pre-existing or freshly grafted) -- NOT necessarily the same
    set as any given mesh's own vertex-group names (see the caller, which
    checks those separately against donor_armature's real bone list).

    UNTESTED IN-GAME as of writing this revision -- the PREVIOUS version
    of this function is what got the real in-game test that found the bug
    this fixes; this fix has been reasoned through and log-checked against
    the structural difference (it cannot graft a bone that isn't a real
    descendant of 'J_Hair'), but has not itself been re-tested in-game yet.
    """
    root_name = "J_Hair"
    src_bones = source_armature.data.bones
    root = src_bones.get(root_name)
    if root is None:
        log.append(
            f"  HEAT SEEK hair graft: source armature '{source_armature.name}' "
            f"has no '{root_name}' bone at all -- nothing to walk, nothing "
            "grafted (the hair mesh's existing vertex groups will resolve "
            "only against whatever bones already exist by name on the "
            "donor armature)."
        )
        return set()

    # BFS over the REAL bone hierarchy (Bone.children), not a naming
    # convention -- parents always collected before children, matching the
    # real addon's own _collect_subtree_bfs exactly.
    subtree_names: list[str] = []
    queue = [root]
    while queue:
        b = queue.pop(0)
        subtree_names.append(b.name)
        queue.extend(b.children)

    log.append(
        f"  HEAT SEEK hair graft: walked {len(subtree_names)} real bone(s) in "
        f"the '{root_name}' subtree on source armature '{source_armature.name}' "
        "(real bone hierarchy, not a vertex-group name list -- see this "
        "function's docstring for why that distinction matters)"
    )

    resolvable = _graft_donor_hair_bones(bpy, donor_armature, source_armature, subtree_names, log)

    # check_parenting equivalent (skeleton_alignment.py) -- belt-and-braces,
    # matches the real addon's own post-alignArmature step.
    donor_bone_names = {b.name for b in donor_armature.data.bones}
    if "J_Root" in donor_bone_names and "J_Hips" in donor_bone_names:
        prev_active = bpy.context.view_layer.objects.active
        prev_mode = prev_active.mode if prev_active is not None else "OBJECT"
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.view_layer.objects.active = donor_armature
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            eb = donor_armature.data.edit_bones
            if "J_Hips" in eb and "J_Root" in eb:
                eb["J_Hips"].parent = eb["J_Root"]
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")
            if prev_active is not None:
                bpy.context.view_layer.objects.active = prev_active
                try:
                    bpy.ops.object.mode_set(mode=prev_mode)
                except RuntimeError:
                    pass

    return resolvable


_HEAD_REGION_NAME_RE = re.compile(r"(head|eye|lash)", re.IGNORECASE)


def _convert_one_heat_seek(
    bpy, vc_interface, vc_interface_save,
    source_mcd: str, out_mcd: str,
    import_args: dict, export_args: dict, rename_map: dict,
    donor_armature, donor_body_mesh,
    label: str, log: list[str],
    donor_head_mesh=None,
) -> None:
    """
    HEAT SEEK mode -- see this file's "HEAT SEEK mode" section header
    comment above for the full background and confirmed sources this is
    built from. Added 2026-10-15 at detourne's explicit request: "Yes, please
    build this. If this step works well, it may just be the ultimate
    method, and we would not need the other options in the tool."

    Classifies every mesh object from the source import into four groups
    and handles each differently:

      - HAIR (_find_hair_mesh_objs -- same heuristic Options 2/3/4 use):
        the source attire's OWN embedded armature (freshly created on
        import -- e.g. 'Skeleton.001' -- never captured or used anywhere
        else in this file before today) is used to graft the real hair
        bone chain into `donor_armature` via _graft_hair_only_subtree. The
        hair mesh is then reparented onto `donor_armature` KEEPING its
        existing vertex weights -- the graft's whole point is to make
        those names resolve for real, not to discard and recompute them.
        If the source has no embedded armature at all (shouldn't happen,
        but defensively handled), hair meshes fall back to the heat-seek
        path below instead of being left with guaranteed-unresolvable
        weights.

      - MOUTH/TEETH (_SIMPLE_REPARENT_NAME_RE): reparented onto
        `donor_armature`, existing weights kept as-is -- matches the real
        tutorial's own "Quick Port Mouth" step (confirmed a plain reparent,
        not a heat-seek call, by reading vynx_2k26_quick_port.py).

      - HEAD-REGION parts (_HEAD_REGION_NAME_RE -- head/eye/eyelash etc.):
        HEAT SEEK via _heat_seek_transfer_weights, using `donor_head_mesh`
        (the donor's own separate head mesh, chosen once in _run_job by
        name match) as the geometric reference.

      - EVERYTHING ELSE (body:skinned, and every attire clothing piece):
        HEAT SEEK via _heat_seek_transfer_weights, using `donor_body_mesh`
        (the donor's own already-correctly-weighted main body mesh) as the
        geometric reference.

    FIX (2026-10-15, after a real in-game test on Yota Tsuji, v12): the
    head/body split above REPLACES the v10-v12 behavior of heat-seeking
    EVERY non-hair, non-mouth part (including head/eye/eyelash) from the
    single `donor_body_mesh`. That test confirmed a real, structural bug:
    with no head geometry anywhere on the body-only reference mesh,
    DATA_TRANSFER's nearest-surface-point mapping snapped head/eye/eyelash
    vertices to the nearest point it COULD find -- down at the collar/
    shoulder -- inheriting shoulder/arm bone weights instead of real head/
    neck ones. In-game this looked like: teeth and eyeballs floating and
    out of sync, half the head bound to the shoulder (visibly collapsing
    when the arm raised, stretching sideways when it lowered). See
    _run_job's own comment where `donor_head_mesh` is chosen for the full
    writeup. UNTESTED IN-GAME as of writing this revision -- reasoned
    through and log-checked against the confirmed mechanism, not yet
    re-tested against a real match load.
    """
    log.append(f"--- {label} (HEAT SEEK mode) ---")
    new_objs = _import_tracked(bpy, vc_interface, source_mcd, import_args, log)
    mesh_objs = [o for o in new_objs if o.type == "MESH"]
    if not mesh_objs:
        raise RuntimeError(f"{label}: import produced no MESH objects: {source_mcd}")
    source_armature = next((o for o in new_objs if o.type == "ARMATURE"), None)
    log.append(
        f"  mesh objects about to be classified ({len(mesh_objs)}): "
        f"{[o.name for o in mesh_objs]}; source's own embedded armature: "
        + (
            f"'{source_armature.name}' ({len(source_armature.data.bones)} bones)"
            if source_armature is not None else "NONE FOUND"
        )
    )

    _apply_rename_map(mesh_objs, rename_map, log)

    hair_objs = _find_hair_mesh_objs(mesh_objs)
    simple_objs = [
        o for o in mesh_objs
        if o not in hair_objs and _SIMPLE_REPARENT_NAME_RE.search(o.name)
    ]
    heat_seek_objs = [o for o in mesh_objs if o not in hair_objs and o not in simple_objs]

    if hair_objs:
        if source_armature is not None:
            _graft_hair_only_subtree(bpy, donor_armature, source_armature, log)
            target_bone_names = {b.name for b in donor_armature.data.bones}
            bone_names = sorted({vg.name for obj in hair_objs for vg in obj.vertex_groups})
            unresolved_hair = [n for n in bone_names if n not in target_bone_names]
            if unresolved_hair:
                log.append(
                    f"  WARNING: HEAT SEEK hair graft: {len(unresolved_hair)} vertex "
                    "group name(s) on the hair mesh still don't match any real bone "
                    f"on the donor armature after grafting the real 'J_Hair' subtree "
                    f"({unresolved_hair[:10]}"
                    + (" ...(truncated)" if len(unresolved_hair) > 10 else "")
                    + ") -- these will export with dropped weight. If these are NOT "
                    "Hair_bNN-pattern names, this is deliberate: only the real "
                    "'J_Hair' bone-hierarchy subtree gets grafted now, never an "
                    "arbitrary non-hair bone name a mesh's vertex groups happen to "
                    "also reference -- see _graft_hair_only_subtree's docstring for "
                    "why (confirmed necessary after a real in-game crash, 2026-10-15)."
                )
            for obj in hair_objs:
                obj.parent = donor_armature
                arm_mod = next((m for m in obj.modifiers if m.type == "ARMATURE"), None)
                if arm_mod is None:
                    arm_mod = obj.modifiers.new(type="ARMATURE", name="Armature")
                arm_mod.object = donor_armature
            log.append(
                f"  HEAT SEEK: hair mesh {[o.name for o in hair_objs]} reparented onto "
                f"'{donor_armature.name}' with existing weights kept (bone-graft path, "
                "not heat-seeked)"
            )
        else:
            log.append(
                "  WARNING: HEAT SEEK: source attire has no embedded ARMATURE "
                "object to graft real hair bone data from -- falling back to "
                "heat-seeking the hair mesh from the donor body instead (will "
                "lose per-strand dynamic motion, same trade-off as Option 4)."
            )
            heat_seek_objs = heat_seek_objs + hair_objs
            hair_objs = []

    if simple_objs:
        for obj in simple_objs:
            obj.parent = donor_armature
            arm_mod = next((m for m in obj.modifiers if m.type == "ARMATURE"), None)
            if arm_mod is None:
                arm_mod = obj.modifiers.new(type="ARMATURE", name="Armature")
            arm_mod.object = donor_armature
        log.append(
            f"  HEAT SEEK: simple mouth/teeth part(s) {[o.name for o in simple_objs]} "
            f"reparented onto '{donor_armature.name}' with existing weights kept "
            "(name-match, not heat-seeked)"
        )

    if heat_seek_objs:
        # Split by region (fix, 2026-10-15 -- see this function's docstring
        # for the confirmed in-game symptom this addresses) so head/eye/
        # eyelash parts get DATA_TRANSFERed from the donor's own HEAD mesh,
        # never the body-only reference.
        head_region_objs = [o for o in heat_seek_objs if _HEAD_REGION_NAME_RE.search(o.name)]
        body_region_objs = [o for o in heat_seek_objs if o not in head_region_objs]

        if head_region_objs:
            head_ref = donor_head_mesh if donor_head_mesh is not None else donor_body_mesh
            if head_ref is None:
                log.append(
                    "  WARNING: HEAT SEEK: no donor head OR body mesh reference "
                    f"was found -- cannot heat-seek {[o.name for o in head_region_objs]}; "
                    "leaving them with their original (likely name-mismatched) "
                    "vertex groups."
                )
            else:
                if donor_head_mesh is None:
                    log.append(
                        "  WARNING: HEAT SEEK: falling back to the body mesh "
                        f"reference for head-region part(s) {[o.name for o in head_region_objs]} "
                        "-- no separate donor head mesh was found. This is the "
                        "exact mismatch suspected of causing the 2026-10-15 "
                        "Yota Tsuji head/shoulder distortion."
                    )
                log.append(
                    f"  HEAT SEEK: recomputing weights for {[o.name for o in head_region_objs]} "
                    f"from donor HEAD mesh '{head_ref.name}' "
                    f"({len(head_ref.data.vertices)} vertex/vertices, already "
                    "correctly weighted to the donor armature)"
                )
                _heat_seek_transfer_weights(bpy, head_ref, head_region_objs, donor_armature, log)

        if body_region_objs:
            if donor_body_mesh is None:
                log.append(
                    "  WARNING: HEAT SEEK: no donor body mesh reference was found -- "
                    f"cannot heat-seek {[o.name for o in body_region_objs]}; leaving them "
                    "with their original (likely name-mismatched) vertex groups."
                )
            else:
                log.append(
                    f"  HEAT SEEK: recomputing weights for {[o.name for o in body_region_objs]} "
                    f"from donor body mesh '{donor_body_mesh.name}' "
                    f"({len(donor_body_mesh.data.vertices)} vertex/vertices, already "
                    "correctly weighted to the donor armature)"
                )
                _heat_seek_transfer_weights(bpy, donor_body_mesh, body_region_objs, donor_armature, log)

    _report_unresolved_vertex_groups(mesh_objs, donor_armature, log)
    _export_mesh_objs(bpy, vc_interface_save, mesh_objs, out_mcd, export_args, label, log, donor_armature=donor_armature)


if __name__ == "__main__":
    main()
