# Piano di migrazione a kajenn-mail-proxy

Data: 12 ottobre 2026.

Obiettivo: portare il mail proxy in un repository autonomo come applicazione
Kajenn `McpOpenApiApplication`, poi sostituire la persistenza interna con Asqueel
sincrono eseguito nel pool di thread Kajenn. Il motore SMTP resta asincrono.

## Stato e origine

- Destinazione approvata: `/Users/gporcari/Sviluppo/kajenn/kajenn-mail-proxy`.
- Sorgente: `genro-mail-proxy`, ramo `develop`, versione 0.7.7.
- Commit iniziale: `495b83907559a308a7c6b57b47a3053d18852355`.
- Ramo di lavoro: `codex/kajenn-migration`.
- Preparazione: clone locale con storia Git; file locali non versionati esclusi.
- Il remote `legacy` identifica il clone sorgente; push disabilitato.
- Destinazione GitHub pubblica approvata: `kajenn-org/kajenn-mail-proxy`, branch `main`.
- PyPI e Read the Docs saranno configurati dall’utente.
- Fase 1: completata nel commit `7485772` (737 test al checkpoint).
- Fase 2: completata; 761 test unit/local, 19 PostgreSQL e 196 fullstack
  superati, con 6 xfail bounce già presenti (issue #103).
- Baseline e ambiente: vedere `docs/migration-baseline.md`.

Questo piano recepisce le decisioni della conversazione. `NEXT_STEPS.md` resta
documentazione storica della linea genro-asgi; le sue proposte non sono ulteriori
requisiti approvati per la migrazione.

## Decisioni comuni

1. Pacchetto finale `kajenn_mail_proxy`, classe `MailProxyApplication`.
2. API applicativa tipizzata condivisa fra REST e MCP; conservazione delle URL
   REST v1 tramite adattatori. Le correzioni di autorizzazione saranno esplicite
   e documentate, anziché perpetuare accessi indebiti per compatibilità.
3. SMTP, retry, allegati, reporting, bounce e PEC conservano il comportamento
   esistente salvo correzioni motivate e verificate.
4. SQLite e PostgreSQL restano i database supportati. Asqueel diventa lo strato
   di persistenza finale, senza mantenere due implementazioni SQL interne a regime.
5. Ogni fase deve soddisfare il proprio criterio di completamento prima della
   successiva. Distinguere sempre regressioni nuove da problemi della baseline.

## Fase 1 Applicazione Kajenn MCP e OpenAPI

### Implementazione

- Sostituire `genro-asgi` con Kajenn, aggiornando import e dipendenze dirette
  alle versioni stabili verificate al momento dell'implementazione.
- Rinominare pacchetto, riferimenti di distribuzione e configurazioni di build.
  Conservare inizialmente il comando CLI `mail-proxy` e le URL v1 per i client.
- Derivare `MailProxyApplication` da `McpOpenApiApplication`.
- Dichiarare grammatica e recipe Kajenn: persistenza, scheduler, concorrenza,
  retry, cache/allegati, reporting e bounce. Stabilire una precedenza esplicita
  fra configurazione di deployment e variazioni persistenti; proteggere i segreti.
- Portare costruzione, avvio e arresto del motore nel lifecycle applicativo;
  eliminare l'istanza globale e le letture DB durante l'import di `server.py`.
- Iniettare nel motore la persistenza attuale; censire i metodi usati e gli
  accessi diretti a tabelle/adapter, includendo CLI, autenticazione e background.
  Delimitare un contratto di operazioni del dominio senza aggiungere un framework.
- Creare metodi tipizzati distinti per lettura, creazione, modifica e cancellazione.
  Non esporre direttamente come tool gli handler v1 che discriminano il verbo HTTP.
- Esporre selettivamente REST/MCP con `channel_channels="mcp,rest"`.
  Health e metriche restano HTTP; accodare una mail non deve attendere SMTP.
- Usare un'unica autorizzazione per i due canali: admin/tenant, appartenenza delle
  risorse e del payload, discovery filtrata. Correggere in particolare suspend,
  activate e add_messages; rivedere la modalità senza token e la composizione
  con l'identità Kajenn per non introdurre privilegi impliciti nel nuovo deployment.
- Conservare gli adattatori REST v1 per URL, payload, risposte ed errori concordati.
- Aggiornare avvio standalone, Docker, CLI, esempi, README e documentazione API.

### Verifiche

- Baseline unit/local_e2e, confrontando eventuali fallimenti preesistenti.
- Tutte le rotte del contratto REST v1 e le correzioni di sicurezza dichiarate.
- MCP initialize, tools/list e tools/call; schema tipizzato, visibilità per ruolo,
  rifiuto dei tool fuori canale e parità delle operazioni REST/MCP.
- Matrice anonimo/admin/tenant A/tenant B, payload batch e identificativi esterni.
- Configurazione, startup/shutdown, assenza di istanze globali del motore.
- Ciclo accodamento → invio → reporting, retry, allegati, bounce/PEC pertinenti.

### Criterio di completamento

Applicazione montabile in Kajenn, API REST e MCP utilizzabili e autorizzate,
client v1 compatibili salvo correzioni documentate, motore ancora funzionante
con la persistenza precedente. Nessuna regressione nuova irrisolta; limitazioni
dei test d'integrazione esplicitamente registrate.

## Fase 2 Persistenza Asqueel sincrona nel pool

### Modello e integrazione

- Dichiarare recipe e tabelle Asqueel per tenant, account, messaggi, eventi,
  send log, command log, configurazione e istanza; conservare chiavi e vincoli.
- Registrare Asqueel in `databases` della configurazione Kajenn.
- Implementare le operazioni del contratto di persistenza della fase 1,
  eliminando dipendenze del motore dagli adapter SQL concreti.
- Mantenere un'interfaccia async per il motore. Delegare ciascuna operazione
  sincrona completa con `await server.run_sync(...)`.
- Anche autenticazione, comandi CLI, avvio e task in background devono avere
  un percorso DB e un proprietario del cleanup espliciti.

### Regole di esecuzione

Una unità transazionale è una singola chiamata al pool: impostazione del contesto
tenant, query/scritture, commit oppure rollback e cleanup sullo stesso thread.
Il contesto va ripristinato anche dopo errori. Non suddividere una transazione
fra più `run_sync`, che potrebbero usare thread diversi.

Le operazioni in background chiudono le proprie connessioni con un `finally`;
non dipendono dal cleanup HTTP. Restituire risultati materializzati al motore.
Non mantenere transazioni aperte durante SMTP, fetch allegati o callback HTTP.

Verificare capacità del pool e attesa delle operazioni; non aggiungere un pool
Asqueel separato senza necessità dimostrata. Gestire shutdown e cancellazione:
la cancellazione dell'attesa non interrompe automaticamente un thread. Un esito
di commit incerto non autorizza retry ciechi o duplicazione delle operazioni.

### Migrazione dati

- Preparare fixture di database precedenti con dati e stati rappresentativi.
- Definire esplicitamente backup, fermo dei writer, applicazione della migrazione,
  verifica e recupero in caso di errore; non usare dati di produzione nei test.
- Preservare identificativi, unicità per tenant, messaggi pendenti, priorità,
  differimenti, stato SMTP/reporting, credenziali e configurazioni persistenti.
- Verificare la disposizione fisica SQLite: gli schemi Asqueel possono avere
  file separati via ATTACH; il file precedente non è automaticamente compatibile.
- Distinguere aggiornamento strutturale dello schema e trasformazione dei dati.

### Verifiche e criterio di completamento

- Stesso contratto di persistenza su SQLite e PostgreSQL.
- Migrazione delle fixture e confronto di dati, vincoli e stati della coda.
- Commit/rollback, errori, cleanup sul thread proprietario, isolamento del contesto
  fra tenant/worker e shutdown con operazioni in corso.
- Event loop reattivo durante lavoro DB lento e invii SMTP concorrenti.
- Concorrenza della selezione dei messaggi e del rate limiting: documentare le
  garanzie dimostrate, senza inferire coordinamento multiprocesso dal solo DB.
- Ripetere REST/MCP e i percorsi fullstack pertinenti, incluse PEC/bounce.
- Rimuovere SQL/adapter precedenti e dipendenze inutilizzate solo dopo parità
  verificata. Aggiornare istruzioni operative e architettura finale.

## Fuori ambito

Asqueel async nativo, riscrittura dello scheduler mail, nuova UI e garanzie di
consegna exactly-once non sono obiettivi di queste due fasi. Eventuali problemi
preesistenti che bloccano il passaggio vanno registrati e risolti esplicitamente.

## Riferimenti

- `src/mail_proxy/mail_proxy_application.py`, `src/mail_proxy/server.py`.
- `src/mail_proxy/mailproxy_db.py`, `src/mail_proxy/sql/`, `src/mail_proxy/entities/`.
- `tests/README.md`, `tests/api_routes.py`, `tests/local_e2e/`, `tests/fullstack/`.
- Kajenn: `applications/mcp.py`, `server.run_sync`, `AsgiDbHandlerBase`.
- [Asqueel e Kajenn](https://github.com/asqueel-org/asqueel/blob/main/xmldocs/booklet/kajenn-app.md).
- [Limiti del runtime Asqueel](https://github.com/asqueel-org/asqueel/blob/main/docs/guide/limitations.md).

## Verifica della fase 1

12 ottobre 2026: suite `pytest -m "not fullstack and not db"`: 737 passati,
217 esclusi; inclusi SMTP reale locale, lifecycle, REST v1 e nuovi contratti MCP.
Log completo `temp/phase1-final.log`, JUnit `temp/phase1-final.xml`. Docker fullstack
e PostgreSQL non ancora eseguiti; saranno verificati con la persistenza finale.

Compatibilità intenzionalmente modificata: assenza del token admin non concede
privilegi (opt-in esplicito `allow_anonymous_admin` disponibile per sviluppo);
token admin di deployment non letto implicitamente dal DB; i tenant non possono
sospendere, attivare o accodare per altri tenant. Discovery materializzata prima
dei filtri per gestire i descriptor lazy incompleti di genro-routes 0.30.1.


## Implementazione della fase 2

La persistenza usa Asqueel 0.5.1 con il database `mail` nel registry Kajenn.
`MailProxyDb` mantiene il contratto async e delega l'intera operazione al pool
`server.run_sync`; `MailStore` e le tabelle sono sincroni. Anche il motore
iniettato nell'applicazione usa il pool Kajenn. La CLI usa il pool del proprio
event loop e chiude esplicitamente la persistenza nel `finally`.

Il modello conserva SQLite `main`, PostgreSQL `public`, chiavi composte e
unicità per tenant. Il bootstrap esegue DDL compatibile attraverso Asqueel:
il migrator generico 0.5.1 tenta ATTACH sullo schema SQLite `main`, quindi
non viene usato per questo layout storico. Aggiornamenti non supportati
falliscono con rollback dell'intera inizializzazione.

Ogni operazione esegue acquire, contesto tenant, commit/rollback e chiusura
connessione nel medesimo worker. La cancellazione del chiamante non cancella
il worker; il drain della chiusura continua anche se viene cancellata la sua
attesa. `:memory:` usa un file temporaneo privato condiviso dai worker.

Il vecchio backend è stato mantenuto durante la parità additiva (731 test)
e rimosso soltanto dopo le prove SQLite, PostgreSQL e lifecycle. Eliminati
`sql/adapters`, `aiosqlite` e il pool psycopg separato. Il pacchetto wheel
non include il vecchio SQL ed è stato provato in un ambiente pulito.

Correzioni verificate: cancellare account/tenant rimuove i relativi eventi
senza cancellare messaggi di tenant omonimi; i log condivisi non vengono
azzerati mentre esiste un altro account omonimo. REST v1 conserva l'envelope
`detail` anche senza header Accept, con status e header originari.

La procedura di manutenzione, backup e recupero è in
[docs/asqueel-migration.md](docs/asqueel-migration.md). Rimane il limite
preesistente dei conteggi rate basati soltanto su `account_id`; usare ID
account globalmente unici. Un solo dispatcher per database: la migrazione
non introduce coordinamento multiprocesso né consegna exactly-once.


### Chiusura e verifiche della fase 2

12 ottobre 2026: verifiche finali riportate in
[docs/asqueel-migration.md](docs/asqueel-migration.md). Test unit/local 761,
PostgreSQL 19, fullstack 196 superati; sei xfail storici bounce restano dichiarati.
Dopo l'allineamento delle porte nei test dei simulatori SMTP, la ripetizione
mirata chiude con 30 superati e uno degli xfail preesistenti, confermando
risposte SMTP 550/451/452 reali. Corrette fixture PEC e callback locali.
Build wheel e smoke test in ambiente pulito riusciti; nuovi moduli Ruff
senza rilievi; Bandit con configurazione repository senza rilievi.

Entrambe le fasi sono implementate. Dopo il checkpoint della migrazione,
la pubblicazione GitHub è stata autorizzata su `kajenn-org/kajenn-mail-proxy`
come repository pubblico. Read the Docs è configurato dall’utente e verificato su
https://kajenn-mail-proxy.readthedocs.io/en/latest/. README, badge, metadati
e documentazione sono allineati a Kajenn e Asqueel. La configurazione del
publisher PyPI è gestita dall’utente; non è stata avviata alcuna release.
