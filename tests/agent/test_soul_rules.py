"""Le regole che l'utente ha dato a Jenny sopravvivono a Dream.

Il problema che questo modulo esiste per risolvere non e' di interfaccia: e'
che ``SOUL.md`` **viene riscritto**. Misurato sugli snapshot del dispositivo di
prova il 19/09/2026 — 58 snapshot in 6,6 giorni, sette versioni distinte del
file, sei riscritture, una ogni 1,1 giorni. E le riscritture sono potature
dentro le sezioni: in tutti e sei i cambi nessuna intestazione e' stata tolta o
aggiunta, mentre le righe dentro si' (+4/-8 nel cambio piu' grosso).

Quindi i banchi qui sotto simulano proprio quello: una passata che pota, che
toglie i marcatori, che toglie il blocco intero. Dopo ognuna, le parole
dell'utente devono tornare dove il prompt le legge — **identiche**, perche' la
verita' sta in un file che il registro di scrittura di Dream non ammette.
"""

from __future__ import annotations

from pathlib import Path

from jenny.agent.soul_rules import (
    HEADING,
    MARK_END,
    MARK_START,
    RULES_FILE,
    extract_rules,
    project,
    read_rules,
    save_rules,
    sync_soul,
    write_rules,
)

SOUL = """# Soul

I am Jenny.

## Who I Am

Girl. 20. Sharp.

## Rules

- Never moralize.
"""

REGOLE = "Chiamami per nome, e niente emoji."


# ── La proiezione ───────────────────────────────────────────────────────────


def test_rules_land_in_the_file_the_prompt_reads() -> None:
    """Il blocco si aggiunge in fondo, coi due marcatori e l'intestazione che
    dice al modello di chi sono quelle righe."""
    nuovo = project(SOUL, REGOLE)
    assert SOUL.rstrip() in nuovo, "il resto del file e' stato toccato"
    assert MARK_START in nuovo and MARK_END in nuovo
    assert HEADING in nuovo
    assert REGOLE in nuovo
    assert extract_rules(nuovo) == REGOLE


def test_projecting_twice_does_not_write_it_twice() -> None:
    """La proiezione si rifa' dopo **ogni** passata di Dream: se non fosse
    idempotente, una settimana di passate sarebbe una settimana di copie."""
    una = project(SOUL, REGOLE)
    due = project(una, REGOLE)
    assert due == una
    assert due.count(MARK_START) == 1
    assert due.count(HEADING) == 1


def test_a_changed_rule_replaces_the_old_one_in_place() -> None:
    """Il posto si conserva. Riscrivere il blocco in fondo a ogni salvataggio
    lo sposterebbe sotto a quel che Dream ha aggiunto nel frattempo, e dopo un
    mese le regole dell'utente sarebbero in coda a tutto."""
    prima = project(SOUL, REGOLE)
    con_coda = prima + "\n## Something Dream added\n\n- a line\n"
    dopo = project(con_coda, "Dammi del tu.")

    assert "Something Dream added" in dopo, "la coda di Dream e' sparita"
    assert REGOLE not in dopo, "la regola vecchia e' rimasta accanto alla nuova"
    assert extract_rules(dopo) == "Dammi del tu."
    assert dopo.index(HEADING) < dopo.index("Something Dream added")


def test_emptying_the_box_takes_the_block_away() -> None:
    """Chi svuota la casella non si aspetta di ritrovarsi un'intestazione con
    niente sotto."""
    con = project(SOUL, REGOLE)
    senza = project(con, "")
    assert MARK_START not in senza and HEADING not in senza
    assert senza.startswith("# Soul")
    assert "Never moralize" in senza
    assert extract_rules(senza) == ""


def test_no_rules_no_block() -> None:
    assert project(SOUL, "") == SOUL
    assert project(SOUL, "   \n ") == SOUL


# ── Quando la passata pota ──────────────────────────────────────────────────


def test_a_pruned_marker_does_not_duplicate_the_block() -> None:
    """La forma di potatura che le misure mostrano: righe tolte dentro una
    sezione. Se a cadere sono i commenti, il blocco si riconosce
    dall'intestazione — se no la proiezione ne aggiungerebbe un secondo, e il
    modello leggerebbe le stesse regole due volte con parole diverse.
    """
    potato = project(SOUL, REGOLE).replace(MARK_START + "\n", "").replace("\n" + MARK_END, "")
    assert MARK_START not in potato

    rifatto = project(potato, REGOLE)
    assert rifatto.count(HEADING) == 1, "un secondo blocco sotto al primo"
    assert rifatto.count(REGOLE) == 1
    assert MARK_START in rifatto, "i marcatori non sono stati rimessi"


def test_a_block_pruned_in_the_middle_is_rewritten_whole() -> None:
    """Le righe dentro la sezione sono esattamente quel che Dream pota. Quel
    che torna non e' «quasi» la regola: e' la regola."""
    intero = project(SOUL, "Chiamami per nome.\nNiente emoji.\nNon scusarti.")
    potato = intero.replace("Niente emoji.\n", "")
    assert "Niente emoji." not in potato

    rifatto = project(potato, "Chiamami per nome.\nNiente emoji.\nNon scusarti.")
    assert extract_rules(rifatto) == "Chiamami per nome.\nNiente emoji.\nNon scusarti."


def test_an_orphan_marker_is_absorbed_not_stacked() -> None:
    """Meta' potatura: resta il marcatore d'apertura. Senza questo ramo
    resterebbe li' per sempre, e ogni proiezione ne metterebbe uno nuovo
    sotto."""
    orfano = SOUL + "\n" + MARK_START + "\nqualcosa di vecchio\n"
    rifatto = project(orfano, REGOLE)
    assert rifatto.count(MARK_START) == 1
    assert "qualcosa di vecchio" not in rifatto
    assert extract_rules(rifatto) == REGOLE


def test_the_block_stops_at_the_next_section() -> None:
    """Col ripiego sull'intestazione, il blocco arriva fino alla prossima di
    pari livello: quel che Dream ha scritto **dopo** non e' dell'utente e non
    si porta via."""
    senza_marcatori = (
        "# Soul\n\n" + HEADING + "\n\n" + REGOLE + "\n\n## Voice\n\nShort bursts.\n"
    )
    assert extract_rules(senza_marcatori) == REGOLE
    rifatto = project(senza_marcatori, "Dammi del tu.")
    assert "Short bursts." in rifatto
    assert "## Voice" in rifatto
    assert extract_rules(rifatto) == "Dammi del tu."


# ── Su disco ────────────────────────────────────────────────────────────────


def test_the_truth_lives_where_dream_cannot_write(tmp_path: Path) -> None:
    """Il registro di scrittura di Dream ammette esattamente ``SOUL.md``,
    ``USER.md``, ``memory/MEMORY.md`` e ``skills/<nome>/SKILL.md``: il file
    delle regole non e' nessuno di quelli, e non e' un caso."""
    assert RULES_FILE.parts[0] == ".jenny"
    assert RULES_FILE.name not in {"SOUL.md", "USER.md", "MEMORY.md", "SKILL.md"}

    write_rules(tmp_path, REGOLE)
    assert (tmp_path / RULES_FILE).is_file()
    assert read_rules(tmp_path).strip() == REGOLE


def test_rules_that_were_never_written_read_as_nothing(tmp_path: Path) -> None:
    assert read_rules(tmp_path) == ""


def test_emptying_the_rules_removes_the_file(tmp_path: Path) -> None:
    write_rules(tmp_path, REGOLE)
    write_rules(tmp_path, "  ")
    assert not (tmp_path / RULES_FILE).exists()
    assert read_rules(tmp_path) == ""


def test_saving_writes_the_truth_and_the_copy(tmp_path: Path) -> None:
    (tmp_path / "SOUL.md").write_text(SOUL, encoding="utf-8")
    saved = save_rules(tmp_path, "  " + REGOLE + "  ")

    assert saved == REGOLE, "il testo non e' stato normalizzato"
    assert read_rules(tmp_path).strip() == REGOLE
    assert extract_rules((tmp_path / "SOUL.md").read_text(encoding="utf-8")) == REGOLE


def test_a_dream_pass_that_took_the_block_away_gets_it_back(tmp_path: Path) -> None:
    """Il caso che conta, simulato: una passata riscrive ``SOUL.md`` **senza**
    il blocco. Alla prima sincronizzazione le parole dell'utente tornano
    identiche, e il resto di quel che la passata ha scritto resta."""
    (tmp_path / "SOUL.md").write_text(SOUL, encoding="utf-8")
    save_rules(tmp_path, REGOLE)

    (tmp_path / "SOUL.md").write_text(SOUL + "\n## Voice\n\nShort bursts.\n", encoding="utf-8")
    assert extract_rules((tmp_path / "SOUL.md").read_text(encoding="utf-8")) == ""

    assert sync_soul(tmp_path) is True
    rifatto = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert extract_rules(rifatto) == REGOLE
    assert "Short bursts." in rifatto, "la sincronizzazione ha buttato via la passata"


def test_a_sync_with_nothing_to_do_writes_nothing(tmp_path: Path) -> None:
    """Gira dopo ogni passata di Dream, cioe' ogni due ore: riscrivere un file
    identico a se' stesso sarebbe un `fsync` a vuoto — e uno snapshot in piu'
    da tenere."""
    (tmp_path / "SOUL.md").write_text(SOUL, encoding="utf-8")
    save_rules(tmp_path, REGOLE)
    assert sync_soul(tmp_path) is False

    prima = (tmp_path / "SOUL.md").stat().st_mtime_ns
    assert sync_soul(tmp_path) is False
    assert (tmp_path / "SOUL.md").stat().st_mtime_ns == prima


def test_a_missing_soul_is_not_invented(tmp_path: Path) -> None:
    """``SOUL.md`` lo ricrea il bootstrap. Crearlo qui vorrebbe dire scrivere
    un'identita' fatta di sole regole dell'utente."""
    write_rules(tmp_path, REGOLE)
    assert sync_soul(tmp_path) is False
    assert not (tmp_path / "SOUL.md").exists()


def test_removing_the_rules_takes_the_block_out_of_the_file(tmp_path: Path) -> None:
    (tmp_path / "SOUL.md").write_text(SOUL, encoding="utf-8")
    save_rules(tmp_path, REGOLE)
    save_rules(tmp_path, "")
    testo = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert HEADING not in testo and MARK_START not in testo
    assert "Never moralize" in testo


# ── Il gancio ───────────────────────────────────────────────────────────────


def test_every_dream_pass_puts_the_rules_back(tmp_path: Path) -> None:
    """Il gancio sta in ``finish_dream_cycle``, che il chiamante chiama nel
    ``finally``: vale quindi anche per un turno crashato a meta' — cioe' il
    caso in cui ``SOUL.md`` ha piu' probabilita' di essere rimasto monco.

    Il banco chiama la funzione vera, con uno store finto che ha i tre file di
    memoria su disco: e' l'unico modo perche' misuri il cablaggio e non se
    stesso.
    """
    from jenny.agent.dream_cycle import finish_dream_cycle

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "SOUL.md").write_text(SOUL, encoding="utf-8")
    save_rules(workspace, REGOLE)

    class _Store:
        """Quel che ``finish_dream_cycle`` usa, e niente altro."""

        soul_file = workspace / "SOUL.md"

        def __init__(self) -> None:
            self.scritto: dict[str, int] = {}

        def set_review_state(self, **kwargs: int) -> None:
            self.scritto = kwargs

    # La passata ha riscritto il file e si e' portata via il blocco.
    (workspace / "SOUL.md").write_text(SOUL + "\n- Be brief.\n", encoding="utf-8")

    store = _Store()
    finish_dream_cycle(store, advanced=True, runs_since_review=3, stuck=0)

    testo = (workspace / "SOUL.md").read_text(encoding="utf-8")
    assert extract_rules(testo) == REGOLE, "le regole non sono tornate dopo la passata"
    assert "- Be brief." in testo, "la sincronizzazione ha buttato via la passata"
    assert store.scritto["runs_since_review"] == 4, "i contatori non vengono piu' scritti"
