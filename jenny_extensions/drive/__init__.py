"""La copia cifrata di Jenny su Google Drive (solo nella build del Play Store).

Il motore è provider-neutro (``cloud.py``, ``cloud_sink.py``); la destinazione
è Google Drive (``cloud_drive.py``), con il token che dà Android
(``android/app/src/play/.../DriveBridge.kt``); ``cloud_service.py`` mette
insieme i pezzi. Al core arrivano solo i ganci dichiarati qui sotto.
"""

from __future__ import annotations

from jenny.extensions import Extension, ExtensionUi, SystemJob

from . import cron
from .routes import make_routers

EXTENSION = Extension(
    name="drive",
    system_jobs=(
        SystemJob(id=cron.JOB_ID, every_ms=cron.EVERY_MS, purpose=cron.PURPOSE, run=cron.run),
    ),
    make_routers=make_routers,
    ui=ExtensionUi(
        package="jenny_extensions.drive.ui",
        files=(
            "main.js",
            "home.js",
            "onboarding.js",
            "cloud-backup.js",
            "cloud-backup-dialogs.js",
            "drive.css",
            "i18n/en.json",
            "i18n/it.json",
        ),
        entry="main.js",
        css=("drive.css",),
        i18n={"en": "i18n/en.json", "it": "i18n/it.json"},
    ),
)
