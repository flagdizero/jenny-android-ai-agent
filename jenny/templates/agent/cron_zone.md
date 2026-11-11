{# The turn of a zone job (jenny/cron/zones.py): the phone has just arrived at a place. Chosen by bound_runner instead of cron_reminder.md, which says "the scheduled time has arrived". #}
The user has just arrived at {{ place }}: the phone's position was detected {{ distance }} from it{% if accuracy %} (accurate to about {{ accuracy }}){% endif %}{% if minutes_late %}, {{ minutes_late }} minutes ago — the message could not go out before, so they may have moved on: say it briefly if it matters{% endif %}. Do what this job asks now and tell the user, in the same session.

Rules:
- Speak directly to the user in their language.
- Do not narrate internal progress, and do not explain how the arrival was detected.
- Do not include user IDs or coordinates.
- Do not add status reports like "Done" or "Reminded" unless they are the natural response.
{% if why_page %}
- A page from the notebook explains why this matters. Say what is due and why, using ONLY that page — never invent — and ask in one short line when they want to do it.
{% endif %}

Job: {{ message }}
{% if why_page %}

The page that explains why:

````markdown
{{ why_page }}
````
{% endif %}
