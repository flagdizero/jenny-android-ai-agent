"""L'arte a due livelli: che i sorgenti siano registrati, e che si spedisca solo il visibile.

La companion sovrappone due immagini sullo stesso quadrato 3000² — un corpo
senza faccia e una faccia da sola — e a runtime non c'è nessuna scala, nessun
offset e nessun pivot: la registrazione è responsabilità dell'artista sul
canvas (v. ``android/image_source/README.md``). Questo è il test che quella
responsabilità la misura invece di guardarla: due coppie ricompongono
**esattamente** la posa cotta da cui vengono, e i riquadri d'inchiostro
combaciano. Se un domani un sorgente arriva spostato o riscalato, lo dice qui
e non l'occhio su uno screenshot.

Serve PIL, che non è una dipendenza del progetto: si salta senza, come i test
client fanno senza ``node``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from jenny.utils.android_assets import _UI_MANIFEST

pytest.importorskip("PIL", reason="Pillow non è una dipendenza del progetto")

from PIL import Image, ImageChops  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "android" / "image_source"
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
CANVAS = 3000
EXPORT_SIZE = 768

# La finestra dell'inchiostro per orientamento: l'unione dei riquadri misurata
# sui sorgenti, allargata di 40 px per lato. I due orientamenti guardano da
# parti diverse, quindi le finestre non coincidono — e non si contengono a
# vicenda: una faccia ``front`` messa per sbaglio fra le ``side`` (o viceversa)
# sfonda il bordo dell'altra. Serve a prendere una scala o un centro sbagliati
# nell'export, che a runtime nessuno correggerebbe.
FACE_WINDOWS = {
    "face_front_": (1071, 1072, 2070, 1648),
    "face_side_": (987, 1012, 1961, 1624),
}

# Le coppie che ricompongono una posa cotta, cioè quelle in cui l'artista ha
# cancellato la faccia da un disegno che esiste già. Le altre facce sono
# ridisegnate e non hanno un originale con cui confrontarsi.
RECOMPOSES = [
    ("body_front_idle", "face_front_normal_talk", "talk_1b"),
    ("body_front_think", "face_front_thinking", "think"),
]

# Corpo → posa cotta da cui viene: stessa sagoma, meno la faccia. Il riquadro
# d'inchiostro deve essere identico, ed è il modo più stretto di dire "stessa
# scala, stessa posizione" senza guardare i pixel della faccia.
SAME_SILHOUETTE = [
    ("body_front_idle", "idle"),
    ("body_front_hand", "talk_1a"),
    ("body_front_think", "think"),
]


def _layers() -> list[str]:
    """La tabella ``LAYERS`` letta dal generatore, non ricopiata qui."""
    spec = importlib.util.spec_from_file_location("gen_pose_webp", SRC / "gen_pose_webp.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(module.LAYERS)


def _sources() -> list[Path]:
    return sorted(p for p in SRC.glob("*.PNG") if p.stem.startswith(("body_", "face_")))


def _rgba(path: Path) -> Image.Image:
    return Image.open(path).convert("RGBA")


def _max_delta(a: Image.Image, b: Image.Image) -> int:
    """Lo scarto massimo su tutti i canali, alfa compreso. Solo PIL, niente numpy."""
    return max(high for _low, high in ImageChops.difference(a, b).getextrema())


def test_every_layer_source_is_a_transparent_3000_square() -> None:
    sources = _sources()
    assert len(sources) == 23, [p.name for p in sources]
    for path in sources:
        im = Image.open(path)
        assert im.size == (CANVAS, CANVAS), f"{path.name}: {im.size}"
        assert im.mode in ("RGBA", "LA", "P"), f"{path.name}: {im.mode} senza alfa"
        assert _rgba(path).getchannel("A").getbbox(), f"{path.name}: tutto trasparente"


def test_the_exported_layers_are_exactly_the_ones_in_the_manifest() -> None:
    """Tabella del generatore, file su disco e manifest dicono la stessa cosa."""
    expected = {f"assets/jenny-{stem.replace('_', '-')}.webp" for stem in _layers()}
    listed = {
        e for e in _UI_MANIFEST
        if e.startswith(("assets/jenny-body-", "assets/jenny-face-"))
    }
    assert listed == expected, f"manifest e LAYERS divergono: {sorted(listed ^ expected)}"
    for entry in sorted(expected):
        path = ASSETS.parent / entry
        assert path.is_file(), f"{entry}: manca su disco (rilanciare gen_pose_webp.py)"
        assert Image.open(path).size == (EXPORT_SIZE, EXPORT_SIZE), entry


def test_the_reserve_is_kept_as_a_source_and_never_shipped() -> None:
    """14 sorgenti aspettano il loro turno: nessuno di loro è un asset.

    Le bocche alternative degli umori (parlato espressivo), l'orientamento
    ``side`` e i corpi del saluto. Un webp che nessun ramo del client può
    mostrare marcirebbe, quindi non si esporta: v. mascot-faces-plan.md, F10.
    """
    reserve = {p.stem for p in _sources()} - set(_layers())
    assert len(reserve) == 14, sorted(reserve)
    for stem in sorted(reserve):
        asset = f"jenny-{stem.replace('_', '-')}.webp"
        assert not (ASSETS / asset).exists(), f"{stem}: esportato ma non usato"
        assert f"assets/{asset}" not in _UI_MANIFEST, f"{stem}: nel manifest ma non usato"


@pytest.mark.parametrize(("body", "pose"), SAME_SILHOUETTE)
def test_a_body_has_the_same_ink_box_as_the_pose_it_came_from(body: str, pose: str) -> None:
    """Stessa scala e stessa posizione: il riquadro dell'alfa deve coincidere al pixel."""
    got = _rgba(SRC / f"{body}.PNG").getchannel("A").getbbox()
    want = _rgba(SRC / f"{pose}.PNG").getchannel("A").getbbox()
    assert got == want, f"{body} è fuori registro rispetto a {pose}: {got} vs {want}"


@pytest.mark.parametrize(("body", "face", "pose"), RECOMPOSES)
def test_composing_a_body_and_a_face_reproduces_the_baked_pose(
    body: str, face: str, pose: str
) -> None:
    """La prova vera della registrazione: la somma dei due livelli *è* l'originale.

    La soglia è 8 su 255 e non 0 perché l'artista ha riesportato i PNG: quel che
    resta è frangia di antialias. Uno spostamento di un solo pixel la sfonda di
    un ordine di grandezza.
    """
    composed = _rgba(SRC / f"{body}.PNG")
    composed.alpha_composite(_rgba(SRC / f"{face}.PNG"))
    delta = _max_delta(composed, _rgba(SRC / f"{pose}.PNG"))
    assert delta <= 8, f"{body} + {face} non ricompone {pose}: scarto massimo {delta}"


def test_every_face_lands_inside_the_window_of_its_orientation() -> None:
    faces = [p for p in _sources() if p.stem.startswith("face_")]
    assert faces, "nessuna faccia trovata"
    for path in faces:
        prefix = next((k for k in FACE_WINDOWS if path.stem.startswith(k)), None)
        assert prefix, f"{path.name}: orientamento non riconosciuto"
        x0, y0, x1, y1 = FACE_WINDOWS[prefix]
        bbox = _rgba(path).getchannel("A").getbbox()
        assert bbox is not None
        assert x0 <= bbox[0] and y0 <= bbox[1] and bbox[2] <= x1 and bbox[3] <= y1, (
            f"{path.name}: inchiostro in {bbox}, fuori da {FACE_WINDOWS[prefix]}"
        )
