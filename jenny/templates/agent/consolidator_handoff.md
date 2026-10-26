{# Appended to consolidator_archive.md when the archived messages leave a conversation that goes on (compaction by length or by idleness, not /new). The facts go to long-term memory; the working state goes back into the next turns' prompt only. #}
This time your reply has two parts.

First, the facts, exactly as described above (or `(nothing)`).

Then a line containing exactly `### Working state`, followed by where the conversation stood at the end of these messages, so it can carry on after they are removed:

- Ongoing: the task or topic in progress, and what counts as done. Write "Nothing ongoing" if it was closed.
- Open items: what was promised, pending or waiting on the user, in order.
- Last step: what was done last, and the next action.
- References: file paths, names, identifiers, numbers or URLs that the next turns will need.

The working state is not memory: it may hold temporary details that the facts leave out. Keep it under 200 words.{% if previous %} An earlier working state is below: update it, keeping what is still open and dropping what is done.

Earlier working state:
{{ previous }}{% endif %}
