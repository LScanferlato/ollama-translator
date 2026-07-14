# Traduttore Multilingua CSV / XLSX / XLS

Traduce termini tecnici da italiano a più lingue usando **Ollama** (LLM locale).

## Requisiti

- Python 3.8+
- Ollama in esecuzione su `localhost:11434` con un modello installato (es. `qwen3:14b`, `llama3`, `gemma3`)
- Dipendenze Python:

```bash
pip install requests openpyxl xlrd
```

## Uso

```bash
python translate_csv.py file.csv
python translate_csv.py file.xlsx
python translate_csv.py file.xls
```

Il file viene modificato **direttamente** (in-place). Puoi interrompere con `Ctrl+C` e riavviare: le celle già tradotte vengono saltate.

## Formato file

Le colonne vengono interpretate automaticamente:

- `ID`, `Categoria`, `Termine`, `Descrizione` → colonne dati
- Tutte le altre colonne → codici lingua da tradurre (es. `EN`, `DE`, `FR`, `ES`, `PT`, `RU`, …)

I CSV possono usare `,` o `;` come separatore (rilevato automaticamente).

I file `.xls` vengono convertiti e salvati come `.xlsx`.

## Configurazione

Modifica le variabili in cima a `translate_csv.py`:

| Variabile | Default | Descrizione |
|---|---|---|
| `MODEL` | `qwen3:14b` | Modello Ollama |
| `DELAY` | `1` | Secondi tra richieste |
| `LINGUA_PARTENZA` | `italiano` | Lingua di partenza |

## Log

Durante l'esecuzione mostra:

```
Caricato CSV: test.csv
Lingue: EN, DE, FR, ES
Righe: 43

[1/43] 'Ingranaggio ipoide'
  EN: hypoid gear
  DE: Hypoidgetriebe
  FR: engrenage hypoïde
  ES: engranaje hipoide
[1/43] 'Ingranaggio ipoide' — 4 lingue tradotte

[2/43] 'Ingranaggio elicoidale' — già completo
```
