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
{% if repair is not none %}

The code has already checked the notebook: {{ machine_summary }}
This step is only the repair. Fix exactly what is listed here, and nothing else; do not reread or verify the rest of the notebook. When you call `plan_step_done`, the code checks again.
{% if repair %}

To repair, by page:
{% for group in repair %}
- {{ group.path }}
{% for item in group["items"] %}
  - {{ item }}
{% endfor %}
{% endfor %}
{% if repair_more %}
(and {{ repair_more }} more: the code lists them again when it checks again)
{% endif %}
{% endif %}

Give the pages to notebook-editor helpers, grouped by subject (a helper takes a group of pages). Lines left to search are repaired by writing "not documented" with the queries tried: this step starts no research.
{% else %}

{{ step.detail }}
{% if step.done_when %}

Done when: {{ step.done_when }}
{% endif %}
{% endif %}
{% if covers %}

This step searches these coverage entries (each one needs its sources saved, or the queries tried):
{% for c in covers %}
- {{ c.id }}: {{ c.item }}{% if c.missing %} — missing: {{ c.missing }}{% endif %}

{% endfor %}
{% endif %}
{% if lost_facts %}

Facts that were in the notebook before this plan and are in no page now{% if lost_total > lost_facts | length %} ({{ lost_facts | length }} shown of {{ lost_total }}){% endif %}. The code compared numbers with units, so it can be wrong: check each one. If it is still true, put it back in the right page; if it went on purpose (wrong, superseded, removed at the user's request), say why in the step note.
{% for f in lost_facts %}
- {{ f.page }}: {{ f.fact }} — "{{ f.context }}"
{% endfor %}
{% endif %}
{% if interrupted %}

The app was closed while these helpers of this step were working. Their conversation was saved: continue each one with `subagent_send` and its task id (it keeps what it had read and written), and do not start new helpers for their work. Then go on with the step as usual.
{% for h in interrupted %}
- `{{ h.task_id }}`: {{ h.label }}{% if h.agent_type %} ({{ h.agent_type }}){% endif %}

{% endfor %}
{% endif %}
{% if waiting %}

Already waiting for the user's approval, from an earlier run of this step: {{ waiting | join(", ") }}. Do not make these changes again: they count as done, and the code holds the step until the user decides.
{% endif %}

Rules:
- Do this step only. Do not start the next one: the code opens it when you close this one.
- You may run up to 5 helpers at once for this step (the slots are Jenny's, shared with every chat: a sixth is refused, not queued); wait for all their results before closing it.
- Helper results of this step arrive together, when the last one is done.
- A writer or notebook-editor that reports **To research** leaves gaps that belong to this step, but you do not research them on your own: call `plan_step_done` when the step's work is done, and the code tells you whether this step gets one round of research (then: a few researchers for all the gaps at once, grouped by subject, and the same helpers sent back with `subagent_send` to fill the pages from what they saved) or whether the gaps stay written in the pages as "not documented", with the queries tried. Never a second round in a step.
- In a `delete` step, helpers that check before the deletion report what must be known first under **Before deleting**, never under **To research**: nothing is researched in a step that deletes.
- A helper that stopped at its budget: continue the same helper with `subagent_send`, it keeps what it read and wrote. Do not start a new one for the same work.
- A helper that went off track (wrong pages, wrong notebook, a task it misread): stop it and undo its changes with `subagent_cancel` and `undo=true`, then start one helper with clearer instructions. Do not close the step on work you undid without redoing it.
- Scripts print only a compact result. A helper's report goes in its final answer, not in a file. A file in the notebook only if the step asks for one: then it is an audit entry in `audit/` with the audit frontmatter (`id`, `target`, `source`, `status`...; see the llm-wiki skill). Never create `outputs/` in a notebook.
- Removing pages is a notebook-editor helper's job (`notebook` action `remove_pages`); your own `notebook` tool only checks, maps and reads.
- This may be a second run of the step (after a restart): look at what is already there and do not duplicate it.
- When it is finished, call `plan_step_done` with a one-line note of what changed (for example "3 pages written, 1 source discarded").
- If there is nothing to do, call `plan_step_skip` with the honest reason.
- If you need the user — a choice, a fact only they know, a permission — call `plan_blocked` with what they must decide (one clear question, or the decisions numbered), and stop there. What you did and found goes in your reply, not in the question. A fact the web can give is not a question for them, and not a reason to block: note it under **To research** in the step and let the code decide.
- If a premise of the plan proves false (sources that are not there, an item that does not exist, a file different from what the plan says) and the later steps would come out poor, do not go on knowing it: stop with `plan_blocked`: say what you found in your reply, and ask in the question whether to go on as you propose.
- Close the step only when its "done when" holds. If a change waits for the user's approval (a draft), still call `plan_step_done`: the code holds the step until they decide.
- Keep your words to the user short: the plan card already shows the list and the progress.
{% endif %}
