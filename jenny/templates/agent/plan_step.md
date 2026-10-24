{% if reminder %}[Plan reminder — step {{ step.n }} of {{ total }} is still open]

You ended your last turn without closing this step, and no helper is working on it. Close it now:

- finished → call `plan_step_done` with a one-line note of what changed;
- nothing to do after all → call `plan_step_skip` with the honest reason;
- you need the user (a choice, a fact only they know, a permission) → call `plan_blocked` with one clear question.

If the step is not finished yet, finish it first and then call `plan_step_done`. This is the only reminder: if this turn ends with the step still open and no helper working on it, the plan stops and waits for the user.
{% else %}[Plan — step {{ step.n }} of {{ total }}]

The user approved this plan, and the code runs it one step at a time. Do step {{ step.n }} now, and only that one.

Plan: {{ title }}
{% for s in steps %}
{{ s.mark }} {{ s.n }}. {{ s.title }}{% if s.note %} — {{ s.note }}{% endif %}

{% endfor %}

Step {{ step.n }}: {{ step.title }}

{{ step.detail }}
{% if step.done_when %}

Done when: {{ step.done_when }}
{% endif %}
{% if covers %}

This step searches these coverage entries (each one needs its sources saved, or the queries tried):
{% for c in covers %}
- {{ c.id }}: {{ c.item }}{% if c.missing %} — missing: {{ c.missing }}{% endif %}

{% endfor %}
{% endif %}
{% if waiting %}

Already waiting for the user's approval, from an earlier run of this step: {{ waiting | join(", ") }}. Do not make these changes again: they count as done, and the code holds the step until the user decides.
{% endif %}

Rules:
- Do this step only. Do not start the next one: the code opens it when you close this one.
- You may run up to 5 helpers at once for this step; wait for all their results before closing it.
- Helper results of this step arrive together, when the last one is done.
- A writer or notebook-editor that reports **To research** has not finished the step: those gaps are part of it. Spawn researchers for them now, a few, grouped by subject; then send the same helper back with `subagent_send` to fill the pages from what they saved. Close the step when the pages are filled, or the gaps were searched and are still empty.
- A helper that stopped at its budget: continue the same helper with `subagent_send`, it keeps what it read and wrote. Do not start a new one for the same work.
- Scripts print only a compact result. A helper's report goes in its final answer, not in a file. A file in the notebook only if the step asks for one: then it is an audit entry in `audit/` with the audit frontmatter (`id`, `target`, `source`, `status`...; see the llm-wiki skill). Never create `outputs/` in a notebook.
- Removing pages is a notebook-editor helper's job (`notebook` action `remove_pages`); your own `notebook` tool only checks, maps and reads.
- This may be a second run of the step (after a restart): look at what is already there and do not duplicate it.
- When it is finished, call `plan_step_done` with a one-line note of what changed (for example "3 pages written, 1 source discarded").
- If there is nothing to do, call `plan_step_skip` with the honest reason.
- If you need the user — a choice, a fact only they know, a permission — call `plan_blocked` with one clear question, and stop there. A fact the web can give is not a question for them: research it.
- If a premise of the plan proves false (sources that are not there, an item that does not exist, a file different from what the plan says) and the later steps would come out poor, do not go on knowing it: stop with `plan_blocked`, saying what you found and what you propose.
- Close the step only when its "done when" holds. If a change waits for the user's approval (a draft), still call `plan_step_done`: the code holds the step until they decide.
- Keep your words to the user short: the plan card already shows the list and the progress.
{% endif %}
