"""Le estensioni impacchettate solo in alcune build di Jenny.

Questo pacchetto sta **fuori** da ``jenny/`` di proposito: Gradle lo mette
nell'APK solo nei flavor che lo vogliono (il flavor ``play``), e una build senza
non ne contiene nemmeno il codice. Il core lo conosce solo per nome, in
``jenny/extensions.py``, e legge da qui un solo nome: ``EXTENSIONS``.

L'import resta leggero (gira all'avvio, nella sync della WebUI): ogni
estensione dichiara i suoi ganci e importa i moduli pesanti solo quando servono.
"""

from __future__ import annotations

from .drive import EXTENSION as _DRIVE

EXTENSIONS = (_DRIVE,)
