"""L'umore della mascotte, letto dagli emoji della risposta.

Due parti, tutte e due pure e senza rete: quale riga si legge (``last_reply``)
e che faccia ne esce (``mood_from_reply``). Le regole sono quelle di
``.agent/mascot-mood-emoji-plan.md`` (D2–D5). Gli esempi sono inventati: il
repository e' pubblico.
"""

from __future__ import annotations

import unicodedata

import pytest

from jenny.cron.session_turns import CRON_HISTORY_META
from jenny.session import mascot_mood as mm
from jenny.session.history_meta import INJECTED_EVENT_META, SUBAGENT_RESULT_EVENT
from jenny.session.manager import Session

REPLY = "Fatto: ho spostato la riunione alle 16 e avvisato tutti 😊"


def _session(*rows: tuple) -> Session:
    session = Session(key="unified:default")
    for role, content, *meta in rows:
        session.add_message(role, content, **(meta[0] if meta else {}))
    return session


def _mood(text: str) -> str:
    return mm.mood_from_reply(text).mood


# --- last_reply ----------------------------------------------------------------------


def test_the_last_reply_not_the_first():
    session = _session(
        ("user", "prima domanda"),
        ("assistant", "prima risposta 😢"),
        ("user", "seconda domanda"),
        ("assistant", REPLY),
    )
    assert mm.last_reply(session) == REPLY


def test_none_when_the_last_row_is_the_user():
    """Dopo un errore l'ultima riga e' dell'utente: l'errore ha gia' la sua faccia."""
    session = _session(("user", "domanda"), ("assistant", REPLY), ("user", "riprova"))
    assert mm.last_reply(session) is None


def test_a_short_reply_still_counts():
    """Il vecchio minimo di 40 caratteri serviva al modello: «ok 😊» e' un umore."""
    session = _session(("user", "tutto bene?"), ("assistant", "ok 😊"))
    assert mm.last_reply(session) == "ok 😊"
    assert _mood("ok 😊") == "happy"


def test_no_user_line_is_needed_any_more():
    """La domanda serviva a chi giudicava; il dizionario guarda solo lei."""
    assert mm.last_reply(Session(key="k")) is None
    assert mm.last_reply(_session(("assistant", REPLY))) == REPLY


def test_command_and_synthetic_rows_are_skipped():
    """Un ``/model`` o un rientro di subagent in coda non sono la sua risposta."""
    session = _session(
        ("user", "com'e' andato il backup?"),
        ("assistant", REPLY),
        ("user", "/model deep", {"_command": True}),
        ("assistant", "Switched model preset to `deep`.", {"_command": True}),
        (
            "user",
            "Scheduled cron job triggered: 30s-test\n\nInternal reminder prompt",
            {CRON_HISTORY_META: True},
        ),
        ("user", "Subagent result: done", {INJECTED_EVENT_META: SUBAGENT_RESULT_EVENT}),
    )
    assert mm.last_reply(session) == REPLY


def test_tool_rows_blank_content_and_think_blocks_are_skipped():
    session = _session(
        ("user", "domanda"),
        ("assistant", "<think>ci penso 😡</think>" + REPLY),
        ("tool", "risultato di uno strumento"),
        ("assistant", "   "),
    )
    session.messages.append({"role": "assistant", "content": None, "tool_calls": [{}]})
    assert mm.last_reply(session) == REPLY


# --- mood_from_reply: le tre facce e il neutro ------------------------------------


@pytest.mark.parametrize(
    ("text", "mood"),
    [
        ("Ecco la lista 😏", "happy"),
        ("Tutto sistemato, finalmente 😌", "happy"),
        ("Complimenti! 🎉", "happy"),
        ("ci tengo ❤️", "happy"),
        ("Mi dispiace, non ci sono riuscita 😔", "sad"),
        ("il server non risponde 😰", "sad"),
        ("è saltato tutto 💔", "sad"),
        ("di nuovo lo stesso errore 😤", "angry"),
        ("ancora spam 🙄", "angry"),
    ],
)
def test_each_face(text, mood):
    assert _mood(text) == mood


@pytest.mark.parametrize(
    "text",
    [
        "Promemoria impostato per domani alle 9.",
        "Buongiorno ☀️ oggi piove 🌧️",
        "Ho trovato tre ristoranti 🍝📅",
        "Boh 🤔",
        "Ops 😅",
        "",
    ],
)
def test_no_face_without_an_emotion(text):
    """Gli ambigui e gli oggetti restano fuori: meglio nessuna faccia che la sbagliata."""
    verdict = mm.mood_from_reply(text)
    assert verdict == mm.MoodVerdict(mm.NEUTRAL_MOOD)


def test_none_is_neutral():
    assert mm.mood_from_reply(None).mood == mm.NEUTRAL_MOOD


# --- il voto -----------------------------------------------------------------------


def test_the_majority_wins():
    verdict = mm.mood_from_reply("non ci sono riuscita 😞 davvero 😞 però ecco 😊")
    assert verdict == mm.MoodVerdict("sad", decided_by="😞", votes=2)


def test_a_tie_goes_to_the_last_one():
    """Il tono sta in coda: «scusa il ritardo 😔 ma eccolo 🎉» e' contenta."""
    assert _mood("scusa il ritardo 😔 ma eccolo 🎉") == "happy"
    assert _mood("eccolo 🎉 ma è rotto 😔") == "sad"


# --- cosa non e' suo ---------------------------------------------------------------


def test_emoji_inside_code_do_not_count():
    assert _mood('```python\nprint("😭")\n```\nfatto 👍') == "happy"
    assert _mood("usa `echo 😡` e basta") == mm.NEUTRAL_MOOD
    assert _mood("```\n😭 non chiuso") == mm.NEUTRAL_MOOD


def test_quoted_lines_do_not_count():
    """Una citazione e' di qualcun altro, spesso di chi le ha scritto."""
    assert _mood("> uffa 😡\nva bene, lo sistemo io 😊") == "happy"
    assert _mood("> uffa 😡\nva bene") == mm.NEUTRAL_MOOD


# --- normalizzazione ---------------------------------------------------------------


def test_variation_selector_and_skin_tones_are_ignored():
    assert _mood("ok 👍🏽") == "happy"
    assert _mood("ok ❤") == "happy"  # senza U+FE0F
    assert _mood("ok ☹️") == "sad"   # con U+FE0F


def test_zwj_sequences_are_looked_up_whole_then_by_their_first_emoji():
    assert _mood("che fatica 😮‍💨") == "sad"          # intera, nel dizionario
    assert _mood("ti adoro ❤️‍🔥") == "happy"          # non c'e': vale il ❤️
    assert _mood("nebbia 😶‍🌫️") == mm.NEUTRAL_MOOD    # 😶 e' fuori


# --- faccine di testo --------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "mood"),
    [
        ("grazie :)", "happy"),
        ("grazie :-)", "happy"),
        ("grazie :))", "happy"),
        ("fatto :D", "happy"),
        ("ok ;)", "happy"),
        ("peccato :(", "sad"),
        ("peccato :'(", "sad"),
        ("uffa >:(", "angry"),
    ],
)
def test_text_emoticons(text, mood):
    assert _mood(text) == mood


@pytest.mark.parametrize(
    "text",
    [
        "vedi https://example.com/:D",
        "alle 12:30) ci vediamo",
        "Nota:Domani",
        "array[i]:(x)",
    ],
)
def test_text_emoticons_need_clean_edges(text):
    assert _mood(text) == mm.NEUTRAL_MOOD


# --- il dizionario -----------------------------------------------------------------


def test_every_face_in_the_dictionary_is_a_mood_with_a_drawing():
    assert set(mm.MOOD_EMOJI) == set(mm.MOODS) - {mm.NEUTRAL_MOOD}


def test_no_emoji_sits_in_two_faces():
    seen: dict[str, str] = {}
    for mood, emojis in mm.MOOD_EMOJI.items():
        for emoji in emojis:
            key = mm._normalize(emoji)
            assert key not in seen, f"{emoji} e' sia {seen[key]} sia {mood}"
            seen[key] = mood


def test_every_entry_is_an_emoji_and_not_a_letter():
    """Una lettera finita nel dizionario per sbaglio (``"D"``) accenderebbe una
    faccia su ogni parola che la contiene."""
    for emojis in mm.MOOD_EMOJI.values():
        for emoji in emojis:
            for ch in mm._normalize(emoji):
                if ch == "‍":
                    continue
                assert unicodedata.category(ch) == "So", f"{emoji!r} contiene {ch!r}"


def test_the_ambiguous_ones_stay_out():
    """Fuori di proposito (v. il piano, D4): se uno ci entra, e' una decisione."""
    index = {mm._normalize(e) for es in mm.MOOD_EMOJI.values() for e in es}
    for emoji in ("😅", "🙃", "😬", "🤔", "😐", "😑", "😶", "👀", "🤷", "😳", "😱", "🫠", "☀️"):
        assert mm._normalize(emoji) not in index, emoji
