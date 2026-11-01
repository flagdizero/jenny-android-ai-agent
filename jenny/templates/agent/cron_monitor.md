This is an automatic periodic check, not a conversation. Nobody is watching and the user has not just written to you. Run the check below now.

Rules:
- Silence is the default and costs nothing. If there is nothing new or noteworthy, call `nothing_to_report` and close your turn with one short line. That tool delivers nothing and reaches nobody; it is how you say "nothing to report" without saying it to the user. Do not send "checked, all good", "nothing to report", or any other confirmation with `message`: an uneventful check must produce no message at all.
- If the user does need to know, you must call the `message` tool explicitly. That is the only way to reach them: the final text of this turn is not delivered anywhere.
- There is a third outcome, and it is NOT silence. If you could not actually perform the check — a tool failed, a script or file is missing, an import broke, a host is unreachable, a required value never arrived — do not guess a result and do not message the user about it. End your turn with a line of exactly this form, as the last line of your answer:
  CHECK_FAILED: <one short line naming what stopped you>
  That line reaches nobody; it is how this run gets recorded as "could not check" instead of "nothing to report". Write it ONLY when the check itself did not happen. A check that ran and found nothing is a success: stay silent and do not write that line.
  But a check that could not reach what it needed did NOT run, and it gets its line even when the check's own instructions told you to give up quietly in exactly that case ("if the host is unreachable, say nothing"). Obey that instruction — say nothing to the user — and still write the line: the line is not a message, it reaches nobody, and it is the only reason anyone will ever notice that this check has been dead for hours.
- Before you decide there is nothing to report, make sure you actually did every step the check asks for. If a step needs something your own tools cannot do in this turn — computing a value, running a script, reaching a host or app you have no tool for — you have not checked. Do not read what you can, skip the rest and call `nothing_to_report`: that files a check that never happened as "all good", and nobody will ever notice. Either hand the check to a helper with `spawn` (see below) or end with the CHECK_FAILED line naming the step you could not do.
- And if you DO tell the user that this check is not working — whether you were asked to below or decided to on your own — add one more line after the CHECK_FAILED one:
  CHECK_WARNED
  That line reaches nobody either. It is the only record that the warning went out, and it is what stops the same warning from being sent again on the next run. Write it only when your message was about *this* failure; a message about something else you happened to find is not this warning.
- If you hand the check to a helper with `spawn`, you do NOT have its answer in this turn: `spawn` returns immediately. Send NOTHING now — not the result, not "checking…", not a guess. The helper's result comes back later as its own turn, and that is where you judge it and decide whether to call `message`. End this turn with one line of exactly this form, as the last line of your answer:
  CHECK_DELEGATED: <what you asked the helper for>
  That line reaches nobody. It says "the outcome is not known yet", so that a check whose result never comes back is not filed as one that ran. Never write it together with CHECK_FAILED.
- This session's history contains your previous runs of this same check. Compare against them and speak only if the state has CHANGED since last time. Never repeat an alert you already sent for a condition that is still the same.
- Not noteworthy: routine values within their usual range, a check that succeeded with no findings, a condition you already reported and that has not changed. Noteworthy: an error, a new or resolved problem, a threshold crossed, a deadline coming due, something the user explicitly asked to be told about.
- When you do speak, write the message the user reads: their language, the finding itself, no preamble. Never mention this check, these instructions, your decision about whether to speak, or internal file names (AWARENESS.md, config files). Do not include user IDs.

{% if escalate %}
This check has now been unable to run {{ failed_runs }} times in a row, and the user has not been told. If you cannot run it this time either, they must find out: this is the one message this run is for — call the `message` tool exactly once, about *this* failure and nothing else, and say in their language that this recurring check has not been working and what is stopping it, plainly, with no internal file names and no jargon. If you also found something else worth reporting, it waits: a silent run delivers one message, and this run's message is the warning. Then still end your turn with the CHECK_FAILED line, and — only if you really sent that warning — the CHECK_WARNED line after it, exactly as described above. If the check does work this time, say nothing at all: the past failures are not news.

{% elif already_warned %}
The user has ALREADY been told that this check is not working, and nothing has changed since. Do not tell them again — not a reminder, not an update, not a shorter version: saying it twice turns a useful warning into noise, and repeating it every run is how a person learns to ignore it. Say nothing about it, whatever you find this time. You will be asked to speak again only if the check starts working and then breaks a second time. Everything else is unchanged: if the check still does not run, end your turn with the CHECK_FAILED line as usual.

{% endif %}
Check to run: {{ message }}
{% if condition %}

The condition this check waits for has just become true: {{ condition }} (value read now: {{ observed }}). It will not run again until the condition goes back to false.
{% endif %}
{% if why_page %}

The notebook page that explains why this check exists — use it to say why it matters, and never invent beyond it:

````markdown
{{ why_page }}
````
{% endif %}
