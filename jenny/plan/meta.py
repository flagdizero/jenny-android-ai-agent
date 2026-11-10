"""I nomi che il piano mette sul filo: i suoi tool e le chiavi dei metadati.

Un modulo foglia: chi deve solo riconoscere un turno di piano (``AgentLoop``,
il gestore degli aiutanti) importa da qui, senza caricare il driver.
"""

from __future__ import annotations

#: I quattro tool del modello. Quando si vedono lo decide il driver
#: (``AgentLoop`` chiede :meth:`PlanDriver.step_open`).
PLAN_PROPOSE_TOOL = "plan_propose"
PLAN_STEP_TOOLS: frozenset[str] = frozenset({"plan_step_done", "plan_step_skip", "plan_blocked"})
PLAN_TOOL_NAMES: frozenset[str] = frozenset({PLAN_PROPOSE_TOOL, *PLAN_STEP_TOOLS})

#: Metadati del messaggio: il turno di pianificazione (il tasto Piano), la
#: revisione di una proposta, la risposta a un piano bloccato.
PLAN_MODE_META = "plan_mode"
PLAN_REVISE_META = "plan_revise"
PLAN_ANSWER_META = "plan_answer"
PLAN_ID_META = "plan_id"
PLAN_STEP_META = "plan_step"
#: Messo dal server su un turno della pianificazione in corso che il client
#: non ha marcato (la risposta a una domanda di Jenny): il blocco del turno lo
#: dice al modello, che non ricomincia da capo.
PLAN_FOLLOWUP_META = "_plan_drafting_followup"

#: Le note di sistema che il modello legge al turno dopo (un ritorno indietro):
#: stanno nei metadati della sessione, e il loop le consuma nel contesto.
PLAN_NOTES_META = "plan_notes"

#: Messo dal driver sul turno che sveglia Jenny sui risultati degli aiutanti
#: rimasti in attesa: ``stalled`` (i fratelli rimasti sono tutti fermi) o
#: ``timeout`` (``HELD_WAKE_AFTER_S``). Il turno è un turno di passo
#: (``PLAN_STEP_EVENT``): stessa verifica di freschezza, stessi tool.
PLAN_WAKE_META = "plan_wake"
