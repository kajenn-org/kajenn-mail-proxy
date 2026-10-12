# Baseline prima della migrazione

Esecuzione del 12 ottobre 2026 sul clone `kajenn-mail-proxy`, prima di modificare
il codice applicativo. La baseline non è completamente verde: il nucleo dei test
eseguiti passa, ma il bootstrap HTTP non è compatibile con le dipendenze risolte.

## Origine e ambiente

- Commit: `495b83907559a308a7c6b57b47a3053d18852355` di `genro-mail-proxy` develop.
- Repository: `/Users/gporcari/Sviluppo/kajenn/kajenn-mail-proxy`.
- Ramo: `codex/kajenn-migration`.
- Ambiente isolato: `.venv`, CPython 3.13.9, macOS.
- Installazione: `uv pip install --python .venv/bin/python -e '.[dev,postgresql,bounce]'`.
- Dipendenze risolte senza retrocedere a versioni precedenti: genro-asgi 0.46.3,
  genro-builders 0.27.0, genro-routes 0.30.1, genro-bag 0.27.0,
  genro-tytx 0.16.2; pytest 9.1.1 e pytest-asyncio 1.4.0.
- Elenco completo: [baseline-requirements.txt](migration/baseline-requirements.txt).
  È una fotografia diagnostica dell'ambiente, non il vincolo delle dipendenze finali.

## Test

Comando, equivalente alla selezione della CI senza raccolta della copertura:

```sh
.venv/bin/python -m pytest -m 'not fullstack and not db' \
  --junitxml=temp/migration-baseline.xml -q
```

Risultato: **556 passed, 217 deselected, 19 warnings, 153 errors**, in 28,04 secondi.
Codice di uscita: 1. Nessun test eseguito ha riportato un assertion failure;
gli errori seguenti impediscono l'esecuzione dei test coinvolti.

| Gruppo | Errori di setup | Diagnosi |
| --- | ---: | --- |
| `tests/local_e2e/` | 143 | `app_class` riceve un'istanza `MailProxyApplication` anziché una classe |
| `tests/unit/10_asgi/` | 10 | Stesso problema con l'istanza `ProbeApplication` |

Le fixture costruiscono `AsgiServer(applications=[app], middleware={"auth": False})`.
Durante la creazione della `ShortcutConfiguration`, la validazione della grammatica
in `genro_builders/builder/_grammar.py` rifiuta `app_class`:

```text
ValueError: Validation failed: 'app_class': expected <class 'type'>, got MailProxyApplication
ValueError: Validation failed: 'app_class': expected <class 'type'>, got ProbeApplication
```

Sono errori del codice originale con l'ambiente appena risolto, precedenti a ogni
modifica di migrazione. Il confronto del contratto HTTP resta da completare dopo
l'adeguamento del bootstrap nella fase 1. Non trattare i test non eseguiti come
passati e non introdurre un downgrade silenzioso per ottenere una baseline verde.

La selezione esclude fullstack e integrazione DB: nessun invio reale, stack Docker
o database di produzione è stato usato. La presenza del driver PostgreSQL fra le
dipendenze non equivale all'esecuzione della suite PostgreSQL. Copertura e matrice
Python completa della CI non sono state eseguite in questa preparazione.

## Analisi statica

```sh
.venv/bin/python -m bandit -r src/mail_proxy -c pyproject.toml \
  -f json -o temp/migration-bandit.json -q
```

Codice di uscita: 0. **0 segnalazioni, 0 errori di scansione**, con le esclusioni
e i controlli disabilitati già presenti in `pyproject.toml`.

## Evidenze locali

Output completi conservati nella cartella `temp/`, esclusa dal versionamento:

- `migration-baseline.log`: output pytest e traceback completi.
- `migration-baseline.xml`: risultati JUnit per test.
- `migration-bandit.json`: report Bandit.
- `migration-install.log`: installazione dell'ambiente.

Il codice applicativo è rimasto invariato. Le modifiche della preparazione sono
il piano, questo resoconto e la fotografia delle dipendenze.
