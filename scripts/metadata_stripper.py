# -*- coding: utf-8 -*-
"""
Metadata Removal — extension for Stable Diffusion WebUI Forge (branch "neo").

What it adds:
  1) A "Metadata Removal" accordion section inside txt2img and img2img.
     When the checkbox is on, it automatically strips metadata from images
     generated in that tab (OFF by default). In this case the freshly
     generated file is ALWAYS cleaned in place (overwritten).

  2) A top-level "Metadata Removal" tab (placed between «Extras» and «PNG Info»)
     with three Extras-style sub-tabs:
       - Single Image        → cleans a single uploaded image (download);
       - Batch Process       → cleans several uploaded images (download);
       - Batch from Directory → cleans every image in a folder on disk.

  3) A "Delete metadata" button inside the Image Browser extension
     (AlUlkesh/stable-diffusion-webui-images-browser), next to "Delete":
     it cleans the currently selected image.

Overwrite or copy? For ON-DISK operations (Batch from Directory and the Image
Browser button) the behavior is decided by a single setting in
Settings → Metadata Removal: "overwrite the original" or "save a clean copy
in the same folder" (with a suffix in the name).

What is removed (everything the "PNG Info" tab could read):
  - PNG : all text chunks (parameters, prompt, workflow, Comment,
          Description, Software, XMP, ...) and any embedded EXIF.
  - JPEG/WEBP : EXIF (including UserComment with prompt and parameters), XMP, comments.

What is kept:
  - The alpha (transparency) channel, when present.
  - The pixels: for PNG the rewrite is lossless.

Dependencies: only Pillow, already included in Forge. No extra install.
"""

from __future__ import annotations

import html
import os
import tempfile
import traceback

import gradio as gr
from PIL import Image

from modules import script_callbacks, scripts, shared

try:  # tqdm ships with Forge: we use it for the console progress bars
    from tqdm import tqdm as _tqdm
except Exception:  # fallback: no bar, plain iteration
    def _tqdm(it, **kwargs):
        return it


def _log(msg: str) -> None:
    """Print to the console log what the extension is doing."""
    print(f"[Metadata Removal] {msg}")


# ---------------------------------------------------------------------------
# Supported formats
# ---------------------------------------------------------------------------
SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif", ".gif"}
_IMG_TYPES = [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif", ".gif"]

# Keys that, if still present after cleaning, signal residual metadata.
_LEAK_KEYS = (
    "parameters", "prompt", "workflow", "exif", "comment", "Comment",
    "Description", "Software", "Title", "Author", "Copyright",
    "XML:com.adobe.xmp", "xmp",
)


def _is_supported(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in SUPPORTED_EXTS


# ---------------------------------------------------------------------------
# Safety limits against malicious images
#   - decompression "bombs" (huge dimensions that exhaust RAM);
#   - animations with a huge number of frames (a small file can declare
#     thousands of them).
# ---------------------------------------------------------------------------
_MAX_FRAMES = 2000                          # max frames per animated image
_MAX_ANIM_TOTAL_PIXELS = 256 * 1024 * 1024  # ~268 Mpx total (~1 GB in RGBA)


def _pixel_cap() -> int:
    """Pixel threshold above which we refuse the image (anti decompression-bomb)."""
    cap = getattr(Image, "MAX_IMAGE_PIXELS", None)
    return cap if cap else 89478485  # Pillow's historical default (~89.5 Mpx)


def _check_bomb(size) -> None:
    """Raise ValueError if the image has too many pixels (possible decompression bomb)."""
    w, h = (size or (0, 0))
    if w and h and w * h > _pixel_cap():
        raise ValueError(
            f"Image too large ({w}x{h}px) — refused to avoid excessive memory use."
        )


# ---------------------------------------------------------------------------
# Core: rebuild an image without any metadata and save it
# ---------------------------------------------------------------------------
def _build_clean_static(src: Image.Image) -> Image.Image:
    """Rebuild a static frame with the same pixels but no metadata.

    paste() copies the pixels exactly (lossless) and does NOT copy the .info
    dictionary (unlike copy(), which keeps the metadata).
    Palette ('P') images are converted to RGB/RGBA: this preserves the
    appearance (and any transparency) while avoiding palette-handling
    differences across Pillow versions.
    """
    work = src
    if work.mode == "P":
        work = work.convert("RGBA" if "transparency" in work.info else "RGB")
    clean = Image.new(work.mode, work.size)
    clean.paste(work)
    return clean


def _clean_frames(im: Image.Image):
    """Extract ALL frames of an animated image, cleaned of metadata.

    Caps the frame count and the total pixels so a malicious animated image
    (a small file can declare thousands of frames) cannot exhaust memory.
    """
    n = getattr(im, "n_frames", 1)
    if n > _MAX_FRAMES:
        raise ValueError(f"Animated image has too many frames ({n} > {_MAX_FRAMES}).")
    frames, durations = [], []
    budget = _MAX_ANIM_TOTAL_PIXELS
    for i in range(n):
        im.seek(i)
        w, h = im.size
        budget -= (w or 0) * (h or 0)
        if budget < 0:
            raise ValueError("Animated image exceeds the total-pixel safety budget.")
        frame = im.convert("RGBA")
        clean = Image.new("RGBA", frame.size)
        clean.paste(frame)
        frames.append(clean)
        durations.append(im.info.get("duration", 100))
    return frames, durations


# Formats that support multiple frames (for animated images)
_ANIMATION_FORMATS = {"GIF", "WEBP", "PNG"}


def _save_static(img: Image.Image, tmp: str, out_format: str, save_kwargs: dict) -> None:
    """Save a single frame (handling JPEG's lack of transparency)."""
    if out_format == "JPEG" and img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")  # JPEG has no transparency
    img.save(tmp, format=out_format, **save_kwargs)


def strip_file(src_path: str, dst_path: str) -> None:
    """Read src_path, remove every metadata field and write to dst_path.

    Writes to a temporary file first and then replaces it, so an interruption
    never leaves a corrupted file in place of the original.
    Animated images (GIF/WEBP/APNG) keep all frames, timings and the loop;
    for PNG the rewrite is lossless.
    """
    with Image.open(src_path) as im:
        _check_bomb(im.size)  # refuse oversized images (anti decompression-bomb)
        im.load()
        is_anim = getattr(im, "is_animated", False) and getattr(im, "n_frames", 1) > 1
        if is_anim:
            loop = im.info.get("loop", 0)
            frames, durations = _clean_frames(im)
        else:
            clean = _build_clean_static(im)

    ext = os.path.splitext(dst_path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        out_format, save_kwargs = "JPEG", {"quality": 95}
    elif ext == ".webp":
        out_format, save_kwargs = "WEBP", {"quality": 95, "method": 6}
    elif ext in (".tiff", ".tif"):
        out_format, save_kwargs = "TIFF", {"compression": "tiff_lzw"}  # lossless, avoids huge TIFFs
    elif ext == ".bmp":
        out_format, save_kwargs = "BMP", {}
    elif ext == ".gif":
        out_format, save_kwargs = "GIF", {}
    else:
        out_format, save_kwargs = "PNG", {"optimize": True}

    dst_dir = os.path.dirname(dst_path) or "."
    os.makedirs(dst_dir, exist_ok=True)

    fd, tmp = tempfile.mkstemp(dir=dst_dir, suffix=ext or ".png")
    os.close(fd)
    try:
        if is_anim and out_format in _ANIMATION_FORMATS:
            anim_kwargs = dict(save_kwargs)
            anim_kwargs.update(save_all=True, append_images=frames[1:],
                               duration=durations, loop=loop)
            if out_format == "GIF":
                # Disposal as a SCALAR: frames are already complete (full-frame) and a
                # LIST crashes the encoder when Pillow collapses to a single frame those
                # that became identical after cleaning.
                anim_kwargs["disposal"] = 2
            try:
                frames[0].save(tmp, format=out_format, **anim_kwargs)
            except Exception:
                # Robust fallback: if the animation encoder still fails, save a single
                # CLEAN frame (what matters is removing the metadata).
                _save_static(frames[0], tmp, out_format, save_kwargs)
        else:
            _save_static(frames[0] if is_anim else clean, tmp, out_format, save_kwargs)
        if os.path.getsize(tmp) <= 0:
            raise OSError("the cleaned file is unexpectedly empty (0 bytes)")
        os.replace(tmp, dst_path)  # atomic replacement
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _residual_metadata(path: str):
    """Reopen the file and return the metadata keys still present (best-effort
    check): PNG text, known info keys, EXIF and typical TIFF tags."""
    try:
        with Image.open(path) as im:
            info = dict(im.info)
            text = dict(getattr(im, "text", {}) or {})
            leftovers = list(text.keys())
            for key in _LEAK_KEYS:
                if key in info and key not in leftovers:
                    leftovers.append(key)
            try:
                # For TIFF the main IFD holds STRUCTURAL tags (dimensions,
                # compression, ...): those are not metadata. So we exclude TIFF from
                # this check; its real metadata is checked on tag_v2 below.
                if getattr(im, "format", None) != "TIFF" and len(im.getexif()) and "exif" not in leftovers:
                    leftovers.append("exif")
            except Exception:
                pass
            tagv2 = getattr(im, "tag_v2", None)
            if tagv2:
                # 270 ImageDescription, 305 Software, 315 Artist, 33432 Copyright,
                # 700 XMP, 33723 IPTC, 37510 UserComment.
                for t in (270, 305, 315, 33432, 700, 33723, 37510):
                    if t in tagv2:
                        leftovers.append(f"tiff:{t}")
    except Exception:
        return []
    return leftovers


def _read_geninfo(path: str) -> str:
    """Read the parameters text (to refresh the Image Browser's info box)."""
    try:
        with Image.open(path) as im:
            return im.info.get("parameters", "") or ""
    except Exception:
        return ""


# Name shown for the section, the tab and the Settings submenu.
TAB_TITLE = "Metadata Removal"

# Output mode (for on-disk operations), chosen in Settings.
MODE_FOLDER = "Save to the «Metadata Removal» folder (in the images root)"
MODE_COPY = "Save a clean copy in the same folder"
MODE_OVERWRITE = "Overwrite the original image"


def _output_settings():
    """Read the save mode and the copy suffix from Settings."""
    mode = getattr(shared.opts, "mr_output_mode", MODE_FOLDER)
    suffix = getattr(shared.opts, "mr_copy_suffix", "_clean") or "_clean"
    return mode, suffix


def _images_root():
    """Root where Forge saves images (where txt2img-images, extras-images, ... live).

    Resolves any junction/symlink (e.g. Stability Matrix) to the real path.
    """
    try:
        master = (getattr(shared.opts, "outdir_samples", "") or "").strip()
        if master:
            return os.path.realpath(os.path.abspath(master))
        for key in ("outdir_txt2img_samples", "outdir_img2img_samples", "outdir_extras_samples"):
            d = (getattr(shared.opts, key, "") or "").strip()
            if d:
                return os.path.dirname(os.path.realpath(os.path.abspath(d)))
    except Exception:
        pass
    return os.path.realpath(os.path.abspath("output"))


def _extension_dir():
    """Dedicated folder (by default «Metadata Removal») inside the images root.

    The name is reduced to a SINGLE safe segment: path separators, drive
    letters (e.g. «C:») and «..» are neutralized, so a wrong value in Settings
    cannot write outside the images root.
    """
    raw = (getattr(shared.opts, "mr_folder_name", TAB_TITLE) or TAB_TITLE).strip() or TAB_TITLE
    name = os.path.basename(raw.replace("\\", "/").rstrip("/"))
    if not name or name in (".", "..") or os.path.splitdrive(name)[0]:
        name = TAB_TITLE
    root = _images_root()
    out = os.path.join(root, name)
    try:
        if os.path.commonpath([os.path.realpath(out), os.path.realpath(root)]) != os.path.realpath(root):
            out = os.path.join(root, TAB_TITLE)
    except Exception:
        out = os.path.join(root, TAB_TITLE)
    return out


def _ensure_output_dir():
    """Return the dedicated folder, creating it; fall back to a temp folder if it fails."""
    out_dir = _extension_dir()
    try:
        os.makedirs(out_dir, exist_ok=True)
        return out_dir
    except Exception:
        return tempfile.mkdtemp(prefix="metadata_removal_")


def _disk_dst(src_path: str, mode: str, suffix: str, used=None) -> str:
    """Destination path on disk based on the mode chosen in Settings.

    In «dedicated folder» and «overwrite» the file KEEPS its ORIGINAL NAME; in the
    dedicated folder, if a file with that name already exists, a numeric suffix
    (_1, _2, …) is added so nothing is ever overwritten. Only in «save a clean copy
    in the same folder» mode is the Settings suffix used.
    """
    if mode == MODE_OVERWRITE:
        return src_path
    if mode == MODE_FOLDER:
        base = os.path.basename(src_path)
        if used is not None:
            return _unique_dst(_extension_dir(), base, used)
        return os.path.join(_extension_dir(), base)
    # «Copy» mode: name = <original><suffix><.ext> in the same folder. The suffix
    # (default «_clean») is RESERVED for clean copies: if a file with that name
    # already exists it is replaced (idempotent — re-cleaning updates the copy,
    # without creating duplicates «_clean_1», «_clean_2», …).
    root, ext = os.path.splitext(src_path)
    return f"{root}{suffix}{ext}"


# ===========================================================================
# 1) Automatic cleaning after generation (section in txt2img / img2img)
#    In this case the freshly generated file is ALWAYS overwritten in place.
# ===========================================================================
class MetadataRemovalScript(scripts.Script):
    def title(self):
        return TAB_TITLE

    def show(self, is_img2img):
        return scripts.AlwaysVisible

    def ui(self, is_img2img):
        with gr.Accordion(TAB_TITLE, open=False):
            enabled = gr.Checkbox(
                value=False,
                label="Strip metadata from saved images",
            )
            gr.HTML(
                "<div style='opacity:.7;font-size:.9em;line-height:1.4'>"
                "When enabled, every image generated in this tab is saved without "
                "prompt, parameters or any other data readable by «PNG Info» "
                "(the file is cleaned in place). Leave it off to save normally.</div>"
            )
        return [enabled]

    def process(self, p, enabled):
        # Read by the global callback _on_image_saved.
        p._mr_enabled = bool(enabled)
        p._mr_stripped = []
        p._mr_failed = []

    def postprocess(self, p, processed, enabled):
        if not getattr(p, "_mr_enabled", False):
            return

        failed = getattr(p, "_mr_failed", []) or []
        if failed:
            print("[Metadata Removal] WARNING: %d image(s) could NOT be cleaned "
                  "and may still contain metadata:" % len(failed))
            for f in failed:
                print("    -", f)
            # VISIBLE warning in the UI (not just the console): whoever enabled cleaning
            # to avoid leaking prompts must notice if a file was left dirty.
            try:
                gr.Warning("Metadata Removal: %d image(s) could NOT be cleaned and may "
                           "still contain metadata (see console for the list)." % len(failed))
            except Exception:
                pass

        # If requested, also delete any .txt parameters file saved next to the image
        # (the webui's "save_txt" option), written AFTER the save.
        if not bool(getattr(shared.opts, "mr_remove_sidecar_txt", True)):
            return
        for path in getattr(p, "_mr_stripped", []) or []:
            txt = os.path.splitext(path)[0] + ".txt"
            try:
                if os.path.isfile(txt):
                    os.remove(txt)
            except OSError:
                pass


def _on_image_saved(params):
    """Runs after the webui has saved an image: clean it in place."""
    p = params.p
    if not getattr(p, "_mr_enabled", False):
        return

    path = params.filename
    if not path or not os.path.isfile(path) or not _is_supported(path):
        return

    try:
        strip_file(path, path)  # auto-clean: always in place
        stripped = getattr(p, "_mr_stripped", None)
        if stripped is not None:
            stripped.append(path)
        _log(f"auto-clean: stripped metadata from {os.path.basename(path)}")
    except Exception:
        failed = getattr(p, "_mr_failed", None)
        if failed is not None:
            failed.append(path)
        print("[Metadata Removal] ERROR while cleaning:", path)
        traceback.print_exc()


# ===========================================================================
# 2) Top tab: batch cleaning (Extras-style)
# ===========================================================================
def _collect_folder_files(folder: str, recursive: bool, skip_suffix=None, skip_dir=None):
    """List the supported images. Skip files with the suffix (copy mode) and files
    inside skip_dir (the output folder, in dedicated-folder mode)."""
    found = []
    skip_abs = (os.path.abspath(skip_dir) + os.sep) if skip_dir else None
    walker = os.walk(folder) if recursive else [(folder, [], os.listdir(folder))]
    for root, _dirs, fnames in walker:
        if skip_abs and (os.path.abspath(root) + os.sep).startswith(skip_abs):
            continue
        for fn in fnames:
            full = os.path.join(root, fn)
            if not (os.path.isfile(full) and _is_supported(full)):
                continue
            if skip_suffix and os.path.splitext(fn)[0].endswith(skip_suffix):
                continue  # avoid re-cleaning (and re-suffixing) copies already created
            found.append(full)
    return found


def _unique_dst(out_dir, base, used):
    """A free path in out_dir, avoiding name collisions."""
    dst = os.path.join(out_dir, base)
    i = 1
    while dst in used or os.path.exists(dst):
        name, ext = os.path.splitext(base)
        dst = os.path.join(out_dir, f"{name}_{i}{ext}")
        i += 1
    used.add(dst)
    return dst


def clean_single(file_path, progress=gr.Progress()):
    """Single Image: clean an uploaded image and give a preview + download."""
    src = file_path if isinstance(file_path, str) else getattr(file_path, "name", None)
    if not src:
        return _msg("Please upload an image.", "warn"), gr.update(visible=False), gr.update(visible=False)
    if not _is_supported(src):
        return _msg("Unsupported image format.", "warn"), gr.update(visible=False), gr.update(visible=False)

    progress(0.0, desc="Cleaning metadata")
    out_dir = _ensure_output_dir()
    dst = _unique_dst(out_dir, os.path.basename(src), set())  # same name (+ suffix on collision)
    _log(f"Single Image: cleaning {os.path.basename(src)} → {out_dir}")
    try:
        strip_file(src, dst)
    except Exception as e:  # noqa: BLE001
        _log(f"Single Image: ERROR on {os.path.basename(src)}: {e}")
        return _msg(f"Error: {html.escape(str(e))}", "err"), gr.update(visible=False), gr.update(visible=False)
    progress(1.0, desc="Done")
    _log(f"Single Image: done → {os.path.basename(dst)}")

    residual = _residual_metadata(dst)
    head = f"<b>Done.</b> Saved to <code>{html.escape(out_dir)}</code> (also downloadable below)."
    return (
        _msg(head + _residual_block(residual), "warn" if residual else "info"),
        gr.update(value=dst, visible=True),
        gr.update(value=dst, visible=True),
    )


def clean_batch(file_paths, progress=gr.Progress()):
    """Batch Process: clean several uploaded images and offer them for download."""
    if not file_paths:
        return _msg("Please add one or more images.", "warn"), gr.update(visible=False)
    paths = [f if isinstance(f, str) else getattr(f, "name", None) for f in file_paths]
    paths = [p for p in paths if p and _is_supported(p)]
    if not paths:
        return _msg("No images in a supported format.", "warn"), gr.update(visible=False)

    out_dir = _ensure_output_dir()
    outputs, ok, errors, residual = [], 0, [], []
    used = set()
    n = len(paths)
    _log(f"Batch Process: cleaning {n} uploaded image(s) → {out_dir}")
    for i, src in enumerate(_tqdm(paths, desc="[Metadata Removal] Batch", unit="img")):
        progress(i / n, desc=f"Cleaning metadata ({i + 1}/{n})")
        dst = _unique_dst(out_dir, os.path.basename(src), used)  # same name (+ suffix on collision)
        try:
            strip_file(src, dst)
            outputs.append(dst)
            ok += 1
            if _residual_metadata(dst):
                residual.append(dst)
        except Exception as e:  # noqa: BLE001
            errors.append((os.path.basename(src), str(e)))
    progress(1.0, desc="Done")
    _log(f"Batch Process: done — {ok}/{n} cleaned, {len(errors)} error(s).")

    return (
        _batch_report(ok, errors, residual, len(paths), out_dir),
        gr.update(value=(outputs or None), visible=bool(outputs)),
    )


def clean_directory(input_dir, recursive, progress=gr.Progress()):
    """Batch from Directory: clean every image in a folder on disk.

    Overwrite or copy (in the same folder) depends on the Settings.
    """
    input_dir = (input_dir or "").strip().strip('"')
    if not input_dir:
        return _msg("Please enter an input directory.", "warn")
    if not os.path.isdir(input_dir):
        return _msg(f"Input directory not found:<br><code>{html.escape(input_dir)}</code>", "err")

    mode, suffix = _output_settings()

    # Safety: with subfolders on, refuse a drive root (e.g. C:\) in ANY mode:
    # overwrite would rewrite every image on the system; dedicated-folder/copy would
    # scan the whole drive creating a flood of copies.
    if bool(recursive):
        norm = os.path.abspath(input_dir)
        if os.path.dirname(norm) == norm:  # it is a drive root (C:\, D:\, /)
            return _msg(
                "Refusing to scan a whole drive root with subfolders. Choose a more "
                "specific folder, or turn off «Include subfolders».", "err")

    skip_suffix = suffix if mode == MODE_COPY else None
    skip_dir = _extension_dir() if mode == MODE_FOLDER else None

    try:
        files = _collect_folder_files(input_dir, bool(recursive), skip_suffix=skip_suffix, skip_dir=skip_dir)
    except (OSError, ValueError, UnicodeError) as e:
        return _msg(
            "Cannot read the directory (permissions or unavailable path):"
            f"<br><code>{html.escape(str(e))}</code>", "err")
    if not files:
        return _msg("No supported images found in this directory.", "warn")

    # In dedicated-folder mode, if requested, recreate the source subfolder structure
    # inside the dedicated folder (instead of flattening everything).
    preserve = (mode == MODE_FOLDER) and bool(getattr(shared.opts, "mr_preserve_structure", True))
    ext_dir = _extension_dir()
    input_abs = os.path.abspath(input_dir)

    used = set()
    ok, errors, residual = 0, [], []
    n = len(files)
    _log(f"Batch from Directory: cleaning {n} image(s) from {input_dir} (mode: {mode}, preserve structure: {preserve})")
    for i, src in enumerate(_tqdm(files, desc="[Metadata Removal] Directory", unit="img")):
        progress(i / n, desc=f"Cleaning metadata ({i + 1}/{n})")
        try:
            if preserve:
                # path relative to the input folder, recreated inside the dedicated folder
                rel = os.path.relpath(os.path.abspath(src), input_abs)
                dst = _unique_dst(ext_dir, rel, used)
            else:
                dst = _disk_dst(src, mode, suffix, used)
            strip_file(src, dst)
            ok += 1
            if _residual_metadata(dst):
                residual.append(dst)
        except Exception as e:  # noqa: BLE001
            errors.append((src, str(e)))
    progress(1.0, desc="Done")
    _log(f"Batch from Directory: done — {ok}/{n} cleaned, {len(errors)} error(s).")

    return _dir_report(ok, errors, residual, mode, suffix, len(files))


def pnginfo_clean(pil_image, fname=""):
    """PNG Info: clean the uploaded image, save it into the dedicated folder and offer it
    as a downloadable copy (the PNG Info image is an upload, so it is always a copy).

    'fname' is the original name recovered from the frontend (the PIL object loses it)."""
    if pil_image is None:
        return gr.update(visible=False), _msg("Load an image into «PNG Info» first.", "warn")
    try:
        _check_bomb(getattr(pil_image, "size", None))
        # File name: first the one passed from the frontend (JS reads the image URL),
        # then the PIL .filename if any, finally "cleaned". We always save as PNG.
        cand = os.path.basename((fname or "").strip())
        if not cand:
            cand = os.path.basename(getattr(pil_image, "filename", "") or "").strip()
        name = (os.path.splitext(cand)[0] if cand else "cleaned") or "cleaned"
        clean = _build_clean_static(pil_image)
        out_dir = _ensure_output_dir()
        dst = _unique_dst(out_dir, f"{name}.png", set())
        clean.save(dst, format="PNG", optimize=True)
        _log(f"PNG Info: cleaned → {os.path.basename(dst)}")
        residual = _residual_metadata(dst)
        head = (f"<b>Done.</b> Saved to <code>{html.escape(out_dir)}</code> as "
                f"<code>{html.escape(os.path.basename(dst))}</code> (download below).")
        return gr.update(value=dst, visible=True), _msg(head + _residual_block(residual), "warn" if residual else "info")
    except Exception as e:  # noqa: BLE001
        return gr.update(visible=False), _msg(f"Error: {html.escape(str(e))}", "err")


# ---------------------------------------------------------------------------
# Messages / reports (simple readable HTML)
# ---------------------------------------------------------------------------
def _msg(text, kind="info"):
    color = {"info": "#3b82f6", "warn": "#d97706", "err": "#dc2626"}.get(kind, "#3b82f6")
    return (
        f"<div style='padding:10px 12px;border-left:4px solid {color};"
        f"background:rgba(127,127,127,.08);border-radius:4px'>{text}</div>"
    )


def _errors_block(errors):
    if not errors:
        return ""
    rows = "".join(
        f"<li><code>{html.escape(os.path.basename(p))}</code> — {html.escape(msg)}</li>"
        for p, msg in errors[:20]
    )
    extra = f"<li>… and {len(errors) - 20} more</li>" if len(errors) > 20 else ""
    return f"<p>⚠️ Errors ({len(errors)}):</p><ul>{rows}{extra}</ul>"


def _residual_block(residual):
    if not residual:
        return ""
    rows = "".join(f"<li><code>{html.escape(os.path.basename(p))}</code></li>" for p in residual[:20])
    extra = f"<li>… and {len(residual) - 20} more</li>" if len(residual) > 20 else ""
    return (
        f"<p>❗ Warning: metadata still detected on these files "
        f"({len(residual)}):</p><ul>{rows}{extra}</ul>"
    )


def _batch_report(ok, errors, residual, total, out_dir):
    head = (
        f"<b>Done.</b><br>"
        f"Images received: {total} — cleaned: {ok} — errors: {len(errors)}.<br>"
        f"Saved to <code>{html.escape(out_dir)}</code> — also downloadable below."
    )
    kind = "err" if errors else ("warn" if residual else "info")
    return _msg(head + _residual_block(residual) + _errors_block(errors), kind)


def _dir_report(ok, errors, residual, mode, suffix, total):
    if mode == MODE_OVERWRITE:
        where = "overwriting the originals"
    elif mode == MODE_FOLDER:
        struct = " (subfolder structure preserved)" if getattr(shared.opts, "mr_preserve_structure", True) else ""
        where = f"in <code>{html.escape(_extension_dir())}</code>{struct}"
    else:
        where = f"as clean copies (suffix <code>{html.escape(suffix)}</code>) in the same folders"
    head = (
        f"<b>Done.</b><br>"
        f"Images found: {total} — cleaned: {ok} — errors: {len(errors)}.<br>"
        f"Saved {where}."
    )
    kind = "err" if errors else ("warn" if residual else "info")
    return _msg(head + _residual_block(residual) + _errors_block(errors), kind)


# ---------------------------------------------------------------------------
# Top tab UI (Extras-style: Single Image / Batch Process / Batch from Directory)
# ---------------------------------------------------------------------------
def on_ui_tabs():
    with gr.Blocks(analytics_enabled=False) as ui_tab:
        gr.HTML(
            "<p style='opacity:.75;margin:.4em 0 1em'>Removes everything readable by "
            "«PNG Info» (prompt, parameters, EXIF, XMP, …) from your images. "
            "PNG is rewritten losslessly; JPEG/WEBP are re-encoded at high quality (95).</p>"
        )
        with gr.Tabs(elem_id="metadata_removal_tabs"):

            with gr.TabItem("Single Image", elem_id="mr_tab_single"):
                with gr.Row():
                    with gr.Column():
                        single_in = gr.File(
                            label="Image",
                            file_count="single",
                            file_types=_IMG_TYPES,
                            type="filepath",
                        )
                        single_btn = gr.Button("Clean metadata", variant="primary")
                    with gr.Column():
                        single_preview = gr.Image(label="Cleaned image", interactive=False, height="50vh", visible=False)
                        single_download = gr.File(label="Download cleaned image", interactive=False, visible=False)
                single_status = gr.HTML()
                single_btn.click(
                    fn=lambda: ("", gr.update(visible=False), gr.update(visible=False)),
                    outputs=[single_status, single_preview, single_download],
                ).then(
                    fn=clean_single,
                    inputs=[single_in],
                    outputs=[single_status, single_preview, single_download],
                )

            with gr.TabItem("Batch Process", elem_id="mr_tab_batch"):
                batch_in = gr.File(
                    label="Images (drop here or click to choose)",
                    file_count="multiple",
                    file_types=_IMG_TYPES,
                    type="filepath",
                    height=320,
                )
                batch_btn = gr.Button("Clean metadata", variant="primary")
                batch_status = gr.HTML()
                batch_download = gr.File(label="Download cleaned images", file_count="multiple", interactive=False, height=320, visible=False)
                batch_btn.click(
                    fn=lambda: ("", gr.update(visible=False)),
                    outputs=[batch_status, batch_download],
                ).then(
                    fn=clean_batch,
                    inputs=[batch_in],
                    outputs=[batch_status, batch_download],
                )

            with gr.TabItem("Batch from Directory", elem_id="mr_tab_dir"):
                dir_in = gr.Textbox(label="Input directory", placeholder=r"e.g. F:\Stable-Diffusion\outputs\txt2img-images")
                dir_recursive = gr.Checkbox(value=False, label="Include subfolders")
                gr.HTML(
                    "<div style='opacity:.7;font-size:.9em;line-height:1.4'>"
                    "Where cleaned images are saved (dedicated folder / overwrite / copy in the "
                    "same folder) is configured in <b>Settings → Metadata Removal</b>.</div>"
                )
                dir_btn = gr.Button("Clean metadata", variant="primary")
                dir_status = gr.HTML()
                dir_btn.click(
                    # Immediate feedback + visible box: this gives Gradio's progress bar
                    # somewhere to draw (on an empty HTML it isn't visible and it looks
                    # like "nothing happens").
                    fn=lambda: _msg("⏳ Cleaning metadata… please wait.", "info"),
                    outputs=[dir_status],
                ).then(
                    fn=clean_directory,
                    inputs=[dir_in, dir_recursive],
                    outputs=[dir_status],
                )

    return [(ui_tab, TAB_TITLE, "metadata_removal_tab")]


# ===========================================================================
# 3) Image Browser integration (AlUlkesh): "Delete metadata" button
#    injected next to the "Delete" button, without modifying that extension.
# ===========================================================================
def ib_delete_metadata(img_file_name):
    """Clean the image selected in the Image Browser according to the Settings.

    Returns TWO values: the parameters text for the «Generation Info» box (empty if the
    image was overwritten; unchanged if a copy was made) and a confirmation HTML message
    to show below the button — so the action is visible even when the original stays
    intact (e.g. dedicated-folder mode)."""
    try:
        path = img_file_name
        if not path or not os.path.isfile(path) or not _is_supported(path):
            geninfo = _read_geninfo(path) if path else ""
            return geninfo, _msg("Select a supported image first.", "warn")
        mode, suffix = _output_settings()
        dst = _disk_dst(path, mode, suffix, set())  # original name (+ suffix on collision)
        strip_file(path, dst)
        _log(f"Image Browser: cleaned {os.path.basename(path)} → {os.path.basename(dst)} (mode: {mode})")
        if mode == MODE_OVERWRITE:
            note = "Metadata removed (original overwritten)."
        elif mode == MODE_FOLDER:
            note = (f"Cleaned copy saved in <code>{html.escape(_extension_dir())}</code> "
                    f"as <code>{html.escape(os.path.basename(dst))}</code>.")
        else:
            note = f"Clean copy saved as <code>{html.escape(os.path.basename(dst))}</code>."
        return _read_geninfo(path), _msg("<b>Done.</b> " + note, "info")
    except Exception:
        print("[Metadata Removal] ERROR cleaning from Image Browser:", img_file_name)
        traceback.print_exc()
        geninfo = _read_geninfo(img_file_name) if img_file_name else ""
        return geninfo, _msg("Error while cleaning (see console).", "err")


# State to hook the Image Browser components as they are created.
_IB = {"last_base": None, "pending": {}}
_DEL_SUFFIX = "_image_browser_del_img_btn"


def _on_after_component(component, **kwargs):
    """Inject the "Delete metadata" button next to the Image Browser's "Delete".

    For each of its tabs the Image Browser creates, in this order: the Delete button
    (elem_id "<base>_image_browser_del_img_btn"), then the "Generation Info" Textbox,
    then the "File Name" Textbox (which holds the path). We capture these three
    components and wire the click. Everything in try/except: if something doesn't match,
    the Image Browser stays intact.
    """
    try:
        eid = getattr(component, "elem_id", None) or ""

        # --- "PNG Info" tab: "Remove metadata" button under the source image ---
        if eid == "pnginfo_image":
            mr_btn = gr.Button("Remove metadata", elem_id="metadata_removal_pnginfo_btn", variant="primary")
            mr_status = gr.HTML()
            mr_download = gr.File(label="Cleaned image (download)", interactive=False, visible=False)
            mr_fname = gr.Textbox(visible=False, elem_id="metadata_removal_pnginfo_fname")
            # The original name of the uploaded image doesn't reach the PIL object (Gradio
            # loses it). We recover it from the frontend by reading the URL of the image
            # shown in the PNG Info tab (Gradio keeps the original name in the file path).
            grab_name_js = r"""(img, fname) => {
              try {
                const root = document.querySelector('#pnginfo_image');
                let name = '';
                if (root) {
                  for (const el of root.querySelectorAll('img')) {
                    if (el.src && el.src.indexOf('file=') !== -1) {
                      let u = decodeURIComponent(el.src.split('file=')[1].split('?')[0]);
                      u = u.replace(/\\/g, '/');
                      name = u.substring(u.lastIndexOf('/') + 1);
                      break;
                    }
                  }
                }
                return [img, name];
              } catch (e) { return [img, '']; }
            }"""
            mr_btn.click(fn=pnginfo_clean, inputs=[component, mr_fname],
                         outputs=[mr_download, mr_status], js=grab_name_js)
            return

        if eid.endswith(_DEL_SUFFIX):
            base = eid[: -len(_DEL_SUFFIX)]
            btn = gr.Button("Delete metadata", elem_id=f"{base}_metadata_removal_btn")
            status = gr.HTML()  # visual confirmation below the button
            _IB["pending"][base] = {"button": btn, "status": status, "info": None}
            _IB["last_base"] = base
            return

        base = _IB["last_base"]
        if not base:
            return
        entry = _IB["pending"].get(base)
        if not entry:
            return

        label = getattr(component, "label", None)
        if isinstance(component, gr.Textbox) and label == "Generation Info" and entry["info"] is None:
            entry["info"] = component
        elif isinstance(component, gr.Textbox) and label == "File Name":
            # "File Name" is the last of the three: now we wire the button.
            if entry["button"] is not None and entry["info"] is not None:
                entry["button"].click(
                    # Immediate "in progress" feedback: the box becomes visible and Gradio
                    # draws the loading animation on it while it works.
                    fn=lambda: _msg("⏳ Cleaning metadata…", "info"),
                    outputs=[entry["status"]],
                ).then(
                    fn=ib_delete_metadata,
                    inputs=[component],
                    outputs=[entry["info"], entry["status"]],
                )
            _IB["last_base"] = None  # close the matching window for this tab
    except Exception:
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Settings (the "Metadata Removal" submenu)
# ---------------------------------------------------------------------------
def on_ui_settings():
    section = ("metadata_removal", TAB_TITLE)
    shared.opts.add_option(
        "mr_output_mode",
        shared.OptionInfo(
            MODE_FOLDER,
            "When cleaning images on disk (Batch from Directory and the Image Browser button):",
            gr.Radio,
            {"choices": [MODE_FOLDER, MODE_COPY, MODE_OVERWRITE]},
            section=section,
        ),
    )
    shared.opts.add_option(
        "mr_folder_name",
        shared.OptionInfo(
            TAB_TITLE,
            "Dedicated folder name (created inside the images root, next to txt2img-images, extras-images, …)",
            section=section,
        ),
    )
    shared.opts.add_option(
        "mr_preserve_structure",
        shared.OptionInfo(
            True,
            "Dedicated-folder mode: recreate the source subfolder structure (Batch from Directory). "
            "If off, all cleaned images are placed flat into the dedicated folder.",
            section=section,
        ),
    )
    shared.opts.add_option(
        "mr_copy_suffix",
        shared.OptionInfo(
            "_clean",
            "Filename suffix for clean copies (used in 'Save a clean copy' mode)",
            section=section,
        ),
    )
    shared.opts.add_option(
        "mr_remove_sidecar_txt",
        shared.OptionInfo(
            True,
            "During automatic cleaning, also delete the sidecar .txt parameters file saved next to the image",
            section=section,
        ),
    )


# ---------------------------------------------------------------------------
# Tab positioning (between «Extras» and «PNG Info»):
# Forge orders the top tabs according to Settings -> UI Tab Order. An extension
# tab CANNOT be reliably inserted automatically among the native tabs: at extension
# startup the tab is not yet a valid "choice" and would be dropped. To position it,
# just set it ONCE (it stays saved):
#   Settings -> UI Tab Order -> add "Metadata Removal" after "Extras"
#   -> Apply settings -> Reload UI
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Callback registration
# ---------------------------------------------------------------------------
script_callbacks.on_image_saved(_on_image_saved)
script_callbacks.on_ui_tabs(on_ui_tabs)
script_callbacks.on_ui_settings(on_ui_settings)
script_callbacks.on_after_component(_on_after_component)
