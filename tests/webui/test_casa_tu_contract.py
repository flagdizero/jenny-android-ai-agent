"""«Tu e Jenny»: il contratto della quarta stanza.

Grep e struttura, non comportamento — quello sta in `test_casa_switch_client.py`,
che le stanze le fa girare davvero. Qui ci sono le cose che si rompono in
silenzio: la porta cablata in `init()`, che nessun banco puo' istanziare, e
l'invariante che tiene insieme tre file — una stanza che il CSS sa accendere ma
da cui `BACK_TO` non sa uscire e' un vicolo cieco, e il tasto Indietro ci cade
dentro senza dire niente.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from jenny.utils.android_assets import _UI_MANIFEST

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "jenny" / "templates" / "ui"
INDEX = UI / "index.html"
ASSETS = UI / "assets"
APP_JS = ASSETS / "casa-app.js"
TU_JS = ASSETS / "casa-tu.js"
CSS = ASSETS / "casa-style.css"
TEMI = ASSETS / "mobile-style.css"
I18N = ASSETS / "i18n"


def _app() -> str:
    return APP_JS.read_text(encoding="utf-8")


# ── La porta ────────────────────────────────────────────────────────────────


def test_the_avatar_opens_you_and_jenny_and_the_workshop_is_a_long_press() -> None:
    """Il bottone in testa era una chiave inglese e portava in officina.

    Adesso e' l'avatar, e apre «Tu e Jenny» — dove l'officina sta in fondo,
    come nella tavola. La scorciatoia per chi in officina ci va dieci volte al
    giorno resta, ed e' quella che la tavola scrive sulla scheda: tenere
    premuto l'avatar.

    Il cablaggio vive in `init()`, che nessun banco puo' far girare senza un
    DOM vero: qui si misura sul sorgente.
    """
    app = _app()
    assert "setupLongPress(this.door, () => this._openInWorkshop(null));" in app, (
        "l'officina non e' piu' sotto il tocco lungo sull'avatar"
    )
    click = re.search(r"this\.door\.addEventListener\('click', \(\) => \{(.*?)\n    \}\);", app, re.S)
    assert click, "l'avatar non ha piu' un gestore del tocco"
    corpo = click.group(1)
    assert "this.openTu()" in corpo, "l'avatar non apre piu' «Tu e Jenny»"
    assert "this.door.dataset.longpress" in corpo and "delete" in corpo, (
        "il flag della pressione lunga non viene consumato: un tocco lungo "
        "aprirebbe l'officina **e** la pagina sotto"
    )
    assert "<i class=\"ti ti-user\">" in INDEX.read_text(encoding="utf-8"), (
        "l'icona non dice piu' dove porta il bottone"
    )


def test_the_workshop_card_is_the_other_way_in() -> None:
    """La stessa porta, dove la tavola la mette: in fondo alla pagina.

    La stanza non sa come si apre l'officina — quello lo sa il guscio, che ha
    la chiave di sessione da passarle. La scheda chiama indietro.
    """
    tu = TU_JS.read_text(encoding="utf-8")
    assert "getElementById('casa-workshop')" in tu and "onWorkshop?.()" in tu, (
        "la scheda dell'officina non chiama piu' indietro"
    )
    assert "onWorkshop: () => this._openInWorkshop(null)," in _app(), (
        "il guscio non passa piu' la porta dell'officina alla stanza"
    )
    html = INDEX.read_text(encoding="utf-8")
    for el_id in ("casa-workshop", "casa-workshop-name", "casa-workshop-hint", "casa-version"):
        assert f'id="{el_id}"' in html, f"{el_id} non esiste nel guscio"


def test_the_rooms_arrive_on_the_phone() -> None:
    """Un file fuori dal manifest non da' 404: `_serve_static` ricade
    sull'officina. Il difetto si vede solo sul telefono, ed e' una stanza che
    non si apre."""
    for asset in ("assets/casa-tu.js", "assets/casa-jenny.js"):
        assert asset in _UI_MANIFEST, (
            f"{asset} non e' nel manifest: sul telefono la stanza non esiste"
        )


def test_the_room_of_her_is_not_the_sprite_of_her() -> None:
    """`.casa-jenny` e' lo sprite che cammina sul bordo, e vive nel guscio da
    prima di questa stanza. Se la stanza avesse preso quel nome, la regola
    della vista avrebbe acceso e spento **lei** invece della pagina — e
    `data-view` avrebbe smesso di parlare solo di stanze."""
    html = INDEX.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    assert '<section class="casa-jenny-room" id="casa-jenny-room">' in html
    assert ".casa-shell[data-view='jenny'] .casa-jenny-room" in css
    assert not re.search(r"\[data-view='jenny'\] \.casa-jenny\b(?!-room)", css), (
        "la regola della stanza morde lo sprite di lei"
    )


def test_the_theme_is_chosen_where_it_is_seen() -> None:
    """Il tema non apre una stanza: si tocca e c'e'. Le pastiglie stanno nella
    pagina, e il tocco le trova per attributo — non per posizione, che cambia
    col numero dei temi."""
    tu = TU_JS.read_text(encoding="utf-8")
    assert "closest('[data-theme]')" in tu, "la striscia non riconosce piu' la pastiglia toccata"
    assert "setTheme(id)" in tu, "il tema non viene piu' applicato"
    html = INDEX.read_text(encoding="utf-8")
    for el_id in ("casa-themes", "casa-theme-label", "casa-theme-value", "casa-theme-desc"):
        assert f'id="{el_id}"' in html, f"{el_id} non esiste nel guscio"


# ── Nessuna stanza senza uscita ─────────────────────────────────────────────


def _rooms_in_css() -> set[str]:
    css = CSS.read_text(encoding="utf-8")
    return set(re.findall(r"\.casa-shell\[data-view='(\w+)'\]", css))


def _rooms_in_back_chain() -> dict[str, str]:
    m = re.search(r"(?ms)^const BACK_TO = \{(.*?)^\};", _app())
    assert m, "BACK_TO non trovata: la catena delle stanze e' sparita"
    return dict(re.findall(r"(\w+): '(\w+)'", m.group(1)))


def test_every_room_the_css_can_light_up_has_a_way_back() -> None:
    """Una stanza in piu' sono tre file: il CSS che la accende, l'HTML che la
    contiene, e la catena che ne esce.

    Dimenticare il terzo non rompe niente all'apertura — si rompe dopo, quando
    il tasto Indietro non trova la stanza nella tabella, ricade sul ramo «esci
    dal quaderno» e cambia conversazione invece di tornare indietro. Silenzioso
    all'occhio di chi scrive, non a quello di chi usa.
    """
    catena = _rooms_in_back_chain()
    accese = _rooms_in_css()
    assert accese, "nessuna regola `data-view` nel foglio: la grep non morde piu'"
    senza_uscita = accese - set(catena) - {"chat"}
    assert not senza_uscita, f"stanze da cui Indietro non sa uscire: {senza_uscita}"
    # E il contrario: una stanza in tabella che il CSS non sa accendere sarebbe
    # un `data-view` senza niente sotto — lo schermo resterebbe quello di prima.
    assert not set(catena) - accese, "la catena nomina stanze che il CSS non accende"


def test_the_back_chain_lands_somewhere_real() -> None:
    """Ogni salto arriva in una stanza che esiste, e nessuna torna in se'
    stessa: una stanza che rimanda a se' e' un tasto Indietro che non fa
    niente, ed e' peggio di un tasto che non c'e'."""
    catena = _rooms_in_back_chain()
    stanze = set(catena) | {"chat"}
    for da, a in catena.items():
        assert a in stanze, f"{da} torna a {a}, che non e' una stanza"
        assert da != a, f"{da} torna in se' stessa"


def test_the_room_is_in_the_shell_from_the_first_frame() -> None:
    """Come le altre: la sezione c'e' nell'HTML e la accende `data-view`, non
    un `hidden` che il JS deve togliere."""
    html = INDEX.read_text(encoding="utf-8")
    assert re.search(r'<section class="casa-tu" id="casa-tu">', html), (
        "la stanza non e' piu' nel guscio"
    )
    assert 'class="casa-tu" id="casa-tu" hidden' not in html, (
        "due meccanismi per la stessa cosa: la vista la accende gia' il CSS"
    )


# ── Le parole ───────────────────────────────────────────────────────────────


def test_the_fourth_room_speaks_both_languages() -> None:
    parole = {}
    for locale in ("it", "en"):
        data = json.loads((I18N / f"{locale}.json").read_text(encoding="utf-8"))
        casa = data["casa"]
        for key in ("title", "open", "workshopHint", "version"):
            assert casa["tu"].get(key, "").strip(), f"casa.tu.{key} manca in {locale}.json"
        assert "{version}" in casa["tu"]["version"], "la riga della versione non interpola niente"
        parole[locale] = casa["tu"]
    assert parole["it"] != parole["en"], "una delle due lingue non e' stata tradotta"


def test_the_eyelet_has_a_phrase_for_every_landing() -> None:
    """L'occhiello nomina la stanza in cui si atterra, e le destinazioni sono
    quelle della catena: una frase che manca lascia a schermo `casa.back.tu`."""
    destinazioni = set(_rooms_in_back_chain().values())
    assert destinazioni, "la catena non porta piu' da nessuna parte"
    for locale in ("it", "en"):
        data = json.loads((I18N / f"{locale}.json").read_text(encoding="utf-8"))
        frasi = data["casa"]["back"]
        for dove in destinazioni:
            assert frasi.get(dove, "").strip(), f"casa.back.{dove} manca in {locale}.json"
        assert len(set(frasi.values())) == len(frasi), (
            "due destinazioni con la stessa frase: l'occhiello ha smesso di dire dove porta"
        )


# ── Le regole che le hai dato tu ────────────────────────────────────────────


def test_the_rules_are_written_through_the_command_and_not_as_a_file() -> None:
    """Salvarle vuol dire **due** scritture: la verita' in un file che Dream non
    puo' riscrivere, e la copia dentro `SOUL.md` che il prompt legge. Se la casa
    le salvasse con `workspace.write` ne farebbe una sola, e la copia
    comincerebbe a divergere dalla verita' al primo salvataggio."""
    jenny = (ASSETS / "casa-jenny.js").read_text(encoding="utf-8")
    assert "rpc.writeSoulRules(" in jenny, "le regole non passano piu' dal comando"
    assert "writeWorkspaceFile" not in jenny, (
        "le regole vengono scritte come un file qualunque: la copia in SOUL.md non si rifa'"
    )
    rpc = (ASSETS / "shared" / "rpc-client.js").read_text(encoding="utf-8")
    assert "soul.rules.write" in rpc, "il comando non esiste piu' lato client"

    from jenny.webui.commands import COMMANDS

    assert "soul.rules.write" in COMMANDS, "il comando non esiste piu' lato server"


def test_the_two_halves_look_at_the_same_file() -> None:
    """La casa legge il file, il server lo scrive: due costanti, un posto solo."""
    from jenny.agent.soul_rules import RULES_FILE

    jenny = (ASSETS / "casa-jenny.js").read_text(encoding="utf-8")
    m = re.search(r"export const RULES_PATH = '([^']+)'", jenny)
    assert m, "la casa non dice piu' da dove legge le regole"
    assert m.group(1) == RULES_FILE.as_posix(), (
        f"la casa legge {m.group(1)}, il server scrive {RULES_FILE.as_posix()}"
    )


def test_the_room_says_what_happens_to_what_you_write() -> None:
    """La frase sotto la casella non e' decorazione: dice che quel testo resta
    tuo e che il resto del carattere non e' modificabile da li'. Senza, un
    campo di testo accanto a «Jenny» promette di poter riscrivere lei."""
    for locale in ("it", "en"):
        data = json.loads((I18N / f"{locale}.json").read_text(encoding="utf-8"))
        jenny = data["casa"]["jenny"]
        for key in ("rules", "rulesHint", "rulesPlaceholder", "rulesSave",
                    "rulesSaved", "rulesFailed"):
            assert jenny.get(key, "").strip(), f"casa.jenny.{key} manca in {locale}.json"


# ── Lei sta dietro, e le schede la coprono davvero ──────────────────────────


def _rule(css: str, selector: str) -> str:
    """Tutto cio' che il foglio dichiara per *selector*, gruppi compresi.

    Unisce i corpi invece di prendere il primo: quelle due proprieta' arrivano
    da due regole diverse — il gruppo che mette davanti le schede e la regola
    che veste quella singola — e guardarne una sola dice «non c'e'».
    """
    corpi = []
    for selettori, corpo in re.findall(r"([^{}]+)\{([^}]*)\}", css):
        nomi = {s.strip().splitlines()[-1].strip() for s in selettori.split(",") if s.strip()}
        if selector in nomi:
            corpi.append(corpo)
    return "\n".join(corpi)


def test_the_cards_pass_in_front_of_her() -> None:
    """A taglia grande lei si siede sulla pagina: le schede delle due stanze
    le passano davanti. Il numero deve stare **sopra** il suo, e non e' una
    costante scritta a caso — se un giorno il suo salisse, queste righe
    smetterebbero di coprirla e nessuno lo direbbe."""
    css = CSS.read_text(encoding="utf-8")
    m = re.search(r"\.casa-jenny \{[^}]*z-index: (\d+)", css, re.S)
    assert m, "lo sprite non ha piu' un z-index"
    suo = int(m.group(1))
    corpo = _rule(css, ".casa-workshop")
    z = re.search(r"z-index: (\d+)", corpo)
    assert z and int(z.group(1)) > suo, (
        f"le schede stanno a {z.group(1) if z else 'nessuno'} e lei a {suo}: le finisce sopra"
    )


def test_a_card_that_has_to_cover_her_is_not_see_through() -> None:
    """`--overlay` e' semi-trasparente: con lei dietro, la scheda dell'officina
    la lasciava vedere **attraverso** — «osserva, regola, ripara» letto sopra la
    sua faccia. Una scheda che deve coprire dev'essere opaca."""
    css = CSS.read_text(encoding="utf-8")
    for selettore in (".casa-workshop", ".casa-rows", ".casa-card"):
        corpo = _rule(css, selettore)
        sfondo = re.search(r"\n  background: ([^;]+);", corpo)
        assert sfondo, f"{selettore} non dichiara piu' uno sfondo"
        assert "--overlay" not in sfondo.group(1), (
            f"{selettore} e' semi-trasparente: lei si vede attraverso"
        )


def test_the_settings_page_does_not_borrow_a_name_the_chat_already_uses() -> None:
    """Un nome di classe vuol dire **una** cosa.

    `casa-block` era gia' la bolla di un messaggio, e chiamando cosi' le schede
    di questa pagina le loro regole sono atterrate su ogni riga della
    conversazione: i messaggi sono diventati schede con bordo e sfondo, e lo
    `z-index` che serviva a coprire Jenny l'ha mandata **dietro la chat**.
    Nessun banco lo vedeva — i due file non si nominano fra loro — e sul
    telefono era la prima cosa che si notava.

    Il banco incrocia i due insiemi: le classi che la chat si costruisce da
    sola, e quelle che le due stanze nuove scrivono nel guscio.
    """
    chat = (ASSETS / "casa-chat.js").read_text(encoding="utf-8")
    della_chat = set()
    for valore in re.findall(r"className = '([^']+)'", chat):
        della_chat |= set(valore.split())
    assert della_chat, "la grep sulle classi della chat non morde piu'"

    html = INDEX.read_text(encoding="utf-8")
    stanze = re.findall(
        r'<section class="casa-(?:tu|jenny-room)".*?</section>', html, re.S
    )
    assert len(stanze) == 2, f"le due stanze non si trovano piu' ({len(stanze)})"
    delle_stanze = set()
    for stanza in stanze:
        for valore in re.findall(r'class="([^"]+)"', stanza):
            delle_stanze |= set(valore.split())

    in_comune = della_chat & delle_stanze
    assert not in_comune, (
        f"queste classi vogliono dire due cose diverse: {sorted(in_comune)}"
    )


# ── L'officina, invertita ───────────────────────────────────────────────────


def _contrasto(a: str, b: str) -> float:
    """Il rapporto di contrasto WCAG fra due colori esadecimali."""

    def luminanza(colore: str) -> float:
        colore = colore.strip().lstrip("#")
        canali = [int(colore[i : i + 2], 16) / 255 for i in (0, 2, 4)]
        lineari = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canali]
        return 0.2126 * lineari[0] + 0.7152 * lineari[1] + 0.0722 * lineari[2]

    chiaro, scuro = sorted((luminanza(a), luminanza(b)), reverse=True)
    return (chiaro + 0.05) / (scuro + 0.05)



def test_the_workshop_card_is_inverted() -> None:
    """La tavola la disegna **scura su pagina chiara**: e' l'unica cosa
    invertita della pagina, e lo e' perche' di la' si va a fare un altro
    mestiere. L'avevo appiattita io, per una ragione reale — era
    semi-trasparente e Jenny si vedeva attraverso — risolta pero' rendendola
    identica a tutte le altre schede.
    """
    corpo = _rule(CSS.read_text(encoding="utf-8"), ".casa-workshop")
    sfondo = re.search(r"\n  background: ([^;]+);", corpo)
    testo = re.search(r"\n  color: ([^;]+);", corpo)
    assert sfondo and "var(--text)" == sfondo.group(1).strip(), (
        "la scheda dell'officina non e' piu' invertita: ha lo sfondo delle altre"
    )
    assert testo and "var(--bg)" == testo.group(1).strip(), (
        "fondo invertito e testo no: la scheda e' illeggibile"
    )

    icona = re.search(r"\.casa-workshop > \.ti-tool \{([^}]*)\}", CSS.read_text(encoding="utf-8"))
    assert icona and "var(--accent-on-text)" in icona.group(1), (
        "l'icona e' tornata a `--accent`: su Chanel e su Fumetto l'accento "
        "**e'** il testo, cioe' esattamente il fondo di questa scheda"
    )


def test_pressing_the_workshop_card_does_not_punch_a_hole_in_it() -> None:
    """Lo stato premuto non puo' tornare traslucido.

    La scheda copre Jenny, e `--overlay` su una superficie invertita e' due
    volte sbagliato: e' semi-trasparente, e la sua tinta e' quella del verso
    opposto — bianca nei temi scuri, dove la scheda invertita e' chiara.
    """
    css = CSS.read_text(encoding="utf-8")
    premuta = _rule(css, ".casa-workshop:active")
    assert premuta.strip(), "la scheda dell'officina non risponde piu' al tocco"
    assert "--overlay" not in premuta, (
        "lo stato premuto e' tornato traslucido: lei si vede attraverso"
    )
    assert "background:" not in premuta, (
        "lo stato premuto riscrive lo sfondo invece di velarlo: un colore "
        "solo per sette temi torna a essere quello sbagliato in qualcuno"
    )


def test_the_workshop_icon_is_legible_on_the_inverted_card_in_every_theme() -> None:
    """L'icona su fondo `--text`, misurata su tutti e sette i temi.

    Non basta che sia «diversa dal fondo»: un'icona da 20 px a 2,4:1 e'
    sbiadita anche se il conto dice che due colori non coincidono. La soglia
    e' 3:1, quella per un oggetto grafico.

    Misurato sul rig, con `--accent` su tutti e sette:

      chanel 1,00 · fumetto 1,00   l'accento **e'** il testo, cioe' il fondo
      sticker 2,41 · pietra 2,69   sotto soglia
      synthwave 3,06 · kyoto 3,68 · y2k 4,58

    Da cui `--accent-on-text`: i tre che passano tengono la loro tinta, gli
    altri quattro prendono `--bg`, che e' l'altra meta' della coppia di
    contrasto della pagina e sta sopra 10:1 per costruzione.

    Il banco vale soprattutto per dopo: ritoccare la tinta di un tema, o
    aggiungerne uno, qui si vede invece di arrivare sullo schermo.
    """
    css = TEMI.read_text(encoding="utf-8")

    def dichiara(corpo: str, nome: str) -> str | None:
        m = re.search(rf"--{nome}:\s*([^;]+);", corpo)
        return m.group(1).strip() if m else None

    # Le regole in ordine, col loro elenco di selettori: una sola regola di
    # gruppo vale per cinque temi, e guardarne solo l'ultimo direbbe che agli
    # altri quattro quel valore non arriva.
    regole = [
        ({s.strip().splitlines()[-1].strip() for s in selettori.split(",") if s.strip()}, corpo)
        for selettori, corpo in re.findall(r"([^{}]+)\{([^}]*)\}", css)
    ]
    temi = sorted({m.group(1) for m in re.finditer(r'\[data-theme="([^"]+)"\]', css)})
    assert len(temi) >= 7, f"i temi trovati sono {len(temi)}, non i sette che esistono"

    for tema in temi:
        vale = {":root", f'[data-theme="{tema}"]'}
        valori: dict[str, str | None] = dict.fromkeys(
            ("text", "accent", "bg", "accent-on-text"), None
        )
        for selettori, corpo in regole:  # in ordine: l'ultimo che parla vince
            if not (selettori & vale):
                continue
            for nome in valori:
                if (v := dichiara(corpo, nome)) is not None:
                    valori[nome] = v
        assert valori["accent-on-text"], f"{tema}: `--accent-on-text` non arriva"
        # Una sola indirezione, che e' tutto cio' che il foglio usa.
        risolto = valori["accent-on-text"]
        for nome in ("accent", "text", "bg"):
            risolto = risolto.replace(f"var(--{nome})", valori[nome] or "")
        rapporto = _contrasto(risolto, valori["text"])
        assert rapporto >= 3.0, (
            f"tema «{tema}»: l'icona dell'officina e' {risolto} su un fondo "
            f"{valori['text']} — contrasto {rapporto:.2f}:1, sotto la soglia di 3:1"
        )
