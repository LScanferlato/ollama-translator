#!/usr/bin/env python3
"""
Traduce (o completa) le colonne di un glossario multilingua usando Ollama
(LLM locale).

A differenza di translate_csv.py, questo script opera direttamente sulle
colonne del glossario: per ogni riga, le traduzioni già presenti nelle altre
lingue vengono usate come riferimento per tradurre le celle vuote (o tutte,
con --overwrite), migliorando coerenza e completezza.

Modello predefinito: abb-decide/apertus-tools:8b-instruct-2509-q4_k_m

Uso:
    python translate_glossary.py glossary_multilingual.csv
    python translate_glossary.py glossary_multilingual.csv --source-col it
    python translate_glossary.py glossary_multilingual.csv --langs en de fr
    python translate_glossary.py glossary_multilingual.csv --max-lines 50

Di default traduce TUTTE le lingue presenti nel file (in-place), riempiendo
le celle vuote. Usa --langs per limitare a poche lingue, --overwrite per
riscrivere anche quelle già piene.
"""

import argparse
import csv
import os
import re
import select
import sys
import tempfile
import termios
import threading
import time
import tty
from pathlib import Path

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
MODEL = "abb-decide/apertus-tools:8b-instruct-2509-q4_k_m"
DELAY = 0.1

MAX_RETRIES = 3
RETRY_DELAY = 2
SALVA_OGNI_N = 25

DEBUG = True


def log(msg):
    print(msg, flush=True)


# ─── Colori per lingua ─────────────────────────────────────────
# Ogni lingua riceve un colore stabile (in base all'hash del codice) così
# è facile scansionare il log. I colori sono attivi solo su terminale.
COLORI = [
    "\033[31m", "\033[32m", "\033[33m", "\033[34m", "\033[35m", "\033[36m",
    "\033[91m", "\033[92m", "\033[93m", "\033[94m", "\033[95m", "\033[96m",
]
RESET = "\033[0m"


def colore_lingua(code):
    if not code or not sys.stdout.isatty():
        return code
    h = abs(hash(code)) % len(COLORI)
    return f"{COLORI[h]}{code}{RESET}"


# ─── Interruzione con ESC ────────────────────────────────────────
# Un thread in background mette stdin in modalità "raw" e resta in ascolto
# del tasto ESC: quando viene premuto, imposta l'evento `interruzione` così
# il ciclo di traduzione può fermarsi con grazia e salvare il progresso.
interruzione = threading.Event()


def _ascolta_esc():
    if not sys.stdin.isatty():
        return
    fd = sys.stdin.fileno()
    try:
        old = termios.tcgetattr(fd)
    except Exception:
        return
    try:
        tty.setraw(fd)
        while not interruzione.is_set():
            r, _, _ = select.select([fd], [], [], 0.2)
            if r:
                try:
                    ch = sys.stdin.read(1)
                except Exception:
                    break
                if ch == "\x1b":  # ESC
                    interruzione.set()
                    break
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            pass


def avvia_controllo_esc():
    if not sys.stdin.isatty():
        return None
    t = threading.Thread(target=_ascolta_esc, daemon=True)
    t.start()
    return t


def ferma_controllo_esc(t):
    interruzione.set()
    if t and t.is_alive():
        t.join()


# Mappa codici locale del glossario -> nome leggibile per il prompt
LINGUE_LEGGIBILI = {
    "af": "afrikaans", "am-ET": "amarico", "ar": "arabo", "as-IN": "assamese",
    "az-Latn": "azerbaigiano", "be": "bielorusso", "bg": "bulgaro", "bn": "bengalese",
    "bs-Cyrl": "bosniaco (cirillico)", "bs-Latn": "bosniaco", "ca": "catalano",
    "ca-ES-valencia": "catalano (valenciano)", "chr-Cher": "cherokee", "ckb": "curdo (sorani)",
    "cs": "ceco", "cy-GB": "gallese", "da": "danese", "de": "tedesco", "el": "greco",
    "en": "inglese", "es": "spagnolo", "et": "estone", "eu": "basco", "fa": "persiano",
    "ff": "fulah", "fi": "finlandese", "fil-PH": "filippino", "fr": "francese",
    "ga": "irlandese", "ga-IE": "irlandese", "gd": "gaelico scozzese", "gl": "galiziano",
    "gu": "gujarati", "guc": "wayuunaiki", "ha-Latn-NG": "hausa", "he": "ebraico",
    "hi": "hindi", "hr": "croato", "hu": "ungherese", "hy": "armeno", "id": "indonesiano",
    "ig-NG": "igbo", "is": "islandese", "it": "italiano", "iu-Latn": "inuktitut",
    "ja": "giapponese", "ka": "georgiano", "kk": "kazako", "km-KH": "khmer",
    "kn": "kannada", "ko": "coreano", "kok": "konkani", "ky": "kirghiso",
    "lb-LU": "lussemburghese", "lo": "lao", "lt": "lituano", "lv": "lettone",
    "mi-NZ": "maori", "mk": "macedone", "ml-IN": "malayalam", "mn": "mongolo",
    "mr": "marathi", "ms": "malese", "mt": "maltese", "mt-MT": "maltese",
    "my": "birmano", "nb-NO": "norvegese (bokmål)", "ne-NP": "nepalese",
    "nl": "olandese", "nn-NO": "norvegese (nynorsk)", "nso-ZA": "sotho settentrionale",
    "or-IN": "odia", "pa-Arab": "punjabi (arabo)", "pa-Guru": "punjabi",
    "pl": "polacco", "prs-AF": "dari", "ps-AF": "pashto", "pt": "portoghese",
    "pt-BR": "portoghese (Brasile)", "pt-PT": "portoghese (Portogallo)",
    "qut-GT": "k'iche'", "quz": "quechua", "ro": "rumeno", "ru": "russo",
    "rw-RW": "kinyarwanda", "sd": "sindhi", "si-LK": "singalese", "sk": "slovacco",
    "sl": "sloveno", "sq": "albanese", "sr-Cyrl": "serbo (cirillico)",
    "sr-Latn": "serbo (latino)", "sv": "svedese", "sw": "swahili", "ta": "tamil",
    "te": "telugu", "tg-Cyrl-TJ": "tagiko", "th": "thailandese", "ti": "tigrino",
    "tk-TM": "turkmeno", "tn-ZA": "tswana", "tr": "turco", "tt-Cyrl": "tataro",
    "ug": "uiguro", "uk": "ucraino", "ur": "urdu", "uz-Cyrl": "uzbeko (cirillico)",
    "uz-Latn": "uzbeko", "vi": "vietnamita", "wo-SN": "wolof", "xh-ZA": "xhosa",
    "yo-NG": "yoruba", "zh-Hans": "cinese (semplificato)", "zh-Hant": "cinese (tradizionale)",
    "zu-ZA": "zulu",
}


def nome_lingua(codice):
    return LINGUE_LEGGIBILI.get(codice, codice)


# Qualificatori istituzionali (UE/EU nelle varie grafie) da rimuovere
# quando compaiono come token autonomi nella traduzione.
QUALIFICATORI_UE = ["UE", "EU", "ЕУ", "ЕС", "ЄС", "EUA", "UEA"]


def rimuovi_qualificatori_ue(testo):
    """Rimuove i token autonomi che indicano l'Unione Europea (es. 'UE',
    'EU', 'ЕУ', 'ЕС', 'ЄС') lasciando intatto il termine tradotto."""
    if not testo:
        return testo
    for q in QUALIFICATORI_UE:
        testo = re.sub(rf"(?u)(?<![\w]){re.escape(q)}(?![\w])", "", testo)
    testo = re.sub(r"\s{2,}", " ", testo).strip().strip(" .,;:-")
    return testo


def pulisci_risposta(testo, lingua):
    if not testo:
        return testo

    testo = testo.split('\n\n')[0].split('\n')[0]

    testo = re.sub(r'(?i)^.*?(Răspunsul|Raspunsul|Raspunziul|Răspuns|Raspuns|The translation|Translation|In response|Nota|Note|Ответ|答案|Übersetzung|Traduction|Traduzione|翻訳|번역)[^:]*:\s*', '', testo)

    parole = testo.split()
    if len(parole) > 5:
        virgolette = re.findall(r'["""\u201c\u201d\u2018\u2019]([^""\u201c\u201d\u2018\u2019]*)["""\u201c\u201d\u2018\u2019]', testo)
        if virgolette:
            testo = virgolette[-1]
        else:
            ultimo = re.split(r'(?i)\b(è|este|is|se traduce come|si traduce come|means|significa|est| heißt)\b', testo)
            if len(ultimo) > 1:
                testo = ultimo[-1].strip().strip('.').strip('"').strip("'").strip('»').strip('«')

    testo = re.sub(r'(?i)(translated|traduzion?e?|traduci)\s*(da|from).+?(a|to)\s*\S+\s*[:|]\s*', '', testo)
    testo = re.sub(r'(?i).+?\b(si traduce|se traduce|significa|means|translates? to|in\s+\S+\s+is)\s+["“]?', '', testo)
    testo = re.sub(r'(?i).+?\b(in|su|en)\s+\S+\s+(si dice|se dice|è)\s+["“]?', '', testo)
    testo = re.sub(r'\s*\([^)]*\)\s*', '', testo)

    if '=' in testo:
        testo = testo.rsplit('=', 1)[-1]

    if ':' in testo:
        parti = testo.rsplit(':', 1)
        if len(parti[-1].strip().split()) <= 8:
            testo = parti[-1]

    testo = testo.strip()
    for q in ['"', "'", '«', '»', '\u201c', '\u201d', '\u2018', '\u2019', '\u201e', '\u201a', '\u00ab', '\u00bb']:
        if testo.startswith(q):
            testo = testo[len(q):]
        if testo.endswith(q):
            testo = testo[:-len(q)]
    testo = testo.strip('. ')

    return testo


def prima_variante(s):
    if not s:
        return ""
    s = s.strip()
    if " | " in s:
        s = s.split(" | ")[0].strip()
    for q in ['"', "'", "«", "»", "“", "”", "‘", "’"]:
        s = s.strip().strip(q)
    return s.strip()


def traduci(testo, lingua, modello=MODEL, session=None, contesto=None):
    lingua_nome = nome_lingua(lingua)
    prompt = (
        f"{testo}\n\n"
        f"Traduci il termine tecnico qui sopra in {lingua_nome}. "
    )
    if contesto:
        riferimenti = "; ".join(
            f"{nome_lingua(c)}: {v}" for c, v in contesto.items() if v and str(v).strip()
        )
        if riferimenti:
            prompt += (
                f"Usa come riferimento queste traduzioni nelle altre lingue "
                f"per coerenza: {riferimenti}. "
            )
    prompt += (
        f"Rispondi con una sola parola o frase breve. "
        f"Non aggiungere qualificatori istituzionali come UE, EU, ЕУ, ЕС, "
        f"ЄС o 'European Union': traduci SOLO il termine. "
        f"Niente spiegazioni, note, prefissi o testo aggiuntivo."
    )
    payload = {
        "model": modello,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1},
    }
    sess = session or requests
    for tentativo in range(1, MAX_RETRIES + 1):
        try:
            resp = sess.post(OLLAMA_URL, json=payload, timeout=120)
            resp.raise_for_status()
            raw = resp.json()["response"].strip()
            return rimuovi_qualificatori_ue(pulisci_risposta(raw, lingua))
        except Exception as e:
            if tentativo < MAX_RETRIES:
                attesa = RETRY_DELAY * tentativo
                log(f"  ERRORE (tentativo {tentativo}/{MAX_RETRIES}): {e} -> riprovo tra {attesa}s")
                time.sleep(attesa)
            else:
                log(f"  ERRORE traduzione '{testo}' -> {lingua}: {e}")
                return ""
    return ""


def rileva_delimitatore(prima_riga):
    virgole = prima_riga.count(",")
    punti = prima_riga.count(";")
    return ";" if punti >= virgole else ","


def carica_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        prima = f.readline()
        f.seek(0)
        delim = rileva_delimitatore(prima)
        reader = csv.DictReader(f, delimiter=delim)
        fn = reader.fieldnames
        if not fn:
            raise ValueError(
                f"Il file '{path}' è vuoto o senza intestazione: impossibile "
                f"leggerlo. Il file potrebbe essere stato troncato da una "
                f"esecuzione interrotta; ripristinalo da un backup.")
        fieldnames = [h.strip() for h in fn]
        righe = [{k.strip(): v for k, v in row.items()} for row in reader]
    return righe, fieldnames


def salva_csv(path, righe, fieldnames):
    """Salvataggio ATOMICO: scrive in un file temporaneo nella stessa cartella
    e poi lo sposta sopra l'originale con os.replace(). Così, anche in caso di
    interruzione (ESC/Ctrl+C/crash) o errore durante la scrittura, il file
    originale resta integro."""
    path = Path(path)
    dirn = path.parent
    fd, tmp = tempfile.mkstemp(dir=dirn, prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(righe)
            f.flush()
            os.fsync(f.fileno())
        if os.path.getsize(tmp) == 0:
            raise RuntimeError("scrittura vuota: annullo per sicurezza")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def scegli_modello(modello_richiesto, modello_default=MODEL):
    if modello_richiesto:
        return modello_richiesto
    try:
        resp = requests.get(OLLAMA_TAGS_URL, timeout=10)
        resp.raise_for_status()
        modelli = resp.json().get("models", [])
    except Exception as e:
        log(f"Impossibile contattare Ollama per listare i modelli: {e}")
        log(f"Uso modello predefinito: {modello_default}")
        return modello_default

    if not modelli:
        log(f"Nessun modello trovato in Ollama. Uso: {modello_default}")
        return modello_default

    nomi = [m["name"] for m in modelli]
    if len(modelli) == 1:
        log(f"Modello disponibile: {nomi[0]}")
        return nomi[0]

    raccomandato = modello_default if modello_default in nomi else nomi[0]
    log("Modelli disponibili in Ollama:")
    for idx, m in enumerate(modelli, 1):
        tag = m.get("name", "?")
        size_gb = m.get("size", 0) / 1e9
        consigliato = "  ← consigliato" if tag == raccomandato else ""
        log(f"  {idx}. {tag} ({size_gb:.1f}GB){consigliato}")

    while True:
        try:
            scelta = input(f"\nScegli modello [1-{len(modelli)}] (default: {raccomandato}): ").strip()
            if not scelta:
                return raccomandato
            idx = int(scelta) - 1
            if 0 <= idx < len(modelli):
                return nomi[idx]
        except (ValueError, IndexError):
            pass
        log("Scelta non valida. Riprova.")


def main():
    parser = argparse.ArgumentParser(
        description="Traduce/completa le colonne di un glossario con Ollama")
    parser.add_argument("file", nargs="?", default="glossary_multilingual.csv",
                        help="File glossario (default: glossary_multilingual.csv)")
    parser.add_argument("--langs", nargs="+", default=None,
                        help="Lingue da tradurre (codici del glossario, es. 'en de fr'). "
                             "Di default: TUTTE le lingue presenti nel file.")
    parser.add_argument("--source-col", default="it",
                        help="Colonna sorgente per la traduzione (default: it)")
    parser.add_argument("--model", default="",
                        help=f"Modello Ollama (default: {MODEL}; se omesso, lista interattiva)")
    parser.add_argument("--delay", type=float, default=DELAY,
                        help=f"Secondi tra richieste (default: {DELAY})")
    parser.add_argument("-n", "--max-lines", type=int, default=0,
                        help="Traduci solo N righe (0=tutte, default: 0)")
    parser.add_argument("--save-interval", type=int, default=0,
                        help="Salva ogni N righe (0 = ad ogni riga tradotta, default: 0)")
    parser.add_argument("--overwrite", action="store_true",
                        help="Sovrascrivi anche le celle già piene")
    parser.add_argument("--context-langs", nargs="+", default=None,
                        help="Lingue da usare come riferimento (default: tutte "
                             "quelle già presenti sulla riga)")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        log(f"File non trovato: {path}")
        sys.exit(1)
    if path.is_file() and path.stat().st_size == 0:
        log(f"ERRORE: il file '{path}' è vuoto (0 byte). Probabilmente è stato "
            f"troncato da un'esecuzione precedente interrotta. Ripristinalo "
            f"da un backup o scaricalo di nuovo.")
        sys.exit(1)

    def split_langs(val):
        if val is None:
            return []
        if isinstance(val, str):
            val = [val]
        return [c.strip() for tok in val for c in tok.split() if c.strip()]

    target_langs = split_langs(args.langs)
    context_langs = split_langs(args.context_langs)

    delay = args.delay
    modello = scegli_modello(args.model)

    righe, fieldnames = carica_csv(path)
    log(f"Caricato glossario: {path.name} ({len(righe)} righe)")

    if args.source_col not in fieldnames:
        log(f"Colonna sorgente '{args.source_col}' non trovata. "
            f"Colonne: {', '.join(fieldnames[:20])}...")
        sys.exit(1)

    # Lingue da tradurre: di default TUTTE le colonne presenti nel file,
    # esclusa la colonna sorgente e la colonna 'source_term'.
    if target_langs:
        # esclude la colonna sorgente e 'source_term' (non ha senso
        # tradurre una lingua dentro se stessa)
        target_langs = [c for c in target_langs
                        if c != args.source_col and c != "source_term"]
        # --langs può anche aggiungere colonne non ancora presenti
        for lang in target_langs:
            if lang not in fieldnames:
                log(f"Colonna target '{lang}' non presente nel glossario; verrà aggiunta.")
                fieldnames.append(lang)
                for riga in righe:
                    riga.setdefault(lang, "")
    else:
        target_langs = [c for c in fieldnames
                        if c != args.source_col and c != "source_term"]

    log(f"Colonna sorgente: {args.source_col}")
    log(f"Lingue da tradurre ({len(target_langs)}): {', '.join(target_langs)}")
    log(f"Modello: {modello}")
    log("")

    max_lines = args.max_lines
    da_elaborare = righe[:max_lines] if max_lines > 0 and max_lines < len(righe) else righe
    if max_lines > 0 and max_lines < len(righe):
        log(f"Tradurrò solo le prime {max_lines} righe su {len(righe)}")

    session = requests.Session()

    def testo_sorgente(riga):
        # sceglie il testo di partenza: colonna sorgente, poi source_term,
        # poi la prima altra lingua non vuota sulla riga
        for col in (args.source_col, "source_term"):
            v = riga.get(col, "").strip()
            if v:
                return v
        for col in fieldnames:
            if col in (args.source_col, "source_term"):
                continue
            v = riga.get(col, "").strip()
            if v:
                return v
        return ""

    # salvataggio automatico: di default ad ogni riga tradotta. Su file molto
    # grandi (es. glossario da 500k righe) riscrivere tutto a ogni riga è
    # lento: usa --save-interval N per salvare ogni N righe.
    if args.save_interval <= 0:
        args.save_interval = 1
    if len(da_elaborare) > 5000 and args.save_interval < 10:
        log(f"AVVISO: salvataggio ogni {args.save_interval} righe su "
            f"{len(da_elaborare)} righe è oneroso. Usa --save-interval N "
            f"(es. 25) per ridurre i salvataggi.")
    log("Puoi interrompere in qualsiasi momento premendo ESC "
        "(il progresso verrà salvato).")
    log("")

    esc_thread = avvia_controllo_esc()

    try:
        for i, riga in enumerate(da_elaborare, 1):
            sorgente = testo_sorgente(riga)
            if not sorgente:
                log(f"[{i}/{len(da_elaborare)}] (nessun testo sorgente, saltata)")
                continue

            tradotte = 0
            for lingua in target_langs:
                if interruzione.is_set():
                    break
                val_attuale = riga.get(lingua, "")
                if val_attuale.strip() and not args.overwrite:
                    continue

                # riferimenti: altre lingue sulla stessa riga
                contesto = {}
                refs = context_langs if context_langs else [
                    c for c in fieldnames if c not in (lingua, args.source_col, "source_term")]
                for code in refs:
                    if code == lingua:
                        continue
                    v = riga.get(code, "").strip()
                    if v:
                        contesto[code] = prima_variante(v)

                trad = traduci(sorgente, lingua, modello=modello,
                               session=session, contesto=contesto)
                riga[lingua] = trad
                log(f"{colore_lingua(lingua)}: {trad}")
                tradotte += 1
                if delay:
                    time.sleep(delay)

            if tradotte == 0 and not interruzione.is_set():
                log(f"[{i}/{len(da_elaborare)}] '{sorgente}' — già completo")
            else:
                log(f"[{i}/{len(da_elaborare)}] '{sorgente}' — {tradotte} lingue tradotte")

            if args.save_interval > 0 and i % args.save_interval == 0:
                salva_csv(path, righe, fieldnames)
                log(f"(salvato progresso: riga {i})")
            log("")

            if interruzione.is_set():
                log("Interruzione (ESC) richiesta: salvo ed esco.")
                break
    except KeyboardInterrupt:
        log("\nInterruzione (Ctrl+C) richiesta: salvo ed esco.")
    finally:
        salva_csv(path, righe, fieldnames)
        ferma_controllo_esc(esc_thread)
        log("Fatto! Glossario aggiornato/salvato.")


if __name__ == "__main__":
    main()
