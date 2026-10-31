{# The instruction appended to the conversation when a long task is about to fill the context window. The model reads everything above it (same prefix as the request it was about to make, so the provider cache is hit) and writes the checkpoint that replaces the earlier steps of the task. #}
[Context checkpoint — not a message from the user.] You have been working on the user's request, and the context window is filling up. Write a checkpoint that will REPLACE the earlier steps of this task: from now on you will see only the conversation before the request, the request itself, this checkpoint, and your most recent steps. Write it for yourself, so you can carry on without repeating work or losing anything the next steps need.

Do not call any tool. Reply with the checkpoint only, in exactly this structure:

## Request
What the user asked for in this task, and what counts as done.

## User instructions
Every constraint, preference, correction or prohibition the user gave for this task, quoted. Write "None" if there are none. Never drop one, even if it seems minor.

## Done so far
The steps already completed and what they produced. Be concrete: file paths, URLs, identifiers, commands, numbers, names, exact values. Keep what a later step or the final answer needs; drop what neither needs.

## Errors and dead ends
What failed, and why, so it is not tried again. Write "None" if nothing failed.

## Open items
What is still to do, in order.

## Current step
What you were doing in the most recent steps, and the very next action.

If an earlier checkpoint is in the conversation, merge it into this one: keep everything from it that still matters. Stay under {{ max_words }} words.
