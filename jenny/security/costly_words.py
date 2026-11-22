"""Le parole che dicono «questo tasto costa»: soldi, distruzione, accesso.

Un posto solo per il browser di Jenny (``agent/tools/browser.py``) e per le sue
mani sul telefono (``jenny/phone/consent.py``): un tasto «Paga» è lo stesso
rischio in una pagina web e in un'app, e due liste divergerebbero.

Confronto a parola intera: "ordina" non deve scattare su "ordinamento", e
"conferma" da sola non deve scattare su ogni banner dei cookie — per questo
c'è "conferma ordine" e non "conferma".
"""

from __future__ import annotations

import re

COSTLY_VERBS = (
    r"pag(?:a|are|amento)", r"acquist(?:a|are|o)", r"compra", r"ordina",
    r"conferma ordine", r"procedi al pagamento", r"abbonati",
    r"pay", r"buy", r"purchase", r"checkout", r"place order", r"subscribe",
    r"elimin(?:a|are)", r"cancell(?:a|are)", r"rimuov(?:i|ere)", r"svuota",
    r"delete", r"remove", r"empty (?:cart|trash)",
    r"trasferisc(?:i|ere)", r"bonifico", r"invia denaro", r"transfer", r"send money",
)
ACCESS_VERBS = (
    # "entra" e' l'etichetta di accesso piu' comune sui siti italiani, e senza
    # di essa l'interlocco non copre il caso piu' frequente qui. Il confine di
    # parola la tiene stretta: non scatta su "rientra", "entrata", "centrale".
    r"accedi", r"entra", r"sign in", r"log ?in",
)
SENSITIVE_VERBS = COSTLY_VERBS + ACCESS_VERBS
SENSITIVE_RE = re.compile(r"\b(?:" + "|".join(SENSITIVE_VERBS) + r")\b", re.IGNORECASE)
COSTLY_RE = re.compile(r"\b(?:" + "|".join(COSTLY_VERBS) + r")\b", re.IGNORECASE)
