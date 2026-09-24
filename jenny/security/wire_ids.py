"""La forma di un id che arriva dal filo (o che si interpola in un comando).

Id di task, di RPC, di correlazione, di job: token opachi corti, charset chiuso
``[A-Za-z0-9_-]``, da 1 a 64 caratteri. Diventano chiavi di dizionario, nomi di
file, argomenti di una route, pezzi di un comando remoto: passano da una
whitelist invece che da una pulizia per sottrazione.

Ancorata con ``\\A…\\Z`` e non ``^…$``: con ``re.match`` il ``$`` combacia anche
prima di un ``\\n`` finale, e fino al 24/09/2026 quattro moduli su cinque
accettavano ``"abc\\n"``.
"""

from __future__ import annotations

import re

WIRE_ID_RE = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")
