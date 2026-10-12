# Migrazione Kajenn e Asqueel

Kajenn e la persistenza Asqueel sono implementati. L'applicazione
`MailProxyApplication` espone operazioni tipizzate condivise da REST e MCP,
conservando gli adattatori REST v1. Il database `mail` è registrato in Kajenn:
il motore usa lo stesso runtime Asqueel del registry.

## Layout e compatibilità

SQLite conserva le otto tabelle nello schema `main` del file esistente;
PostgreSQL usa `public`. Le chiavi degli account sono `(tenant_id, id)` e i
messaggi mantengono la chiave interna `pk` e l'unicità `(tenant_id, id)`.
Identificativi, coda, eventi, configurazioni e token persistenti devono essere
preservati durante l'aggiornamento.

Il bootstrap di compatibilità esegue DDL attraverso il runtime Asqueel.
Non viene applicato il migrator strutturale di Asqueel 0.5.1: la gestione
SQLite di `main` tramite ATTACH non è compatibile con il layout precedente a
file singolo. Una futura adozione del migrator richiede una verifica distinta
del layout e della trasformazione dei dati.

Le destinazioni `:memory:` e `sqlite::memory:` usano un file temporaneo privato,
condiviso fra i worker della stessa persistenza e rimosso alla chiusura. Non
sono database durevoli dopo la chiusura.

## Transazioni e lifecycle

Ogni unità sincrona completa viene delegata a `server.run_sync`. Acquisizione
del runtime, impostazione del contesto tenant, query, commit oppure rollback e
chiusura della connessione avvengono sullo stesso thread. Il contesto viene
ripristinato anche in caso di errore. I risultati ritornano materializzati al
motore asincrono; le transazioni non restano aperte durante SMTP, fetch remoti
o callback HTTP.

Lo shutdown attende le operazioni DB pendenti prima di chiudere la persistenza.
La cancellazione dell'attesa non interrompe automaticamente il thread: un
commit dall'esito incerto non autorizza un retry cieco.

La CLI usa il proprio event loop e `asyncio.to_thread` come runner predefinito;
chiude la persistenza in `finally`. Non introduce un pool Asqueel separato.

## Procedura operativa

La procedura richiede una finestra di manutenzione e verifiche su una copia.
Non eseguire automaticamente comandi distruttivi sul database originale.

1. Identificare e fermare **tutti** i writer: server, worker, CLI, script e
   processi di manutenzione. Impedire il riavvio automatico della vecchia
   versione durante la finestra. Registrare versione, configurazione e stato
   iniziale della coda.
2. Creare un backup coerente. Per SQLite usare il meccanismo di backup SQLite
   oppure una copia a database completamente fermo che includa correttamente
   l'eventuale stato WAL; copiare soltanto il file principale mentre esistono
   writer non produce una garanzia di coerenza. Per PostgreSQL usare `pg_dump`
   con gli writer arrestati. Conservare anche configurazione e segreti di
   deployment con le protezioni appropriate.
3. Verificare che il backup sia ripristinabile in una destinazione separata.
   Avviare la nuova versione su quella copia, con invio disattivato e senza
   accesso a SMTP, IMAP e callback di produzione. Non collegare la copia ai
   servizi di consegna reali.
4. Confrontare conteggi per tabella e tenant, chiavi e vincoli, riferimenti
   account/messaggi, coda pendente, priorità e differimenti, eventi e stato dei
   report. Verificare token tenant, configurazione persistente, impostazioni
   bounce e credenziali senza stamparne i valori. Provare autenticazione,
   discovery MCP e operazioni REST su dati di prova, senza inviare messaggi.
5. Procedere al rollout solo dopo l'esito positivo delle verifiche. Mantenere
   gli writer precedenti spenti; avviare la nuova versione sul database scelto,
   controllare health, autorizzazione, conteggi e stato della coda prima di
   riabilitare invio e callback. Documentare il momento in cui ripartono le
   scritture.
6. Se serve rollback, fermare prima **tutti** i writer nuovi. Ripristinare
   l'intero backup coerente e la versione/configurazione precedente; verificare
   i dati prima di riaprire il servizio. Non mescolare writer vecchi e nuovi e
   non ripristinare singole tabelle in un database già modificato. Il ripristino
   del backup perde le scritture successive: eventuali invii già effettuati
   richiedono riconciliazione e non devono essere ripetuti automaticamente.

## Autorizzazione e compatibilità v1

REST e MCP applicano gli stessi controlli admin/tenant; discovery e chiamata
sono filtrate per ruolo e canale. Gli handler v1 che discriminano il verbo HTTP
non sono tool MCP. Health e metriche restano HTTP.

Le correzioni di sicurezza sono intenzionali: un tenant non può sospendere o
riattivare la coda di un altro tenant né accodare un batch che contiene messaggi
altrui. Il batch viene verificato prima di accodare qualsiasi elemento.

L'assenza di un token admin configurato non promuove gli anonimi ad admin.
La vecchia amministrazione anonima richiede esplicitamente
`allow_anonymous_admin=True`; le identità admin Kajenn attendibili possono
ricevere i permessi applicativi. Il deployment deve configurare esplicitamente
`GMP_API_TOKEN`: la factory standalone non recupera implicitamente il token
admin dal database.

## Evidenze e limiti

Verifica finale del 12 ottobre 2026:

- **761 test unit/local superati**, inclusi REST/MCP, SMTP locale, transazioni,
  contesto tenant, cancellazione e lifecycle Kajenn.
- **19 test PostgreSQL superati** su istanze Docker effimere PostgreSQL 15 e 17,
  incluse fixture 0.7.7 e parametri SQL con cast.
- **196 test fullstack superati e 6 xfail preesistenti**; inclusi PEC,
  allegati, reporting, security e IMAP. I sei xfail riguardano correlazione
  e reporting bounce dell'issue storica #103: non sono risultati superati.
- Dopo la correzione delle porte dei simulatori SMTP: **30 test superati e
  1 xfail**, con risposte SMTP reali 550/451/452, senza connection refused.
- Callback CRUD locale: **8 test superati** dopo allineamento del percorso
  del client fittizio, senza chiamate a endpoint esterni.
- Wheel costruito; avvio SQLite e lifecycle verificati in ambiente pulito,
  senza aiosqlite né psycopg-pool. Ruff sui nuovi moduli e Bandit con la
  configurazione del progetto: nessuna segnalazione.

Log locali in `temp/phase2-unit-final.*`,
`temp/fullstack-asqueel-complete196.*`, `temp/fullstack-asqueel-smtp30.*`
e `temp/fullstack-asqueel.*`. Per ripetere i 19 test PostgreSQL:
`pytest tests/postgres_tests tests/unit/00_core/test_asqueel_postgresql.py`.
Le prove hanno usato dati sintetici, porte dedicate e container poi rimossi.
Il primo run ha rilevato anche un timeout intermittente del server SMTP di
prova con un allegato da 2 MiB; lo stesso caso è passato sia nella verifica
mirata sia nella suite completa successiva, senza alterare dimensione o timeout.

Resta un limite preesistente del rate log: registra solo `account_id`, perciò
account omonimi di tenant diversi possono condividere i conteggi. Usare ID
account globalmente unici fino a una modifica dedicata dello schema.

Usare un solo processo dispatcher per database (per esempio Uvicorn con
`--workers 1`). La selezione della coda e l'invio SMTP non formano una
transazione distribuita; il commit Asqueel riguarda soltanto la singola
operazione DB.

I test non dimostrano coordinamento della coda fra più processi né consegna
exactly-once. Un ripristino o un retry dopo un esito SMTP/commit incerto può
richiedere riconciliazione manuale.
