#!/usr/bin/env python3
"""
Traduttore multilingua per CSV / XLSX / XLS.
Utilizza Ollama (LLM locale) per tradurre termini tecnici.

Uso:
    python translate_csv.py file.csv
    python translate_csv.py file.xlsx
    python translate_csv.py file.xls

Il file viene modificato direttamente (in-place) per consentire
arresto e ripresa senza perdere progressi.
"""

import argparse
import csv
import re
import sys
import time
from pathlib import Path

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
MODEL = "qwen3:14b"
DELAY = 0.5
LINGUA_PARTENZA = "italiano"

COLONNA_SOURCE = "IT"
ESCLUSE = {"ID", "Categoria", "Termine", "Descrizione", "IT"}

TUTTE_LINGUE = ["EN", "DE", "FR", "ES", "PT", "RU", "ZH", "JA", "KO",
                "AR", "NL", "PL", "TR", "CS", "DA", "FI", "EL", "HU",
                "RO", "SV", "BG", "HR", "SK", "SL", "LT", "LV", "ET",
                "HE", "HI", "ID", "MS", "TH", "VI", "UK", "SR", "NO"]

MAX_RETRIES = 3
RETRY_DELAY = 2
SALVA_OGNI_N = 0

DEBUG = True


def log(msg):
    print(msg, flush=True)


def nome_lingua(codice):
    mappa = {
        "EN": "inglese", "DE": "tedesco", "FR": "francese",
        "ES": "spagnolo", "PT": "portoghese", "RU": "russo",
        "ZH": "cinese", "JA": "giapponese", "KO": "coreano",
        "AR": "arabo", "NL": "olandese", "PL": "polacco",
        "TR": "turco", "CS": "ceco", "DA": "danese",
        "FI": "finlandese", "EL": "greco", "HU": "ungherese",
        "RO": "rumeno", "SV": "svedese", "BG": "bulgaro",
        "HR": "croato", "SK": "slovacco", "SL": "sloveno",
        "LT": "lituano", "LV": "lettone", "ET": "estone",
        "HE": "ebraico", "HI": "hindi", "ID": "indonesiano",
        "MS": "malese", "TH": "thailandese", "VI": "vietnamita",
        "UK": "ucraino", "SR": "serbo", "NO": "norvegese",
    }
    return mappa.get(codice.upper(), codice)


def pulisci_risposta(testo, lingua):
    if not testo:
        return testo

    # toglie tutto dopo un doppio a capo
    testo = testo.split('\n\n')[0].split('\n')[0]

    # toglie prefissi come "Raspunsul este:", "The translation is:", ecc.
    testo = re.sub(r'(?i)^.*?(Răspunsul|Raspunsul|Raspunziul|Răspuns|Raspuns|The translation|Translation|In response|Nota|Note|Ответ|答案|Übersetzung|Traduction|Traduzione|翻訳|번역)[^:]*:\s*', '', testo)

    # se la risposta contiene ancora parti in italiano o sembra una spiegazione, estrai la traduzione
    parole = testo.split()
    if len(parole) > 5:
        # prova a estrarre da virgolette (ascii e unicode)
        virgolette = re.findall(r'["""\u201c\u201d\u2018\u2019]([^""\u201c\u201d\u2018\u2019]*)["""\u201c\u201d\u2018\u2019]', testo)
        if virgolette:
            testo = virgolette[-1]
        else:
            # prova a prendere solo l'ultima parte dopo "è" / "este" / "is" / "se traduce come"
            ultimo = re.split(r'(?i)\b(è|este|is|se traduce come|si traduce come|means|significa|est| heißt)\b', testo)
            if len(ultimo) > 1:
                testo = ultimo[-1].strip().strip('.').strip('"').strip("'").strip('»').strip('«')

    # toglie "Translated from X to Y: Z" o "Traduzione da X a Y: Z"
    testo = re.sub(r'(?i)(translated|traduzion?e?|traduci)\s*(da|from).+?(a|to)\s*\S+\s*[:|]\s*', '', testo)

    # toglie spiegazioni come "X in Y si traduce come Z"
    testo = re.sub(r'(?i).+?\b(si traduce|se traduce|significa|means|translates? to|in\s+\S+\s+is)\s+["“]?', '', testo)
    testo = re.sub(r'(?i).+?\b(in|su|en)\s+\S+\s+(si dice|se dice|è)\s+["“]?', '', testo)

    # toglie spiegazioni in parentesi (es. "이포드 기어 (Iphord Gear)")
    testo = re.sub(r'\s*\([^)]*\)\s*', '', testo)

    # se contiene =, prende la parte dopo l'ultimo =
    if '=' in testo:
        testo = testo.rsplit('=', 1)[-1]

    # se contiene : con frase corta dopo, prende quella
    if ':' in testo:
        parti = testo.rsplit(':', 1)
        if len(parti[-1].strip().split()) <= 8:
            testo = parti[-1]

    # rimuove virgolette di ogni tipo (anche unicode) da inizio e fine
    testo = testo.strip()
    for q in ['"', "'", '«', '»', '\u201c', '\u201d', '\u2018', '\u2019', '\u201e', '\u201a', '\u00ab', '\u00bb']:
        if testo.startswith(q):
            testo = testo[len(q):]
        if testo.endswith(q):
            testo = testo[:-len(q)]
    testo = testo.strip('. ')

    return testo


def traduci(testo, lingua, modello=MODEL, session=None):
    lingua_nome = nome_lingua(lingua)
    prompt = (
        f"{testo}\n\n"
        f"Traduci il termine tecnico qui sopra in {lingua_nome}. "
        f"Rispondi con una sola parola o frase breve. "
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
            result = resp.json()
            raw = result["response"].strip()
            return pulisci_risposta(raw, lingua)
        except Exception as e:
            if tentativo < MAX_RETRIES:
                attesa = RETRY_DELAY * tentativo
                log(f"  ERRORE (tentativo {tentativo}/{MAX_RETRIES}): {e} -> riprovo tra {attesa}s")
                time.sleep(attesa)
            else:
                log(f"  ERRORE traduzione '{testo}' -> {lingua}: {e}")
                return ""
    return ""


def traduci_se_pieno(testo, lingua, session):
    if not testo or not testo.strip():
        return ""
    return traduci(testo, lingua, session=session)


# ─── CSV ───────────────────────────────────────────────────────────

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
        righe = [{k.strip(): v for k, v in row.items()} for row in reader]
    fieldnames = [h.strip() for h in reader.fieldnames]
    return righe, fieldnames


def salva_csv(path, righe, fieldnames):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(righe)


# ─── XLSX ──────────────────────────────────────────────────────────

def carica_xlsx(path):
    import openpyxl
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    fieldnames = [cell.value for cell in ws[1]]
    righe = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        righe.append({fieldnames[i]: (row[i] if i < len(row) else "") for i in range(len(fieldnames))})
    return righe, fieldnames, wb, ws


def salva_xlsx(ws, righe, fieldnames):
    for r_idx, riga in enumerate(righe, start=2):
        for c_idx, col in enumerate(fieldnames, start=1):
            ws.cell(row=r_idx, column=c_idx, value=riga.get(col, ""))


# ─── XLS ───────────────────────────────────────────────────────────

def carica_xls(path):
    import xlrd
    wb_xls = xlrd.open_workbook(path)
    ws = wb_xls.sheet_by_index(0)
    fieldnames = [str(cell.value).strip() for cell in ws.row(0)]
    righe = []
    for r in range(1, ws.nrows):
        righe.append({fieldnames[c]: str(ws.cell_value(r, c)) for c in range(len(fieldnames))})
    return righe, fieldnames


# ─── Core ─────────────────────────────────────────────────────────

def colonne_lingua(fieldnames):
    return [c for c in fieldnames if c not in ESCLUSE]


def scegli_modollo(modello_richiesto, modello_default=MODEL):
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
        size = m.get("size", 0)
        size_gb = size / 1e9
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
    parser = argparse.ArgumentParser(description="Traduci CSV/XLSX/XLS con Ollama")
    parser.add_argument("file", help="Percorso del file da tradurre")
    parser.add_argument("--source-col", default=COLONNA_SOURCE,
                        help=f"Colonna sorgente (default: {COLONNA_SOURCE})")
    parser.add_argument("--save-interval", type=int, default=SALVA_OGNI_N,
                        help="Salva ogni N righe (0=salva solo alla fine, default: 0)")
    parser.add_argument("--delay", type=float, default=DELAY,
                        help=f"Secondi tra richieste (default: {DELAY})")
    parser.add_argument("-n", "--max-lines", type=int, default=0,
                        help="Traduci solo N righe (0=tutte, default: 0)")
    parser.add_argument("--model", default="",
                        help=f"Modello Ollama (default: {MODEL}; se omesso, lista interattiva)")
    parser.add_argument("--all", action="store_true",
                        help="Aggiungi tutte le lingue mancanti e traduci")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        log(f"File non trovato: {path}")
        sys.exit(1)

    col_source = args.source_col
    save_interval = args.save_interval
    delay = args.delay
    modello = scegli_modollo(args.model)

    ext = path.suffix.lower()
    is_xlsx = ext in (".xlsx", ".xlsm")
    is_xls = ext == ".xls"

    wb = ws = out_path = None

    if is_xlsx:
        righe, fieldnames, wb, ws = carica_xlsx(path)
        out_path = path
        log(f"Caricato XLSX: {path.name}")
    elif is_xls:
        righe, fieldnames = carica_xls(path)
        out_path = path.with_suffix(".xlsx")
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(fieldnames)
        log(f"Caricato XLS: {path.name} -> salverò come {out_path.name}")
    else:
        righe, fieldnames = carica_csv(path)
        log(f"Caricato CSV: {path.name}")

    lingue = colonne_lingua(fieldnames)

    if args.all:
        for codice in TUTTE_LINGUE:
            if codice not in fieldnames:
                fieldnames.append(codice)
                for riga in righe:
                    riga[codice] = ""
        if wb is not None:
            for c_idx, col in enumerate(fieldnames, start=1):
                ws.cell(row=1, column=c_idx, value=col)
        lingue = TUTTE_LINGUE[:]
        log(f"Aggiunte tutte le {len(lingue)} lingue disponibili")

    if not lingue:
        log("Nessuna colonna lingua trovata.")
        sys.exit(1)

    if col_source not in fieldnames:
        log(f"Colonna sorgente '{col_source}' non trovata nel file. "
            f"Colonne disponibili: {', '.join(fieldnames)}")
        sys.exit(1)

    max_lines = args.max_lines
    da_elaborare = righe[:max_lines] if max_lines > 0 and max_lines < len(righe) else righe
    if max_lines > 0 and max_lines < len(righe):
        log(f"Tradurrò solo le prime {max_lines} righe su {len(righe)}")

    log(f"Colonna sorgente: {col_source}")
    log(f"Lingue destinazione: {', '.join(lingue)}")
    log(f"Righe: {len(righe)}")
    log("")

    session = requests.Session()

    for i, riga in enumerate(da_elaborare, 1):
        termine = riga.get(col_source, "")
        if not termine.strip():
            log(f"[{i}/{len(da_elaborare)}] (riga vuota, saltata)")
            continue

        tradotte = 0
        for lingua in lingue:
            if riga.get(lingua, "").strip():
                continue
            trad = traduci(termine, lingua, modello=modello, session=session)
            riga[lingua] = trad
            log(f"  {lingua}: {trad}")
            tradotte += 1
            if delay:
                time.sleep(delay)

        if tradotte == 0:
            log(f"[{i}/{len(da_elaborare)}] '{termine}' — già completo")
        else:
            log(f"[{i}/{len(da_elaborare)}] '{termine}' — {tradotte} lingue tradotte")

        if wb is not None:
            if save_interval > 0 and i % save_interval == 0:
                salva_xlsx(ws, righe, fieldnames)
                wb.save(out_path)
        else:
            if save_interval > 0 and i % save_interval == 0:
                salva_csv(path, righe, fieldnames)

        log("")

    if wb is not None:
        salva_xlsx(ws, righe, fieldnames)
        wb.save(out_path)
    else:
        salva_csv(path, righe, fieldnames)

    log("Fatto! Tutte le traduzioni completate.")


if __name__ == "__main__":
    main()
