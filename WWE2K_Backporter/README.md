# WWE2K26 -> WWE2K25 Custom Character Backporter (v2.0 -- no-hair / male characters)

A Windows desktop app (Python + Tkinter) that takes a WWE2K26 custom
character mod folder and produces a folder restructured and skeleton-fixed
for WWE2K25.

**Scope of this version**: characters with no dynamic hair simulation (no
`.ycl` file) -- i.e. male wrestlers, or anyone with static/no hair geometry.
Dynamic hair (`.ycl`) is a separate, still-unsolved problem (see the project
doc's Athena investigation) and is deliberately out of scope here -- if the
source character has a `.ycl` file, this tool will still convert everything
else correctly, it just won't do anything special for the hair.

## What's new in v2 (read this first)

Every character tested through v1's byte-patch-only pipeline (Blake
Christian) still had real, visible problems in-game even after the version-
tag and per-material fixes: the mesh stayed bound to the *2K26* skeleton,
which WWE2K25 doesn't recognize, causing crashes and geometry distortion.
Fixing that for real -- not just renaming bytes, but actually rebinding the
mesh onto a real WWE2K25 skeleton and recomputing skin weights -- turned out
to require either Blender's own mesh/armature tooling or the game's native
model library. There is no safe way to do it with plain byte patching (the
addon's own *disabled* auto-remap code confirms this: real bone-topology
differences between the two games need actual weight recomputation).

So v2 adds a **Blender headless integration**: the app can now drive your
existing Blender + WWE2K_MDL addon install in the background (no Blender
window ever opens) to do the real skeleton work, then applies the already-
validated byte-level fixes (terminator patch, generic BaseModel materials)
on top. This mirrors, step for step, the manual Blender workflow already
confirmed working in-game on Blake Christian.

**This is a first-draft implementation of that integration, written by
reading your WWE2K_MDL addon's source, not by running it** -- I don't have
Blender or your addon available in the environment where I write code. The
BYTE-LEVEL parts (terminator fix, hollow-`.mtls` detection, generic
BaseModel materials) are all tested against your real example files and are
solid; the BLENDER-DRIVING part is the new, less-tested piece.

**Known-fixed bug (first real run, 2026-09-20 -- Lio Rush test)**: the
Blender pass crashed immediately with `AttributeError: 'Object' object has
no attribute 'mdl_properties'`. Cause: the script only `import`ed the
addon's Python modules directly, without ever calling the addon's real
`register()` -- so custom properties the addon registers on Blender's own
`Object` type (like `mdl_properties`) never existed, and the import code
crashes the instant it touches one. **When this happens, the app silently
falls back to the old byte-patch-only path for the WHOLE conversion** --
which is a much bigger problem for a BaseModel file that uses the newer
`CTG!` chunk layout instead of `MDL!` (seen on Lio Rush): the byte-patch
path has no way to touch that layout at all, so it gets copied through 100%
UNCHANGED, still a raw, un-retargeted WWE2K26 file -- **this is very likely
what actually crashed the game to desktop**, independent of anything to do
with skeleton binding. Fixed by properly enabling the addon the same way
Blender's own Preferences > Add-ons checkbox does (`addon_utils.enable()`),
which registers everything correctly and matches how the addon is already
installed on your machine.

**Known-fixed bugs (retest, 2026-09-20 -- Lio Rush, Blender pass fully
succeeded this time)**: loaded and played in-game -- BaseModel looked
correct, no bone weight distortion -- but with **no attire at all**. Two
separate real bugs, found by inspecting the actual output bytes rather than
trusting the success-looking log:

1. **Size-field bug, new variant.** Every `.mcd` this addon exports has a
   leading size-field bug (documented below under "Terminator fix") -- but
   until now it always co-occurred with a missing `END!` terminator, so the
   fix only checked for that combination. This run's Blender-exported files
   *did* have a correctly-formed `END!` terminator already, with the same
   wrong size field anyway -- a case the old check didn't recognize, so it
   logged "terminator already correct" and left the actual defect in place.
   Fixed: the two symptoms are now checked independently.
2. **No attire because the source attire's `.mtls` was hollow.** The tool
   already detects hollow/placeholder attire `.mtls` files (near-empty,
   no real texture data) and warns -- but previously just shipped the bad
   file with a warning, requiring you to manually re-export via Blender's
   "Generate MTLs" checkbox and swap it in yourself. **That manual step is
   now automatic**: the Blender pass requests `generate_mtls` for every
   attire export (same mechanism as that checkbox) and uses the result
   instead of the source's own file. This is very likely why Lio Rush's
   attire didn't render at all.

## Requirements

- Python 3.9+ on Windows (from python.org, with Tkinter -- the default
  install option). No `pip install` needed; everything here is standard
  library.
- Blender (any recent version your WWE2K_MDL addon supports) installed
  somewhere the app can find `blender.exe`.
- Your WWE2K_MDL addon **enabled in Blender normally** (Edit > Preferences >
  Add-ons, ticked on) -- that's it, nothing extra to set up. The app finds
  and enables it the same way Blender itself does.

## Running it

1. Unzip this folder anywhere.
2. Run `python app.py`.
3. Fill in:
   - **WWE2K26 character folder** -- the `<charname>_A0` folder.
   - **Output folder (parent)** -- where the converted folder gets created.
   - **Roster slot ID** / **Roster name** -- same as before.
   - **blender.exe** -- browse to it. The app tries to auto-fill a common
     install path, but check it.
   - **Addon folder** -- leave this **blank** if your WWE2K_MDL addon is
     enabled in Blender the normal way (Preferences > Add-ons) -- the app
     finds it automatically. Only fill this in as a fallback if the addon
     lives somewhere non-standard and auto-detection fails (you'll see that
     clearly in the log: `addon_utils.enable(...) did not find it...`).
   - Leave "Use Blender..." checked (recommended). Unchecking it falls back
     to the old byte-patch-only path, which will very likely still crash or
     distort in-game the same way v1 did, since it skips skeleton rebinding
     entirely -- only use it if the Blender pass fails and you want output
     anyway to inspect.
4. Click **Convert**. The Blender pass can take a while (it launches
   Blender in the background, imports two full models, exports two more) --
   watch the log pane for `[blender] ...` lines.

## Building a standalone .exe (added 2026-10-13)

If you'd rather not run `python app.py` from source every time, run
`build_exe.bat` (in this same folder) on your Windows machine. It installs
PyInstaller if needed and produces `dist\WWE2K_Backporter.exe` -- a single
file you can copy anywhere and double-click, with no `.py` files or
`python` install required alongside it. It still calls out to your
existing Blender install exactly as before; nothing about the actual
conversion changes.

Build it on the same machine (really, the same OS) you'll run it on --
PyInstaller packages for whatever platform it runs on, so this has to be a
native Windows build, not something built elsewhere and copied over.

## What it does now

**Skeleton (new, via Blender)**: for the BaseModel and each attire, imports
a bundled real, unmodified WWE2K25 `baseModel.mcd` (your validated "generic"
donor -- see below) into the same Blender scene as the raw 2K26 source, then
exports targeting WWE2K25. The addon's own cross-version auto-redirect
detects the real 2K25 armature present and rebinds the 2K26 mesh onto it --
this is the exact mechanism that fixed Blake Christian's crash/distortion
manually. On top of that, the app renames the 4 vertex groups that have a
clean 1:1 name mapping between the two games' bone tables (`H_Delt_L_OS01`,
`H_Delt_R_OS01`, `H_Bicep_L`, `H_Bicep_R`), so those specific bones deform
correctly too.

**Not attempted**: the harder case -- bones that don't have a 1:1 name
match because 2K26 subdivides them into multiple new bones (the Pec/Lat/Delt
region: `H_Pec_L_1/2/3`, `H_Lat_L_1/2/3`, `H_Delt_L_1/2`). That needs real
weight redistribution across the new bone subdivisions, which was never
validated even in manual testing. **Expect possible minor arms/shoulders
deformation issues to remain** -- this matches the last confirmed manual
test result exactly, it's a known, pre-existing gap, not a regression.

**BaseModel materials**: uses a bundled, verbatim real WWE2K25
`baseModel.mtls` (from Blake Christian's own validated `312_CM_Punk`
WWE2K25 reference) for every character, instead of touching the source's
own file. Confirmed (project doc, 2026-09-19): BaseModel materials
reference only generic, character-agnostic texture names, so one known-good
real file works for anyone. This is also more reliable than the addon's own
"Generate MTLs" feature, which has a confirmed (if usually minor) bug
tagging a few records with the wrong shader type.

**Attire materials**: when the Blender pass runs, the app now asks the addon
to **generate a fresh `.mtls`** for every attire (same mechanism as its own
"Generate MTLs" export checkbox) and uses that instead of the source's own
file. This is the automatic fix for a real, confirmed bug: a source attire
`.mtls` can be **hollow/placeholder** (near-empty records, no actual texture
data, still tagged as if normal) -- and the game doesn't error on this, it
just renders **no attire at all** (confirmed on Lio Rush, 2026-09-20). The
generated file is itself checked with the same hollow-file heuristic as a
safety net; if the Blender pass didn't run (or that attire's export didn't
produce one), the tool falls back to the old behavior -- patch the source's
own `.mtls` and warn if it looks hollow.

**Known remaining issue (found 2026-09-20, Ricochet test): shader-type
mismatches in generated attire materials.** generate_mtls correctly
discovers every real material slot (even ones a hollow source file never
declared at all, e.g. a whole missing "jacket" record) and populates real
texture references -- confirmed a genuine improvement, not just noise. But
it doesn't always tag the right **shader type** per material -- e.g. a
jacket's alpha-cutout "feathers" got tagged with the plain opaque shader
and rendered as solid geometry instead of thin/see-through. This matches
this project's own earlier, already-documented finding that the addon's
Generate MTLs feature has a "confirmed (if usually minor) bug tagging a
few records with the wrong shader type" -- evidently not always minor.
**Tried and reverted, 2026-09-20**: loading real textures during attire
import (`load_textures=True`) on the theory that shader-type inference
might depend on the material's node graph having a real texture hookup.
Confirmed no effect -- and confirmed it wasn't even a fair test: Blender's
own image loader failed to decode every single source `.dds` file
regardless (`IMB_ibImageFromMemory: unknown file-format`), a separate,
unrelated limitation of Blender's stock DDS support for whatever
compression these use. Reverted to `load_textures=False` for attire too.

Also found a second, more telling data point: `combinations_ALPHA` and
`combinations2_ALPHA` -- two source materials with the identical naming
convention -- got **different** shader types in the same export
(`yCh_hair` vs `yCh_gear`). That rules out a simple name-pattern fix on
this tool's side (there's nothing consistent to pattern-match against) and
points at this being a real inconsistency inside the addon's own DLL
material writer, not something reachable from the Python/Blender-scripting
layer this tool operates at. **No further automatic fix attempted** --
next step, if you want to chase this further, is probably comparing
against a *manual*, in-Blender "Generate MTLs" export (not headless) on
the same source to see if the interactive path gets the same wrong
result (confirms it's a DLL-level bug, not something about running
headless) or a different one (points at something headless-specific worth
investigating further).

**Separate issue, also seen twice now: BaseModel material color mismatches
(teeth, eye/eyelash glow).** Not yet investigated as a root cause -- the
bundled generic `baseModel.mtls` template may not be as fully
character-agnostic as the project's earlier testing found. Unrelated to the
attire shader-type issue above (different files, different mechanism). See
"Custom donor character" below for a new, experimental workaround.

## Custom donor character (optional, EXPERIMENTAL, added 2026-09-23)

**Update 2026-10-13: this field now works in EVERY conversion mode, not
just "3. Donor Hair".** It used to live inside the "Conversion mode" box
and only be enabled when mode 3 was selected. It's now its own box,
"BaseModel materials donor (optional, works in any mode)", right below
"Conversion mode" in the GUI -- useful with "1. Straight conversion" and
"2. Pinned Hair" too, with no hair transplant involved unless you're on
mode 3.

**Update 2026-10-13 (same day): you usually don't even need to set this
field anymore.** The known head2/eye_l/mouthbag wrong-shader-type bug
(see "Known limitations" below) is now fixed automatically, with NO donor
folder required: when this field is left blank, the bundled generic
BaseModel template is now run through the exact same auto-rename logic
described below (as if it were a donor), instead of being copied in
dumb/verbatim like before. Set this field explicitly only if you have a
specific reason to prefer a different real character's materials (e.g.
the bundled template's teeth/eyelash color doesn't look right for this
particular character -- see the color-mismatch note below).

Instead of always using the bundled generic BaseModel template (a real
WWE2K25 Blake Christian export) as the Blender armature-redirect donor and
BaseModel-materials source, you can point the app at **any real WWE2K25
character folder** to use as the donor for one specific conversion -- it
doesn't need to be related to the character you're converting; only the
materials file structure matters. This was added at detourne's request after
finding a real WWE2K26 character (SayaKamitani) who already has her own
real WWE2K25 folder with a similar enough hairstyle to be useful as a
donor -- see the project doc for the full folder discovery notes.

Field in the GUI: **"Real WWE2K25 character folder"**, in the "BaseModel
materials donor" box. Point it at a real WWE2K25 character's folder (the
`<id>_Name` folder itself, or its parent -- same folder-resolution rule as
the main source folder). Leave it blank and everything behaves exactly as
before (BaseModel materials come from Blender's auto-generated file, or
the bundled generic template as a fallback). This only takes effect when
Blender skeleton conversion (above) is also on, since it's wired into that
pass.

When a donor folder is set, three things change for that run:

1. **Armature donor.** The donor's own `BaseModel/baseModel.mcd` is used as
   the Blender armature-redirect donor instead of the bundled generic one.
   Confirmed working (Saya Kamitani, 2026-09-23) -- body and attire loaded
   in-game with correct weighting, no distortion.
2. **BaseModel materials.** The donor's own `BaseModel/baseModel.mtls` is
   copied in as-is (no version patching needed -- it's already a real
   WWE2K25 file) in place of the bundled generic template. Targets the
   teeth/eye/eyelash color mismatch issue above.

   **First real test found a real bug, now fixed (2026-09-23, Saya
   Kamitani): completely black/untextured face.** Root cause, confirmed by
   byte-level string extraction from the donor's `.mtls`: it references
   texture channels `head_color`/`head_nrm` by name, but that particular
   WWE2K26 source mod's own `BaseModel/Textures` only ships
   `head2_color`/`head2_nrm` (a texture-naming variant that mod's own
   author used -- her real WWE2K25 release uses the plain `head_`
   naming instead). So the game looked for `head_color.dds` in the output
   and found nothing at all. **Fix**: when a custom donor is set, after
   copying the source's own textures as always, any texture *filename* the
   donor's `Textures` folder has that the source didn't provide is now also
   copied in as a fallback (never overrides a source-provided file of the
   same name). Safe specifically because this only runs for a real,
   same-character custom donor -- never the generic bundled template, whose
   own textures belong to a different character and must never be painted
   onto someone else's face. Verified with a synthetic test (donor fills
   the missing name, source's own texture of a name both have still wins).
   **Not yet re-confirmed in-game after this fix.**
3. **Attire hair files (opt-in, OFF by default as of 2026-09-23).** Dynamic
   hair (`.ycl`) has always been out of scope for this tool -- copied
   through unmodified at best, never actually converted. This donor feature
   attempted, for the first time, to carry the donor's own real WWE2K25 hair
   companion files (`.ycl`, `.hair_ywa`, `.hair_corr_ywa`, `.other_ywa`,
   `.other_corr_ywa`) onto the converted attire.

   **CONFIRMED BROKEN on a real test (Saya Kamitani, 2026-09-23): wrong hair
   texture, animation completely broken.** Root cause, confirmed by
   byte-level inspection of both `.mtls` files: the donor's real WWE2K25
   hair is a single mesh/material ("`hair_ALPHA:skinned`", one hair piece),
   but this particular WWE2K26 source mesh has **three** separate hair
   material slots (`hair_ALPHA`, `hair2_ALPHA`, `hair4_ALPHA`) -- a
   genuinely different hair mesh structure, not just a different texture.
   The donor's `.ycl`/`.hair_ywa`/`.hair_corr_ywa` files are per-vertex
   dynamic-hair simulation data bound to the donor's own specific mesh
   topology; they cannot be swapped onto a structurally different hair mesh
   and work correctly, no matter how similar the two hairstyles look
   cosmetically. **This is now believed to be a fundamental limitation of
   file-swapping at this layer, not a bug fixable with a small patch** --
   matches this project's original "dynamic hair is a separate, still-
   unsolved problem" characterization. The GUI checkbox for this
   ("Also carry over the donor's hair files") now defaults to **off**; the
   BaseModel-materials donor feature above works independently of it.

If any part of the donor folder is missing or malformed (no BaseModel, no
`.mtls`, no hair files found), that specific piece silently falls back to
the normal behavior (generic template / no hair override) with a warning in
the log -- never aborts the whole conversion.

### Donor Hair mode (the UI's 3rd "Conversion mode" radio option) -- manual `.ycl` tassel-merge required

This supersedes item 3 above (the old, confirmed-broken "also carry over
the donor's hair files" checkbox) -- the current "Donor Hair" radio option
in the UI uses the donor's own attire as the BASE scene and grafts the
source attire's clothing onto it, keeping the donor's own native hair mesh
untouched (`donor_attire_as_base` / `_convert_attire_donor_base`
internally). This works for the hair itself.

**But a real in-game test (Saya Kamitani, 2026-10-03) found that the
donor's `.ycl` -- copied in wholesale, since it's correct for the donor's
own hair physics -- has no idea about the source attire's own cloth/tassel
dynamics nodes (e.g. armband, belt, shoes, or tights tassels). Those nodes
just don't exist in the donor's file, so affected tassels render with no
dynamics at all (they don't animate), even though the donor's hair animates
correctly.**

**Required manual fix, every time, until this is automated (confirmed
working by detourne, 2026-10-03):** open the output `660_Attire.ycl`-style file
in YclEditor.exe and manually add the source attire's own tassel/cloth
dynamics nodes back in (copied from the original 2K26 source `.ycl`),
leaving the donor's hair nodes as they are. The converter's own log will
remind you of this every time Donor Hair mode runs with a donor `.ycl` set.

**Why this isn't automated**: two independent investigations (string-mining
YclEditor.exe's binary, and byte-diffing a real hand-merged file against
the donor-only original) both confirmed that roughly 99.7% of a `.ycl` file
is an unmapped bulk region of physics data whose relationship to the small,
well-understood name tables at the file's tail isn't known -- and that
YclEditor.exe (the one tool that can safely write this format) has no
command-line, batch, or scripting interface to drive instead of its GUI.
See the project's findings doc for the full investigation. Not expected to
change without either fully reverse-engineering that bulk region (high
risk of subtle, hard-to-detect corruption) or getting access to the Pro
Wrestling Mods community's own `.ycl`/`.jsim` tooling knowledge.

### Donor hair MESH transplant (added 2026-09-25, UNTESTED IN-GAME)

Item 3 above ("Attire hair files") was confirmed broken because it only ever
carried over the donor's *companion* files (`.ycl`, `.hair_ywa`, etc.) while
leaving the source's own hair mesh geometry in place -- the companion files
are per-vertex simulation data bound to the donor's own mesh topology, so
swapping them onto a structurally different source hair mesh (different
material-slot count, different vertex layout) can never work.

At detourne's request, the donor hair checkbox now does something more
ambitious: it transplants the donor's **actual hair mesh geometry** out of
the donor's own `*attire*.mcd` and into the output attire, **replacing the
source's own hair mesh object(s) entirely**, before then carrying over the
companion files as before. With both the mesh and its companion files now
coming from the same donor character, the structural mismatch that broke
item 3 shouldn't be possible any more -- because there's no longer a
source-side hair mesh left to mismatch against.

How it works (`_transplant_donor_hair_mesh` in `blender_headless_convert.py`,
runs during the Blender pass, attire-only, before any of the hair-bone-fix /
hair-bone-graft / rigid-hair-to-head options):

1. The donor's own attire `.mcd` is imported once per job (found by the same
   `*attire*.mcd` name-matching convention used everywhere else in this
   tool), and any mesh object in it with `"hair"` in its object name is
   collected as a transplant candidate.
2. For each source attire, the source's own hair mesh object(s) are found
   (same heuristic as the rigid-hair-to-head option: any mesh with at least
   one `Hair_bNN`-style vertex group) and **removed**.
3. Each donor hair candidate is copied and linked into the scene in the
   source hair mesh's place.
4. **v2 fix (2026-09-25, see below): the donor's own real hair BONES are
   grafted into the donor armature before the mesh is reparented onto it.**
5. The mesh copy is then reparented onto the donor armature (`.parent` and
   its Armature modifier's `.object` both repointed).
6. Textures: the transplanted mesh references the *donor's* texture file
   names, so `_convert_attire()` now also fills in any texture the source
   doesn't already provide from the donor attire's own `Textures` folder
   (same merge-safe, never-overrides-the-source-file approach already used
   for the custom-donor BaseModel-materials case above).

If the source attire has no hair mesh to begin with, the transplant is
skipped (nothing to replace). If the donor has no importable hair mesh
object, the source's own (known-problematic) hair mesh is left in place
rather than being deleted with nothing to put back, and a warning is logged.

#### v1 bug, found by the first log-only test (Kosei Fujita, 2026-09-25)

v1 just reparented the copied donor hair mesh straight onto the donor's main
BaseModel armature (207 bones) and hoped its vertex groups would resolve.
The first log-only diagnostic run proved that wrong: **26 of the donor hair
mesh's 27 vertex groups** (`Hair_b01`/`Hair_b02`/`Hair_b05` chain links, plus
a `J_Hair_b` root) **didn't match any real bone** on that armature at all --
per `Blender/blender_utils.py`'s `getBoneIndex`/`getVertexWeights` (the same
weight-writing mechanism traced earlier in this project for the rigid-hair
option below), a vertex's weight is only written if its vertex group's name
matches a real bone on whatever armature the mesh is parented to at export
time. So ~96% of the transplanted mesh's weight data would have silently
exported as zero -- the exact same "stretched pillar" failure Option 4 hit
on its own first (narrow) attempt, except here there's no rigid-pin
fallback, since the entire point of this feature is to keep real motion.

Root cause: a donor character's real dynamic hair bones don't live on the
main BaseModel skeleton at all -- they live on a **separate, attire-embedded
armature** that gets created fresh every time an attire `.mcd` with embedded
skeleton data is imported (confirmed directly in the log: the donor hair
object was "originally parented to `Skeleton.001`", a second armature
object distinct from the donor's main `Skeleton`). That second armature is
a real, working WWE2K25 rig for that exact hairstyle -- unlike the source's
own broken hair bones (which don't exist as real data *anywhere* in this
pipeline, forcing the rigid-hair-to-head and hair-bone-graft options below
to either pin rigidly or synthesize positions from mesh vertex averages), we
already have the donor's actual rest-pose bone transforms sitting right
there.

**v2 fix**: a new function, `_graft_donor_hair_bones`, copies those real
bones (head, tail, *and* roll -- not just position, a lesson learned the
hard way across the hair-bone-graft option's 5 revisions) from the donor's
own attire-embedded armature into the donor's main armature, walking each
bone's *real* parent chain (not a naming-convention guess) until it reaches
a bone that already exists by name on the main armature -- normally a real
shared body bone like `J_Head`, since both armatures are two separate
exports of the same real WWE2K25 character. The mesh is then reparented
only after this graft, so its vertex groups resolve against real bone data
instead of nothing. The log now reports, per donor hair mesh, either "all N
vertex group(s) now resolve to a real bone in the donor armature" or which
ones still don't (and why).

**Correction to an earlier assumption**: the original write-up here claimed
Options 2/3/4 (below) would naturally leave a transplanted donor hair mesh
alone because it "uses the donor's own bone-chain layout, not the source's
uniform `Hair_bNN` naming." **That was wrong.** The real donor hairstyle
tested uses `Hair_b01`/`Hair_b02`/`Hair_b05`-style vertex group names too --
the same pattern Options 2/3/4 key off of. **Leave Options 2/3/4 off when
using this feature** (matches the settings used in the first real test) --
in particular, Option 4 would rigidly pin the transplanted mesh to `J_Head`
and throw away its real motion if left on.

This feature (now v2) is still **untested in-game** as of this writing --
the v1 log-only test surfaced the bone-graft bug above before any in-game
attempt was made, and v2 hasn't had its own log-only test yet. Per this
project's established practice, run another log-only conversion first and
check the log for the `DONOR HAIR TRANSPLANT:` summary line and the new
per-mesh "all vertex group(s) now resolve" line (or any remaining
`WARNING:` lines) before trying it in-game.

**Terminator fix (every `.mcd`, always)**: every single addon export tested
in this project -- Blender-driven or not -- has the leading `MDL!`
substream's size field wrong (it doesn't correctly predict where the
payload/`END!` terminator begins). This shows up two ways: most exports
have no `END!` chunk at all (size field == the file's own current length);
a Blender-headless export can *also* already have a correct `END!` chunk
present while the size field is still wrong the same way (found 2026-09-20,
previously undetected because the checks assumed the two symptoms always
came together). The app now detects and fixes both variants independently
(tested against real files: leaves a genuinely correct file byte-identical,
correctly repairs both known bug patterns, idempotent) on every `.mcd` it
writes, whether it came from the Blender pass or the byte-patch fallback
path.

Everything from v1 still applies: folder/file renaming to WWE2K25
conventions, `Materials/*.jmtl` dropped, `Textures/*.dds` copied through,
`.hpl` template fallback when the source has none.

## Known limitations / open risk areas

1. **Confirmed working end-to-end in-game (Lio Rush, 2026-09-20)**: the
   Blender pass, `CTG!`-layout handling, and the size-field/terminator fix
   are all confirmed via a real in-game test -- BaseModel loads correctly
   with no crash and no bone weight distortion. Attire materials
   (auto-`generate_mtls`) are the newest piece and not yet confirmed
   in-game as of this build -- that's the next thing to hear back on.
2. **Arms/shoulders (Pec/Lat/Delt subdivided bones)**: known, pre-existing
   gap, not fixed by this version -- see above.
3. **Attire `generate_mtls` quality**: the addon's legacy DLL materials
   writer (same one behind its manual "Generate MTLs" checkbox) is
   confirmed to run for every attire now, but the headless import pass
   currently skips loading textures (`load_textures=False`, for speed) --
   unconfirmed whether that affects the generated file's completeness. The
   generated file is checked with the same hollow-file heuristic as a
   safety net and will warn if it still looks empty.
4. **Dynamic hair (`.ycl`)**: out of scope for THIS TOOL's automated
   pipeline, but the underlying format problem is now SOLVED for the
   real-mesh case. **BREAKTHROUGH (2026-09-25, Yuki Arai/838 Molly
   Holly)**: `YclEditor.exe`'s built-in "Export As WWE 2K25" action,
   run manually on the source's own `.ycl`, produces a genuinely correct
   conversion -- confirmed via a real in-game test (worked perfectly)
   after two earlier false-negative results (a crash, then a stall) both
   turned out to be caused by mismatched/missing mesh files, not by
   anything wrong with YclEditor's `.ycl` output itself. `YclEditor.exe`
   is bundled with CakeView (`Cakeview vX.XX/tools/ycleditor/YclEditor.exe`)
   and is GUI-only -- no CLI/headless mode exists (checked via string
   analysis), so this is currently a manual step: run it on the source
   `.ycl`, Export As WWE 2K25, drop the result into this tool's output in
   place of the plain copy-through `.ycl` it currently produces.

   NOT yet confirmed for a full custom-mesh conversion (this tool's own
   Blender-exported `.mcd`, as used for every donor-armature character --
   Shota Umino, Saya Kamitani, etc.) -- the Yuki Arai test used the real
   modder-released WWE2K25 mesh, not a Blender-pass output, so it's still
   an open question whether this project's own Blender export preserves
   vertex order/count closely enough for a YclEditor-converted `.ycl` to
   stay valid against it. See the vertex-count diagnostic added to
   `blender_headless_convert.py` (2026-09-25) and the project doc for
   the ongoing investigation into that specific question.
5. **Multiple attires**: secondary-attire naming (`_Alt_Attire` with a
   user-assigned ID) still hasn't been confirmed against a real multi-attire
   WWE2K25 example. The GUI now has a "Multi-attire target IDs" box (added
   2026-10-13) that lists every non-primary attire folder it finds and lets
   you type a real WWE2K25 slot ID for each one -- click "Rescan" after
   setting the source folder if the list doesn't update automatically.
   Leaving one blank falls back to the old placeholder (the source's own
   folder prefix), which is almost never a valid slot ID -- you'll get a
   warning about it in the log. Also note: if a mod has 3+ attire folders,
   every non-primary one still gets the same `_Alt_Attire` suffix and will
   collide -- this tool only distinguishes "primary" vs. "not primary" attire
   folders, not a 3rd/4th slot, so it's only safe for default+1-alt mods for
   now.
6. **Custom donor character -- CONFIRMED WORKING IN-GAME (2026-09-23,
   Saya Kamitani, hair donation left off).** Armature donor, BaseModel
   materials (with the texture-fallback fix), body/attire weighting, and
   attire physics (tassels dangled correctly) all confirmed correct in a
   real in-game test. This is the feature's core goal, achieved.
   Dynamic hair on the SOURCE's own (non-donor) converted `.ycl` remains
   broken, but now precisely diagnosed rather than guessed at, via
   byte-level comparison of the Blender-`generate_mtls`-produced attire
   `.mtls` against the donor's real one:
   - **Main hair piece discolored**: the shader TYPE is correct
     (`yCh_hair`, matching the donor's own real hair shader) and its
     texture channels (`hair_flow`, `hair_rai`, etc.) correctly reference
     real, present texture files -- not a missing-file problem like the
     BaseModel bug above. But `yCh_hair` gets its actual color from *numeric
     shader parameters* baked into the `.mtls` (`g_hairAlbedo`,
     `g_rootColorR/G/B`, `g_tipColorR/G/B`, `g_dyeHack`, `g_mHairDyeMap`),
     stored as raw floats with no readable structure -- `generate_mtls`
     correctly auto-populates texture slot references from filenames, but
     there's no evidence it fills these hand-tuned color-grading values
     with anything but a default. Not something this tool can safely
     byte-patch (no schema, no way to verify a guessed value -- same
     reasoning as the earlier decision not to reverse-engineer `.jmtl`).
   - **Stretching in long hair strands -- FIX IMPLEMENTED 2026-09-24, NOT
     YET CONFIRMED IN-GAME.** Root cause confirmed by reading the addon's
     own real export code (`Blender/blender_utils.py`,
     `getVertexWeights`/`getBoneIndex`): at export, a vertex's weight slot
     is written only if its vertex group's *name* is found in the target
     (donor) armature's bone list; if not found, that slot silently stays
     at its default (weight 0.0) -- never reassigned, never warned about.
     Confirmed via byte-level bone-name extraction that the source's hair
     rig (16 uniform chains, `Hair_bNN`/`Hair_bNN_MM`, 144 bones) commonly
     doesn't exist by name in a real donor's own hair rig at all (a real
     example: only 69/144 matched, 75/144 missing including 6 whole
     chains) -- every unmatched hair bone's vertices were silently going
     to weight zero, which is exactly what produces "stretching."

     **Fix ("Option 2", picked over the alternative full-hair-object-swap
     after detourne asked about tassel-physics side effects -- this option
     never touches `.ycl`, so it can't repeat the tassel-dynamics
     regression described in point 6 below):** before export, every hair
     vertex group not present in the target skeleton has its weights moved
     onto the nearest surviving bone on the same chain (walking
     `Hair_bNN_MM` -> `Hair_bNN_(MM-1)` -> ... -> `Hair_bNN`), or, if the
     whole chain is missing, onto a universal `J_Head` fallback (confirmed
     present in both games' skeletons -- not a guess). The orphaned vertex
     group is then removed so nothing references a nonexistent bone at
     export.

     **CONFIRMED BROKEN on a real test (Saya Kamitani, 2026-09-24): caused
     a crash to desktop while loading the match.** The run's own log
     revealed a real design flaw, not just an unlucky edge case:

     ```
     'hair_ALPHA:skinned': collapsed 144 orphaned hair bone vertex group(s)
       onto 1 surviving bone(s) (12635 weight-slot(s) moved)
     OPTION 2 hair-bone fix: 144 hair bone(s) total collapsed ... 144
       landed on the universal 'J_Head' fallback
     ```

     **All 144 hair bones collapsed onto `J_Head` -- not the partial fix
     ("only the subset actually missing from this donor") that was
     designed, discussed, and agreed to.** Cross-referencing with a fact
     already confirmed earlier in this project (hair bones exist ONLY in
     attire `.mcd` files, never in a BaseModel skeleton -- checked and
     confirmed 0 matches in both source's and donor's BaseModel files):
     the `donor_armature` this fix checks bone names against is *always*
     built from the BaseModel donor import (see `_run_job` in
     `blender_headless_convert.py`), which by construction can never
     contain a single `Hair_bNN` bone. So the fix's chain-walk logic never
     actually gets to engage -- it degenerates to "collapse 100% of the
     hair mesh onto one rigid bone" on every single conversion, not just
     the ones with a real bone-name gap. That's a far more drastic
     structural change to the mesh (~3,160 vertices force-bound to one
     bone) than what was scoped, and the only functional difference
     between this run and the last confirmed-working test on the same
     character -- so it's the leading suspect for the crash, though not
     100% pinned down (the `.ycl` dangling-reference risk below is now
     also guaranteed for all 144 bones rather than a partial subset, and
     could independently be a contributing/actual cause -- would need the
     real output `.mcd`/`.ycl` bytes to fully separate the two).

     **Default flipped to OFF (2026-09-24)**, same treatment as the
     hair-*file* donor carryover after its own confirmed-broken result.
     The "Fix stretched/distorted hair bones" checkbox stays available,
     off by default, for further diagnosis only -- turning it off
     reproduces the exact pre-2026-09-24 Blender pass (the one already
     confirmed crash-free and working end-to-end for
     armature/materials/attire physics -- see point 6 above).

     **What a real fix would need**: a target skeleton that actually
     contains hair bones to check against -- which this pipeline doesn't
     have unless a real, structurally-compatible donor attire is also
     used (and donor hair carryover is separately confirmed broken due to
     a mesh-structure mismatch on the one real donor available). Until
     that's solved, this specific approach can't do a genuine partial fix;
     it can only do all-or-nothing, and all-or-nothing is now confirmed
     unsafe. Not proposing a next attempt without detourne's input.

     **The `.ycl` dangling-reference risk**, flagged in advance of this
     test result: this fix only changes mesh *vertex weights*, not `.ycl`.
     `.ycl` still contains physics-simulation entries for every hair bone
     that got collapsed away (now all 144, not a partial set), pointing at
     bone names absent from the exported skeleton. This was *expected* to
     degrade gracefully by analogy with the mesh-weight export path's own
     silent-skip behavior -- that expectation, never actually confirmed,
     is the other live suspect for the crash alongside the all-or-nothing
     collapse above.

     The main-hair-piece discoloration issue above is a separate, still-
     open problem (numeric shader color parameters, not bone weights) --
     unaffected by this fix either way.
   - Attire hair-*file* carryover from a donor (the `.ycl`/`.hair_ywa`
     experiment) is separately confirmed broken (structural hair-mesh
     mismatch) and stays off by default in the GUI.

   **Update 2026-10-13 -- the "custom donor" fix above is now ALSO the
   default, even with no donor folder set at all.** Separately confirmed
   bug: BaseModel head2/eye_l/mouthbag get the wrong shader TYPE (visually
   wrong head/face/eyes, confirmed in-game on Lio Rush, 2026-10-13) from
   Blender's "Generate MTLs" (used automatically for every BaseModel
   export since 2026-10-02, to fix the separate slot-name-mismatch
   "invisible head" bug this section opened with). No safe byte-level
   auto-patch exists for this (two earlier attempts both regressed -- head
   solid black, then face too dark -- and were never shipped). detourne's
   fix idea: let Blender autogenerate the mtls (for its correct slot
   *names*), then overwrite it with the bundled clean template (which has
   correct shader *types*), renamed to match. `_convert_base_model()` in
   converter.py now does exactly this by default: when no custom donor
   folder is set, the bundled generic template is run through the SAME
   auto-slot-rename pipeline described above (as if it were a "donor"),
   instead of being copied in dumb/verbatim. Confirmed via a synthetic
   test (a fake character whose own mesh names its head object "head2",
   matching the real Athena case) that this correctly produces a renamed
   `head2` slot carrying the template's own correct shader type -- fixing
   both bugs at once, automatically, with no donor-folder setup needed.
   The explicit "BaseModel materials donor" field is still there for
   cases where the bundled template's OTHER values (teeth/eyelash color,
   etc.) don't look right for a particular character -- see the
   teeth/eyelash color-mismatch note earlier in this section -- but it's
   no longer required just to dodge the wrong-shader-type bug. Not yet
   re-tested in-game with this exact change.
7. **NEW, unexplained: BaseModel can lose mesh objects during the Blender
   pass (Shota Umino, 2026-09-25, hair-bone fix OFF)**. Found during a
   pre-flight safety check, *not* an in-game-confirmed crash -- flagging it
   here as an open risk, not a repeat of point 6's crash.

   Source (2K26) `baseModel.mcd` has 8 `:skinned` mesh objects: `body,
   eye_caruncle, eye_l, eye_r, eyelash, head, mouthbag, teeth`. The
   converted output only has 6 -- **`eye_r:skinned` and
   `eye_caruncle:skinned` are both missing**, asymmetrically (left eye
   present, right eye and its caruncle piece both gone). Confirmed via
   direct byte/string comparison of both `.mcd` files. Attire meshes for
   the same character are unaffected (all 10 `:skinned` objects present in
   both source and output); this is BaseModel-only.

   The conversion log's own import line reads
   `imported ...\Shota_Umino_2K26\BaseModel\baseModel.mcd: 8 new object(s)`
   -- i.e. Blender's importer *did* bring all 8 source meshes into the
   scene. Nothing in `_convert_one()` (`blender_headless_convert.py`)
   filters `mesh_objs` by name afterward -- every imported `MESH`-type
   object is selected and passed to `saveModelFile()` unconditionally. So
   if the 8-in/6-out drop is real and not an artifact of how objects are
   counted at import (the log line doesn't distinguish mesh vs. non-mesh
   objects), the loss most likely happens inside the addon's compiled C
   export library itself, which is opaque to us -- not something this
   Python wrapper does deliberately or can currently see.

   Checked whether this is a materials mismatch that could crash on load:
   the donor's `baseModel.mtls` (copied as-is into the output -- see point
   6) defines only 8 generic materials (`body, eye_l, eye_overlay, eye,
   eyelash, head, mouthbag, teeth`) -- there is no material named `eye_r`
   or `caruncle` at all, even for a working donor. That means the
   right-eye/caruncle meshes were never going to reference materials named
   after themselves; both eyes normally share the generic `eye`/
   `eye_overlay` entries. So this specific mismatch does not look like the
   "mesh references a missing material" failure mode seen elsewhere in
   this project -- lowers, but doesn't eliminate, crash risk.

   Not yet confirmed whether this is Shota-specific or systemic: Saya
   Kamitani's earlier BaseModel exports (point 6) never showed a mesh-
   object-count mismatch, so this looks new rather than a known standing
   issue -- but only two characters have been checked this closely so far.

   **Diagnostics added (2026-09-25), not yet exercised**: `_convert_one()`
   now logs the exact mesh object names about to be exported, right after
   import/rename (`"mesh objects about to be exported (N): [...]"`) --
   this will show on the *next* conversion of any character whether a
   dropped mesh was already gone in Python before `saveModelFile()` (fixable
   here) or only vanishes inside the DLL (would need a different fix
   entirely). Blender's own stdout is now also captured and scanned for
   any line containing warn/error/fail/skip/duplicate/discard and surfaced
   in the log -- previously this was captured by the subprocess call but
   silently discarded, so a C-library warning about this exact mesh could
   have been produced and never shown to anyone. Re-running Shota Umino's
   conversion (or any other) with this build will tell us more than static
   analysis of the one existing output can.

   **Status: unresolved, no in-game test performed.** Not treated as a
   confirmed crash risk on the level of point 6's Option 2 bug, but also
   not clearable as safe from file analysis alone -- see the project doc
   for the full investigation writeup.

   **UPDATE (2026-09-25) -- real in-game test result: NO CRASH.** detourne
   tested this exact conversion in-game. Loaded fine. The eye_r/caruncle
   question above remains genuinely unconfirmed (neither screenshot gave a
   clear front-on view of the right eye), but it did not crash the game.
   The build's no-crash safety is now confirmed for two characters (Saya
   with the fix off, Shota).

   His actual report: **"it looked OK except the hair was static and
   floating off the head."** This is not a new bug -- it's real-world
   confirmation of the mechanism the hair-bone-weight fix (points above)
   was written to solve, just surfacing as a different symptom than
   Option 2's crash. With the fix off, hair mesh vertex groups still
   reference `Hair_bNN` bones by name, but the exported skeleton (always
   BaseModel-donor-derived) never contains them -- so those vertices get
   no valid bone binding and stay rigid in their bind pose while the rest
   of the character animates underneath, i.e. exactly "static and floating
   off the head." This is real confirmation that something in the export
   path silently drops unresolvable vertex-group bone references rather
   than erroring -- previously only hypothesized as the mesh-weight-side
   counterpart to the `.ycl` dangling-reference risk.

   Re-enabling Option 2 would likely fix "floating" (hair would become
   rigidly attached to the head) but is the confirmed-broken-crash path,
   via a structural bug that isn't character-specific -- not recommended
   without further safety work. The real fix remains Option 1 (donor hair
   mesh/texture/`.ycl` transplant, see project doc) -- this result
   confirms that work is solving a real, now in-game-observed problem, not
   a theoretical one.

   **UPDATE (2026-09-25) -- Option 2 tested again on Shota Umino (fix ON):
   did NOT crash, but hair is visibly warped/distorted.** Correcting an
   overclaim from the paragraph above ("no reason to expect it wouldn't
   reproduce [the crash]") -- it didn't reproduce. Same all-or-nothing
   collapse mechanism, same copied-through `.ycl` with now-dangling hair
   bone references as Saya's crashing run, but this run completed fine
   in-game -- just with rigid, warped hair (every hair vertex glued to one
   fallback bone regardless of its original position -- the expected
   visual result of this crude workaround, not a new bug). This means
   Option 2's crash is not universal/guaranteed -- something specific to
   Saya's conversion (possibly her 3-piece hair structure vs Shota's
   single piece, possibly something else) triggered it, not the collapse
   mechanism by itself. Real cause of Saya's crash still not identified.
   Net effect on guidance: unchanged -- still off by default, still not a
   real fix (always looks wrong even when it doesn't crash), but "always
   crashes" is no longer an accurate description of the risk.

   Also confirmed in this same test: the missing-mesh-object anomaly
   above (BaseModel `eye_r`/`eye_caruncle`) does not appear to affect
   anything visible in-game ("I believe the right eye was OK") -- treating
   this as resolved/non-issue in practice, even though the underlying file
   -level cause is still not fully identified.

7. **Dynamic hair `.ycl` proper-conversion research (2026-09-25)**: at
   detourne's request, dug into the `.ycl` format itself (still unsolved, but
   meaningfully further along than before). No public documentation
   exists anywhere (checked Pro Wrestling Mods wiki, GitHub); CakeView.exe
   turned out to be a launcher that shells out to a separate
   `ycleditor.exe` for real `.ycl` work, so no shortcut there either.

   **Confirmed (10/10 real files, no exceptions)**: `.ycl` header byte
   offset 18-19 (upper half of the uint16 pair at offset 16) is the
   literal game version number -- 25 or 26. Same pattern already proven
   for `.mtls` (`_patch_mtls_version_for_2k25()`). This is a low-risk,
   well-verified, one-field patch worth testing in isolation before
   tackling the harder bulk-data problem -- unknown whether the engine
   even checks it, but cheap to find out.

   Found two better-matched real conversion pairs (Momo Kohgo's and Yuki
   Arai's Molly Holly attires) than the previously-only-available Saya
   pair, which turned out to have a structurally different rig on each
   side and was a poor reference. Also found two undocumented files in
   detourne's `MOD EXAMPLES/Yuki Arai/` folder (`Conversion Attempt1` and
   `ConversionAttempt2`, byte-identical to each other) that differ from
   the verified real WWE2K25 target by only 0.075% (3,074 of 4,085,440
   bytes) -- strong evidence of a near-complete prior solution to this
   exact problem. The residual gap is now characterized into two bounded
   patterns (a handful of nulled/sentinel reference fields, and a large
   repeating index region offset by a constant -8 from the real file's
   values) rather than being fully opaque. **Provenance of these two
   files is unknown -- no README/notes found alongside them** -- asked
   detourne directly since knowing their origin could shortcut the rest of
   this problem.

   Still not a working merge algorithm. See the project doc's ".ycl
   deep-dive" section for full detail (header field map, the stride-4
   region analysis, the diff patterns).

   **UPDATE (2026-09-25) -- BREAKTHROUGH, then a bigger discovery.**
   YclEditor.exe's "Export As WWE 2K25" was tested in-game two more
   times: paired with the REAL, known-working WWE2K25 mesh for Yuki
   Arai's Molly Holly slot, it **worked perfectly**. Two earlier
   failures (a crash, then a system stall) both turned out to be caused
   by mismatched/missing mesh files, not the `.ycl` conversion itself --
   see the project doc for the full story. This makes the `.ycl` format
   conversion itself a solved problem for the per-vertex CLOTH-SIM case
   (Yuki Arai's hairstyle: large `.ycl`, `SimMesh_CtLink` data).

   BUT: Shota Umino's own source `.ycl` turned out to be a completely
   different, much simpler kind -- a **bone-chain** system. String
   extraction found it's full of `Hair_b00`, `Hair_b00_00`,
   `Hair_b00_01`, ... entries: per-bone physics on the exact `Hair_bNN`/
   `Hair_bNN_MM` bones this tool has been calling "orphaned hair bones"
   since Option 1/2 above. For this kind of rig, a correctly-converted
   `.ycl` has nothing to act on unless those exact bones exist in the
   exported skeleton -- neither Shota's real WWE2K26 BaseModel nor his
   real WWE2K25 donor's BaseModel contains any of them (checked both).
   **-> See Option 3 below, built the same day to address this
   specifically.**

8. **Hair-chain bone grafting ("Option 3", EXPERIMENTAL, added
   2026-09-25)**: a new checkbox alongside Options 1/2, for bone-chain
   hair rigs specifically (confirmed present on Shota Umino; likely
   others). Creates the source's missing `Hair_bNN`/`Hair_bNN_MM` bones
   directly in the donor armature before export, so the addon's exporter
   -- which only writes a vertex's weight if its vertex group's name
   matches a REAL bone in the target armature -- stops silently dropping
   those weights. Meant to be paired with the source's own `.ycl` run
   through YclEditor.exe's "Export As WWE 2K25" (manual step -- swap the
   file in after this tool's own conversion, same as the Yuki Arai test
   above) for a real end-to-end test.

   **v1 result (2026-09-25): FAILED in-game.** Placed every new bone as a
   fixed short segment straight down from `J_Head`, mesh-independent.
   In-game the hair rendered as a wildly stretched, erratic vertical
   pillar. detourne's own diagnostic -- deleting the converted `.ycl`
   entirely and still seeing the same stretching -- proved the defect
   was in the grafted bones' bind-pose geometry, not the `.ycl`
   conversion.

   **v2 fix (2026-09-25)**: each grafted bone's HEAD is now the
   weight-averaged position of the actual mesh vertices already assigned
   to that bone's vertex group (computed in the donor armature's local
   space before entering Blender edit mode), so the bind pose roughly
   matches where the hair geometry really sits instead of an arbitrary
   stub near the head. Each bone's TAIL points at its chain child's
   derived head position when known, so a multi-link chain follows the
   mesh's own shape; a chain's last link (or any bone with literally no
   weighted geometry -- logged when it happens) falls back to continuing
   its parent's direction. Bone transforms are still not reconstructed
   *real* rig data (no `.mcd` examined anywhere in this project contains
   an actual `Hair_bNN` bone to copy from), but they're now
   mesh-consistent instead of arbitrary. **In-game result: FAILED**, same
   catastrophic stretched-pillar look as v1 -- confirmed (via a diagnostic
   log) to be a genuinely different set of positions than v1, so this
   ruled out "same bug, didn't actually change anything" as an
   explanation.

   **v3 fix (2026-09-25) -- axis-swap.** Comparing the v2 grafted bones'
   computed positions against the real donor skeleton's real bones
   (`J_Head`, `J_Hips`, `J_Spine1`/`J_Spine2`, `J_Neck`) showed this
   armature's vertical axis is **Y** (more negative = up), but the
   mesh-derived centroid math was putting the "vertical-looking" values
   in **Z** instead. Fix: swap the Y/Z components of the computed
   centroid before using it as a bone head position. Confirmed correct
   by a diagnostic dump matching hand-calculated predicted values almost
   exactly. **In-game result: MAJOR improvement** -- hair was
   recognizable as the right hairstyle for the first time, but detourne
   still reported it as "stretched and a little erratic."

   **v4 fix (2026-09-25) -- chain-tip length.** A diagnostic showing
   every chain's min/max link length turned up a `min=0.0400` -- exactly
   the small fixed fallback length any chain's *terminal* link (the tip,
   with no "next" bone to aim its tail at) was using, instead of scaling
   with that chain's own real link spacing (~2.5 units). Every strand
   tip was effectively a near-zero-length degenerate bone. Fix: a
   terminal link's tail length now reuses its own parent link's real
   length instead of the fixed constant (confirmed via diagnostic: min
   link length rose from 0.04 to 1.3953). **In-game result: mixed** --
   clean from some camera angles, but detourne reported "the same section
   of hair is weighted improperly" from others.

   **v5 fix (2026-09-25) -- outward bone roll.** A new per-chain
   diagnostic (span/reach/root-distance for all ~30 chains) showed no
   position outliers at all -- combined with detourne's observation that
   the remaining stretching was now localized to the top/crown/back of
   the head (not the front, which hangs straight and looks correct),
   this pointed at bone *orientation* (roll/twist) rather than position:
   curved/bent chains are far more sensitive to bone roll than
   hanging-straight ones. Fix: each grafted bone's roll is aligned so
   its local Z axis points radially outward from `J_Head`
   (`align_roll()`). **In-game result: FAILED** -- detourne confirmed this
   did not fix the issue. After 5 revisions (v1 through v5) each
   measurably changing the result but never fully fixing it, Option 3
   was set aside in favor of Option 4 below rather than attempting a 6th
   refinement of bone data that was never available to reconstruct from
   in the first place.

   **Aside, answering "why did Blake Christian's hair work fine
   (static, no stretching)?"**: he isn't a bone-chain hair case at all.
   His WWE2K26 `.mcd` contains zero `Hair_bNN` strings, and his `.ycl`
   is for a completely different thing -- a dynamic **jacket**
   (`S_Jacket`/`M_Jacket`), driven off ordinary standing skeleton bones
   (`J_Spine1`, `J_Neck`, `H_Elbow_L_tw01`, etc.), unrelated to hair
   entirely. His hair is presumably just ordinary geometry bound to
   normal, always-present bones, so it never hit the orphaned-hair-bone
   problem Option 1/2/3 exist to work around -- there was nothing to
   graft or collapse for him in the first place.

9. **Rigid hair pinned to `J_Head` ("Option 4", CONFIRMED WORKING
   in-game 2026-09-25 on Shota Umino -- see the full investigation
   below)**: after Option 3's five revisions each improved but
   never fully fixed Shota Umino's bone-chain hair in-game, detourne asked
   for a fundamentally different, much blunter approach instead of a 6th
   refinement: give up on dynamic hair motion entirely. Every hair
   vertex (anything carrying weight in a `Hair_bNN`/`Hair_bNN_MM` vertex
   group) has **all** of its existing weight removed -- hair-named
   groups and any other bone it happened to also be weighted to -- and
   is reassigned 100% to `J_Head`. The hair becomes one rigid piece that
   moves only as part of the head, exactly like any other static
   attachment (same as how Blake Christian's ordinary, non-bone-chain
   hair already behaves). This sidesteps every position/orientation/
   scale assumption that caused all five Option 3 failures, at the cost
   of losing hair physics entirely.

   Implementation: `_pin_hair_to_head()` in
   `blender_headless_convert.py`. No synthesized bone geometry is
   created at all -- this only touches existing vertex weights. Since no
   vertex ends up weighted to any `Hair_bNN`/`Hair_bNN_MM` bone anymore,
   a `.ycl`'s per-bone physics (if present) has nothing left to act on
   either way -- **there's no need to run the source `.ycl` through
   YclEditor.exe for this option**, unlike Options 2/3.

   Takes precedence over Options 2/3 if more than one checkbox is
   ticked at once (a log line notes when this happens) -- Option 4
   immediately overwrites all hair weight regardless, so running the
   graft/remap first would just be wasted work.

   **First in-game result (2026-09-25): FAILED -- same artifact.** detourne's
   own words: "the hair is still weighted to something else and not
   pinned to the head," and the screenshot showed the exact same
   catastrophic stretched-pillar look as every failed Option 3 revision.
   The Blender log confirmed the fix itself ran correctly (8194 hair
   vertices pinned 100% to `J_Head`, 120 now-empty hair vertex groups
   removed), and tracing through the WWE2K_MDL addon's own export code
   (`Blender/blender_utils.py`'s `getVertexWeights`/`getBoneIndex`, plus
   `getMeshSkin`/`CVertexSkin`) confirmed a single-bone, weight-1.0
   vertex should export cleanly with no special-casing that would drop
   or corrupt it.

   That combination -- weights provably correct going into the export,
   the export mechanism provably able to handle a single-bone vertex,
   yet an in-game result *identical in character* to every earlier
   failure -- points at something the weight fix can't touch at all:
   the source attire's own `.ycl` file. It's still copied into the
   output **verbatim, in raw WWE2K26 format** (never run through
   YclEditor.exe's "Export As WWE 2K25" here), and for a bone-chain rig
   it's full of per-bone `Hair_bNN`/`Hair_bNN_MM` physics entries. The
   original assumption was "no vertex references those bones anymore,
   so the file has nothing to act on either way" -- but that assumes the
   `.ycl` only ever *reads* existing skinned-mesh weights, when it may
   instead describe its own physics/bone setup that the 2K25 engine
   still tries to load and simulate regardless of what the mesh is
   weighted to, independent of and prior to any skinning.

   **Fix shipped (2026-09-25, same day): when Option 4 is on, the source
   `.ycl` is no longer copied into the output at all** (a log line
   explains why it's skipped). A donor `.ycl` from the "Custom donor
   character" hair-carryover feature, if set, is unaffected -- that's a
   real, known-working WWE2K25 file, not the source's raw one.

   **UPDATE (2026-09-25, same day) -- ruled out.** detourne had already
   deleted the `.ycl` before the in-game test above, so the artifact
   happened with hair 100% pinned to `J_Head` AND no `.ycl` present at
   all. That rules the `.ycl` out entirely as the cause -- the skip-copy
   fix above is harmless and still worth keeping, but it isn't the fix.

   **Added instead: a round-trip export verification (log-only, no game
   launch needed).** Since the weights going INTO export were confirmed
   correct (the pre-export log) and the export mechanism was traced by
   hand through the addon's own source and looked correct, but the
   in-game result says otherwise, the next cheapest thing to check is
   what's actually IN the exported file -- rather than trust the
   hand-trace. `_verify_rigid_hair_export()` in
   `blender_headless_convert.py` now runs automatically whenever Option 4
   is on: right after each export, it re-imports the file *that was just
   written* into a scratch Blender scene and reports, per mesh: how many
   vertices have literally ZERO total weight (a vertex with no bone
   influence at all commonly snaps toward the world origin in most
   skinning pipelines -- a very plausible match for "a thin stretched
   pillar reaching off to one side"), how many are still weighted to a
   `Hair_bNN` group (should be zero), how many are weighted only to
   `J_Head` (the expected/correct case), and how many are weighted to
   something else entirely. This needs only a log-only conversion run (no
   game launch) to check -- look for a `RE-IMPORTED '<mesh>':` line per
   mesh in the log.

   **Result (2026-09-25, same day) -- found it.** A log-only run with the
   round-trip check turned up the real gap: `'hair:skinned'` came back
   with **2024 of 10218 vertices at literally ZERO total weight**. Those
   are vertices that were never weighted to any `Hair_bNN` group in the
   first place -- so the original, narrower version of Option 4 (which
   only pinned vertices already carrying `Hair_bNN` weight) never touched
   them at all -- but whatever bone(s) they *were* weighted to also
   doesn't exist in the target skeleton, so the addon's exporter silently
   dropped them too, the exact same "unknown bone name -> dropped"
   behavior this whole project has been working around since Option 1,
   just never caught for this specific mesh before now. Every other
   mesh in both the BaseModel and Attire round-trip checks came back with
   zero unweighted vertices -- this gap was specific to the hair mesh.

   **v2 fix (2026-09-25, same day): broadened the pin.**
   `_pin_hair_to_head()` now pins **every vertex belonging to any mesh
   object that has at least one `Hair_bNN`-named group anywhere on it**
   -- not just the subset that happened to already carry `Hair_bNN`
   weight. Once a mesh is identified as "a hair mesh" at all, the whole
   object is treated as hair and pinned 100% to `J_Head`, full stop. This
   also matches detourne's original wording ("all hair is just assigned to
   J_Head") more literally than the v1 per-vertex check did. The updated
   log line for each affected mesh reports how many of its pinned
   vertices already carried `Hair_bNN` weight vs. how many didn't (the
   ones v1 would have missed).

   A follow-up log-only round-trip check confirmed the fix was clean:
   `'hair:skinned'` came back with **all 10218 vertices weighted only to
   `J_Head`, zero unweighted, zero still on a `Hair_bNN` group**.

   **CONFIRMED WORKING IN-GAME (2026-09-25, Shota Umino, v2/broadened
   revision).** detourne's exact words: "This version worked." The
   stretched-hair artifact is gone. This closes out an investigation that
   ran through Option 2 (broken), 5 revisions of Option 3 (each closer,
   never fully fixed), and 2 revisions of Option 4 -- the actual root
   cause across all of it turned out to be a mix of two independent
   things: (1) Option 3's synthesized bone positions/orientations were
   never going to be exactly right since no real `Hair_bNN` rig data
   exists in any `.mcd` examined in this project, and (2) roughly 1 in 5
   of the hair mesh's own vertices were weighted to bone names that don't
   exist in the target skeleton at all (unrelated to the `Hair_bNN`
   chain specifically) and were silently exported with zero weight --
   invisible until a round-trip export-verification diagnostic checked
   the actual output file instead of trusting the pre-export state.
   **Option 4 (v2) is now the recommended approach for any character with
   bone-chain dynamic hair** (hair goes rigid/static -- no more jiggle --
   but renders correctly instead of stretching). Options 2 and 3 remain
   available for further experimentation but are not recommended.

## Files

- `app.py` -- the GUI.
- `converter.py` -- all the byte-level conversion rules + folder/file
  structuring (this is what actually produces the output folder).
- `mcd_fix.py` -- the terminator-fix logic, unit-testable on its own.
- `blender_headless_convert.py` -- runs *inside* Blender (via
  `blender --background --python`); does the donor-armature import + 4-bone
  rename + export, for the BaseModel and each attire.
- `blender_runner.py` -- launches Blender as a subprocess to run the above,
  and reports back what happened.
- `templates/generic_2k25_basemodel/` -- the bundled real WWE2K25 donor
  (`baseModel.mcd` used by the Blender pass, `baseModel.mtls` used directly
  as every converted character's BaseModel materials).
