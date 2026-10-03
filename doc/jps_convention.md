# JPS side-by-side eye-order: empirical study

**Date:** 2026-10-03 · **Question:** for `.JPS` files, which eye is on the left?
**Samples:** `samples/` (19 files from 10 sources) · **Sources:** `sources.txt`
**Method note:** an automated disparity screener (`analyze.py`) proved unreliable for
comfort-mounted ("windowed") pairs — the largest-|disparity| features are often
*background*, not foreground — so every determination below was re-done with targeted
foreground/background feature matching: pick one clear foreground feature F and one
background feature B, measure x-positions in each half via NCC template matching, and
compare relative positions. Rule: the half where the foreground sits more **rightward**
relative to background is the **left**-eye view (left eye sees near objects shifted right).
`uncertain` was recorded rather than guessed where features were weak.

## Headline findings

1. **The wild is split.** Among 8 independent side-by-side pairs: **4 right-on-left,
   4 left-on-left.** No majority. Bill Costa's own repo contains *both* orders.
2. **The actual spec default is right-eye-first.** The VRex "General Purpose
   Stereoscopic Data Descriptor" (1997) — the closest thing JPS has to a canonical
   spec — says: *"If a JPEG file is thought to contain a stereo image, but a
   stereoscopic descriptor is not present, an application should assume the layout
   of the stereo image to be: Side-by-Side, Full Height, **Right Field First**."*
   (`SD_JPS_DEFAULT`). The "left-on-left is the JPS convention" line is folk wisdom,
   not spec.
3. **sView agrees with the spec.** sView's source maps `.jps` → `SideBySide_RL`
   (right-on-left) by default, and *saves* JPS cross-eyed by default. NVIDIA 3D Vision
   also saved JPS right-eye-first.
4. **Sample-file hosts ship fake JPS files.** samplefiles.org (4 files),
   filesamples.com (4 files), shin-aska's dataset, and Filestar's sample repo all
   serve single 2D photos renamed to `.jps` — no stereo pair at all. (filesamples.com
   and shin-aska even use the *same* star-trails stock photo.)
5. **Almost nobody writes the `_JPSJPS_` descriptor.** Of 11 valid pairs, only the
   two StereoPhotoView-generated files carried the VRex APP3 marker. Everyone else
   relies on convention — which, per finding 1, doesn't exist.

## Per-file determinations (valid stereo pairs)

| # | Source | File (WxH) | Halves equal? | Eye order | Evidence | Confidence |
|---|--------|-----------|---------------|-----------|----------|------------|
| 1 | Bill-Costa/3D-Test-Images | ti1-2160-uhd.jps (3130×2160) | yes (1565×2160) | **RIGHT-on-left** | FG=Mario cap "M" logo vs BG="Toast" sign text: FG 46px more rightward in right half (NCC .998/.956) | high |
| 2 | Bill-Costa/3D-Test-Images | ti1-1080-fhd.jps (1564×1080) | yes | **RIGHT-on-left** | same photo as #1; targeted d=+13 (NCC .996/.936) | high |
| 3 | bill-costa/multi-image-jpeg-splitter | 02-super-mario.jps (1044×720) | yes | **RIGHT-on-left** | pixel-verified near-identical to #2 (mean abs diff 1.6); inherits order. EXIF: Panasonic DMC-3D1 | medium |
| 4 | Bill-Costa/3D-Test-Images | depth-bw-1080.jps (3840×1080) | yes | **LEFT-on-left** | embedded "LEFT"/"RIGHT" labels: LEFT text in left half; repo README criterion: LEFT must be seen by left eye | high |
| 5 | Wikimedia Commons | JPS-sample.jpg (3720×1080) | yes | **LEFT-on-left** | FG=courtyard chairs vs BG=building window: FG 33px more rightward in left half | medium-high |
| 6 | Wikimedia Commons | Kasuga Lantern (cross-eye stereo pair).jpeg (4420×2304) | yes | **RIGHT-on-left** | filename-labeled cross-eye; pixel-verified: its left half == parallel version's right half (diff 5.0 vs 45.1) | high |
| 7 | Wikimedia Commons | Kasuga Lantern (parallel).jpg (4420×2304) | yes | **LEFT-on-left** | filename-labeled parallel; swap-verified against #6 | high |
| 8 | StereoPhotoView (via exiftool#78) | FujiFilmRL.jpg (7168×2016) | yes | **RIGHT-on-left** | embedded `_JPSJPS_`: SBS + right-field-first; filename "RL"; FFmpeg reads it as "side by side (inverted)" | high |
| 9 | StereoPhotoView (via exiftool#78) | FujiFilmAB.jpg (3584×4032) | yes (over-under) | **over-under, LEFT on top** | embedded `_JPSJPS_`: layout=over-under + left-field-first | high |
| 10 | StereoPOV (ichthyostega.de) | woodbox_full.jps (2048×768) | yes | **RIGHT-on-left** | targeted d=+8/+10 (thin margin; small depth range vs global crop offset); site states previews are "for cross-eyed freeviewing" | medium-low |
| 11 | StereoPOV (ichthyostega.de) | dusty_full.jps (1334×500) | yes | **LEFT-on-left** | two independent FG/BG feature pairs: d=−43 and −34 | medium |

**Aggregate (8 independent side-by-side pairs): 4 right-on-left, 4 left-on-left.**
(#2/#3 are the same photo as #1; #9 is over-under, excluded from the SBS count.)

### Invalid / excluded downloads

| File | Reason |
|------|--------|
| filestar sample.jps (850×566) | single 2D photo (laptop), no pair structure |
| samplefiles.org dl_id 113–116 | single 2D stock photos renamed `.jps` |
| filesamples.com sample_*.jps (×4) | single 2D photos (one is the same star-trails stock photo as shin-aska's) |
| shin-aska sample.jps (640×426) | single 2D photo (star trails) |
| stereophotoview-FujiFilmLR.jpg | download URL dead (AccessDenied) |

## The spec, precisely

VRex, Inc., *General Purpose Stereoscopic Data Descriptor* (1997; authors Jon Siragusa,
David C. Swift; with Chasm Graphics, Stereographics, Nuvision 3D) — fetched from
`http://paulbourke.net/stereographics/stereoimage/spec.pdf`, saved as
`docs/paulbourke-stereoimage-spec.pdf`:

- The descriptor is a 32-bit value in a JPEG **APP3** marker with 8-byte identifier
  `"_JPSJPS_"`, then 16-bit length, then the descriptor, then optional comments.
- Field-order bit (bit 18, `SD_LEFT_FIELD_FIRST = 0x040000`): 0 = right field first
  (topmost/leftmost), 1 = left field first.
- **Default when no descriptor is present:** *"Side-by-Side, Full Height, Right Field
  First"* — i.e. **right eye on the left** (`SD_JPS_DEFAULT`).
- A file carrying the descriptor "should have extension `.jps`".

## How other viewers handle JPS

| Viewer | Default eye order for JPS | Evidence |
|--------|---------------------------|----------|
| **sView** (open source) | **Right-on-left.** `.jps`/`.pns` → `theToSwapJps ? SideBySide_LR : SideBySide_RL`, swap default off; *saves* JPS cross-eyed by default; `SideBySide_RL` is literally named `"crossEyed"` in its enum strings | `StShared/StFormatEnum.cpp:13` (`StFormat_SideBySide_RL_STRING = "crossEyed"`), `:70-81`, `StImageViewer/StImageViewer.cpp:185` (`ToSaveCrossEyed` default true), `StImageViewer/StImageLoader.cpp:749` |
| **StereoPhoto Maker** | **No automatic assumption.** Uses the user's Launch-Preference "Stereo-Image Format" (side-by-side, above/below, …); user picks per open | `stereo.jpn.org/eng/stphmkr/help/runtime_options.htm`: "The stereo and display formats of the image are assumed to be those specified in Launch Preferences" |
| **StereoPhotoView** | Writes correct `_JPSJPS_` descriptors (observed: RL file = right-first; AB file = left-first over-under) | empirical (files #8, #9) |
| **FFmpeg** | Honors `_JPSJPS_`: sets `AV_STEREO3D_FLAG_INVERT` when the left-first flag is clear; **no marker → no stereo interpretation** (plain 2D decode) | `libavcodec/mjpegdec.c:1992-2031` |
| **ExifTool** (≥12.26) | Reads the `_JPSJPS_` descriptor; no documented default assumption | `github.com/exiftool/exiftool/issues/78` |
| **ImageMagick** | **Reads JPS as plain JPEG** (no stereo split). **Writes** JPS by appending image list left-to-right — i.e. `convert left.jpg right.jpg out.jps` = left-on-left | `coders/jpeg.c:2321` (registers "JPS"), `:2957-2962` (`AppendImages(image, MagickFalse)` in the writer) |
| **NVIDIA 3D Vision** (historical) | **Saved JPS right-eye-first** (observed in NVIDIA-saved game captures); photo viewer discontinued, no primary default doc found | `github.com/danielcamposramos/sony-bravia-linux/blob/HEAD/docs/3d-photos-on-bravia.md` |
| **Kdenlive** | Side-by-side **default = parallel (left-on-left)**; "side by side crosseye" is the explicit alternative | `docs.kdenlive.org` stereoscopic_3d ("side by side parallel *) … left eye left and right eye right", `*) default`) |
| **W3C 3D-web proposal** | `stereo-order-type` initial value `"lr"`; example markup uses `src="1.jps"` | `w3.org/2011/webtv/3dweb/3dweb_proposal_121130.html` |
| **Blender** | Side-by-side render = left then right (left first); cross-eye is an opt-in | `docs.blender.org` stereoscopy usage |
| **3DCombine** | No documented JPS eye-order default found | — |

## Recommendation for PixelSpy

1. **Default to right-eye-on-left.** It's the VRex spec default, sView's default, NVIDIA's
   write order, and it matches the first real file Christopher tested (ti1). The
   left-on-left "convention" is folk wisdom with no normative source.
2. **Honor the `_JPSJPS_` APP3 descriptor when present.** It's an 8-byte magic scan plus
   one bit test (`0x04` in the flags byte = left-first); StereoPhotoView writes it and
   FFmpeg/ExifTool read it. Cheap, and it removes ambiguity for well-formed files.
3. **Ship the swap-eyes toggle regardless — it's the actual requirement.** A 4/4
   empirical split (and one repo containing both orders) means *no* default is safe.
   Show the active assumption in the UI, e.g. a "Stereo layout: Right eye left (JPS
   default)" indicator with the swap control next to it — the teaching-parenthetical
   pattern fits here.
4. **Don't trust per-source documentation blindly.** Bill Costa's README table says
   JPS = "right/left", yet his own depth-bw test file is left-on-left while his ti1
   photo is right-on-left.

## Caveats

- n=8 independent side-by-side pairs is small; treat the 4/4 split as "both orders are
  common", not as a population estimate.
- The automated disparity screener (`analyze.py`, kept in this directory) is retained
  for triage only — it misjudged window-mounted pairs and was overruled by targeted
  measurement wherever they disagreed.
- Two determinations are medium-low/medium (woodbox #10, dusty #11); both are
  included with their confidence stated rather than dropped.
- Labeled/ground-truth anchors: depth-bw (#4, embedded labels), Kasuga pair (#6/#7,
  filename labels + pixel swap-verification), FujiFilmRL/AB (#8/#9, embedded VRex
  descriptors + FFmpeg cross-check).
