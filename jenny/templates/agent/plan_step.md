{% if reminder %}[Plan reminder — step {{ step.n }} of {{ total }} is still open]

You ended your last turn without closing this step, and no helper is working on it. Close it now:

- finished → call `plan_step_done` with a one-line note of what changed;
- nothing to do after all → call `plan_step_skip` with the honest reason;
- you need the user (a choice, a missing fact, a permission) → call `plan_blocked` with one clear question.

If the step is not finished yet, finish it first and then call `plan_step_done`. This is the only reminder: if this turn ends with the step still open and no helper working on it, the plan stops and waits for the user.
{% else %}[Plan — step {{ step.n }} of {{ total }}]

The user approved this plan, and the code runs it one step at a time. Do step {{ step.n }} now, and only that one.

Plan: {{ title }}
{% for s in steps %}
{{ s.mark }} {{ s.n }}. {{ s.title }}{% if s.note %} — {{ s.note }}{% endif %}

{% endfor %}

Step {{ step.n }}: {{ step.title }}

{{ step.detail }}

Rules:
- Do this step only. Do not start the next one: the code opens it when you close this one.
- You may run several helpers in parallel for this step; wait for all their results before closing it.
- This may be a second run of the step (after a restart): look at what is already there and do not duplicate it.
- When it is finished, call `plan_step_done` with a one-line note of what changed (for example "3 pages written, 1 source discarded").
- If there is nothing to do, call `plan_step_skip` with the honest reason.
- If you need the user — a choice, a missing fact, a permission — call `plan_blocked` with one clear question, and stop there.
- Keep your words to the user short: the plan card already shows the list and the progress.
{% endif %}
