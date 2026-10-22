"""
WWE2K26 -> WWE2K25 Custom Character Backporter
------------------------------------------------
A small desktop GUI (Tkinter, ships with standard Python on Windows) that
takes a WWE2K26 custom character mod folder and produces a restructured
folder laid out for WWE2K25 installation.

Run with:  python app.py
(Requires Python 3.9+. No third-party packages needed.)

See converter.py for the actual conversion rules and README.md for the
current known limitations / things that still need in-game verification.
"""

from __future__ import annotations

import queue
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from blender_runner import AttireJob, guess_blender_exe, run_blender_conversion
from converter import (
    AttireTarget,
    ConversionError,
    ConversionPlan,
    default_plan,
    find_attire_folders,
    find_donor_attire_folders,
    find_character_root,
    find_donor_hair_files,
    run_conversion,
    _TEMPLATES_DIR,
)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("WWE2K26 -> WWE2K25 Character Backporter")
        self.geometry("720x560")
        self.minsize(640, 480)

        # Window/taskbar icon -- a square crop of the WWE 2K25 logo's "W"
        # mark, bundled at templates/icon.ico (routed through converter.py's
        # _TEMPLATES_DIR so this resolves correctly both run-from-source and
        # frozen into the PyInstaller .exe -- see its comment). iconbitmap
        # only accepts .ico on Windows; wrapped in try/except since a
        # missing/corrupt icon file should never stop the app from starting.
        try:
            self.iconbitmap(default=str(_TEMPLATES_DIR / "icon.ico"))
        except Exception:
            pass

        self.source_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.slot_id_var = tk.StringVar()
        self.roster_name_var = tk.StringVar()

        self.use_blender_var = tk.BooleanVar(value=True)
        self.blender_exe_var = tk.StringVar(value=guess_blender_exe() or "")
        self.addon_dir_var = tk.StringVar()

        # Multi-attire target-ID overrides (added 2026-10-13). Keyed by the
        # SOURCE attire folder name (e.g. "178_Alt_Attire") -> a StringVar
        # the user can edit. default_plan() in converter.py gives every
        # non-primary attire a placeholder target_id equal to the source's
        # own numeric prefix, which is almost never a valid WWE2K25 slot ID
        # (confirmed on a real conversion: produced an "XXX_Alt_Attire"
        # output folder). This dict lets the user fix that from the GUI
        # instead of there being no way to override it at all. Rebuilt by
        # _rescan_attires() whenever the source folder changes.
        self.attire_id_vars: dict[str, tk.StringVar] = {}

        # Conversion mode is hardcoded to "Hair Graft Only" -- real
        # hair-bone-chain graft on top of the plain conversion pipeline, no
        # donor-attire-as-base, no Heat Seek DATA_TRANSFER recompute, no hair
        # pinning. This has been the most consistent mode in-game, so the
        # mode picker/info UI was removed (2026-10-21) since there's no
        # longer a reason to show it. The other modes are still implemented
        # in converter.py / blender_headless_convert.py (see the project doc
        # for their history) in case a future character needs one again --
        # only this app.py front-end was trimmed.
        self.mode_var = tk.StringVar(value="hairgraft")

        # Donor character folder -- optional in every mode, including this
        # one: when set, its real baseModel.mtls is used instead of
        # Blender's auto-generated BaseModel materials, which avoids the
        # known head2/eye_l/mouthbag wrong-shader-type bug. Any real
        # WWE2K25 character folder works; it doesn't need to be related to
        # the character being converted.
        self.donor_folder_var = tk.StringVar()

        self._log_queue: "queue.Queue[str]" = queue.Queue()

        self._build_ui()
        self.after(100, self._poll_log_queue)

    # ---------------------------------------------------------------- UI ---

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        frm_top = ttk.Frame(self)
        frm_top.pack(fill="x", **pad)

        ttk.Label(frm_top, text="WWE2K26 character folder:").grid(row=0, column=0, sticky="w")
        source_entry = ttk.Entry(frm_top, textvariable=self.source_var, width=60)
        source_entry.grid(row=0, column=1, sticky="ew", padx=4)
        source_entry.bind("<FocusOut>", lambda e: self._rescan_attires())
        ttk.Button(frm_top, text="Browse...", command=self._pick_source).grid(row=0, column=2)

        ttk.Label(frm_top, text="Output folder (parent):").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm_top, textvariable=self.output_var, width=60).grid(row=1, column=1, sticky="ew", padx=4)
        ttk.Button(frm_top, text="Browse...", command=self._pick_output).grid(row=1, column=2)

        frm_top.columnconfigure(1, weight=1)

        frm_target = ttk.LabelFrame(self, text="Target WWE2K25 slot")
        frm_target.pack(fill="x", **pad)

        ttk.Label(frm_target, text="Roster slot ID (e.g. 312):").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(frm_target, textvariable=self.slot_id_var, width=12).grid(row=0, column=1, sticky="w", pady=4)

        ttk.Label(frm_target, text="Roster name (for the output folder label, e.g. CM_Punk):").grid(
            row=1, column=0, sticky="w", padx=6, pady=4
        )
        ttk.Entry(frm_target, textvariable=self.roster_name_var, width=30).grid(row=1, column=1, sticky="w", pady=4)

        frm_attire_ids = ttk.LabelFrame(self, text="Multi-attire target IDs (added 2026-10-13)")
        frm_attire_ids.pack(fill="x", **pad)

        top_row = ttk.Frame(frm_attire_ids)
        top_row.pack(fill="x", padx=6, pady=(4, 2))
        self.attire_ids_status = ttk.Label(
            top_row,
            text="(set the character folder above, then click Rescan)",
        )
        self.attire_ids_status.pack(side="left")
        ttk.Button(top_row, text="Rescan", command=self._rescan_attires).pack(side="right")

        self.attire_ids_frame = ttk.Frame(frm_attire_ids)
        self.attire_ids_frame.pack(fill="x", padx=6, pady=(0, 6))
        self.attire_ids_frame.columnconfigure(1, weight=0)

        # Donor character folder -- whenever it's set, its real
        # baseModel.mtls is used (via the proven auto-slot-rename path,
        # confirmed working in-game on Saya Kamitani/Athena) INSTEAD of
        # Blender's auto-generated BaseModel materials -- which is exactly
        # what fixes the known head2/eye_l/mouthbag wrong-shader-type bug
        # (see the Lio Rush finding in the project doc). Optional in Hair
        # Graft Only mode (the only mode this build runs), since that mode
        # doesn't use donor-attire-as-base. Any real WWE2K25 character
        # folder works as the donor; it doesn't need to be related to the
        # character being converted.
        frm_donor = ttk.LabelFrame(
            self, text="BaseModel donor (optional - default is based on CM Punk)"
        )
        frm_donor.pack(fill="x", **pad)

        ttk.Label(
            frm_donor,
            text="Real WWE2K25 character folder (any character):",
            justify="left",
        ).grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.donor_folder_entry = ttk.Entry(frm_donor, textvariable=self.donor_folder_var, width=50)
        self.donor_folder_entry.grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        self.donor_folder_btn = ttk.Button(frm_donor, text="Browse...", command=self._pick_donor_folder)
        self.donor_folder_btn.grid(row=0, column=2, pady=4)

        frm_donor.columnconfigure(1, weight=1)

        frm_blender = ttk.LabelFrame(self, text="Blender setup (skeleton rebinding engine)")
        frm_blender.pack(fill="x", **pad)

        ttk.Checkbutton(
            frm_blender,
            text=(
                "Use Blender to properly rebind the model onto a real WWE2K25 "
                "skeleton (fixes crashes/distortion). Off = old byte-patch-only "
                "path, no skeleton fix -- not recommended, kept only as a fallback."
            ),
            variable=self.use_blender_var,
            command=self._on_toggle_blender,
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=6, pady=4)

        ttk.Label(frm_blender, text="blender.exe:").grid(row=1, column=0, sticky="w", padx=6)
        self.blender_exe_entry = ttk.Entry(frm_blender, textvariable=self.blender_exe_var, width=55)
        self.blender_exe_entry.grid(row=1, column=1, sticky="ew", padx=4)
        self.blender_exe_btn = ttk.Button(frm_blender, text="Browse...", command=self._pick_blender_exe)
        self.blender_exe_btn.grid(row=1, column=2)

        ttk.Label(
            frm_blender,
            text="Addon folder (optional --\nonly if not already enabled\nin Blender's Add-ons list):",
        ).grid(row=2, column=0, sticky="w", padx=6, pady=4)
        self.addon_dir_entry = ttk.Entry(frm_blender, textvariable=self.addon_dir_var, width=55)
        self.addon_dir_entry.grid(row=2, column=1, sticky="ew", padx=4)
        self.addon_dir_btn = ttk.Button(frm_blender, text="Browse...", command=self._pick_addon_dir)
        self.addon_dir_btn.grid(row=2, column=2)

        frm_blender.columnconfigure(1, weight=1)

        self._rescan_attires()  # initial (empty) state of the multi-attire ID list

        self.convert_btn = ttk.Button(self, text="Convert", command=self._on_convert)
        self.convert_btn.pack(pady=6)

        frm_log = ttk.LabelFrame(self, text="Log")
        frm_log.pack(fill="both", expand=True, **pad)

        self.log_text = tk.Text(frm_log, wrap="word", state="disabled")
        scroll = ttk.Scrollbar(frm_log, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    # ------------------------------------------------------------ actions ---

    def _pick_source(self):
        path = filedialog.askdirectory(title="Select the WWE2K26 character folder")
        if path:
            self.source_var.set(path)
            # best-effort: pre-fill roster name guess from folder name
            guess = Path(path).name
            if guess.endswith("_A0"):
                guess = guess[:-3]
            if not self.roster_name_var.get():
                self.roster_name_var.set(guess)
            self._rescan_attires()

    def _rescan_attires(self):
        """
        Scans the current source folder for attire subfolders and rebuilds
        the "Multi-attire target IDs" list: one editable field per
        non-primary attire (the first/primary attire still uses the
        "Roster slot ID" field above). Safe to call any time -- including
        with an empty/invalid source folder, in which case it just shows a
        status message and clears the list.
        """
        for w in self.attire_ids_frame.winfo_children():
            w.destroy()
        self.attire_id_vars = {}

        source = self.source_var.get().strip()
        if not source:
            self.attire_ids_status.configure(text="(set the character folder above, then click Rescan)")
            return

        try:
            character_root = find_character_root(Path(source))
            attire_folders = find_attire_folders(character_root)
        except ConversionError as e:
            self.attire_ids_status.configure(text=f"(couldn't scan for attires: {e})")
            return
        except OSError as e:
            self.attire_ids_status.configure(text=f"(couldn't read that folder: {e})")
            return

        if len(attire_folders) <= 1:
            self.attire_ids_status.configure(
                text=f"(found {len(attire_folders)} attire folder -- nothing to override)"
            )
            return

        self.attire_ids_status.configure(
            text=(
                f"Found {len(attire_folders)} attire folders. The first "
                f"({attire_folders[0].name}) uses the Roster slot ID above as "
                "its '_Default_Attire'. Set a real WWE2K25 slot ID for each "
                "other one below (these all become '_Alt_Attire' -- if there are "
                "3+ attires here, every non-primary one still collides on that "
                "same suffix, see the README):"
            )
        )
        for i, folder in enumerate(attire_folders):
            if i == 0:
                continue
            placeholder = folder.name.split("_", 1)[0]
            var = tk.StringVar(value=placeholder)
            self.attire_id_vars[folder.name] = var
            row = i - 1
            ttk.Label(self.attire_ids_frame, text=f"{folder.name} -> target ID:").grid(
                row=row, column=0, sticky="w", pady=2
            )
            ttk.Entry(self.attire_ids_frame, textvariable=var, width=12).grid(
                row=row, column=1, sticky="w", padx=6, pady=2
            )

    def _pick_output(self):
        path = filedialog.askdirectory(title="Select where the converted folder should be created")
        if path:
            self.output_var.set(path)

    def _pick_blender_exe(self):
        path = filedialog.askopenfilename(
            title="Select blender.exe",
            filetypes=[("Blender executable", "blender.exe"), ("All files", "*.*")],
        )
        if path:
            self.blender_exe_var.set(path)

    def _pick_addon_dir(self):
        path = filedialog.askdirectory(
            title="Select the FOLDER THAT CONTAINS your WWE2K_MDL addon folder"
        )
        if path:
            self.addon_dir_var.set(path)

    def _pick_donor_folder(self):
        path = filedialog.askdirectory(
            title="Select the real WWE2K25 donor character's folder (or its parent)"
        )
        if path:
            self.donor_folder_var.set(path)

    def _on_toggle_blender(self):
        state = "normal" if self.use_blender_var.get() else "disabled"
        self.blender_exe_entry.configure(state=state)
        self.blender_exe_btn.configure(state=state)
        self.addon_dir_entry.configure(state=state)
        self.addon_dir_btn.configure(state=state)

    def _append_log(self, text: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _show_post_conversion_reminder(self):
        """
        Shown once per successful conversion (added 2026-10-22), queued from
        _do_conversion's background thread via "__REMINDER__" and popped here
        on the main thread -- never call messagebox directly from that
        thread, same rule as _append_log.
        """
        messagebox.showinfo(
            "Conversion finished -- manual steps still needed",
            "Before testing this in-game:\n\n"
            "1. Open the attire's .ycl file in the .ycl editor and export it "
            "as WWE2K25.\n\n"
            "2. Open BOTH attire.mtls and basemodel.mtls in Material Editor "
            "and resave, alternating between the two files (attire, then "
            "basemodel, then attire again, and so on) until the @ref values "
            "are populated in both.",
        )

    def _poll_log_queue(self):
        try:
            while True:
                line = self._log_queue.get_nowait()
                if line == "__DONE__":
                    self.convert_btn.configure(state="normal")
                elif line == "__REMINDER__":
                    self._show_post_conversion_reminder()
                else:
                    self._append_log(line)
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _on_convert(self):
        source = self.source_var.get().strip()
        output = self.output_var.get().strip()
        slot_id = self.slot_id_var.get().strip()
        roster_name = self.roster_name_var.get().strip()

        if not source or not output or not slot_id or not roster_name:
            messagebox.showerror(
                "Missing information",
                "Please fill in the source folder, output folder, slot ID, and roster name.",
            )
            return

        if not slot_id.isdigit():
            if not messagebox.askyesno(
                "Non-numeric slot ID",
                f"'{slot_id}' doesn't look like a plain number. Every example this "
                "app was built from used a numeric roster slot ID. Continue anyway?",
            ):
                return

        use_blender = self.use_blender_var.get()
        blender_exe = self.blender_exe_var.get().strip()
        # addon_dir is now OPTIONAL: the primary path enables the addon by
        # name the same way Blender's own Preferences > Add-ons checkbox
        # does (addon_utils.enable), which finds it automatically if it's
        # installed the normal way. This field is only a fallback for a
        # non-standard install location.
        addon_dir = self.addon_dir_var.get().strip()
        if use_blender and not blender_exe:
            if not messagebox.askyesno(
                "Blender integration not fully configured",
                "The skeleton-conversion checkbox is on, but blender.exe isn't set. "
                "Continuing will fall back to the old byte-patch-only path (no "
                "skeleton/armature fix) for this run. Continue anyway?",
            ):
                return
            use_blender = False

        self.convert_btn.configure(state="disabled")
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        # Multi-attire target-ID overrides (added 2026-10-13) -- collect
        # whatever's currently in the per-attire Entry fields, keyed by
        # source attire folder name. Blank entries just fall back to
        # default_plan()'s own placeholder (handled in _do_conversion).
        attire_id_overrides = {
            name: var.get().strip() for name, var in self.attire_id_vars.items()
        }
        non_blank_overrides = [v for v in attire_id_overrides.values() if v]
        if len(non_blank_overrides) != len(set(non_blank_overrides)):
            if not messagebox.askyesno(
                "Duplicate attire target IDs",
                "Two or more of the multi-attire target IDs you entered are "
                "identical -- that will make two output attire folders collide. "
                "Continue anyway?",
            ):
                return

        donor_folder = self.donor_folder_var.get().strip()

        # This build only runs "Hair Graft Only" (see the Conversion mode
        # frame / __init__ comment) -- every other mode's flag is hardcoded
        # off rather than deleted from the pipeline entirely, in case one of
        # them is ever wanted again. See the project doc for the full history.
        donor_hair = False
        fix_hair_bones = False
        graft_hair_bones = False
        donor_attire_as_base = False
        rigid_hair = False
        use_heat_seek = False
        graft_real_hair_bones = True

        thread = threading.Thread(
            target=self._do_conversion,
            args=(
                Path(source), Path(output), slot_id, roster_name,
                use_blender,
                Path(blender_exe) if blender_exe else None,
                Path(addon_dir) if addon_dir else None,
                Path(donor_folder) if donor_folder else None,
                donor_hair,
                fix_hair_bones,
                graft_hair_bones,
                rigid_hair,
                donor_attire_as_base,
                attire_id_overrides,
                use_heat_seek,
                graft_real_hair_bones,
            ),
            daemon=True,
        )
        thread.start()

    def _do_conversion(
        self,
        source: Path,
        output: Path,
        slot_id: str,
        roster_name: str,
        use_blender: bool,
        blender_exe: Path | None,
        addon_dir: Path | None,
        donor_folder: Path | None = None,
        donor_hair: bool = False,
        fix_hair_bones: bool = False,  # "Option 2" -- confirmed broken, stripped from UI 2026-10-02
        graft_hair_bones: bool = False,  # "Option 3" -- superseded by rigid_hair, stripped from UI 2026-10-02
        rigid_hair: bool = False,
        donor_attire_as_base: bool = False,
        attire_id_overrides: dict[str, str] | None = None,
        use_heat_seek: bool = False,
        graft_real_hair_bones: bool = False,
    ):
        def log(msg: str):
            self._log_queue.put(msg)

        try:
            log(f"Building conversion plan for '{source}'...")
            plan = default_plan(source, output, slot_id, roster_name)
            # Materials/*.jmtl is dropped unconditionally now (removed from
            # the UI 2026-10-22) -- no WWE2K25 example has ever shipped this
            # folder, so there was never a real reason to keep it optional.
            plan.drop_materials = True
            plan.skip_source_ycl = rigid_hair

            if len(plan.attires) > 1:
                # Apply any multi-attire target-ID overrides from the GUI
                # (added 2026-10-13) -- without this, non-primary attires
                # keep default_plan()'s placeholder ID (the source's own
                # numeric prefix), which is almost never a valid WWE2K25
                # slot ID. Blank/missing overrides just keep that placeholder.
                for attire in plan.attires:
                    if attire.is_primary:
                        continue
                    override = (attire_id_overrides or {}).get(attire.source_folder_name, "").strip()
                    if override:
                        log(
                            f"  Using target ID '{override}' for attire "
                            f"'{attire.source_folder_name}' (was placeholder '{attire.target_id}')."
                        )
                        attire.target_id = override
                    else:
                        log(
                            f"  WARNING: no target ID override given for attire "
                            f"'{attire.source_folder_name}' -- keeping placeholder "
                            f"'{attire.target_id}', which is probably NOT a valid "
                            "WWE2K25 slot ID. Set one in the 'Multi-attire target IDs' "
                            "box before converting if this attire should install cleanly."
                        )

                log(
                    f"NOTE: found {len(plan.attires)} attire folders. Only the first "
                    f"({plan.attires[0].source_folder_name}) is treated as the primary "
                    "'_Default_Attire'; every other one becomes '_Alt_Attire' using the "
                    "target ID set for it above. (If there are 3+ attire folders, every "
                    "non-primary one still gets the SAME '_Alt_Attire' suffix and will "
                    "collide -- this tool currently only distinguishes primary vs. "
                    "non-primary, not a 3rd/4th attire slot.) This multi-attire naming "
                    "has not been confirmed against a real WWE2K25 example -- check the "
                    "output carefully."
                )

            blender_tmp_ctx = None
            if use_blender:
                blender_tmp_ctx = tempfile.TemporaryDirectory(prefix="wwe2k_blender_out_")
                self._run_blender_pass(plan, blender_exe, addon_dir, Path(blender_tmp_ctx.name), log, donor_folder, donor_hair, fix_hair_bones, graft_hair_bones, rigid_hair, donor_attire_as_base, use_heat_seek, graft_real_hair_bones)
            elif donor_folder is not None:
                log(
                    "  WARNING: a custom donor folder was set, but Blender skeleton "
                    "conversion is off -- the donor feature only runs as part of the "
                    "Blender pass, so it's being skipped this run."
                )

            try:
                out_path = run_conversion(plan)
                for line in plan.log:
                    log(line)
                log("")
                log(f"SUCCESS. Converted folder created at:\n{out_path}")
                self._log_queue.put("__REMINDER__")
            finally:
                if blender_tmp_ctx is not None:
                    blender_tmp_ctx.cleanup()
        except ConversionError as e:
            log(f"CONVERSION FAILED: {e}")
        except Exception as e:  # noqa: BLE001 -- surface unexpected errors to the log too
            log(f"UNEXPECTED ERROR: {type(e).__name__}: {e}")
        finally:
            # Never touch Tk widgets directly from this background thread --
            # only ever via the queue, polled on the main thread by
            # _poll_log_queue.
            self._log_queue.put("__DONE__")

    def _run_blender_pass(
        self,
        plan: ConversionPlan,
        blender_exe: Path,
        addon_dir: Path | None,
        out_dir: Path,
        log,
        donor_folder: Path | None = None,
        donor_hair_enabled: bool = False,
        fix_hair_bones: bool = False,  # "Option 2" -- confirmed broken, stripped from UI 2026-10-02
        graft_hair_bones: bool = False,  # "Option 3" -- superseded by rigid_hair, stripped from UI 2026-10-02
        rigid_hair_to_head: bool = False,
        donor_attire_as_base: bool = False,
        use_heat_seek: bool = False,
        graft_real_hair_bones: bool = False,
    ) -> None:
        """
        Runs the Blender headless armature-redirect pass for the BaseModel
        and every attire in the plan, and wires the results into
        plan.blender_basemodel_mcd / plan.blender_attire_mcds so
        run_conversion() picks them up instead of raw-byte-patching the
        source .mcd files. Any failure here is logged as a warning and the
        affected file(s) simply fall back to the byte-patch-only path --
        never aborts the whole conversion.

        donor_folder (added 2026-09-23; un-gated from mode 3 in the GUI
        2026-10-13): when set, this is a real WWE2K25 character's folder to
        use INSTEAD of the bundled generic template, both as the Blender
        armature-redirect donor and as the source of
        plan.custom_donor_basemodel_mtls -- the latter takes priority over
        Blender's own auto-generated BaseModel materials in
        _convert_base_model, which is what fixes the known head2/eye_l/
        mouthbag wrong-shader-type bug (see the 2026-10-13 Lio Rush finding
        in the project doc). This is independent of donor_hair_enabled/
        donor_attire_as_base below -- those two control the HAIR-specific
        behavior only; a donor folder with both of those off just fixes
        BaseModel materials/skeleton, with no hair transplant. When
        donor_folder is not set, behavior is identical to every previous
        version of this tool.

        donor_hair_enabled (added 2026-09-23, default OFF): also populates
        plan.donor_hair_files from the donor's attire folder, and (added
        2026-09-25) resolves donor_attire_mcd_path so the Blender pass can
        transplant the donor's real hair MESH into the output too. The
        original companion-files-only version was CONFIRMED BROKEN on a
        real test (Saya Kamitani, 2026-09-23) -- the donor's real WWE2K25
        hair turned out to be a single hair mesh piece while the WWE2K26
        source mesh has 3 separate hair material slots (hair/hair2/hair4),
        a structural mismatch that produced wrong hair textures and no
        working animation, because only the companion files were donor's;
        the mesh was still the source's mismatched one. Now that the mesh
        itself is transplanted too (see _transplant_donor_hair_mesh in
        blender_headless_convert.py), that specific mismatch shouldn't
        recur -- UNTESTED IN-GAME as of writing this revision. Left off by
        default either way, since it's still a real behavior change worth
        an active choice.

        donor_attire_as_base (added 2026-10-02): the newer, INVERTED
        pipeline -- builds each attire starting from the donor's own real
        attire .mcd (native hair AND native skeleton, already correctly
        bound together) and grafts the 2K26 source mod's clothing onto it,
        instead of keeping the source attire as the base and transplanting
        the donor's hair mesh in. See
        blender_headless_convert.py's _convert_attire_donor_base. Also
        triggers the donor-attire .mcd / companion-hair-file resolution
        below (same block donor_hair_enabled uses), even if
        donor_hair_enabled itself is off, since this mode needs that same
        donor attire .mcd as its base scene. UNTESTED IN-GAME as of
        writing.

        use_heat_seek (added 2026-10-15): the "4. Heat Seek" mode -- a
        different pipeline entirely (blender_headless_convert.py's
        _convert_one_heat_seek), used for BOTH the BaseModel and every
        attire. Takes precedence over fix_hair_bones/graft_hair_bones/
        rigid_hair_to_head (Options 2/3/4 don't apply at all in this mode).
        If donor_attire_as_base is ALSO somehow on at the same time, that
        one wins for attires -- see run_blender_conversion's own docstring
        for the exact precedence. EXPERIMENTAL / UNTESTED IN-GAME.

        graft_real_hair_bones (added 2026-10-15): the "5. Hair Graft Only"
        mode -- isolates just the hair-bone-chain graft (CONFIRMED WORKING
        in-game, 2026-10-15, Yota Tsuji) on top of the plain mode-1
        "Straight conversion" pipeline, with none of Heat Seek's other
        still-being-validated DATA_TRANSFER weight recompute. Ignored for
        any part use_heat_seek already handles (use_heat_seek takes
        priority) -- see run_blender_conversion's own docstring and
        blender_headless_convert.py's _convert_one docstring.
        """
        # Routed through converter.py's own _TEMPLATES_DIR (added 2026-10-13
        # for the standalone-.exe build) rather than this file's own
        # __file__, since that already handles the PyInstaller-frozen case
        # (sys._MEIPASS) correctly -- see its comment for why.
        donor = _TEMPLATES_DIR / "generic_2k25_basemodel" / "baseModel.mcd"
        donor_root: Path | None = None
        # Set below (donor hair carryover block) when a donor attire's own
        # *attire*.mcd is found -- used for the donor hair MESH transplant
        # (2026-09-25), not just the companion-file (.ycl etc.) carryover.
        donor_attire_mcd_path: Path | None = None
        if donor_folder is not None:
            try:
                donor_root = find_character_root(donor_folder)
            except ConversionError as e:
                log(f"  WARNING: couldn't resolve custom donor folder ({e}) -- falling back to bundled generic donor")
                donor_root = None
            if donor_root is not None:
                custom_donor_mcd = donor_root / "BaseModel" / "baseModel.mcd"
                if custom_donor_mcd.is_file():
                    donor = custom_donor_mcd
                    log(f"  Using custom donor BaseModel (EXPERIMENTAL): {donor}")
                else:
                    log(
                        f"  WARNING: custom donor folder has no BaseModel/baseModel.mcd "
                        f"at {donor_root} -- falling back to bundled generic donor"
                    )
                    donor_root = None

                custom_donor_mtls = donor_root / "BaseModel" / "baseModel.mtls" if donor_root else None
                if custom_donor_mtls is not None and custom_donor_mtls.is_file():
                    plan.custom_donor_basemodel_mtls = custom_donor_mtls
                elif donor_root is not None:
                    log(f"  WARNING: custom donor has no BaseModel/baseModel.mtls -- keeping generic BaseModel materials")

                if donor_root is not None and (donor_hair_enabled or donor_attire_as_base):
                    try:
                        donor_attire_folders = find_donor_attire_folders(donor_root)
                    except ConversionError as e:
                        log(f"  WARNING: couldn't find an attire folder in the custom donor ({e}) -- no donor hair will be used")
                        donor_attire_folders = []
                    if donor_attire_folders:
                        donor_hair_files = find_donor_hair_files(donor_attire_folders[0])
                        if donor_hair_files:
                            plan.donor_hair_files = donor_hair_files
                            if donor_attire_as_base:
                                log(
                                    f"  Found {len(donor_hair_files)} donor hair companion "
                                    f"file(s) in {donor_attire_folders[0]} -- these are the "
                                    "RIGHT companion files for this mode (the donor's own "
                                    "hair mesh+skeleton are kept untouched as the base, so "
                                    "its own .ycl etc. stay correctly matched to them): "
                                    f"{sorted(donor_hair_files.keys())}"
                                )
                            else:
                                log(
                                    f"  Found {len(donor_hair_files)} donor hair file(s) in "
                                    f"{donor_attire_folders[0]} -- CONFIRMED BROKEN on a real "
                                    f"test (mismatched hair mesh structure), enabled anyway "
                                    f"because the checkbox is on: {sorted(donor_hair_files.keys())}"
                                )
                        else:
                            log(f"  (no hair companion files found in {donor_attire_folders[0]}, skipping donor hair)")

                        # Donor hair MESH transplant (added 2026-09-25): find the
                        # donor attire's own *attire*.mcd so the Blender pass can
                        # import it and splice its real hair mesh into the source
                        # attire, replacing the source's own hair mesh entirely --
                        # same folder/extension-matching convention used
                        # everywhere else in this tool (substring "attire",
                        # case-insensitive).
                        donor_attire_mcd_matches = [
                            f for f in donor_attire_folders[0].iterdir()
                            if f.is_file() and f.suffix.lower() == ".mcd" and "attire" in f.name.lower()
                        ]
                        if donor_attire_mcd_matches:
                            donor_attire_mcd_path = donor_attire_mcd_matches[0]
                            if donor_attire_as_base:
                                log(f"  Found donor attire .mcd to use as the BASE scene: {donor_attire_mcd_path}")
                            else:
                                log(f"  Found donor attire .mcd for hair-mesh transplant: {donor_attire_mcd_path}")

                            # The transplanted/base-scene donor hair mesh
                            # references the DONOR's own texture file names --
                            # without this, _convert_attire()'s texture copy
                            # only pulls from the SOURCE attire's Textures
                            # folder, and the donor hair would render with
                            # missing/wrong textures in-game.
                            # _fill_missing_donor_textures() only fills in
                            # textures the source doesn't already have, so
                            # this is safe to set even if some names collide.
                            donor_attire_textures_dir = donor_attire_folders[0] / "Textures"
                            if donor_attire_textures_dir.is_dir():
                                plan.donor_attire_textures_dir = donor_attire_textures_dir
                                log(f"  Donor attire textures dir for donor hair: {donor_attire_textures_dir}")
                            else:
                                log(
                                    f"  WARNING: no Textures folder found alongside the donor "
                                    f"attire .mcd ({donor_attire_folders[0]}) -- the donor hair "
                                    "mesh may be missing textures in-game"
                                )
                        else:
                            log(
                                f"  WARNING: no *attire*.mcd found in {donor_attire_folders[0]} -- "
                                "donor hair will have nothing to use this run "
                                "(companion files above, if any, are unaffected)"
                            )
                elif donor_root is not None:
                    log("  (donor hair carryover left off -- opt in via one of the donor-hair checkboxes to use it)")
        if not donor.is_file():
            log(f"  WARNING: bundled donor BaseModel not found at {donor} -- skipping Blender pass entirely")
            return

        character_root = plan.source_root
        source_basemodel = character_root / "BaseModel" / "baseModel.mcd"
        out_basemodel = out_dir / "basemodel_out.mcd"

        attire_jobs = []
        attire_out_paths: dict[str, Path] = {}
        for attire in plan.attires:
            src_dir = character_root / attire.source_folder_name
            # Match by extension + "attire" anywhere in the name (case-
            # insensitive) -- same fix as converter.py's file_rules, see
            # find_attire_folders for why a fixed "_Attire.mcd" suffix isn't
            # safe to assume.
            matches = [
                f for f in src_dir.iterdir()
                if f.is_file() and f.suffix.lower() == ".mcd" and "attire" in f.name.lower()
            ]
            if not matches:
                log(f"  WARNING: no *attire*.mcd file found in {src_dir} -- skipping Blender pass for this attire")
                continue
            out_path = out_dir / f"attire_{attire.source_folder_name}_out.mcd"
            attire_jobs.append(AttireJob(source_mcd=matches[0], out_mcd=out_path))
            attire_out_paths[attire.source_folder_name] = out_path

        donor_attire_as_base_active = bool(donor_attire_as_base and donor_attire_mcd_path)
        log(
            "Running Blender headless (this can take a while)... "
            f"[heat seek: {'ON (EXPERIMENTAL)' if use_heat_seek else 'OFF'}] "
            f"[hair graft only: {'ON' if (graft_real_hair_bones and not use_heat_seek) else 'OFF'}] "
            f"[rigid hair to head: {'ON' if rigid_hair_to_head else 'OFF'}] "
            f"[donor attire as base: {'ON' if donor_attire_as_base_active else 'OFF'}] "
            f"[donor hair mesh transplant: "
            f"{'ON' if (donor_attire_mcd_path and not donor_attire_as_base_active) else 'OFF'}]"
        )
        result = run_blender_conversion(
            blender_exe=blender_exe,
            addon_parent_dir=addon_dir,
            donor_basemodel_mcd=donor,
            source_basemodel_mcd=source_basemodel,
            out_basemodel_mcd=out_basemodel,
            attires=attire_jobs,
            fix_hair_bone_weights=fix_hair_bones,
            graft_hair_bones=graft_hair_bones,
            rigid_hair_to_head=rigid_hair_to_head,
            donor_attire_mcd=donor_attire_mcd_path,
            use_donor_attire_as_base=donor_attire_as_base_active,
            use_heat_seek=use_heat_seek,
            graft_real_hair_bones=graft_real_hair_bones,
        )
        for line in result.log:
            log(f"  [blender] {line}")
        if result.stderr.strip():
            log("  [blender stderr]\n" + result.stderr.strip())
        # DIAGNOSTIC (added 2026-09-25): our own script's structured log
        # (above) only contains lines we explicitly logged. If Blender
        # itself, the addon, or its compiled C library prints a warning
        # (e.g. about a duplicate/degenerate/skipped mesh) via plain
        # print(), it goes to stdout and was previously discarded entirely
        # -- invisible both here and to the user. Surface any such lines
        # now (full raw stdout is extremely verbose from Blender's own
        # startup banner, so only lines that look like a warning/error/skip
        # are shown; say how many total lines were filtered out either
        # way, so a silent capture failure is distinguishable from "nothing
        # interesting happened").
        if result.stdout.strip():
            stdout_lines = result.stdout.splitlines()
            flagged = [
                ln for ln in stdout_lines
                if any(k in ln.lower() for k in ("warn", "error", "fail", "skip", "duplicate", "discard"))
            ]
            if flagged:
                log(
                    f"  [blender stdout -- {len(flagged)} flagged line(s) of "
                    f"{len(stdout_lines)} total captured]\n" + "\n".join(flagged)
                )
            else:
                log(f"  [blender stdout: {len(stdout_lines)} line(s) captured, none flagged as warning/error]")

        if not result.ok:
            log(f"  WARNING: Blender pass failed ({result.error}) -- falling back to byte-patch-only path for this run.")
            return

        if out_basemodel.is_file():
            plan.blender_basemodel_mcd = out_basemodel
            # blender_headless_convert.py requests generate_mtls=True for
            # BaseModel exports too now (added 2026-10-02 -- see that
            # script's export_args_basemodel comment) so its companion
            # materials file, with slot names guaranteed to match this
            # character's own real mesh object names, lands next to the
            # .mcd the same way attire's already does. Pick it up here if
            # it landed; converter.py falls back to the custom donor file
            # (if set) or the generic template if not.
            generated_basemodel_mtls = out_basemodel.with_suffix(".mtls")
            if generated_basemodel_mtls.is_file():
                plan.blender_basemodel_mtls = generated_basemodel_mtls
        for name, path in attire_out_paths.items():
            if path.is_file():
                plan.blender_attire_mcds[name] = path
                # blender_headless_convert.py requests generate_mtls=True for
                # attire exports and writes the companion materials file next
                # to the .mcd, same stem, ".mtls" extension -- pick it up here
                # if it landed (see that script's log lines for confirmation
                # either way; converter.py falls back gracefully if missing).
                generated_mtls = path.with_suffix(".mtls")
                if generated_mtls.is_file():
                    plan.blender_attire_mtls[name] = generated_mtls
        log("Blender pass finished OK.")


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
