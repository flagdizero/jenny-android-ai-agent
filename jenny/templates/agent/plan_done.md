[Plan — done]

The plan is finished: every step is closed. The user has not read the result yet.

Plan: {{ title }}
{% for s in steps %}
{{ s.mark }} {{ s.n }}. {{ s.title }}{% if s.note %} — {{ s.note }}{% endif %}

{% endfor %}

Now answer, here in the chat, the question the user asked when the plan started: the answer itself, in a few lines, in the user's language. For a comparison, which one to pick and why ("if you need X, take A; if you need Y, take B"); for a job that changed things, what is different now and what is left open. If you need the result to answer, read it first. Then say in one line where the full result is. Do not list what each step did: the plan card shows it. Do not start new work.
