# Traduttore Multilingua per Glossari e Dataset CSV / XLSX / XLS
Versione rivista — presentazione in stile accademico-scientifico

## Abstract
Questo progetto fornisce strumenti per la traduzione assistita di termini tecnici contenuti in file CSV, XLSX e XLS mediante modelli di linguaggio eseguiti localmente tramite Ollama. Gli script mirano a facilitare la produzione di glossari multilingue coerenti, minimizzando le spiegazioni non necessarie e preservando la qualità terminologica tramite l'uso di riferimenti contestuali tra le lingue.

## Introduzione
La traduzione terminologica è un'attività critica in contesti tecnici e scientifici, dove la coerenza lessicale e la fedeltà semantica sono essenziali. Questo repository mette a disposizione due strumenti complementari:
- `translate_csv.py`: traduce colonne designate in file CSV/XLSX/XLS, aggiungendo o aggiornando colonne lingua in-place.
- `translate_glossary.py`: completa o riscrive colonne di un glossario multilingue, usando traduzioni già presenti come contesto per garantire coerenza interlinguistica.

Gli script sono progettati per essere eseguiti in locale, sfruttando Ollama come backend per i modelli di linguaggio, consentendo la piena proprietà dei dati e la riproducibilità degli esperimenti.

## Obiettivi
- Fornire uno strumento semplice per generare traduzioni automatiche di termini tecnici.
- Mantenere coerenza terminologica sfruttando traduzioni già presenti come contesto.
- Consentire un'esecuzione ripetibile e controllabile (parametri di modello, delay, salvataggi incrementali).
- Minimizzare i rischi di perdita dati mediante salvataggi atomici e supporto per interruzioni controllate.

## Metodologia
Gli script usano richieste HTTP verso l'API locale di Ollama (`localhost:11434`) per inviare prompt progettati per restituire una risposta breve (termine o breve frase) senza spiegazioni o prefissi. Il flusso generale è:
1. Caricamento del file (CSV/XLSX/XLS).
2. Identificazione della colonna sorgente e delle colonne target (lingue).
3. Per ogni riga, per ogni lingua target:
   - Se già presente, viene saltata (configurabile).
   - Si costruisce un prompt che richiede una traduzione concisa; nello script per glossario si includono riferimenti contestuali (altre colonne non vuote).
   - Invio della richiesta al modello Ollama e pulizia/normalizzazione della risposta.
4. Salvataggio periodico (opzione `--save-interval`) o salvataggio finale. Per `translate_glossary.py` il salvataggio è atomico (scrittura su file temporaneo e `os.replace()`).

## Requisiti
- Python 3.8 o superiore
- Ollama attivo su localhost:11434 con almeno un modello installato
- Dipendenze Python:
  - `requests`
  - `openpyxl` (per .xlsx)
  - `xlrd` (per .xls)
Installazione dipendenze:
```bash
pip install requests openpyxl xlrd
```

## Architettura e design delle componenti
- I moduli principali sono script autonomi che operano in CLI.
- Connessione a Ollama mediante endpoint REST (`/api/generate`, `/api/tags`) per selezione e interrogazione dei modelli.
- Funzioni di pre/post-processing:
  - rilevamento automatico del delimitatore CSV (`,` vs `;`);
  - normalizzazione e pulizia della risposta (`pulisci_risposta`);
  - rimozione di qualificatori istituzionali nel glossario (`rimuovi_qualificatori_ue`).
- Meccanismi di robustezza:
  - retry configurabili (`MAX_RETRIES`, `RETRY_DELAY`);
  - salvataggio atomico per evitare corruzione dei file;
  - gestione dell'interruzione con salvataggio del progresso (`ESC` o `Ctrl+C`).

## Uso (esempi)
Esempi d'uso per `translate_csv.py`:
```bash
# Traduci file CSV (in-place)
python translate_csv.py dataset.csv

# Specificare colonna sorgente e modello
python translate_csv.py dataset.csv --source-col IT --model "qwen3:14b"

# Aggiungi tutte le colonne di lingua e traduci
python translate_csv.py dataset.csv --all

# Traduci solo le prime 100 righe, con delay e salvataggi incrementali
python translate_csv.py dataset.csv -n 100 --delay 0.5 --save-interval 10
```

Esempi d'uso per `translate_glossary.py`:
```bash
# Traduci/Completa un glossario (default file: glossary_multilingual.csv)
python translate_glossary.py glossary_multilingual.csv

# Traduci solo alcune lingue e usa colonne di contesto specifiche
python translate_glossary.py glossary_multilingual.csv --langs en fr de --context-langs it es

# Sovrascrivi tutte le celle anche se già piene
python translate_glossary.py glossary_multilingual.csv --overwrite
```

## Formato dei file e convenzioni
- Il formato del foglio è tabellare; la prima riga è intestazione.
- Colonne con nomi come `ID`, `Categoria`, `Termine`, `Descrizione` sono considerate metadati; tutte le altre colonne sono interpretate come codici lingua (es.: `EN`, `DE`, `FR`).
- I CSV possono usare `,` o `;` come separatore: viene effettuata una rilevazione automatica.
- I file `.xls` vengono convertiti in `.xlsx` per il salvataggio finale.

## Parametri rilevanti e configurazione
- MODEL: nome del modello Ollama predefinito (es. `qwen3:14b`).
- DELAY: attesa tra richieste per controllare il throughput e limitare picchi di carico.
- MAX_RETRIES / RETRY_DELAY: parametri per la tolleranza ai guasti di rete o a errori temporanei dell'API.
- SALVA_OGNI_N / --save-interval: numero di righe fra due salvataggi successivi per ridurre il carico I/O su dataset molto grandi.
- --overwrite (glossario): forza la riscrittura anche di celle già popolate.

## Valutazione e limitazioni
- Qualità: la qualità delle traduzioni dipende fortemente dal modello scelto e dal prompt design. L'uso di riferimenti contestuali (presente in `translate_glossary.py`) migliora la coerenza terminologica, ma non garantisce adeguata disambiguazione in assenza di contesto.
- Errori comuni: il modello può restituire spiegazioni o frasi lunghe; per questo è implementata una funzione di pulizia che tenta di estrarre la forma concisa desiderata.
- Bias e responsabilità: i modelli di linguaggio possono introdurre errori sistematici o terminologia non appropriata in specifici domini. Si raccomanda una revisione umana finale, soprattutto per testi destinati ad uso ufficiale o normativo.

## Riproducibilità e best practice sperimentali
- Fissare il modello e i parametri (`MODEL`, `DELAY`, `MAX_RETRIES`) per esperimenti ripetibili.
- Usare salvataggi atomici e checkpoint per consentire ripresa sicura su dataset grandi.
- Conservare snapshot dei file sorgente e dei log di esecuzione per audit e confronto delle versioni tradotte.
- Annotare esempi di prompt e output campione come baseline per ulteriori valutazioni.

## Esempio di output di log
Durante l'esecuzione si vedranno messaggi simili al seguente:
```
Caricato CSV: vocab.csv
Lingue: EN, DE, FR, ES
Righe: 120

[1/120] 'Ingranaggio ipoide'
  EN: hypoid gear
  DE: Hypoidgetriebe
  FR: engrenage hypoïde
  ES: engranaje hipoide
[1/120] 'Ingranaggio ipoide' — 4 lingue tradotte
```

## Suggerimenti per l'uso in produzione
- Eseguire test preliminari su un campione rappresentativo del dataset per tarare `DELAY` e `MODEL`.
- Integrare controlli automatici di qualità (p. es. confronti con glossari esistenti, regole terminologiche).
- Versionare i file glossario e applicare review umana per le traduzioni finali.

## Licenza e crediti
- Licenza: inserire qui la licenza desiderata (es. MIT, Apache-2.0).
- Autore: [LScanferlato] — repository: ollama-translator.
- Ringraziamenti: si ringrazia la comunità e i manutentori dei modelli utilizzati via Ollama.

## Contatti e riproducibilità
Per domande, suggerimenti o segnalazioni di bug, aprire un issue nel repository GitHub: https://github.com/LScanferlato/ollama-translator

## Note bibliografiche (opzionali)
Per una trattazione generale sui rischi e le migliori pratiche nell'uso di modelli di linguaggio per traduzione terminologica, consultare la letteratura su:
- valutazione terminologica e glossari (terminology management),
- prompt engineering e controllo della generazione,
- procedure di validazione umana nelle pipeline di traduzione automatica.
