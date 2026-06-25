# Metadata Removal — estensione per Stable Diffusion WebUI Forge (Neo)

> L'interfaccia dell'estensione è **in inglese**. Questa guida è in italiano per comodità.

Rimuove **tutti** i metadati dalle immagini generate con Stable Diffusion: prompt,
parametri, modello, seed, e qualsiasi altra informazione che il tab **PNG Info**
sarebbe in grado di leggere (incluso EXIF e XMP per JPEG/WEBP).

Compatibile con **Stable Diffusion WebUI Forge – Neo**
([Haoming02/sd-webui-forge-classic](https://github.com/Haoming02/sd-webui-forge-classic), branch `neo`)
e, in generale, con le webui derivate da AUTOMATIC1111.

## Cosa fa

L'estensione lavora in **tre punti**:

1. **Sezione "Metadata Removal" in txt2img / img2img**
   Una sezione a fisarmonica con una spunta:
   - **spenta di default** → le immagini si salvano normalmente;
   - **attiva** (*Strip metadata from saved images*) → ogni immagine generata viene
     salvata **senza metadati** (il file viene ripulito sul posto).

2. **Scheda "Metadata Removal" in alto** (tra **Extras** e **PNG Info**)
   Tre sotto-schede in stile *Extras*:
   - **Single Image** → ripulisci un'immagine caricata e scaricala (con anteprima);
   - **Batch Process** → ripulisci più immagini caricate e scaricale;
   - **Batch from Directory** → ripulisci tutte le immagini di una cartella su disco
     (con barra di avanzamento; opzione *Include subfolders*).

3. **Pulsante "Delete metadata" nell'Image Browser**
   Accanto al pulsante *Delete* dell'estensione
   [images-browser](https://github.com/AlUlkesh/stable-diffusion-webui-images-browser):
   ripulisce l'immagine attualmente selezionata mentre sfogli la libreria.

4. **Pulsante "Remove metadata" nel tab PNG Info**
   Sotto l'immagine *Source*: ripulisce l'immagine caricata e la offre come **copia
   pulita da scaricare** (come *Single Image*, perché l'immagine è un upload).

## Dove vengono salvate le immagini pulite (una sola impostazione)

Per le operazioni **su disco** (*Batch from Directory* e il pulsante *Delete metadata*
dell'Image Browser) il comportamento si sceglie **una volta** in
**Settings → Metadata Removal** tra **tre modalità**:

- **Save to the «Metadata Removal» folder (in the images root)** → crea (se non esiste)
  una **cartella dedicata** con il nome dell'estensione **nella radice delle immagini**,
  cioè accanto a `txt2img-images`, `extras-images`, ecc., e vi salva tutte le immagini
  pulite; gli originali restano intatti. *(default)*
  Esempio: `F:\Stability_Matrix\Data\Images\Metadata Removal\`. Il percorso si adatta
  automaticamente a dove ogni utente salva le immagini (vengono risolte anche le
  *junction* di Stability Matrix).
- **Save a clean copy in the same folder** → crea una copia pulita nella stessa cartella
  dell'originale, con un suffisso nel nome (predefinito `_clean`); l'originale resta intatto.
  Il suffisso è **riservato** alle copie pulite: se esiste già un file con quel nome viene
  sostituito (così ri-pulendo si aggiorna la copia, senza creare doppioni).
- **Overwrite the original image** → riscrive direttamente il file, senza copie.

Il **nome della cartella dedicata** è configurabile in *Settings → Metadata Removal*
(predefinito: `Metadata Removal`).

> **Nome dei file:** le immagini pulite mantengono **lo stesso nome** dell'originale
> (cartella dedicata e sovrascrivi). Se nella cartella dedicata esiste già un file con
> quel nome, viene aggiunto un **suffisso numerico** (`_1`, `_2`, …) così non si
> sovrascrive mai nulla. Solo la modalità *«copia nella stessa cartella»* usa il suffisso
> testuale (predefinito `_clean`). Anche il pulsante di **PNG Info** salva con il nome
> dell'immagine caricata (se Forge lo fornisce; altrimenti `cleaned.png`).

> Nota: la pulizia automatica dopo la generazione (punto 1) ripulisce **sempre sul posto**
> il file appena creato, a prescindere da questa impostazione.
> Single Image, Batch Process e il pulsante di PNG Info lavorano su file *caricati*
> (copie temporanee): salvano la versione pulita nella **cartella dedicata** e la offrono
> anche come **copia da scaricare** (non possono sovrascrivere file sul tuo disco, perché
> non ne conoscono l'origine).

## Cosa viene rimosso

Tutto ciò che apparirebbe in **PNG Info**:

- **PNG**: ogni blocco di testo (`parameters`, `prompt`, `workflow`, `Comment`,
  `Description`, `Software`, XMP e qualunque chiave aggiunta da altri nodi/estensioni)
  ed eventuali EXIF incorporati.
- **JPEG / WEBP**: EXIF (incluso `UserComment`), XMP e commenti.

## Cosa viene mantenuto

- Il **profilo colore ICC** (fedeltà dei colori) — mantenuto **sempre**.
- Il **canale di trasparenza** (alfa), quando presente.
- I **pixel**: per i PNG la riscrittura è **senza perdita di qualità**
  (JPEG/WEBP ricodificati a qualità 95).
- Le **immagini animate** (GIF/WEBP/APNG) mantengono **tutti i fotogrammi**, i tempi e
  il loop.

## Installazione

**Metodo 1 — da URL (consigliato).** In Forge/A1111: tab **Extensions → Install from URL**,
incolla:
```
https://github.com/xXIlRizzoXx/sd-forge-metadata-removal
```
poi **Install** e **riavvia Forge per intero** (non basta "Reload UI" per una estensione nuova).

**Metodo 2 — manuale.** Copia (o `git clone`) l'intera cartella dell'estensione dentro
`extensions` di Forge:
```
<cartella-di-forge>\extensions\sd-forge-metadata-removal\
```
(deve contenere `scripts\metadata_stripper.py`), poi riavvia Forge per intero.

Dopo il riavvio troverai la sezione in txt2img/img2img, la scheda **Metadata Removal** in alto
(tra Extras e PNG Info) e il pulsante **Delete metadata** nell'Image Browser.

Non servono dipendenze aggiuntive: usa **Pillow**, già incluso in Forge.

## Settings → Metadata Removal

- **Modalità di salvataggio** per le operazioni su disco (cartella dedicata / copia nella
  stessa cartella / sovrascrivi). *(default: cartella dedicata)*
- **Nome della cartella dedicata** creata nella radice delle immagini (predefinito
  `Metadata Removal`).
- **Mantieni la struttura delle sottocartelle** (predefinito **attivo**): in modalità
  cartella dedicata, *Batch from Directory* ricrea dentro «Metadata Removal» la stessa
  struttura di cartelle dell'origine (es. `2026-05-28/Accantonate/foto.png`). Se disattivo,
  tutte le immagini pulite finiscono "piatte" nella cartella dedicata.
- **Suffisso** per le copie pulite (predefinito `_clean`).
- Eliminare anche l'eventuale file `.txt` dei parametri durante la pulizia automatica.

## Posizione della scheda (tra Extras e PNG Info)

Forge ordina le schede in alto in base a **Settings → UI Tab Order**. Una scheda di
estensione non può essere inserita automaticamente tra le schede native, quindi va
impostato **una volta** (poi resta salvato anche ai riavvii):

1. **Settings → UI Tab Order**
2. aggiungi **`Metadata Removal`** subito dopo **`Extras`**
3. **Apply settings** → **Reload UI**

Risultato: `txt2img · img2img · Extras · Metadata Removal · PNG Info · …`

## Verifica

Dopo la pulizia trascina l'immagine nel tab **PNG Info**: non deve comparire alcun
parametro. L'estensione esegue anche un controllo automatico e segnala nel riepilogo
eventuali file in cui fossero rimasti metadati.

## Sicurezza e robustezza

L'estensione è stata sottoposta a due audit di sicurezza multi-agente (uno finale prima
della pubblicazione) e include alcune protezioni:

- **Immagini malevole**: rifiuta immagini con dimensioni sproporzionate (*decompression
  bomb*) e animazioni con un numero enorme di fotogrammi, per non esaurire la memoria.
- **GIF animate**: se l'encoder non riesce a salvare l'animazione, ripiega su un singolo
  fotogramma **comunque ripulito** (l'immagine non resta mai con i metadati).
- **Percorsi**: il nome della cartella dedicata è ridotto a un nome semplice, così un
  valore errato nei Settings non può far scrivere fuori dalla radice delle immagini;
  con *Include subfolders* attivo, viene rifiutata la pulizia dalla radice di un disco
  (es. `C:\`) **in tutte le modalità**.
- **Niente sovrascritture accidentali**: nella cartella dedicata i nomi che già esistono
  ricevono automaticamente un suffisso numerico, senza cancellare file preesistenti.
- **Scrittura sicura**: ogni file viene scritto prima su un file temporaneo e poi
  sostituito in modo atomico; un salvataggio vuoto (0 byte) viene rifiutato per non
  rovinare l'originale.
- **Auto-pulizia "best-effort"**: se per un'immagine la pulizia automatica fallisce,
  compare un **avviso visibile** nell'interfaccia (oltre al log in console), così sai
  che quel file potrebbe contenere ancora i metadati.
