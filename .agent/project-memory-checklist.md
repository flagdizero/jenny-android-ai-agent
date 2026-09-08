# Checklist — la memoria dei progetti

Piano: [`project-memory-plan.md`](./project-memory-plan.md). Le fasi sono ordinate: **0 blocca A**,
e **C e' indipendente da tutto** (si puo' fare per prima, e conviene).

## Fase 0 — fare spazio in `USER.md` *(bloccante)*

- [x] Misurare il costo per turno di alzare `dream.userBudgetChars` da 3.000: e' un blocco in
      **ogni** prompt di **ogni** sessione, non solo nelle chat che ne beneficiano
- [x] Non servita: col tetto a 4.000 l'apertura e' partita da 2.466/4.000 (62%). Dopo la prima
      passata reale `USER.md` sta a **3.612/4.000 (90%)**, dentro il tetto senza potatura forzata
- [x] **4.000**, scritto con la misura accanto (`config/schema.py`, dove il
      commento spiega gia' che 3.000 e' un numero misurato e non sacro)
- [x] `USER.md` ricontrollato voce per voce: **20 su 20 ancora presenti** — una e' stata
      *sostituita* con una versione piu' completa (`replace`, non `remove`), e le due voci uscite
      sono in `memory/archive/`

## Fase C — la cattura smette di prendersi i fatti personali *(indipendente)*

- [x] `agent/project.md`: la regola di cattura dice esplicitamente che un fatto **sulla persona**
      non va nel journal
- [x] Verificato su un turno vero (08/09, progetto di prova): il messaggio conteneva una regola
      di wiki **e** un fatto sulla persona; nel journal e' finita solo la regola
- [ ] Le pagine gia' scritte male restano: decidere se ripulirle a mano o lasciarle (non e'
      lavoro di questo piano, ma va deciso e non dimenticato). **Aperto**

## Fase A — la corsia di diario

- [x] **Test prima del codice**: una voce `project:` non compare in nessuno dei quattro rami di
      `read_recent_history_for_prompt` (personale, interna, giardiniere, altro progetto)
- [x] `MemoryStore.append_history` scrive una voce con chiave `project:` invece di rifiutarla
- [x] Riscrivere la docstring di `append_history`: l'isolamento e' «una chiave piu' una
      destinazione», non piu' «un'assenza»
- [x] Aggiornare `.agent/security.md` **nello stesso commit** — oggi dichiara il contrario
- [x] `Consolidator`: il ramo che copiava i messaggi dentro il progetto quando la chiamata LLM
      falliva presuppone che `append_history` non scriva. Rileggerlo.

## Fase B — estrazione ristretta e destinazione unica

- [x] Nuovo template `agent/dream_project.md` con le quattro regole (la bozza provata e' `v4`)
- [x] `build_dream_prompt`: le voci `project:` costruiscono il prompt di progetto, le personali
      quello di sempre; **non nello stesso batch**
- [x] `MemoryEntryTool`: destinazioni ammesse passate dal chiamante; una scrittura verso `memory`
      da voce di progetto e' **rifiutata** e loggata
- [x] Test: rifiuto verso `memory`, passaggio verso `user`
- [x] Il cursore di Dream avanza correttamente su un batch misto e su uno di sole voci di progetto

## Fase D — consolidazione dei progetti anche a tempo

- [x] **Percorso suo**: `AutoCompact._harvest_project_diary` riassume senza troncare. La
      manopola resta spenta — misurato che solo 3 sessioni di progetto su 9 erano mai state
      consolidate, quindi appoggiarsi alla compattazione lasciava la corsia vuota
- [x] Verificare che `_pages_carry_the_project` **non** blocchi la corsia (difende il contenuto
      del progetto, non il diario: sono due depositi con due orologi)
- [x] Test: una chat di progetto **corta** produce comunque la voce

## Fase E — la varianza

- [x] **N = 3**, finestra di rilettura invece di un cursore che arretra (che andrebbe in
      livelock su un batch corto), guardando la curva
      1→4 passate misurata nel piano
- [x] Verificare che il blocco «gia' registrato» impedisca la doppia scrittura sulla rilettura
- [ ] Misurare il costo in token di N passate contro il guadagno in fatti recuperati *(da fare
      sul campo, dopo qualche notte: serve una serie, non un run)*

## Verifica finale, sul telefono

- [x] Prima passata reale su 9 progetti: **7 voci nuove** in `USER.md`, tutte della classe
      giusta (cura in corso, colesterolo, allergie, il pattern sociale, le piante possedute) — e
      l'ipotesi tiroidea registrata **con il suo stato** ("ancora da verificare"), non come
      diagnosi. Il conto pieno sui 24 va rifatto dopo qualche notte
- [x] **Controllo negativo passato**: dal progetto di lavoro puro, zero voci. E nella prova
      controllata le due regole di wiki del messaggio non sono atterrate da nessuna parte,
      mentre il fatto sulla persona nello stesso messaggio si
- [x] `USER.md` 3.612/4.000, nessuna voce preesistente sparita
- [x] Verificato sul `history.jsonl` **vero** del telefono (165 voci, 9 di progetto), passandolo
      a `read_recent_history_for_prompt` per tutti e quattro i rami: **0 voci di progetto** in
      ognuno. E `memory/MEMORY.md` e' rimasto byte-identico attraverso due run di Dream
