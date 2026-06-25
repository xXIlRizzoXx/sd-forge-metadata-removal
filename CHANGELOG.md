# Changelog

Tutte le modifiche rilevanti del progetto sono elencate qui.
Formato ispirato a [Keep a Changelog](https://keepachangelog.com/it/1.1.0/);
versionamento secondo [SemVer](https://semver.org/lang/it/).

## [1.0.0] — 2026-06-25

Prima versione pubblica di **Metadata Removal**, estensione per
**Stable Diffusion WebUI Forge (Neo)** (compatibile anche con le webui derivate da
AUTOMATIC1111).

Rimuove **tutti** i metadati leggibili dal tab «PNG Info» (prompt, parametri, modello,
seed, EXIF, XMP, commenti) **senza alterare i pixel** e **mantenendo il profilo colore ICC**.

### Funzioni

- **Pulizia automatica dopo la generazione** — una spunta nella sezione «Metadata Removal»
  di txt2img/img2img (spenta di default): se attiva, ogni immagine generata viene salvata
  senza metadati. Funziona **dopo ADetailer** (la pulizia è sempre l'ultimo passo).
- **Scheda «Metadata Removal»** in alto (tra «Extras» e «PNG Info»), in stile «Extras», con
  tre sotto-schede:
  - **Single Image** — ripulisce un'immagine caricata (anteprima + download);
  - **Batch Process** — ripulisce più immagini caricate (download multiplo);
  - **Batch from Directory** — ripulisce tutte le immagini di una cartella su disco,
    con opzione «Include subfolders».
- **Pulsante «Remove metadata» nel tab PNG Info** — ripulisce l'immagine caricata e la offre
  come copia da scaricare, mantenendone il nome originale.
- **Pulsante «Delete metadata» nell'Image Browser** (estensione *images-browser*) — accanto
  a «Delete», con messaggio di conferma.
- **Tre modalità di salvataggio su disco** (Settings → Metadata Removal): cartella dedicata
  «Metadata Removal» nella radice immagini *(default)* · copia con suffisso `_clean` ·
  sovrascrivi l'originale.
- **Nomi file originali** mantenuti (suffisso numerico `_1`, `_2`… solo in caso di collisione,
  per non sovrascrivere mai nulla nella cartella dedicata).
- **Mantieni la struttura delle sottocartelle**: in modalità cartella dedicata, ricrea
  l'albero di cartelle dell'origine (impostazione attiva di default).
- **Log in console** con barre di avanzamento (`tqdm`) per ogni operazione.
- **Impostazioni**: modalità di salvataggio, nome cartella dedicata, mantieni struttura,
  suffisso copie, eliminazione del file `.txt` dei parametri durante la pulizia automatica.

### Formati e qualità

- **PNG**: riscrittura **senza perdita di qualità** (rimossi tutti i blocchi di testo ed EXIF).
- **JPEG / WEBP**: EXIF/XMP/commenti rimossi (ricodifica ad alta qualità, 95).
- **GIF / WEBP / APNG animate**: mantenuti tutti i fotogrammi, i tempi e il loop.
- **Profilo colore ICC** e **canale di trasparenza (alfa)**: sempre mantenuti.

### Sicurezza e robustezza

- Protezione anti *decompression-bomb* (limite di pixel) e limite di fotogrammi per le
  immagini animate.
- Sanificazione del nome della cartella dedicata; rifiuto della scansione dalla radice di
  un disco (es. `C:\`) con sottocartelle attive, in tutte le modalità.
- Scrittura **atomica** (file temporaneo + sostituzione) con rifiuto dei salvataggi a 0 byte.
- Avviso **visibile** nell'interfaccia se la pulizia automatica fallisce su un file.
- Superati **due audit di sicurezza multi-agente** (uno finale prima della pubblicazione).

### Dipendenze

- Solo **Pillow** (già incluso in Forge). Nessuna installazione aggiuntiva.

[1.0.0]: https://github.com/xXIlRizzoXx/sd-forge-metadata-removal/releases/tag/v1.0.0
