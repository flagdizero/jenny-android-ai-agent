"""Kept only so an older installation's copy is overwritten on update.

Validation lives in the `app` tool: `app action=draft` (or `edit`) checks the
manifest, the reader and the screen, runs the reader once on the app's real
data, and tells you exactly what is wrong. There is nothing to run here.
"""

MESSAGE = (
    "validate_app.py is retired: validation is `app action=draft` (or `edit`), which "
    "checks the manifest, the reader and the screen and runs the reader once."
)

if __name__ == "__main__":
    print(MESSAGE)
