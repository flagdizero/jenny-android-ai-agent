"""Agent-context summary of the installed Jenny Apps (progressive disclosure:
one line per app; AGENT.md stays lazy-read via the file tools).

Dal 06/10/2026 dice anche **dove** un'app si può usare: in un quaderno, le app
posate sulla sua bacheca sono quelle che la chat può cambiare
(``jenny/apps/permissions.py``), le altre si leggono soltanto.
"""

from __future__ import annotations

from pathlib import Path

from jenny.apps.manifest import LoadedApp, scan_apps


def _line(app: LoadedApp, *, here: bool) -> str:
    if app.broken or app.manifest is None:
        return (f"- **{app.slug}** — BROKEN ({app.error}). Fix `apps/{app.slug}/app.json` "
                "with the `app` tool (action=edit) if the user asks.")
    # Composta a pezzi perche' le azioni sono opzionali: un'app di sola
    # visualizzazione ne ha zero, e un `— tools: ` vuoto in mezzo alla riga
    # sembrerebbe un elenco che non e' stato caricato.
    m = app.manifest
    parts = [f"- **{m.name}** (`{app.slug}`) — {m.description}"]
    if m.actions:
        tools = ", ".join(f"`{app.slug}_{a.name}`" for a in m.actions)
        parts.append(f"tools: {tools}")
    else:
        parts.append("no agent-facing actions (display-only app)")
    if m.reader is not None:
        parts.append(f"reader every {m.reader['every']} min")
    if m.section is not None:
        parts.append(f"summary: {m.section.view}")
    if here:
        parts.append(f"data: `apps/{app.slug}/data/`")
        if (app.dir / "AGENT.md").is_file():
            parts.append(f"context: `apps/{app.slug}/AGENT.md`")
    return " — ".join(parts)


def build_apps_summary(workspace: Path, notebook_root: Path | None = None) -> str:
    """One markdown bullet per app; empty string when there are no apps.

    With *notebook_root* (a notebook's chat) the apps placed on its board come
    first, as the ones this chat can use; the others are named as read-only.
    """
    apps = scan_apps(workspace)
    if not apps:
        return ""
    lines: list[str] = []
    if notebook_root is not None:
        from jenny.board.placements import placed_slugs

        placed = placed_slugs(notebook_root)
        mine = [a for a in apps if a.slug in placed]
        others = [a for a in apps if a.slug not in placed]
        if mine:
            lines.append("On this notebook's board — this chat can use and change them:")
            lines.extend(_line(a, here=False) for a in mine)
        if others:
            names = ", ".join(f"{a.manifest.name if a.manifest else a.slug} (`{a.slug}`)"
                              for a in others)
            lines.append(f"Other apps of the user, read-only from here (place one on this board "
                         f"with `app action=place` if the user wants it here): {names}")
    else:
        lines.extend(_line(a, here=True) for a in apps)
    return "\n".join(lines)
