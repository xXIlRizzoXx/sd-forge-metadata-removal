# Metadata Removal — extension for Stable Diffusion WebUI Forge (Neo)

Removes **all** metadata from images generated with Stable Diffusion: prompt,
parameters, model, seed, and any other information the **PNG Info** tab could read
(including EXIF and XMP for JPEG/WEBP).

Compatible with **Stable Diffusion WebUI Forge – Neo**
([Haoming02/sd-webui-forge-classic](https://github.com/Haoming02/sd-webui-forge-classic), branch `neo`)
and, in general, with AUTOMATIC1111-derived web UIs.

## What it does

The extension works in **four places**:

1. **"Metadata Removal" section in txt2img / img2img**
   An accordion section with a checkbox:
   - **off by default** → images are saved normally;
   - **enabled** (*Strip metadata from saved images*) → every generated image is saved
     **without metadata** (the file is cleaned in place).

2. **Top "Metadata Removal" tab** (between **Extras** and **PNG Info**)
   Three *Extras*-style sub-tabs:
   - **Single Image** → clean an uploaded image and download it (with a preview);
   - **Batch Process** → clean several uploaded images and download them;
   - **Batch from Directory** → clean every image in a folder on disk
     (with a progress bar; *Include subfolders* option).

3. **"Delete metadata" button in the Image Browser**
   Next to the *Delete* button of the
   [images-browser](https://github.com/AlUlkesh/stable-diffusion-webui-images-browser)
   extension: cleans the currently selected image while you browse your library.

4. **"Remove metadata" button in the PNG Info tab**
   Below the *Source* image: cleans the uploaded image and offers it as a **clean
   downloadable copy** (like *Single Image*, because the image is an upload).

## Where cleaned images are saved (a single setting)

For **on-disk** operations (*Batch from Directory* and the Image Browser *Delete metadata*
button) the behavior is chosen **once** in **Settings → Metadata Removal**, among **three
modes**:

- **Save to the «Metadata Removal» folder (in the images root)** → creates (if missing) a
  **dedicated folder** named after the extension **in the images root**, i.e. next to
  `txt2img-images`, `extras-images`, etc., and saves all cleaned images there; originals
  stay intact. *(default)*
  Example: `F:\Stability_Matrix\Data\Images\Metadata Removal\`. The path adapts
  automatically to wherever each user saves images (Stability Matrix *junctions* are
  resolved too).
- **Save a clean copy in the same folder** → creates a clean copy in the same folder as
  the original, with a suffix in the name (default `_clean`); the original stays intact.
  The suffix is **reserved** for clean copies: if a file with that name already exists it
  is replaced (so re-cleaning updates the copy, without creating duplicates).
- **Overwrite the original image** → rewrites the file directly, no copies.

The **dedicated folder name** is configurable in *Settings → Metadata Removal*
(default: `Metadata Removal`).

> **File names:** cleaned images keep the **same name** as the original (dedicated folder
> and overwrite). If a file with that name already exists in the dedicated folder, a
> **numeric suffix** (`_1`, `_2`, …) is added so nothing is ever overwritten. Only the
> *«save a clean copy in the same folder»* mode uses the textual suffix (default `_clean`).
> The **PNG Info** button also saves with the uploaded image's name (if Forge provides it;
> otherwise `cleaned.png`).

> Note: automatic cleaning after generation (point 1) always cleans the freshly created
> file **in place**, regardless of this setting.
> Single Image, Batch Process and the PNG Info button work on *uploaded* files (temporary
> copies): they save the clean version into the **dedicated folder** and also offer it as
> a **downloadable copy** (they cannot overwrite files on your disk, because they don't
> know their origin).

## What is removed

Everything that would appear in **PNG Info**:

- **PNG**: every text chunk (`parameters`, `prompt`, `workflow`, `Comment`,
  `Description`, `Software`, XMP and any key added by other nodes/extensions) and any
  embedded EXIF.
- **JPEG / WEBP**: EXIF (including `UserComment`), XMP and comments.

## What is kept

- The **ICC color profile** (color fidelity) — **always** kept.
- The **transparency (alpha) channel**, when present.
- The **pixels**: for PNG the rewrite is **lossless** (JPEG/WEBP are re-encoded at
  quality 95).
- **Animated images** (GIF/WEBP/APNG) keep **all frames**, timings and the loop.

## Installation

**Method 1 — from URL (recommended).** In Forge/A1111: **Extensions → Install from URL**,
paste:
```
https://github.com/xXIlRizzoXx/sd-forge-metadata-removal
```
then **Install** and **fully restart Forge** ("Reload UI" is not enough for a new extension).

**Method 2 — manual.** Copy (or `git clone`) the whole extension folder into Forge's
`extensions`:
```
<forge-folder>\extensions\sd-forge-metadata-removal\
```
(it must contain `scripts\metadata_stripper.py`), then fully restart Forge.

After the restart you'll find the section in txt2img/img2img, the **Metadata Removal** tab
at the top (between Extras and PNG Info) and the **Delete metadata** button in the Image
Browser.

No extra dependencies are needed: it uses **Pillow**, already included in Forge.

## Settings → Metadata Removal

- **Save mode** for on-disk operations (dedicated folder / copy in the same folder /
  overwrite). *(default: dedicated folder)*
- **Dedicated folder name** created in the images root (default `Metadata Removal`).
- **Recreate the source subfolder structure** (default **on**): in dedicated-folder mode,
  *Batch from Directory* recreates inside «Metadata Removal» the same folder structure as
  the source (e.g. `2026-05-28/keep/photo.png`). If off, all cleaned images go "flat" into
  the dedicated folder.
- **Suffix** for clean copies (default `_clean`).
- Also delete the sidecar `.txt` parameters file during automatic cleaning.

## Tab position (between Extras and PNG Info)

Forge orders the top tabs according to **Settings → UI Tab Order**. An extension tab can't
be inserted automatically among the native tabs, so it must be set **once** (then it stays
saved across restarts):

1. **Settings → UI Tab Order**
2. add **`Metadata Removal`** right after **`Extras`**
3. **Apply settings** → **Reload UI**

Result: `txt2img · img2img · Extras · Metadata Removal · PNG Info · …`

## Verification

After cleaning, drag the image into the **PNG Info** tab: no parameter should appear. The
extension also runs an automatic check and reports in the summary any files where metadata
might have remained.

## Security and robustness

The extension went through two multi-agent security audits (one final audit before
publication) and includes several protections:

- **Malicious images**: refuses images with disproportionate dimensions (*decompression
  bomb*) and animations with a huge number of frames, to avoid exhausting memory.
- **Animated GIFs**: if the encoder can't save the animation, it falls back to a single
  **still-cleaned** frame (the image never stays with its metadata).
- **Paths**: the dedicated folder name is reduced to a simple name, so a wrong value in
  Settings cannot write outside the images root; with *Include subfolders* on, cleaning
  from a drive root (e.g. `C:\`) is refused **in all modes**.
- **No accidental overwrites**: in the dedicated folder, names that already exist
  automatically get a numeric suffix, without deleting pre-existing files.
- **Safe writing**: each file is written to a temporary file first and then replaced
  atomically; an empty (0-byte) save is refused so the original is never ruined.
- **Best-effort auto-clean**: if automatic cleaning fails for an image, a **visible
  warning** appears in the UI (in addition to the console log), so you know that file may
  still contain metadata.

## License

[MIT](LICENSE) © xXIlRizzoXx
