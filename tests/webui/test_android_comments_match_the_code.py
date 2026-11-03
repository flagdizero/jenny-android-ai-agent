"""KDoc e commenti del lato Android che descrivevano un codice che non c'è più.

Qui si leggono **i commenti**, apposta: sono
loro il difetto. Ogni frase tolta è fissata dall'assenza, e dove si può si
lega il commento al codice che descrive, così una prossima deriva si vede.

- ``FloatingOverlayController``: il KDoc di classe descriveva «una finestra,
  due taglie» (sono tre finestre, e cambia taglia solo la maniglia, al
  ``DOWN``); ``buildGrip`` diceva che lei vive nella maniglia (vive nella sua
  finestra); ``startFlight`` che la finestra si muove a taglia di sprite e che
  il personaggio riempie il 45% del canvas (il volo si disegna nel palco, e il
  personaggio ne riempie circa il 73%); ``openArena`` che la mascotte diventa
  un margine dentro la finestra; ``parkX`` e ``STAGE_TOUCHABLE`` parlavano del
  «solo fumetto», che non esiste più; il ramo ``ACTION_CANCEL`` attribuiva a
  ``collapse`` la chiusura dell'arena, che fa ``restGrip``.
- ``MainActivity.saveToDownloads``: «Titan 2, Android 11» e «thread binder del
  bridge» (gira su ``nativeExecutor`` e risponde con una Promise).
- ``JennyApplication``: citava un ``configChanges`` di tre voci.
- Manifest: «le sue tre stringhe» di ``activity_main.xml``.
"""

from __future__ import annotations

import re

from support.kotlin_source import ANDROID_MAIN, function_body, read_code, read_comments

ANDROID = ANDROID_MAIN


def test_the_floating_controller_describes_three_windows() -> None:
    comments = read_comments("FloatingOverlayController")
    for stale in (
        "Una finestra, due taglie",
        "e in cui lei vive",
        "45% del canvas",
        "diventa un margine",
        "il solo fumetto)",
        "solo fumetto di risposta — rientra",
        "Si richiude adesso",
        "La finestra resta della taglia dello sprite e si muove",
    ):
        assert stale not in comments, f"commento stantio tornato: {stale!r}"
    assert "Tre finestre" in comments
    # Il KDoc dice tre finestre, montate in quest'ordine: il codice le monta così.
    attach = function_body(read_code("FloatingOverlayController"), "attach")
    assert attach.count("wm.addView(") == 3


def test_the_flight_is_drawn_in_the_stage_as_the_kdoc_says() -> None:
    code = read_code("FloatingOverlayController")
    flight = function_body(code, "drawFlight")
    assert "art.translationX = left" in flight, "l'arte del volo si trasla nel palco"
    assert "flightArt = art" in function_body(code, "buildViews")


def test_save_to_downloads_names_its_thread() -> None:
    comments = read_comments("MainActivity")
    assert "Titan 2, Android 11" not in comments
    assert "thread binder" not in comments


def test_jenny_application_does_not_quote_a_stale_config_changes() -> None:
    comments = read_comments("JennyApplication")
    assert 'configChanges="orientation|screenSize|keyboardHidden"' not in comments


def test_the_manifest_counts_the_layout_strings_right() -> None:
    layout = (ANDROID / "res/layout/activity_main.xml").read_text(encoding="utf-8")
    count = len(re.findall(r'android:text="', layout))
    words = {3: "tre", 4: "quattro", 5: "cinque", 6: "sei", 7: "sette", 8: "otto"}
    manifest = (ANDROID / "AndroidManifest.xml").read_text(encoding="utf-8")
    assert f"scrive le sue {words[count]}\n" in manifest, (
        f"il layout ha {count} stringhe e il commento del manifest ne conta un altro numero"
    )
