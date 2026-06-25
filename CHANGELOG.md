# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/).

## [1.0.0] — 2026-06-25

First public release of **Metadata Removal**, an extension for **Stable Diffusion WebUI
Forge (Neo)** (also compatible with AUTOMATIC1111-derived web UIs).

Removes **all** metadata readable by the «PNG Info» tab (prompt, parameters, model, seed,
EXIF, XMP, comments) **without altering the pixels** and **keeping the ICC color profile**.

### Features

- **Automatic cleaning after generation** — a checkbox in the «Metadata Removal» section of
  txt2img/img2img (off by default): when enabled, every generated image is saved without
  metadata. It runs **after ADetailer** (cleaning is always the last step).
- **«Metadata Removal» top tab** (between «Extras» and «PNG Info»), Extras-style, with three
  sub-tabs:
  - **Single Image** — clean an uploaded image (preview + download);
  - **Batch Process** — clean several uploaded images (multi-file download);
  - **Batch from Directory** — clean every image in a folder on disk, with an
    «Include subfolders» option.
- **«Remove metadata» button in the PNG Info tab** — cleans the uploaded image and offers it
  as a downloadable copy, keeping its original name.
- **«Delete metadata» button in the Image Browser** (the *images-browser* extension) — next
  to «Delete», with a confirmation message.
- **Three on-disk save modes** (Settings → Metadata Removal): dedicated «Metadata Removal»
  folder in the images root *(default)* · copy with `_clean` suffix · overwrite the original.
- **Original file names** preserved (numeric suffix `_1`, `_2`… only on collision, so nothing
  is ever overwritten in the dedicated folder).
- **Recreate the subfolder structure**: in dedicated-folder mode, mirrors the source folder
  tree (setting, on by default).
- **Console logging** with progress bars (`tqdm`) for every operation.
- **Settings**: save mode, dedicated folder name, recreate structure, copy suffix, delete the
  sidecar `.txt` parameters file during automatic cleaning.

### Formats and quality

- **PNG**: **lossless** rewrite (all text chunks and EXIF removed).
- **JPEG / WEBP**: EXIF/XMP/comments removed (re-encoded at high quality, 95).
- **Animated GIF / WEBP / APNG**: all frames, timings and the loop are kept.
- **ICC color profile** and **alpha (transparency) channel**: always kept.

### Security and robustness

- Decompression-bomb protection (pixel cap) and a frame cap for animated images.
- Dedicated-folder name sanitization; refusal to scan a drive root (e.g. `C:\`) with
  subfolders enabled, in all modes.
- **Atomic** writes (temporary file + replace) with 0-byte saves refused.
- A **visible warning** in the UI if automatic cleaning fails on a file.
- Passed **two multi-agent security audits** (one final audit before publication).

### Dependencies

- Only **Pillow** (already included in Forge). No extra install.

[1.0.0]: https://github.com/xXIlRizzoXx/sd-forge-metadata-removal/releases/tag/v1.0.0
