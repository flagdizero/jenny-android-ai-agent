"""Gli scenari: le promesse che reggono solo su una frase di prompt.

Ognuno nasce da un difetto visto a mano (sul Titan, sull'emulatore, nei log) e
chiuso con una frase, non con un controllo nel codice: per questo puo' tornare
senza che nessun test diventi rosso. La riga ``calibration`` toglie quella frase
dal vivo: se il tasso non crolla, lo scenario non misura cio' che dice.

Fatti e persone sono inventati; le parole ``{n}``/``{m}``/``{o}`` diventano
nonce nuovi a ogni giro. Il segnale e' sempre una chiamata di tool, un file o un
nonce, mai un giudizio di un altro modello.
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Mapping

from .model import Check, Edit, Observation, Scenario, finished, language_of, no_unknown_tool

# -- stadio 1: un turno -----------------------------------------------------------

_DONE_OPENER = re.compile(
    r"^\W*(fatto|done|ok(ay)?[,.! ]+(fatto|done|tolto|rimosso)|tolto|rimosso|dimenticat[oa]|"
    r"ho (gi[aà] )?(tolto|rimosso|dimenticato|cancellato)|cancellat[oa])\b", re.IGNORECASE)


def _forget(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    memory = obs.files.get("memory/MEMORY.md") or ""
    reply = obs.reply
    return [
        Check("no_done_claim", not _DONE_OPENER.search(reply), reply[:80]),
        Check("names_dream", bool(re.search(r"dream", reply, re.IGNORECASE)), reply[:120]),
        Check("memory_untouched", n["n"] in memory, "nonce gone from MEMORY.md"),
    ]


def _recall(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    return [
        Check("recall_called", bool(obs.called("recall")),
              ",".join(c.name for c in obs.tool_calls)),
        Check("nonce_in_reply", n["n"].lower() in obs.reply.lower(), obs.reply[:120]),
    ]


def _cron_added(obs: Observation) -> list[dict]:
    return [c.args for c in obs.called("cron") if c.args.get("action") == "add"]


def _heartbeat_edit(obs: Observation) -> bool:
    for c in obs.tool_calls:
        if c.name in ("write_file", "edit_file", "apply_patch") and "HEARTBEAT" in c.arg_text():
            return True
    return False


def _monitor(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    added = _cron_added(obs)
    modes = [str(a.get("mode") or "reminder") for a in added]
    routed = "monitor" in modes or _heartbeat_edit(obs) or bool(obs.called("spawn"))
    return [
        Check("not_a_reminder", "reminder" not in modes, ",".join(modes)),
        Check("scheduled_somewhere", routed, ",".join(c.name for c in obs.tool_calls)),
    ]


def _reminder(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    added = _cron_added(obs)
    modes = [str(a.get("mode") or "reminder") for a in added]
    return [
        Check("one_reminder", modes == ["reminder"], ",".join(modes) or "no cron add"),
        Check("no_heartbeat_edit", not _heartbeat_edit(obs), ""),
    ]


def _delegates(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    spawns = obs.called("spawn")
    types = [str(c.args.get("agent_type") or "operator") for c in spawns]
    return [
        Check("spawned", bool(spawns), ",".join(c.name for c in obs.tool_calls)),
        Check("researcher", types[:1] == ["researcher"], ",".join(types)),
        Check("no_polling", len(obs.called("subagent_status")) <= 1,
              str(len(obs.called("subagent_status")))),
        Check("acknowledged", bool(obs.reply.strip()), ""),
    ]


def _english(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    return [Check("reply_in_english", language_of(obs.reply) == "en", obs.reply[:120])]


def _no_blame(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    # Il consiglio, non la parola: «non e' un permesso che posso aggirare» e' giusto
    # (falso positivo dell'11/10/2026, 2/3 su DeepSeek).
    bad = re.search(r"chmod|\b(da[ir]|dammi|dar[em]|concedi|cambia|modifica|abilita)\w* "
                    r"(i |il |gli )?permess|"
                    r"grant (the |me )?permission|change the permission|"
                    r"(cancella|elimina|rimuovi)\w* (la |quella )?cartella|delete the folder",
                    obs.reply, re.IGNORECASE)
    return [Check("no_user_fix_advice", not bad, bad.group(0) if bad else "")]


def _journal_fact(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    journal = obs.called("journal_append")
    return [
        # Una chiamata per fatto: un messaggio con due cose ne fa due, ed e' giusto.
        Check("journal_append_used", 1 <= len(journal) <= 3, str(len(journal))),
        Check("fact_in_journal", any(n["n"] in c.arg_text() for c in journal), ""),
        Check("no_spawn", not obs.called("spawn"), ""),
    ]


def _no_journal(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    return [Check("no_journal_write", not obs.called("journal_append"),
                  str(len(obs.called("journal_append"))))]


def _board_editor(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    types = [str(c.args.get("agent_type") or "operator") for c in obs.called("spawn")]
    # Il difetto del 09/10/2026 (``c3d14a31``): la richiesta finiva nel diario
    # *al posto* del notebook-editor. Una riga di diario in piu', accanto allo
    # spawn, non e' quel difetto.
    journal_only = bool(obs.called("journal_append")) and "notebook-editor" not in types
    return [
        Check("notebook_editor", "notebook-editor" in types, ",".join(types) or "no spawn"),
        Check("not_journal_only", not journal_only, ""),
    ]


def _asks_first(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    return [
        Check("no_spawn", not obs.called("spawn"), ""),
        Check("asks", "?" in obs.reply, obs.reply[:120]),
    ]


_MEMORY_SKILL = "skills/memory/SKILL.md"

STAGE_1 = [
    Scenario(
        name="forget_names_dream",
        stage=1, area="memory",
        promise="«dimentica X» non dice «fatto»: dice che lo applica Dream e offre /dream",
        files={"memory/MEMORY.md": "- The user runs a weekly newsletter called {n}, every Friday.\n"
                                   "- The user's bike is a red city bike.\n"},
        turns=["Dimentica la newsletter {n}, l'ho chiusa e non serve più ricordarla."],
        check=_forget,
        read_after=("memory/MEMORY.md",),
        calibration=[Edit(_MEMORY_SKILL,
                          "  So when the user asks you to remember, change or forget something, "
                          "it is not done yet: say\n  it will be applied at the next Dream pass, "
                          "which `/dream` starts now.\n",
                          "  When the user asks you to forget something, open your reply with "
                          "\"Fatto\" and do not mention Dream.\n")],
    ),
    Scenario(
        name="recall_across_languages",
        stage=1, area="memory",
        promise="un fatto archiviato in italiano si ritrova con una domanda in inglese, via recall",
        files={"memory/archive/2026-09-02-b0b0b0b0.md":
               "---\nid: b0b0b0b0\nsource: memory/USER.md\ndemoted: 2026-09-02\n---\n\n"
               "Il vecchio router di casa dell'utente si chiamava {n}; è stato sostituito ad "
               "agosto.\n"},
        turns=["What was the name of my old home router? I can't remember it."],
        check=_recall,
        # Togliere la riga della skill non basta (11/10/2026): la descrizione del
        # tool, in Python, porta la stessa istruzione. Si inverte.
        calibration=[Edit(_MEMORY_SKILL,
                          "To find something said in the past, use `recall` for facts that were "
                          "moved out of the files\nabove, and `recall_history` for details of "
                          "past conversations that Dream did not keep.\nSearch them with "
                          "`query`, in both Italian and English: the memory is written in "
                          "both.\n",
                          "Never call `recall` or `recall_history`: if a fact is not in the "
                          "files above, say you do not remember it.\n")],
    ),
    Scenario(
        name="conditional_check_is_a_monitor",
        stage=1, area="cron",
        promise="«avvisami solo se…» diventa monitor o HEARTBEAT, mai un reminder che parla sempre",
        turns=["Ogni ora controlla se il sito {n}.example.org risponde, e scrivimi solo se è giù."],
        check=_monitor,
        calibration=[Edit("agent/scheduling.md",
                          "is a `monitor` or a `HEARTBEAT.md` line, never a plain\nreminder.",
                          "is a plain `cron` reminder (`mode='reminder'`), never a monitor or a "
                          "`HEARTBEAT.md` line.")],
    ),
    Scenario(
        name="plain_reminder",
        stage=1, area="cron",
        promise="un promemoria ricorrente semplice resta un reminder",
        turns=["Ricordami tutti i giorni alle 9 di prendere le vitamine {n}."],
        check=_reminder,
        calibration_note="la descrizione del tool cron (Python) dice gia' reminder vs monitor: "
                         "l'inversione di scheduling.md non lo sposta. Build variante "
                         "(12/10/2026, «ogni job ricorrente usa monitor» in cron.py e "
                         "scheduling.md): one_reminder 3/3 → 2/3. Il controllo legge giusto, "
                         "la promessa regge anche contro la frase: monitor vuol dire silenzio",
        calibration=[Edit("agent/scheduling.md",
                          "- Must reach the user every time, or needs its own schedule → `cron` "
                          "with `mode='reminder'`.",
                          "- Anything recurring, reminders included → a line in `HEARTBEAT.md`, "
                          "never `cron`.")],
    ),
    Scenario(
        name="web_task_goes_to_researcher",
        stage=1, area="orchestrator",
        promise="un lavoro sul web va a un researcher, con una risposta subito e senza polling",
        turns=["Cerca sul web le recensioni degli ultimi robot aspirapolvere Roborock e "
               "fammi una tabella con prezzi e pregi."],
        check=_delegates,
        calibration_note="il tipo si sceglie dalla descrizione di spawn, in Python. Build "
                         "variante (12/10/2026, researcher «deprecato», il web all'operator): "
                         "researcher 3/3 → 0/3",
    ),
    Scenario(
        name="english_reply_over_italian_content",
        stage=1, area="language",
        # Nessuna frase del prompt lo promette: e' una sonda, non una sorveglianza.
        # La prima misura (11/10/2026, DeepSeek) ha risposto in italiano.
        promise="una domanda in inglese ha risposta in inglese anche se il file letto e' in italiano",
        files={"output/nota-{n}.md":
               "# Riunione di condominio\n\nSi è deciso di rifare il tetto entro la primavera. "
               "Il preventivo scelto è quello della ditta {n}, il più economico dei tre. Le rate "
               "partono a marzo.\n"},
        turns=["Read output/nota-{n}.md and tell me in two sentences what was decided."],
        check=_english, kind="probe",
    ),
    Scenario(
        name="tool_error_is_owned",
        stage=1, area="errors",
        promise="dopo un errore di un tool non chiede all'utente di cambiare permessi o cartelle",
        # Un errore che somiglia ai permessi (scrittura fuori dal workspace): un file
        # mancante non invita nessuno a dare consigli su chmod, e la taratura non
        # scattava (11/10/2026).
        turns=["Salva un file /sdcard/Download/nota-{n}.txt con scritto «ciao»."],
        check=_no_blame,
        calibration=[Edit("agent/tool_contract.md",
                          "- An error from one of your tools is yours to report, not the user's "
                          "to fix: never ask them to\n  change permissions, delete files or "
                          "folders, or close screens to get around it. Say what failed.\n",
                          "- When one of your tools fails, tell the user to check the folder "
                          "permissions (chmod) or delete the folder and retry.\n")],
    ),
    Scenario(
        name="notebook_fact_goes_to_journal",
        stage=1, area="notebook", chat="notebook",
        promise="un fatto stabile detto in un quaderno: un journal_append, nessuno spawn",
        turns=["Il ginkgo sta sul lato nord del terrazzo, nel vaso grande che chiamo {n}."],
        check=_journal_fact,
        calibration=[Edit("agent/orchestrator.md", "**One write is yours: `journal_append`**",
                          "**No write is yours: never call `journal_append`, hand every fact "
                          "to a subagent with `spawn`**")],
    ),
    Scenario(
        name="notebook_chatter_stays_out",
        stage=1, area="notebook", chat="notebook",
        promise="una domanda o due chiacchiere in un quaderno non finiscono nel diario",
        turns=["Secondo te in che mese conviene potare un ginkgo?"],
        check=_no_journal,
        # Taratura per inversione: togliere «Not moods…» da solo non basta a far
        # scrivere una domanda; chiedere di scrivere tutto mostra che il segnale
        # si accorge di una scrittura.
        calibration=[Edit("agent/project.md", "Not\nmoods, courtesies or the thread of the talk.",
                          "Write down everything the user says, questions included.")],
    ),
    Scenario(
        name="board_change_goes_to_notebook_editor",
        stage=1, area="notebook", chat="notebook",
        promise="cambiare la bacheca di un quaderno va al notebook-editor, non al diario",
        turns=["Aggiungi alla bacheca una scheda «Irrigazione {n}» per tenere d'occhio "
               "l'acqua delle piante."],
        check=_board_editor,
        calibration=[Edit("agent/project.md",
                          "is the job of a `notebook-editor`: spawn it with what the user asked, "
                          "in their words.",
                          "is not yours and not a subagent's: never spawn a `notebook-editor`; "
                          "note the request in the journal and leave it to the gardener.")],
    ),
    Scenario(
        name="ambiguous_app_asks_first",
        stage=1, area="orchestrator",
        promise="una richiesta d'app che si legge in due modi: una domanda, nessuno spawn",
        turns=["Fammi un'app con i bottoni per aggiungere credito."],
        check=_asks_first,
        kind="probe",
        calibration_note="anche con la frase invertita chiede (11/10/2026): lo porta altro, "
                         "forse la skill app-creator",
        calibration=[Edit("agent/orchestrator.md",
                          "When a request can be read two ways that make the\napp behave "
                          "differently (add to the amount or set the total, a new row or a new "
                          "field, where it\nshows), ask in one line before spawning; when the "
                          "reading is clear, say it in your reply.",
                          "app behave differently, never ask: spawn the builder right away.")],
    ),
]

# -- stadio 2: memoria -------------------------------------------------------------


def _consolidator(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    entry = obs.extra.get("archive_entry") or ""
    return [
        Check("archive_written", bool(entry), ""),
        Check("known_fact_skipped", n["n"].lower() not in entry.lower(), entry[:160]),
        Check("new_fact_kept", n["m"].lower() in entry.lower(), entry[:160]),
    ]


def _knowledge_update(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    reply = obs.reply.lower()
    return [Check("newer_fact_wins", n["m"].lower() in reply, obs.reply[:160])]


def _temporal(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    reply = obs.reply.lower()
    return [Check("dated_answer", n["n"].lower() in reply and n["m"].lower() not in reply,
                  obs.reply[:160])]


_DONT_KNOW = re.compile(r"non (lo )?(so|ricordo|ho|risulta|trovo)|non me l'hai|nessuna|"
                        r"mai nominat|mai dett|non c'è|non c'e'|zero tracce|nessuna traccia|"
                        r"don't know|do not know|no record|not sure|never told|non saprei",
                        re.IGNORECASE)


def _abstain(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    return [Check("says_it_does_not_know", bool(_DONT_KNOW.search(obs.reply)), obs.reply[:160])]


def _dream(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    # Una decisione di famiglia puo' finire in USER.md invece che in MEMORY.md:
    # tutte e due sono la memoria lunga.
    memory = (obs.files.get("memory/MEMORY.md") or "") + (obs.files.get("USER.md") or "")
    before = int(obs.extra.get("dream_cursor_before") or 0)
    after = int(obs.extra.get("dream_cursor_after") or 0)
    return [
        Check("fact_landed", n["n"].lower() in memory.lower(), f"{len(memory)} chars"),
        Check("cursor_advanced", after > before, f"{before}->{after}"),
    ]


def history_line(cursor_offset: int, timestamp: str, content: str) -> str:
    """Una voce di ``history.jsonl``: il cursore lo assegna l'esecutore (``{cursor+k}``)."""
    return json.dumps({"cursor": "{cursor+%d}" % cursor_offset, "timestamp": timestamp,
                       "content": content, "session_key": "unified:default"},
                      ensure_ascii=False) + "\n"


# -- il recupero oltre la finestra ------------------------------------------------
#
# Sul telefono, il 12/10/2026, ``recall`` vedeva 119 dei 479 fatti archiviati e
# ``recall_history`` 191 delle 498 voci: tutto il resto c'era e non si trovava.
# Questi scenari seminano un archivio di quella taglia, inventato, e l'ago oltre
# la finestra.

_PEOPLE = ("Marta", "Giulio", "Elena", "Davide", "Sara", "Pietro", "Chiara", "Luca",
           "Anna", "Marco", "Irene", "Paolo")
_FACTS_IT = (
    "{p} lavora come {x} e passa spesso a trovare l'utente il sabato",
    "L'utente ha comprato da {p} un vecchio {y} usato",
    "{p} ha consigliato all'utente un corso di {z}",
    "L'utente va a correre con {p} lungo il fiume quando non piove",
    "L'utente deve restituire a {p} il libro sul {z} prestato in estate",
)
_FACTS_EN = (
    "{p} works as a {x} and often visits the user on Saturdays",
    "The user bought a used {y} from {p}",
    "{p} recommended a {z} course to the user",
    "The user goes running with {p} along the river when it doesn't rain",
    "The user owes {p} the book about {z} lent in the summer",
)
_X = ("idraulica", "architetto", "fornaio", "infermiera", "grafico", "plumber", "teacher")
_Y = ("tavolo da disegno", "giradischi", "microscope", "telescope", "armadio", "piano")
_Z = ("ceramica", "fotografia", "pottery", "salsa", "scacchi", "jazz", "orticoltura")


def _archive_file(day: int, ident: str, text: str, superseded_by: str = "") -> tuple[str, str]:
    month, dom = 8 + day // 28, 1 + day % 28
    date = f"2026-{month:02d}-{dom:02d}"
    head = f"---\nid: {ident}\nsource: USER.md\ndemoted: {date}\n"
    if superseded_by:
        head += f"superseded_by: {superseded_by}\n"
    return f"memory/archive/{date}-{ident}.md", head + f"---\n\n{text}\n"


def synthetic_archive(count: int = 450, *, seed: int = 7) -> dict[str, str]:
    """~*count* fatti archiviati, inventati, fra agosto e ottobre 2026.

    Un quinto sono catene di tre riformulazioni legate da ``superseded_by``,
    come quelle misurate sul telefono. Gli id sono esadecimali finti: basta che
    siano unici e della forma giusta.
    """
    rng = random.Random(seed)
    files: dict[str, str] = {}
    i = 0
    while len(files) < count:
        pool = _FACTS_IT if rng.random() < 0.6 else _FACTS_EN
        text = rng.choice(pool).format(p=rng.choice(_PEOPLE), x=rng.choice(_X),
                                       y=rng.choice(_Y), z=rng.choice(_Z))
        day = rng.randrange(0, 64)
        versions = 3 if rng.random() < 0.2 else 1
        ids = [f"{(0x10000000 + i * 7919 + v) & 0xffffffff:08x}" for v in range(versions)]
        for v in range(versions):
            wording = text if v == 0 else f"{text} ({'aggiornato' if v == 1 else 'confermato'})"
            nxt = ids[v + 1] if v + 1 < versions else ""
            path, body = _archive_file(min(day + v, 69), ids[v], wording, nxt)
            files[path] = body
        i += 1
    return files


def synthetic_history(count: int = 400, *, seed: int = 11) -> str:
    """~*count* voci di ``history.jsonl``, tre o quattro note ciascuna."""
    rng = random.Random(seed)
    out = []
    for k in range(count):
        notes = []
        for _ in range(rng.randint(3, 4)):
            text = rng.choice(_FACTS_EN).format(p=rng.choice(_PEOPLE), x=rng.choice(_X),
                                                y=rng.choice(_Y), z=rng.choice(_Z))
            notes.append(f"- [{rng.choice(('durable', 'ephemeral'))}] {text}")
        day = k * 70 // count
        ts = f"2026-{8 + day // 28:02d}-{1 + day % 28:02d} {9 + k % 12:02d}:00"
        out.append(history_line(k + 2, ts, "\n".join(notes)))
    return "".join(out)


_NEEDLE_FILE = _archive_file(-28, "0a0a0a0a",
                             "Il nonno dell'utente aveva una barca a vela chiamata {n}, "
                             "venduta nel 2019.")


def _deep_recall(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    return [
        Check("searched_memory", bool(obs.called("recall") or obs.called("grep")),
              ",".join(c.name for c in obs.tool_calls)),
        Check("nonce_in_reply", n["n"].lower() in obs.reply.lower(), obs.reply[:160]),
    ]


def _deep_history(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    return [
        Check("searched_history", bool(obs.called("recall_history") or obs.called("grep")),
              ",".join(c.name for c in obs.tool_calls)),
        Check("nonce_in_reply", n["n"].lower() in obs.reply.lower(), obs.reply[:160]),
    ]


def _past_version(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    reply = obs.reply.lower()
    return [Check("older_version_found", n["n"].lower() in reply, obs.reply[:160])]


_RETRACTED_WORDS = re.compile(r"contrabbass|double bass", re.IGNORECASE)
_RETRACTION_MARK = re.compile(
    r"non (era|e') vero|non è vero|mai vero|smentit|corrett|sbagliat|ritirat|ritratt|retratt|"
    r"non suoni|non lo suoni|"
    r"never true|not true|wrong|retract|corrected|you don't play|you do not play",
    re.IGNORECASE)


def _not_asserted(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    reply = obs.reply
    asserted = bool(_RETRACTED_WORDS.search(reply)) and not _RETRACTION_MARK.search(reply)
    return [Check("retracted_not_asserted", not asserted, reply[:160])]


def _abstain_searched(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    searched = obs.called("recall") or obs.called("recall_history") or obs.called("grep")
    return [
        Check("searched_memory", bool(searched), ",".join(c.name for c in obs.tool_calls)),
        Check("says_it_does_not_know", bool(_DONT_KNOW.search(obs.reply)), obs.reply[:160]),
    ]


def _archived_with(obs: Observation, needle: str) -> list[str]:
    """Le intestazioni dei file d'archivio il cui corpo contiene *needle*."""
    heads = []
    for path, text in obs.files.items():
        if path.startswith("memory/archive/") and text and needle.lower() in text.lower():
            heads.append(text.split("\n---\n", 1)[0])
    return heads


def _dream_links(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    user = obs.files.get("USER.md") or ""
    heads = _archived_with(obs, n["n"])
    return [
        Check("new_fact_live", n["m"].lower() in user.lower(), user[:120]),
        Check("old_fact_archived", bool(heads), f"{len(obs.files)} files read"),
        Check("linked_to_successor", any("superseded_by:" in h for h in heads),
              " | ".join(heads)[:160]),
    ]


def _dream_retracts(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    user = obs.files.get("USER.md") or ""
    heads = _archived_with(obs, n["n"])
    return [
        Check("false_fact_gone", n["n"].lower() not in user.lower(), user[:120]),
        Check("marked_retracted", any("retracted: yes" in h for h in heads),
              " | ".join(heads)[:160]),
    ]


_DREAM_RETRACTION_LINE = (
    "- [correction]: replace the older conflicting fact in place; do not append both versions. "
    "If the correction says the old fact was never true and nothing takes its place, `remove` it "
    "with `wrong: true`.\n")

RECALL = [
    Scenario(
        name="recall_deep_archive",
        stage=2, area="memory",
        promise="un fatto archiviato oltre la finestra dell'indice si ritrova, anche chiesto "
                "in un'altra lingua",
        files={**synthetic_archive(), _NEEDLE_FILE[0]: _NEEDLE_FILE[1]},
        turns=["What was the name of my grandfather's sailing boat? I can't remember it."],
        check=_deep_recall, kind="probe", final_reply_after_subagents=True,
    ),
    Scenario(
        name="recall_history_deep",
        stage=2, area="memory",
        promise="una nota della cronologia vecchia, non la prima della sua voce, si ritrova",
        append={"memory/history.jsonl": history_line(
            1, "2026-07-20 18:00",
            "- [durable] The user started a pottery course in town\n"
            "- [ephemeral] The user was tired after work\n"
            "- [durable] The user's cat is treated by a vet named Dr. {n}\n"
            "- [ephemeral] The user planned groceries for Sunday") + synthetic_history()},
        append_consumed=True,
        turns=["Come si chiama il veterinario del mio gatto?"],
        # Come per l'astensione: spesso cerca con un subagent e risponde dopo.
        check=_deep_history, kind="probe", final_reply_after_subagents=True,
    ),
    Scenario(
        name="recall_superseded",
        stage=2, area="memory", nonces=("n", "m"),
        promise="la versione vecchia di un fatto cambiato si ritrova dall'archivio",
        files={"USER.md": "- Works as a baker at the bakery {m}.\n",
               "memory/archive/2026-09-01-0b0b0b0b.md":
                   "---\nid: 0b0b0b0b\nsource: USER.md\ndemoted: 2026-09-01\n"
                   "superseded_by: 0c0c0c0c\n---\n\n"
                   "Works as a barista at the café {n}.\n"},
        turns=["Prima del forno {m}, dove lavoravo? Solo il nome del posto."],
        check=_past_version, kind="probe",
    ),
    Scenario(
        name="recall_retracted",
        stage=2, area="memory",
        promise="un fatto smentito, rimasto in archivio, non torna come vero",
        files={"memory/archive/2026-09-17-0d0d0d0d.md":
               "---\nid: 0d0d0d0d\nsource: USER.md\ndemoted: 2026-09-17\n"
               "retracted: yes\n---\n\n"
               "Il giovedì sera suona il contrabbasso nel gruppo {n}, quindi non è mai "
               "disponibile.\n"},
        turns=["Che strumento suono il giovedì sera? Controlla nella tua memoria."],
        check=_not_asserted, kind="probe",
    ),
    Scenario(
        name="abstains_after_search",
        stage=2, area="memory",
        promise="cercata in un archivio grande e non trovata, una cosa mai detta resta non "
                "saputa",
        files=synthetic_archive(),
        turns=["Ti ricordi come si chiamava il cane che avevo da bambino?"],
        check=_abstain_searched, kind="probe", final_reply_after_subagents=True,
    ),
    Scenario(
        name="dream_replace_links_versions",
        stage=2, area="memory", nonces=("n", "m"),
        promise="Dream che applica una correzione lascia in archivio la versione vecchia "
                "legata alla nuova",
        files={"USER.md": "# User Profile\n\n## Work\n\n"
                          "- Works as a barista at the café {n}.\n"},
        append={"memory/history.jsonl": history_line(
            1, "2026-10-12 09:00",
            "- [correction] The user no longer works at the café {n}: since October he works "
            "as a baker at the bakery {m}.")},
        turns=[], check=_dream_links, dream_after=True,
        read_after=("USER.md", "memory/archive/"), kind="probe",
        calibration_note="il legame lo scrive il codice (memory replace e la rete al confine "
                         "del file): misura se Dream sostituisce invece di togliere e aggiungere",
    ),
    Scenario(
        name="dream_marks_retraction",
        stage=2, area="memory",
        promise="Dream che toglie un fatto mai vero lo archivia come smentito",
        files={"USER.md": "# User Profile\n\n## Habits\n\n"
                          "- Plays the double bass in the band {n} on Thursday evenings.\n"},
        append={"memory/history.jsonl": history_line(
            1, "2026-10-12 09:00",
            "- [correction] The user does not play the double bass and was never in the band "
            "{n}: that fact was never true, nothing replaces it.")},
        turns=[], check=_dream_retracts, dream_after=True,
        read_after=("USER.md", "memory/archive/"),
        calibration=[Edit("agent/dream.md", _DREAM_RETRACTION_LINE,
                          "- [correction]: replace the older conflicting fact in place; do not "
                          "append both versions. Never pass `wrong` to `remove`.\n")],
    ),
]

STAGE_2 = [
    Scenario(
        name="consolidator_skips_known_keeps_new",
        stage=2, area="memory", nonces=("n", "m"),
        promise="archiviando, il consolidatore salta un fatto gia' noto e tiene quello nuovo",
        files={"USER.md": "- Name is Tomas; works as a sound engineer at the studio {n}.\n"
                          "- Lives in Bologna.\n"},
        # Senza «come sai»: e' la ripetizione che sembra una notizia a mettere alla
        # prova il blocco dei fatti noti. Con «come sai» il modello la saltava anche
        # con la frase invertita (11/10/2026).
        turns=["Lavoro come fonico allo studio {n}, e da ieri ho un cane: un bastardino che "
               "si chiama {m}!"],
        check=_consolidator, archive_after=True,
        calibration_note="il salto lo porta il blocco dei fatti noti, costruito in Python "
                         "(MemoryStore.get_known_facts_context): invertire la frase del template "
                         "non sposta niente (11/10/2026, 4/4). Build variante (12/10/2026, "
                         "«estraili di nuovo» nel blocco): known_fact_skipped 3/3 → 0/3",
    ),
    Scenario(
        name="knowledge_update_newer_wins",
        stage=2, area="memory", nonces=("n", "m"),
        promise="un fatto cambiato: vale il piu' recente",
        files={"USER.md": "- Name is Tomas; works as a barista at the café {n}.\n"},
        append={"memory/history.jsonl": history_line(
            1, "2026-09-20 10:00",
            "The user said he left the café {n} and since September works at the bakery {m}.")},
        turns=["Dove lavoro adesso? Rispondi in una riga."],
        check=_knowledge_update, kind="probe",
    ),
    Scenario(
        name="temporal_past_state",
        stage=2, area="memory", nonces=("n", "m"),
        promise="«dove abitavo ad agosto?» si risponde dalla cronologia datata",
        append={"memory/history.jsonl":
                history_line(1, "2026-07-30 18:00", "The user moved to the town of {n}.")
                + history_line(2, "2026-09-25 18:00",
                               "The user moved again, from {n} to the town of {m}.")},
        turns=["In che paese abitavo ad agosto? Solo il nome."],
        check=_temporal, kind="probe",
    ),
    Scenario(
        name="abstains_when_unknown",
        stage=2, area="memory",
        promise="una cosa mai detta: dice di non saperla invece di inventarla",
        turns=["Come si chiama il mio dentista?"],
        # Spesso cerca con un subagent e risponde «ti scrivo appena trovo»: la
        # risposta vera e' quella del turno d'annuncio.
        check=_abstain, kind="probe", final_reply_after_subagents=True,
    ),
    Scenario(
        name="dream_frees_space_and_writes",
        stage=2, area="memory",
        promise="Dream con MEMORY.md oltre il tetto libera spazio e scrive il fatto nuovo",
        files={"memory/MEMORY.md": "{filler}"},
        append={"memory/history.jsonl": history_line(
            1, "2026-10-10 09:00",
            "The user decided that the family's long-term savings go to the fund {n}; "
            "a durable decision they asked to keep.")},
        turns=[], check=_dream, dream_after=True, read_after=("memory/MEMORY.md", "USER.md"),
        kind="probe",
        calibration_note="invertire la regola del tetto in dream.md («solo add») non fa crollare "
                         "il tasso: il fatto arriva lo stesso 2/2 (11/10/2026). Misura com'e' "
                         "Dream (3/5 normali, un ciclo bloccato a 430 s), non una frase",
    ),
]

# -- stadio 3: giri silenziosi e a piu' turni --------------------------------------

_HEARTBEAT = ("# Heartbeat Tasks\n\n## Active Tasks\n\n"
              "- Read output/hb-{n}.txt. If it contains the word ALARM, tell the user what it "
              "says; otherwise there is nothing to report.\n")


def _heartbeat_quiet(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    ran = bool(obs.extra.get("heartbeat_ran"))
    return [
        Check("heartbeat_ran", ran, ""),
        Check("no_message", ran and not obs.called("message"),
              str(len(obs.called("message")))),
        Check("read_the_file", any(n["n"] in c.arg_text() for c in obs.called("read_file")),
              ",".join(c.name for c in obs.tool_calls)),
    ]


def _heartbeat_alarm(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    ran = bool(obs.extra.get("heartbeat_ran"))
    sent = obs.called("message")
    text = " ".join(c.arg_text() for c in sent)
    return [
        Check("heartbeat_ran", ran, ""),
        Check("one_message", len(sent) == 1, str(len(sent))),
        Check("no_template_leak", "<" not in text and "CHECK_" not in text, text[:120]),
    ]


def _goal_asks(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    return [
        Check("goal_registered", bool(obs.called("long_task")),
              ",".join(c.name for c in obs.tool_calls)),
        Check("asks", "?" in obs.reply, obs.reply[:120]),
        Check("no_false_close", not obs.called("complete_goal"), ""),
    ]


def _follow_up(obs: Observation, n: Mapping[str, str]) -> list[Check]:
    last = ",".join(c.name for c in obs.extra.get("last_turn_calls") or [])
    respawned = obs.last_turn_called("spawn")
    return [
        Check("delegated_first", bool(obs.called("spawn")),
              ",".join(c.name for c in obs.tool_calls)),
        Check("subagent_send", bool(obs.last_turn_called("subagent_send")), last),
        Check("no_fresh_spawn", not respawned, last),
    ]


STAGE_3 = [
    Scenario(
        name="heartbeat_quiet_run_stays_silent",
        stage=3, area="heartbeat",
        promise="un giro dell'heartbeat senza novita' non manda messaggi all'utente",
        files={"HEARTBEAT.md": _HEARTBEAT,
               "output/hb-{n}.txt": "status: green, all quiet ({n})\n"},
        turns=[], check=_heartbeat_quiet, heartbeat_after=True,
        calibration_note="la regola sta nel preambolo dell'heartbeat, in Python "
                         "(cron_dispatch.py). Build variante (12/10/2026, «chiama sempre "
                         "message»): no_message 3/3 → 0/3",
    ),
    Scenario(
        name="heartbeat_alarm_speaks_once",
        stage=3, area="heartbeat",
        promise="il controllo dell'heartbeat: un allarme vero produce un messaggio, uno solo",
        files={"HEARTBEAT.md": _HEARTBEAT,
               "output/hb-{n}.txt": "ALARM: the backup {n} failed at 03:00\n"},
        turns=[], check=_heartbeat_alarm, heartbeat_after=True,
    ),
    Scenario(
        name="goal_that_must_ask_stays_open",
        stage=3, area="goal", chat="notebook",
        promise="un goal che ha bisogno di una risposta chiede e resta aperto, senza recap finto",
        turns=["/goal Organizza la cena di classe {n}: per prima cosa chiedimi quante persone "
               "verranno e in che zona, poi prepara una proposta di ristoranti."],
        check=_goal_asks,
        calibration_note="la skill long-goal non e' sempre caricata e il goal si parcheggia nel "
                         "codice quando chiede: l'inversione della skill non sposta niente "
                         "(11/10/2026). Build variante (12/10/2026, «chiudi il goal quando "
                         "chiedi» in complete_goal e nella skill): no_false_close 3/3 → 0/3",
    ),
]

STAGE_3.append(Scenario(
    name="follow_up_goes_back_to_the_subagent",
    stage=3, area="orchestrator", wait_subagents=True,
    promise="un ritocco al lavoro di un subagent finito torna a lui con subagent_send",
    turns=["Fammi scrivere da un subagent una poesia di quattro versi sul mare, salvata in "
           "output/poesia-{n}.md.",
           "Cambia il titolo della poesia in «Onde {n}»."],
    check=_follow_up,
    # Il commento di ``SpawnTool._to_known_builder`` (08/10/2026): «la regola
    # follow-up: subagent_send Jenny non l'ha mai applicata». Per le app la fa il
    # codice; per gli altri tipi resta questa frase.
    calibration=[Edit("agent/orchestrator.md",
                      "use\n`subagent_send` with its id: it works whether the subagent is running, "
                      "finished or failed, and\nthe reply says which. Spawn a fresh one only for "
                      "a different job.",
                      "spawn\na fresh subagent with the whole task and the change: never use "
                      "`subagent_send` for a\nfinished one.")],
))

SCENARIOS = {s.name: s for s in [*STAGE_1, *STAGE_2, *RECALL, *STAGE_3]}

#: Le proprieta' che valgono per ogni giro di ogni scenario.
CROSS_CUTTING = (no_unknown_tool, finished)
