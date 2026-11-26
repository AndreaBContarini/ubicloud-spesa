# Spesa Ubicloud

Pagina che stima quanto si spende sui runner GitHub Actions di Ubicloud.
Ubicloud non espone la fatturazione via API, quindi la spesa si ricostruisce dai job GitHub.

## Come funziona

- `collect.py` legge ogni 30 minuti i run GitHub Actions dei repo dell'organizzazione e tiene solo i job girati su runner `ubicloud-*`.
- Ogni job costa minuti arrotondati per eccesso × prezzo al minuto del listino ufficiale Ubicloud (`config/billing_rates/github.yml` nel repo ubicloud/ubicloud).
- I dati vanno in `data/AAAA-MM.json`, un file per mese; `data/state.json` evita di rileggere i run già contati (i rerun degli ultimi 7 giorni vengono ripresi).
- `index.html` legge i file e mostra totale, proiezione, tetto di spesa, giorni, repo, runner, job e ultimi run.
- Il workflow `.github/workflows/aggiorna.yml` aggiorna i dati e ripubblica su GitHub Pages.

## Configurazione del repo

- Secret `GH_READ_TOKEN`: token fine-grained, proprietario l'organizzazione, tutti i repo, permessi Actions: Read e Metadata: Read.
- Variabile `GH_ORG`: nome dell'organizzazione.
- Variabile `START_DATE`: prima data da contare (serve solo al primo giro).

## Da aggiornare a mano

- Tetto e avvisi: costante `BUDGET` in `index.html` (oggi 60 $, avvisi a 30 e 45 $).
- Opzione premium: se la si accende in console, Ubicloud fattura premium anche i label standard. Segnare il periodo in `PREMIUM_WINDOWS` in `collect.py`.
- Listino: se Ubicloud cambia prezzi, aggiornare `RATES` in `collect.py`.

## Limiti

Stima, non fattura: non conta credito gratuito, cache GitHub e concorrenza extra (centesimi).
Il numero esatto resta nella console Ubicloud, sezione Billing.

## Prova in locale

```bash
GH_TOKEN=$(gh auth token) GH_ORG=<org> START_DATE=2026-11-26 python3 collect.py
python3 -m http.server 8765   # poi http://localhost:8765
```
