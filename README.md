# WWE2K26 -> WWE2K25 Custom Character Backporter

A Windows desktop app (Python + Tkinter) that takes a WWE2K26 custom
character mod folder and converts it into a folder laid out and fixed up
for WWE2K25 -- real skeleton rebinding, working hair, and correct materials,
not just a renamed copy.

This has been run end-to-end and confirmed working in-game across multiple
real characters (including ones with several separate hair pieces), so the
core conversion pipeline described below is the actual, current behavior,
not a work-in-progress. The "Known limitations" section near the bottom
lists what's still genuinely rough around the edges.

## What it does

- **Skeleton rebinding (via headless Blender).** Imports a real, unmodified
  WWE2K25 `baseModel.mcd` into the same scene as the raw WWE2K26 source (for
  both the BaseModel and every attire) and exports targeting WWE2K25. The
  WWE2K_MDL addon's own cross-version auto-redirect detects the real 2K25
  armature and rebinds the 2K26 mesh onto it -- this is what actually fixes
  the crashes/geometry distortion a raw byte-copied port produces. No
  Blender window ever opens; it's driven headless in the background.
- **Hair that actually animates.** Grafts the character's real hair-bone
  chain from the source armature onto the output armature (instead of
  leaving hair mesh bound to bones that don't exist in WWE2K25, which
  renders it rigid/static). Confirmed in-game on characters with a single
  hair piece and characters with several separate hair mesh pieces in the
  same attire.
- **BaseModel materials donor (optional).** By default, BaseModel materials
  are built from a bundled generic WWE2K25 template (based on a real CM
  Punk export), run through an auto-rename pass that also fixes a known
  head/eye_l/mouthbag wrong-shader-type bug automatically -- no setup
  needed. You can instead point the app at any real WWE2K25 character
  folder to use its materials/skeleton as the donor for one specific
  conversion; it doesn't need to be related to the character being
  converted.
- **Attire materials built from known-good templates.** Rather than using
  the WWE2K_MDL addon's own material export (which has a confirmed bug
  wiring some texture pointers to the wrong table entries), every attire
  material is built fresh from a bundled, known-good default template
  (gear or hair, matched per slot), with the real texture names substituted
  in. This sidesteps that bug entirely.
- **Multi-attire support.** If the source character has more than one
  attire folder, the first is converted as the primary `_Default_Attire`
  (using the Roster Slot ID field); each additional one gets its own
  editable target-ID field in the GUI and is converted as `_Alt_Attire`.
- **Everything else**: folder/file renaming to WWE2K25 conventions,
  `Materials/*.jmtl` always dropped (no real WWE2K25 example ever ships
  this folder), `Textures/*.dds` copied through, a placeholder `.hpl` mask
  generated when the source has none.

Internally, a few other conversion pipelines (a "donor attire as base" mode,
an experimental "Heat Seek" mode, rigid hair-to-head pinning) still exist in
`converter.py`/`blender_headless_convert.py` from earlier development, but
aren't exposed in the GUI -- the app is hardcoded to the "hair graft" mode
described above, since that's the one that's actually held up in-game
across every character tested.

## Requirements

- Python 3.9+ on Windows (from python.org, with Tkinter -- the default
  install option). No `pip install` needed; everything here is standard
  library.
- Blender (any recent version your WWE2K_MDL addon supports), with the
  WWE2K_MDL addon **enabled in Blender normally** (Edit > Preferences >
  Add-ons, ticked on). The app finds and enables it the same way Blender
  itself does -- nothing extra to configure.
- For the manual finishing steps after conversion (see below): MaterialEditor.exe
  and a `.ycl` editor capable of exporting in WWE2K25 format. Both are
  third-party WWE2K modding tools, not bundled with this app.

## Running it

1. Unzip this folder anywhere.
2. Run `python app.py` (or build a standalone `.exe` -- see below).
3. Fill in:
   - **WWE2K26 character folder** -- the source mod's `<charname>_A0`-style
     folder.
   - **Output folder (parent)** -- where the converted folder gets created.
   - **Roster slot ID** / **Roster name** -- the WWE2K25 slot you're
     installing into, and a label for the output folder.
   - If the character has more than one attire, click **Rescan** under
     "Multi-attire target IDs" and set a real WWE2K25 slot ID for each
     non-primary one.
   - **BaseModel donor (optional)** -- leave blank to use the bundled
     generic template, or point it at a real WWE2K25 character folder.
   - **blender.exe** -- browse to it if the app didn't auto-detect it.
   - **Addon folder** -- leave blank if WWE2K_MDL is enabled in Blender
     normally; only needed as a fallback for a non-standard install.
4. Click **Convert**. This can take a while -- Blender runs in the
   background importing/exporting full models. Watch the log pane for
   `[blender] ...` lines.
5. A pop-up will remind you of the manual steps below once conversion
   finishes.

### Building a standalone .exe

Run `build_exe.bat` (in this same folder) on a Windows machine. It installs
PyInstaller if needed and produces `dist\WWE2K_Backporter.exe` -- a single
file you can copy anywhere and double-click, no Python install required
alongside it. Build it on the same OS you'll run it on.

## After conversion: required manual steps

The converter can't finish these two steps itself -- they depend on
external tools (MaterialEditor.exe, a `.ycl` editor) that don't expose a
scriptable interface. **Skip either of these and the character will not
load correctly in-game.**

1. **Open the attire's `.ycl` file in your `.ycl` editor and export it as
   WWE2K25.** The source file is still in its WWE2K26 format.
2. **Open both `attire.mtls` and `basemodel.mtls` in MaterialEditor.exe and
   resave, alternating between the two files, until the `@ref` texture
   hash fields are populated in both.** This app deliberately zeroes those
   fields when it builds materials from the default templates;
   MaterialEditor's own "AutoSyncRefs" fills them in with real hashes on
   save -- but only when the file being saved lives inside a folder path
   that contains one of its recognized game-folder names (`Characters` is
   the natural one, matching the game's own install layout under
   `.../discovery/Backports/Characters/<name>/...`). If you're editing from
   somewhere else, copy the output into that location *first*. It can take
   more than one open-and-resave pass before every field populates --
   that's normal, keep resaving until the log/no-longer-zero values
   confirm it.

## Known limitations

- **Arms/shoulders/neck muscle bones (Pec/Lat/Delt/Neck subdivisions)
  aren't remapped.** Only 4 bones with a clean 1:1 name match between the
  two games' skeletons are renamed; WWE2K26's subdivided muscle-flex bones
  in those regions have no WWE2K25 equivalent, so vertex groups referencing
  them export with dropped weight. Cosmetic -- that part of the mesh stays
  rigid at its bind pose instead of flexing -- not a crash or a missing
  part.
- **No automatic clipping mask when the source has no `.hpl` file.** The
  app writes a genuine empty placeholder instead, which means no
  body/attire clipping protection (e.g. legs may clip through tights) until
  a real `.hpl` is hand-built in Blender and dropped into the source
  attire folder before converting.
- **Texture-filename mismatches can pull the wrong texture.** If a source
  character's mesh/material is named e.g. `head` but its own texture files
  are authored under a different prefix (e.g. `head2_color.dds`), the
  gap-filler that completes missing texture names can pull a donor
  character's texture under the expected name instead of the source's own
  file sitting right next to it under the "wrong" name. Worth a quick check
  of the source's own `Textures` folder on any custom-donor conversion.
- **3+ attires collide on naming.** The tool currently only distinguishes
  "primary" vs. "non-primary" attire -- every non-primary attire becomes
  `_Alt_Attire`, so a character with 3 or more attire folders will have the
  2nd and 3rd (etc.) collide on that same suffix.
- **The `@ref` manual resave step above** isn't optional and can take more
  than one pass -- see "After conversion" above.

## Files

- `app.py` -- the GUI.
- `converter.py` -- the conversion rules and output folder/file structuring.
- `blender_headless_convert.py` -- runs *inside* Blender
  (`blender --background --python`): donor-armature import, bone renaming/
  hair-bone grafting, and export, for the BaseModel and each attire.
- `blender_runner.py` -- launches Blender as a subprocess to run the above
  and reports back what happened.
- `mcd_fix.py` -- standalone `.mcd` terminator/size-field fix logic.
- `templates/generic_2k25_basemodel/` -- the bundled real WWE2K25 donor
  BaseModel (`.mcd`/`.mtls`) used when no custom donor is set.
- `templates/generic_2k25_attire_defaults/` -- the known-good default
  material templates (`gear`/`hair`) attire materials are rebuilt from.
- `templates/default_attire.hpl` -- the empty-mask placeholder used when a
  source attire has no `.hpl` file.
- `build_exe.bat` / `WWE2K_Backporter.spec` -- standalone `.exe` packaging.
