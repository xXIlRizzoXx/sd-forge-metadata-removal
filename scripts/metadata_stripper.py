# -*- coding: utf-8 -*-
"""
Metadata Removal — estensione per Stable Diffusion WebUI Forge (branch "neo").
(L'interfaccia utente è in inglese; i commenti nel codice sono in italiano per Matteo.)

Cosa aggiunge:
  1) Una sezione a fisarmonica "Metadata Removal" dentro txt2img e img2img.
     Se la spunta è attiva, ripulisce automaticamente i metadati dalle immagini
     generate in quella scheda (di default SPENTA). In questo caso il file appena
     generato viene SEMPRE ripulito sul posto (sovrascritto).

  2) Una scheda in alto "Metadata Removal" (posizionata tra «Extras» e «PNG Info»)
     con tre sotto-schede in stile Extras:
       - Single Image        → ripulisce una singola immagine caricata (download);
       - Batch Process       → ripulisce più immagini caricate (download);
       - Batch from Directory → ripulisce tutte le immagini di una cartella su disco.

  3) Un pulsante "Delete metadata" dentro l'estensione Image Browser
     (AlUlkesh/stable-diffusion-webui-images-browser), accanto al pulsante "Delete":
     ripulisce l'immagine attualmente selezionata.

Sovrascrivi o copia? Per le operazioni SU DISCO (Batch from Directory e pulsante
dell'Image Browser) il comportamento è deciso da un'unica impostazione in
Settings → Metadata Removal: "sovrascrivi l'originale" oppure "salva una copia
pulita nella stessa cartella" (con un suffisso nel nome).

Cosa viene rimosso (tutto ciò che il tab "PNG Info" potrebbe leggere):
  - PNG : tutti i blocchi di testo (parameters, prompt, workflow, Comment,
          Description, Software, XMP, ...) e qualsiasi EXIF incorporato.
  - JPEG/WEBP : EXIF (incluso UserComment con prompt e parametri), XMP, commenti.

Cosa viene mantenuto:
  - Il profilo colore ICC (fedeltà dei colori) — mantenuto sempre.
  - Il canale di trasparenza (alfa), quando presente.
  - I pixel: per il PNG la riscrittura è senza perdita di qualità.

Dipendenze: solo Pillow, già incluso in Forge. Nessuna installazione aggiuntiva.
"""

from __future__ import annotations

import html
import os
import tempfile
import traceback

import gradio as gr
from PIL import Image

from modules import script_callbacks, scripts, shared

try:  # tqdm è incluso in Forge: lo usiamo per le barre di avanzamento nella console
    from tqdm import tqdm as _tqdm
except Exception:  # ripiego: nessuna barra, semplice iterazione
    def _tqdm(it, **kwargs):
        return it


def _log(msg: str) -> None:
    """Stampa nel log della console cosa sta facendo l'estensione."""
    print(f"[Metadata Removal] {msg}")


# ---------------------------------------------------------------------------
# Formati gestiti
# ---------------------------------------------------------------------------
SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif", ".gif"}
_IMG_TYPES = [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif", ".gif"]

# Chiavi che, se ancora presenti dopo la pulizia, segnalano metadati residui.
_LEAK_KEYS = (
    "parameters", "prompt", "workflow", "exif", "comment", "Comment",
    "Description", "Software", "Title", "Author", "Copyright",
    "XML:com.adobe.xmp", "xmp",
)


def _is_supported(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in SUPPORTED_EXTS


# ---------------------------------------------------------------------------
# Limiti di sicurezza contro immagini malevole
#   - "bomb" di decompressione (dimensioni enormi che esauriscono la RAM);
#   - animazioni con un numero enorme di fotogrammi (un file piccolo può
#     dichiararne migliaia).
# ---------------------------------------------------------------------------
_MAX_FRAMES = 2000                          # max fotogrammi per immagine animata
_MAX_ANIM_TOTAL_PIXELS = 256 * 1024 * 1024  # ~268 Mpx totali (~1 GB in RGBA)


def _pixel_cap() -> int:
    """Soglia di pixel oltre la quale rifiutiamo l'immagine (anti decompression-bomb)."""
    cap = getattr(Image, "MAX_IMAGE_PIXELS", None)
    return cap if cap else 89478485  # default storico di Pillow (~89,5 Mpx)


def _check_bomb(size) -> None:
    """Solleva ValueError se l'immagine ha troppi pixel (possibile bomb di decompressione)."""
    w, h = (size or (0, 0))
    if w and h and w * h > _pixel_cap():
        raise ValueError(
            f"Image too large ({w}x{h}px) — refused to avoid excessive memory use."
        )


# ---------------------------------------------------------------------------
# Nucleo: ricostruisce un'immagine senza alcun metadato e la salva
# ---------------------------------------------------------------------------
def _build_clean_static(src: Image.Image) -> Image.Image:
    """Ricostruisce un fotogramma statico con gli stessi pixel ma senza metadati.

    paste() copia i pixel esattamente (senza perdita) e NON copia il dizionario
    .info (al contrario di copy(), che invece conserva i metadati).
    Le immagini a palette ('P') vengono portate a RGB/RGBA: così si preserva
    l'aspetto (e l'eventuale trasparenza) evitando differenze di gestione della
    palette tra le varie versioni di Pillow.
    """
    work = src
    if work.mode == "P":
        work = work.convert("RGBA" if "transparency" in work.info else "RGB")
    clean = Image.new(work.mode, work.size)
    clean.paste(work)
    return clean


def _clean_frames(im: Image.Image):
    """Estrae TUTTI i fotogrammi di un'immagine animata, ripuliti dai metadati.

    Limita il numero di fotogrammi e i pixel totali per non esaurire la memoria con
    immagini animate malevole (un file piccolo può dichiarare migliaia di fotogrammi).
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


# Formati che supportano più fotogrammi (per le immagini animate)
_ANIMATION_FORMATS = {"GIF", "WEBP", "PNG"}
# Formati che supportano il profilo colore ICC
_ICC_FORMATS = {"PNG", "JPEG", "WEBP", "TIFF"}


def _save_static(img: Image.Image, tmp: str, out_format: str, save_kwargs: dict) -> None:
    """Salva un singolo fotogramma (gestendo la mancanza di trasparenza nel JPEG)."""
    if out_format == "JPEG" and img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")  # il JPEG non ha trasparenza
    img.save(tmp, format=out_format, **save_kwargs)


def strip_file(src_path: str, dst_path: str, keep_icc: bool = True) -> None:
    """Legge src_path, rimuove ogni metadato e scrive in dst_path.

    Scrive prima su un file temporaneo e poi lo sostituisce, così un'eventuale
    interruzione non lascia mai un file danneggiato al posto dell'originale.
    Le immagini animate (GIF/WEBP/APNG) mantengono tutti i fotogrammi, i tempi e
    il loop; per il PNG la riscrittura è senza perdita di qualità.
    """
    with Image.open(src_path) as im:
        _check_bomb(im.size)  # rifiuta immagini sproporzionate (anti decompression-bomb)
        im.load()
        icc = im.info.get("icc_profile") if keep_icc else None
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
        out_format, save_kwargs = "TIFF", {"compression": "tiff_lzw"}  # senza perdita, evita TIFF enormi
    elif ext == ".bmp":
        out_format, save_kwargs = "BMP", {}
    elif ext == ".gif":
        out_format, save_kwargs = "GIF", {}
    else:
        out_format, save_kwargs = "PNG", {"optimize": True}

    if icc and out_format in _ICC_FORMATS:
        save_kwargs["icc_profile"] = icc

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
                # Disposal come SCALARE: i fotogrammi sono già completi (full-frame) e
                # una LISTA manda in crash l'encoder quando Pillow riduce a un solo
                # fotogramma quelli divenuti identici dopo la pulizia.
                anim_kwargs["disposal"] = 2
            try:
                frames[0].save(tmp, format=out_format, **anim_kwargs)
            except Exception:
                # Ripiego robusto: se l'encoder d'animazione fallisce comunque, salva un
                # singolo fotogramma PULITO (l'importante è rimuovere i metadati).
                _save_static(frames[0], tmp, out_format, save_kwargs)
        else:
            _save_static(frames[0] if is_anim else clean, tmp, out_format, save_kwargs)
        if os.path.getsize(tmp) <= 0:
            raise OSError("the cleaned file is unexpectedly empty (0 bytes)")
        os.replace(tmp, dst_path)  # sostituzione atomica
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _residual_metadata(path: str):
    """Riapre il file e restituisce le chiavi di metadati ancora presenti (verifica
    best-effort): testo PNG, chiavi note in info, EXIF e tag TIFF tipici."""
    try:
        with Image.open(path) as im:
            info = dict(im.info)
            text = dict(getattr(im, "text", {}) or {})
            leftovers = list(text.keys())
            for key in _LEAK_KEYS:
                if key in info and key not in leftovers:
                    leftovers.append(key)
            try:
                # Per il TIFF la IFD principale contiene tag STRUTTURALI (dimensioni,
                # compressione, ...): non sono metadati. Quindi escludiamo il TIFF da
                # questo controllo; i suoi metadati veri sono controllati su tag_v2 sotto.
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
    """Legge il testo dei parametri (per aggiornare il riquadro info dell'Image Browser)."""
    try:
        with Image.open(path) as im:
            return im.info.get("parameters", "") or ""
    except Exception:
        return ""


# Nome mostrato per la sezione, la scheda e il sottomenu di Settings.
TAB_TITLE = "Metadata Removal"

# Modalità di output (per le operazioni su disco), scelta nei Settings.
MODE_FOLDER = "Save to the «Metadata Removal» folder (in the images root)"
MODE_COPY = "Save a clean copy in the same folder"
MODE_OVERWRITE = "Overwrite the original image"


def _output_settings():
    """Legge dai Settings la modalità di salvataggio e il suffisso per le copie."""
    mode = getattr(shared.opts, "mr_output_mode", MODE_FOLDER)
    suffix = getattr(shared.opts, "mr_copy_suffix", "_clean") or "_clean"
    return mode, suffix


def _images_root():
    """Radice dove Forge salva le immagini (dove stanno txt2img-images, extras-images, ...).

    Risolve eventuali junction/symlink (es. Stability Matrix) per il percorso reale.
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
    """Cartella dedicata (di default «Metadata Removal») dentro la radice immagini.

    Il nome viene ridotto a un SINGOLO segmento sicuro: separatori di percorso,
    lettere di unità (es. «C:») e «..» vengono neutralizzati, così un valore errato
    nei Settings non può far scrivere fuori dalla radice delle immagini.
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
    """Restituisce la cartella dedicata creandola; ripiega su una cartella temporanea se non riesce."""
    out_dir = _extension_dir()
    try:
        os.makedirs(out_dir, exist_ok=True)
        return out_dir
    except Exception:
        return tempfile.mkdtemp(prefix="metadata_removal_")


def _disk_dst(src_path: str, mode: str, suffix: str, used=None) -> str:
    """Percorso di destinazione su disco in base alla modalità scelta nei Settings.

    In «cartella dedicata» e in «sovrascrivi» il file MANTIENE il NOME ORIGINALE; nella
    cartella dedicata, se esiste già un file con quel nome, viene aggiunto un suffisso
    numerico (_1, _2, …) così non si sovrascrive mai nulla. Solo in modalità «copia nella
    stessa cartella» si usa il suffisso scelto nei Settings.
    """
    if mode == MODE_OVERWRITE:
        return src_path
    if mode == MODE_FOLDER:
        base = os.path.basename(src_path)
        if used is not None:
            return _unique_dst(_extension_dir(), base, used)
        return os.path.join(_extension_dir(), base)
    # Modalità «copia»: nome = <originale><suffisso><.ext> nella stessa cartella. Il
    # suffisso (predefinito «_clean») è RISERVATO alle copie pulite: se esiste già un file
    # con quel nome viene sostituito (idempotente — ri-pulendo si aggiorna la copia, senza
    # creare doppioni «_clean_1», «_clean_2», …).
    root, ext = os.path.splitext(src_path)
    return f"{root}{suffix}{ext}"


# ===========================================================================
# 1) Pulizia automatica dopo la generazione (sezione in txt2img / img2img)
#    In questo caso il file appena generato viene SEMPRE sovrascritto sul posto.
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
        # Letti dal callback globale _on_image_saved.
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
            # Avviso VISIBILE nell'interfaccia (non solo console): chi ha attivato la
            # pulizia per non diffondere i prompt deve accorgersi se un file è rimasto sporco.
            try:
                gr.Warning("Metadata Removal: %d image(s) could NOT be cleaned and may "
                           "still contain metadata (see console for the list)." % len(failed))
            except Exception:
                pass

        # Se richiesto, elimina anche l'eventuale file .txt dei parametri salvato
        # accanto all'immagine (opzione "save_txt" del webui), scritto DOPO il salvataggio.
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
    """Eseguito dopo che il webui ha salvato un'immagine: pulizia sul posto."""
    p = params.p
    if not getattr(p, "_mr_enabled", False):
        return

    path = params.filename
    if not path or not os.path.isfile(path) or not _is_supported(path):
        return

    try:
        strip_file(path, path, keep_icc=True)  # auto-pulizia: sempre sul posto
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
# 2) Scheda in alto: pulizia in blocco (Extras-style)
# ===========================================================================
def _collect_folder_files(folder: str, recursive: bool, skip_suffix=None, skip_dir=None):
    """Elenca le immagini supportate. Salta i file col suffisso (modalità copia) e i file
    dentro skip_dir (la cartella di output, in modalità cartella dedicata)."""
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
                continue  # evita di ri-pulire (e ri-suffissare) le copie già create
            found.append(full)
    return found


def _unique_dst(out_dir, base, used):
    """Percorso libero in out_dir, evitando collisioni di nome."""
    dst = os.path.join(out_dir, base)
    i = 1
    while dst in used or os.path.exists(dst):
        name, ext = os.path.splitext(base)
        dst = os.path.join(out_dir, f"{name}_{i}{ext}")
        i += 1
    used.add(dst)
    return dst


def clean_single(file_path, progress=gr.Progress()):
    """Single Image: ripulisce un'immagine caricata e ne dà anteprima + download."""
    src = file_path if isinstance(file_path, str) else getattr(file_path, "name", None)
    if not src:
        return _msg("Please upload an image.", "warn"), gr.update(visible=False), gr.update(visible=False)
    if not _is_supported(src):
        return _msg("Unsupported image format.", "warn"), gr.update(visible=False), gr.update(visible=False)

    progress(0.0, desc="Cleaning metadata")
    out_dir = _ensure_output_dir()
    dst = _unique_dst(out_dir, os.path.basename(src), set())  # stesso nome (+ suffisso su collisione)
    _log(f"Single Image: cleaning {os.path.basename(src)} → {out_dir}")
    try:
        strip_file(src, dst, keep_icc=True)
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
    """Batch Process: ripulisce più immagini caricate e le offre in download."""
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
        dst = _unique_dst(out_dir, os.path.basename(src), used)  # stesso nome (+ suffisso su collisione)
        try:
            strip_file(src, dst, keep_icc=True)
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
    """Batch from Directory: ripulisce tutte le immagini di una cartella su disco.

    Sovrascrivi o copia (nella stessa cartella) dipende dall'impostazione nei Settings.
    """
    input_dir = (input_dir or "").strip().strip('"')
    if not input_dir:
        return _msg("Please enter an input directory.", "warn")
    if not os.path.isdir(input_dir):
        return _msg(f"Input directory not found:<br><code>{html.escape(input_dir)}</code>", "err")

    mode, suffix = _output_settings()

    # Sicurezza: con sottocartelle attive, rifiuta la radice di un disco (es. C:\) in
    # QUALSIASI modalità: in sovrascrittura riscriverebbe ogni immagine del sistema; in
    # cartella dedicata/copia scansionerebbe l'intero disco creando una marea di copie.
    if bool(recursive):
        norm = os.path.abspath(input_dir)
        if os.path.dirname(norm) == norm:  # è una radice di unità (C:\, D:\, /)
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

    # In modalità cartella dedicata, se richiesto, ricrea la struttura delle sottocartelle
    # di partenza dentro la cartella dedicata (invece di appiattire tutto).
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
                # percorso relativo alla cartella di input, ricreato nella cartella dedicata
                rel = os.path.relpath(os.path.abspath(src), input_abs)
                dst = _unique_dst(ext_dir, rel, used)
            else:
                dst = _disk_dst(src, mode, suffix, used)
            strip_file(src, dst, keep_icc=True)
            ok += 1
            if _residual_metadata(dst):
                residual.append(dst)
        except Exception as e:  # noqa: BLE001
            errors.append((src, str(e)))
    progress(1.0, desc="Done")
    _log(f"Batch from Directory: done — {ok}/{n} cleaned, {len(errors)} error(s).")

    return _dir_report(ok, errors, residual, mode, suffix, len(files))


def pnginfo_clean(pil_image, fname=""):
    """PNG Info: ripulisce l'immagine caricata, la salva nella cartella dedicata e la offre
    come copia da scaricare (l'immagine in PNG Info è un upload, quindi è sempre una copia).

    'fname' è il nome originale recuperato dal frontend (l'oggetto PIL non lo conserva)."""
    if pil_image is None:
        return gr.update(visible=False), _msg("Load an image into «PNG Info» first.", "warn")
    try:
        _check_bomb(getattr(pil_image, "size", None))
        # Nome del file: prima quello passato dal frontend (JS legge l'URL dell'immagine),
        # poi l'eventuale .filename del PIL, infine "cleaned". Salviamo sempre in PNG.
        cand = os.path.basename((fname or "").strip())
        if not cand:
            cand = os.path.basename(getattr(pil_image, "filename", "") or "").strip()
        name = (os.path.splitext(cand)[0] if cand else "cleaned") or "cleaned"
        clean = _build_clean_static(pil_image)
        icc = pil_image.info.get("icc_profile") if getattr(pil_image, "info", None) else None
        out_dir = _ensure_output_dir()
        dst = _unique_dst(out_dir, f"{name}.png", set())
        save_kwargs = {"optimize": True}
        if icc:
            save_kwargs["icc_profile"] = icc
        clean.save(dst, format="PNG", **save_kwargs)
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
            "PNG is rewritten losslessly; JPEG/WEBP are re-encoded at high quality (95). "
            "The ICC color profile is kept.</p>"
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
                    # Feedback immediato + riquadro visibile: così la barra di
                    # avanzamento di Gradio ha dove disegnarsi (altrimenti su un HTML
                    # vuoto non si vede e sembra "non fare nulla").
                    fn=lambda: _msg("⏳ Cleaning metadata… please wait.", "info"),
                    outputs=[dir_status],
                ).then(
                    fn=clean_directory,
                    inputs=[dir_in, dir_recursive],
                    outputs=[dir_status],
                )

    return [(ui_tab, TAB_TITLE, "metadata_removal_tab")]


# ===========================================================================
# 3) Integrazione con l'Image Browser (AlUlkesh): pulsante "Delete metadata"
#    iniettato accanto al pulsante "Delete", senza modificare quell'estensione.
# ===========================================================================
def ib_delete_metadata(img_file_name):
    """Ripulisce l'immagine selezionata nell'Image Browser secondo l'impostazione Settings.

    Restituisce DUE valori: il testo dei parametri per il riquadro «Generation Info»
    (vuoto se l'immagine è stata sovrascritta; invariato se è stata fatta una copia) e un
    messaggio HTML di conferma da mostrare sotto il pulsante — così l'azione è visibile
    anche quando l'originale resta intatto (es. modalità cartella dedicata)."""
    try:
        path = img_file_name
        if not path or not os.path.isfile(path) or not _is_supported(path):
            geninfo = _read_geninfo(path) if path else ""
            return geninfo, _msg("Select a supported image first.", "warn")
        mode, suffix = _output_settings()
        dst = _disk_dst(path, mode, suffix, set())  # nome originale (+ suffisso su collisione)
        strip_file(path, dst, keep_icc=True)
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


# Stato per agganciare i componenti dell'Image Browser man mano che vengono creati.
_IB = {"last_base": None, "pending": {}}
_DEL_SUFFIX = "_image_browser_del_img_btn"


def _on_after_component(component, **kwargs):
    """Inietta il pulsante "Delete metadata" accanto al "Delete" dell'Image Browser.

    L'Image Browser crea, per ogni sua scheda, in quest'ordine: il pulsante Delete
    (elem_id "<base>_image_browser_del_img_btn"), poi la Textbox "Generation Info",
    poi la Textbox "File Name" (che contiene il percorso). Catturiamo questi tre
    componenti e colleghiamo il click. Tutto in try/except: se qualcosa non combacia,
    l'Image Browser resta intatto.
    """
    try:
        eid = getattr(component, "elem_id", None) or ""

        # --- Tab "PNG Info": pulsante "Remove metadata" sotto l'immagine sorgente ---
        if eid == "pnginfo_image":
            mr_btn = gr.Button("Remove metadata", elem_id="metadata_removal_pnginfo_btn", variant="primary")
            mr_status = gr.HTML()
            mr_download = gr.File(label="Cleaned image (download)", interactive=False, visible=False)
            mr_fname = gr.Textbox(visible=False, elem_id="metadata_removal_pnginfo_fname")
            # Il nome originale dell'immagine caricata non arriva nell'oggetto PIL (Gradio
            # lo perde). Lo recuperiamo dal frontend leggendo l'URL dell'immagine mostrata
            # nel tab PNG Info (Gradio conserva il nome originale nel percorso del file).
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
            status = gr.HTML()  # conferma visiva sotto il pulsante
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
            # "File Name" è l'ultimo dei tre: ora colleghiamo il pulsante.
            if entry["button"] is not None and entry["info"] is not None:
                entry["button"].click(
                    # Feedback immediato "in corso": il riquadro diventa visibile e Gradio
                    # ci disegna sopra l'animazione di caricamento mentre lavora.
                    fn=lambda: _msg("⏳ Cleaning metadata…", "info"),
                    outputs=[entry["status"]],
                ).then(
                    fn=ib_delete_metadata,
                    inputs=[component],
                    outputs=[entry["info"], entry["status"]],
                )
            _IB["last_base"] = None  # chiude la finestra di abbinamento per questa scheda
    except Exception:
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Settings (sottomenu "Metadata Removal")
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
# Posizionamento della scheda (tra «Extras» e «PNG Info»):
# Forge ordina le schede in alto secondo Settings -> UI Tab Order. Una scheda di
# estensione NON può essere inserita automaticamente in modo affidabile tra le schede
# native: all'avvio dell'estensione la scheda non e' ancora una "scelta" valida e
# verrebbe scartata. Per posizionarla basta impostarlo UNA volta (resta salvato):
#   Settings -> UI Tab Order -> aggiungi "Metadata Removal" dopo "Extras"
#   -> Apply settings -> Reload UI
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Registrazione dei callback
# ---------------------------------------------------------------------------
script_callbacks.on_image_saved(_on_image_saved)
script_callbacks.on_ui_tabs(on_ui_tabs)
script_callbacks.on_ui_settings(on_ui_settings)
script_callbacks.on_after_component(_on_after_component)
