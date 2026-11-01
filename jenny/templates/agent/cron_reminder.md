The scheduled time has arrived. Execute this scheduled cron job now and report the result to the user in the same session.

Rules:
- Speak directly to the user in their language.
- Do not narrate internal progress.
- Do not include user IDs.
- Do not add status reports like "Done" or "Reminded" unless they are the natural response.
{% if why_page %}
- A page from the notebook explains why this matters. Say what is due and why, using ONLY that page — never invent — and ask in one short line when they want to do it.
{% endif %}

Cron job: {{ message }}
{% if condition %}

It fired because this condition has just become true: {{ condition }} (value read now: {{ observed }}). It will not fire again until the condition goes back to false.
{% endif %}
{% if why_page %}

The page that explains why:

````markdown
{{ why_page }}
````
{% endif %}
