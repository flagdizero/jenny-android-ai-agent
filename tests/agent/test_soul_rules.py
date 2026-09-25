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

RULES = "Chiamami per nome, e niente emoji."


# ── La proiezione ───────────────────────────────────────────────────────────


def test_rules_land_in_the_file_the_prompt_reads() -> None:
    """Il blocco si aggiunge in fondo, coi due marcatori e l'intestazione che
    dice al modello di chi sono quelle righe."""
    fresh = project(SOUL, RULES)
    assert SOUL.rstrip() in fresh, "il resto del file e' stato toccato"
    assert MARK_START in fresh and MARK_END in fresh
    assert HEADING in fresh
    assert RULES in fresh
    assert extract_rules(fresh) == RULES


def test_projecting_twice_does_not_write_it_twice() -> None:
    """La proiezione si rifa' dopo **ogni** passata di Dream: se non fosse
    idempotente, una settimana di passate sarebbe una settimana di copie."""
    one = project(SOUL, RULES)
    due = project(one, RULES)
    assert due == one
    assert due.count(MARK_START) == 1
    assert due.count(HEADING) == 1


def test_a_changed_rule_replaces_the_old_one_in_place() -> None:
    """Il posto si conserva. Riscrivere il blocco in fondo a ogni salvataggio
    lo sposterebbe sotto a quel che Dream ha aggiunto nel frattempo, e dopo un
    mese le regole dell'utente sarebbero in coda a tutto.

    ``previous`` e' quel che ``save_rules`` passa: le regole che si stanno
    sostituendo. Senza, le loro righe sarebbero righe estranee al blocco, e si
    salverebbero fuori come se le avesse scritte Dream."""
    before = project(SOUL, RULES)
    with_tail = before + "\n## Something Dream added\n\n- a line\n"
    after = project(with_tail, "Dammi del tu.", previous=RULES)

    assert "Something Dream added" in after, "la coda di Dream e' sparita"
    assert RULES not in after, "la regola vecchia e' rimasta accanto alla nuova"
    assert extract_rules(after) == "Dammi del tu."
    assert after.index(HEADING) < after.index("Something Dream added")


def test_emptying_the_box_takes_the_block_away() -> None:
    """Chi svuota la casella non si aspetta di ritrovarsi un'intestazione con
    niente sotto."""
    con = project(SOUL, RULES)
    without = project(con, "")
    assert MARK_START not in without and HEADING not in without
    assert without.startswith("# Soul")
    assert "Never moralize" in without
    assert extract_rules(without) == ""


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
    potato = project(SOUL, RULES).replace(MARK_START + "\n", "").replace("\n" + MARK_END, "")
    assert MARK_START not in potato

    redone = project(potato, RULES)
    assert redone.count(HEADING) == 1, "un secondo blocco sotto al primo"
    assert redone.count(RULES) == 1
    assert MARK_START in redone, "i marcatori non sono stati rimessi"


def test_a_block_pruned_in_the_middle_is_rewritten_whole() -> None:
    """Le righe dentro la sezione sono esattamente quel che Dream pota. Quel
    che torna non e' «quasi» la regola: e' la regola."""
    whole = project(SOUL, "Chiamami per nome.\nNiente emoji.\nNon scusarti.")
    potato = whole.replace("Niente emoji.\n", "")
    assert "Niente emoji." not in potato

    redone = project(potato, "Chiamami per nome.\nNiente emoji.\nNon scusarti.")
    assert extract_rules(redone) == "Chiamami per nome.\nNiente emoji.\nNon scusarti."


def test_markers_in_the_wrong_order_are_not_a_block() -> None:
    """Chiusura prima dell'apertura: non e' un blocco, e prenderlo per tale
    vorrebbe dire tagliare il file al contrario — dall'apertura indietro fino
    alla chiusura, cioe' mangiarsi quel che c'era in mezzo. Si ricade
    sull'intestazione, che e' il riconoscimento che regge alle potature."""
    crooked = "# Soul\n\n" + MARK_END + "\n" + HEADING + "\n\n" + RULES + "\n" + MARK_START + "\n"
    redone = project(crooked, "Dammi del tu.")
    assert "# Soul" in redone, "il file e' stato tagliato al contrario"
    assert extract_rules(redone) == "Dammi del tu."
    assert redone.count(HEADING) == 1


def test_an_orphan_marker_is_absorbed_not_stacked() -> None:
    """Meta' potatura: resta il marcatore d'apertura. Senza questo ramo
    resterebbe li' per sempre, e ogni proiezione ne metterebbe uno nuovo
    sotto.

    Quel che il marcatore orfano si trascina dietro fino in fondo al file non
    e' una regola dell'utente, quindi non si butta: resta, ma **fuori** dal
    blocco. Una volta questo banco ne pretendeva la sparizione, ed era il
    difetto: il cursore di Dream e' gia' avanzato, e una riga tolta qui e'
    persa per sempre."""
    orphan = SOUL + "\n" + MARK_START + "\nqualcosa di vecchio\n"
    redone = project(orphan, RULES)
    assert redone.count(MARK_START) == 1
    assert extract_rules(redone) == RULES
    assert "qualcosa di vecchio" in redone.split(MARK_END, 1)[1]


def test_the_block_stops_at_the_next_section() -> None:
    """Col ripiego sull'intestazione, il blocco arriva fino alla prossima di
    pari livello: quel che Dream ha scritto **dopo** non e' dell'utente e non
    si porta via."""
    without_markers = (
        "# Soul\n\n" + HEADING + "\n\n" + RULES + "\n\n## Voice\n\nShort bursts.\n"
    )
    assert extract_rules(without_markers) == RULES
    redone = project(without_markers, "Dammi del tu.")
    assert "Short bursts." in redone
    assert "## Voice" in redone
    assert extract_rules(redone) == "Dammi del tu."


# ── Su disco ────────────────────────────────────────────────────────────────


def test_the_truth_lives_where_dream_cannot_write(tmp_path: Path) -> None:
    """Il registro di scrittura di Dream ammette esattamente ``SOUL.md``,
    ``USER.md``, ``memory/MEMORY.md`` e ``skills/<nome>/SKILL.md``: il file
    delle regole non e' nessuno di quelli, e non e' un caso."""
    assert RULES_FILE.parts[0] == ".jenny"
    assert RULES_FILE.name not in {"SOUL.md", "USER.md", "MEMORY.md", "SKILL.md"}

    write_rules(tmp_path, RULES)
    assert (tmp_path / RULES_FILE).is_file()
    assert read_rules(tmp_path).strip() == RULES


def test_rules_that_were_never_written_read_as_nothing(tmp_path: Path) -> None:
    assert read_rules(tmp_path) == ""


def test_emptying_the_rules_removes_the_file(tmp_path: Path) -> None:
    write_rules(tmp_path, RULES)
    write_rules(tmp_path, "  ")
    assert not (tmp_path / RULES_FILE).exists()
    assert read_rules(tmp_path) == ""


def test_saving_writes_the_truth_and_the_copy(tmp_path: Path) -> None:
    (tmp_path / "SOUL.md").write_text(SOUL, encoding="utf-8")
    saved = save_rules(tmp_path, "  " + RULES + "  ")

    assert saved == RULES, "il testo non e' stato normalizzato"
    assert read_rules(tmp_path).strip() == RULES
    assert extract_rules((tmp_path / "SOUL.md").read_text(encoding="utf-8")) == RULES


def test_a_dream_pass_that_took_the_block_away_gets_it_back(tmp_path: Path) -> None:
    """Il caso che conta, simulato: una passata riscrive ``SOUL.md`` **senza**
    il blocco. Alla prima sincronizzazione le parole dell'utente tornano
    identiche, e il resto di quel che la passata ha scritto resta."""
    (tmp_path / "SOUL.md").write_text(SOUL, encoding="utf-8")
    save_rules(tmp_path, RULES)

    (tmp_path / "SOUL.md").write_text(SOUL + "\n## Voice\n\nShort bursts.\n", encoding="utf-8")
    assert extract_rules((tmp_path / "SOUL.md").read_text(encoding="utf-8")) == ""

    assert sync_soul(tmp_path) is True
    redone = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert extract_rules(redone) == RULES
    assert "Short bursts." in redone, "la sincronizzazione ha buttato via la passata"


def test_a_sync_with_nothing_to_do_writes_nothing(tmp_path: Path) -> None:
    """Gira dopo ogni passata di Dream, cioe' ogni due ore: riscrivere un file
    identico a se' stesso sarebbe un `fsync` a vuoto — e uno snapshot in piu'
    da tenere."""
    (tmp_path / "SOUL.md").write_text(SOUL, encoding="utf-8")
    save_rules(tmp_path, RULES)
    assert sync_soul(tmp_path) is False

    before = (tmp_path / "SOUL.md").stat().st_mtime_ns
    assert sync_soul(tmp_path) is False
    assert (tmp_path / "SOUL.md").stat().st_mtime_ns == before


def test_a_missing_soul_is_not_invented(tmp_path: Path) -> None:
    """``SOUL.md`` lo ricrea il bootstrap. Crearlo qui vorrebbe dire scrivere
    un'identita' fatta di sole regole dell'utente."""
    write_rules(tmp_path, RULES)
    assert sync_soul(tmp_path) is False
    assert not (tmp_path / "SOUL.md").exists()


def test_removing_the_rules_takes_the_block_out_of_the_file(tmp_path: Path) -> None:
    (tmp_path / "SOUL.md").write_text(SOUL, encoding="utf-8")
    save_rules(tmp_path, RULES)
    save_rules(tmp_path, "")
    text = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert HEADING not in text and MARK_START not in text
    assert "Never moralize" in text


# ── Quel che Dream scrive dentro il blocco ──────────────────────────────────


def _capture_warnings() -> tuple[list[str], int]:
    from loguru import logger

    seen: list[str] = []
    sink = logger.add(lambda m: seen.append(str(m)), level="WARNING", format="{message}")
    return seen, sink


def test_a_line_dream_added_inside_the_block_survives_the_sync(tmp_path: Path) -> None:
    """Una passata aggiunge una riga *dentro* il blocco dell'utente. Il suo
    cursore a quel punto e' gia' avanzato: se la proiezione la togliesse, quel
    fatto non tornerebbe mai piu'. Resta, subito dopo il blocco, e un WARNING
    dice quante righe sono state salvate."""
    from loguru import logger

    (tmp_path / "SOUL.md").write_text(SOUL, encoding="utf-8")
    save_rules(tmp_path, RULES)
    soul = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    dreamt = soul.replace(RULES + "\n", RULES + "\n- Dream: she answers in Italian.\n")
    (tmp_path / "SOUL.md").write_text(dreamt, encoding="utf-8")

    seen, sink = _capture_warnings()
    try:
        assert sync_soul(tmp_path) is True
    finally:
        logger.remove(sink)

    text = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert "- Dream: she answers in Italian." in text, "la riga di Dream e' stata buttata"
    assert extract_rules(text) == RULES, "la riga di Dream e' rimasta dentro il blocco"
    assert text.index(MARK_END) < text.index("- Dream: she answers in Italian.")
    assert any("1 line(s)" in m for m in seen), seen
    # Idempotente: la seconda passata non ha piu' niente da spostare.
    assert sync_soul(tmp_path) is False


def test_trailing_text_after_a_markerless_heading_survives(tmp_path: Path) -> None:
    """Senza marcatori il blocco arriva fino alla prossima ``## `` o alla fine
    del file. Un paragrafo che Dream ha messo in coda senza un'intestazione
    nuova sta quindi *dentro* quel ripiego, e non e' dell'utente."""
    (tmp_path / "SOUL.md").write_text(SOUL, encoding="utf-8")
    save_rules(tmp_path, RULES)
    soul = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    pruned = soul.replace(MARK_START + "\n", "").replace("\n" + MARK_END, "")
    pruned += "\nShe keeps her answers short.\n\nShe never apologises twice.\n"
    (tmp_path / "SOUL.md").write_text(pruned, encoding="utf-8")

    assert sync_soul(tmp_path) is True
    text = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert "She keeps her answers short." in text
    assert "She never apologises twice." in text
    assert extract_rules(text) == RULES
    assert text.count(HEADING) == 1
    assert text.index(MARK_END) < text.index("She keeps her answers short.")


def test_a_rule_with_a_heading_line_is_not_duplicated(tmp_path: Path) -> None:
    """Le regole dell'utente possono contenere una riga ``## ``. Nel ripiego
    sull'intestazione quella riga non e' la sezione dopo: se chiudesse il
    blocco, la coda delle regole resterebbe fuori e la proiezione la
    scriverebbe una seconda volta."""
    rules = "Chiamami per nome.\n## Lavoro\nNiente riunioni prima delle 10."
    (tmp_path / "SOUL.md").write_text(SOUL, encoding="utf-8")
    save_rules(tmp_path, rules)
    soul = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    pruned = soul.replace(MARK_START + "\n", "").replace("\n" + MARK_END, "")
    (tmp_path / "SOUL.md").write_text(pruned, encoding="utf-8")

    sync_soul(tmp_path)
    text = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert text.count("## Lavoro") == 1, text
    assert text.count("Niente riunioni prima delle 10.") == 1, text
    assert extract_rules(text) == rules


def test_changing_the_rules_replaces_them_and_keeps_dream_lines(tmp_path: Path) -> None:
    """Il salvataggio vero, su disco: le regole vecchie spariscono (sono quelle
    che si sostituiscono), la riga che Dream aveva messo nel blocco resta."""
    (tmp_path / "SOUL.md").write_text(SOUL, encoding="utf-8")
    save_rules(tmp_path, RULES)
    soul = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    dreamt = soul.replace(RULES + "\n", RULES + "\n- Dream line.\n")
    (tmp_path / "SOUL.md").write_text(dreamt, encoding="utf-8")

    save_rules(tmp_path, "Dammi del tu.")
    text = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert RULES not in text, "la regola vecchia e' stata salvata come se fosse di Dream"
    assert extract_rules(text) == "Dammi del tu."
    assert "- Dream line." in text


def test_emptying_the_rules_keeps_what_dream_wrote_in_the_block(tmp_path: Path) -> None:
    (tmp_path / "SOUL.md").write_text(SOUL, encoding="utf-8")
    save_rules(tmp_path, RULES)
    soul = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    (tmp_path / "SOUL.md").write_text(
        soul.replace(RULES + "\n", RULES + "\n- Dream line.\n"), encoding="utf-8"
    )

    save_rules(tmp_path, "")
    text = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert HEADING not in text and MARK_START not in text and RULES not in text
    assert "- Dream line." in text
    assert "Never moralize" in text


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
    save_rules(workspace, RULES)

    class _Store:
        """Quel che ``finish_dream_cycle`` usa, e niente altro."""

        soul_file = workspace / "SOUL.md"

        def __init__(self) -> None:
            self.written: dict[str, int] = {}

        def set_review_state(self, **kwargs: int) -> None:
            self.written = kwargs

    # La passata ha riscritto il file e si e' portata via il blocco.
    (workspace / "SOUL.md").write_text(SOUL + "\n- Be brief.\n", encoding="utf-8")

    store = _Store()
    finish_dream_cycle(store, advanced=True, runs_since_review=3, stuck=0)

    text = (workspace / "SOUL.md").read_text(encoding="utf-8")
    assert extract_rules(text) == RULES, "le regole non sono tornate dopo la passata"
    assert "- Be brief." in text, "la sincronizzazione ha buttato via la passata"
    assert store.written["runs_since_review"] == 4, "i contatori non vengono piu' scritti"


def test_dreams_prompt_names_the_block_it_must_not_touch() -> None:
    """La proiezione ripara, ma il prompt e' il primo argine: dice a Dream che
    quel blocco lo scrive l'app. Il banco lega le due metà — se i marcatori
    cambiassero nel codice, il prompt nominerebbe un blocco che non esiste."""
    from jenny.utils.helpers import load_bundled_template

    text = load_bundled_template("agent/dream.md") or ""
    assert MARK_START in text and MARK_END in text and HEADING.lstrip("# ") in text
