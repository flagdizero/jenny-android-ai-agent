#!/usr/bin/env python3
# Host-only: diagnostic script. Never imported by the Android runtime.
"""Le promesse che reggono su una frase di prompt, misurate sull'app vera.

Non e' un test e non da' un verdetto: per ogni scenario fa N giri su ogni
modello e scrive il **tasso** di ogni proprieta', confrontato con il riferimento
salvato in ``scripts/behaviour/baseline.json``. ``5/5 → 5/5`` e' silenzio,
``5/5 → 1/5`` e' il segnale. Il ragionamento sta in ``scripts/behaviour/README.md``.

Gira contro l'app installata su un emulatore (build debug, ``run-as``), col
provider che l'app ha gia': la chiave non lascia il dispositivo. Fotografa il
workspace all'inizio e lo rimette alla fine, riavviando l'app.

Uso::

    python scripts/behaviour_check.py --models deepseek-v4.1-flash,claude-haiku-5-5 --runs 3
    python scripts/behaviour_check.py --only forget_names_dream --calibrate
    python scripts/behaviour_check.py --stage 1 --save-baseline
    python scripts/behaviour_check.py --restore      # dopo un giro interrotto male

Ogni giro costa token veri: il report dice quanti.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from behaviour.device import Device  # noqa: E402
from behaviour.runner import Runner, summarize  # noqa: E402
from behaviour.scenarios import SCENARIOS  # noqa: E402

BASELINE = ROOT / "scripts" / "behaviour" / "baseline.json"
BASELINE_RUNS = ROOT / "scripts" / "behaviour" / "baseline-runs.json"


def _rate(cell) -> str:
    passed, total = cell
    return f"{passed}/{total}"


def _print_summary(table: dict, baseline: dict) -> None:
    for key in sorted(table):
        row = table[key]
        scenario, model, mode = key.split("|")
        base = baseline.get(key, {}).get("checks", {})
        cells = []
        for name, cell in sorted(row["checks"].items()):
            was = base.get(name)
            mark = ""
            if was and was[1]:
                drop = cell[0] / max(cell[1], 1) - was[0] / was[1]
                mark = f" (was {_rate(was)})" if abs(drop) >= 0.34 else ""
            cells.append(f"{name} {_rate(cell)}{mark}")
        err = f"  errors {row['errors']}" if row["errors"] else ""
        kind = SCENARIOS[scenario].kind if scenario in SCENARIOS else ""
        print(f"{scenario:40} {kind:5} {model:22} {mode:10} {'; '.join(cells)}{err}  "
              f"~{row['tokens']} uncached")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--serial", default="emulator-5554")
    ap.add_argument("--models", default="", help="comma list; empty = the model the app has")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--only", default="", help="comma list of scenario names")
    ap.add_argument("--stage", type=int, default=0)
    ap.add_argument("--calibrate", action="store_true",
                    help="also run each scenario with its guarantee removed")
    ap.add_argument("--save-baseline", action="store_true")
    ap.add_argument("--out", default="", help="where the full report goes (default: /tmp)")
    ap.add_argument("--restore", action="store_true", help="put back a leftover snapshot")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    device = Device(args.serial)
    if args.restore:
        device.forward()
        device.restore()
        print("restored")
        return 0

    names = [n for n in args.only.split(",") if n] or list(SCENARIOS)
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        print(f"unknown scenarios: {unknown}", file=sys.stderr)
        return 2
    selected = [SCENARIOS[n] for n in names if not args.stage or SCENARIOS[n].stage == args.stage]

    runner = Runner(device, seed=args.seed)
    runner.begin()
    original_model = device.http_get("/webui/bootstrap").get("model_name")
    models = [m for m in args.models.split(",") if m] or [original_model]
    results = []
    down = 0  # giri di fila col provider giu'
    try:
        for model in models:
            if model != device.http_get("/webui/bootstrap").get("model_name"):
                device.set_model(model)
            for scenario in selected:
                modes = [False] + ([True] if args.calibrate and scenario.calibration else [])
                for calibrated in modes:
                    if calibrated:
                        try:
                            runner.apply_calibration(scenario)
                        except Exception as exc:  # noqa: BLE001 — una taratura rotta, non la suite
                            print(f"[{model}] {scenario.name}: calibration skipped: {exc}")
                            runner.undo_calibration()
                            continue
                    try:
                        for i in range(args.runs):
                            r = runner.run(scenario, model, calibrated=calibrated)
                            results.append(r)
                            down = down + 1 if r.error.startswith("ProviderDownError") else 0
                            if down >= 2:
                                raise SystemExit(f"[{model}] provider unreachable twice in a "
                                                 f"row, stopping: {r.error}")
                            status = r.error or " ".join(
                                f"{c.name}={'ok' if c.ok else 'NO'}" for c in r.checks)
                            print(f"[{model}] {scenario.name}{' (calibrated)' if calibrated else ''}"
                                  f" #{i + 1}: {status}  {r.seconds}s", flush=True)
                    finally:
                        if calibrated:
                            runner.undo_calibration()
    finally:
        try:
            if original_model and device.http_get("/webui/bootstrap").get("model_name") \
                    != original_model:
                device.set_model(original_model)
        finally:
            runner.end()
            _report(results, args)
    return 0


def _report(results, args) -> None:
    table = summarize(results)
    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    print()
    _print_summary(table, baseline.get("rates", {}))
    out_dir = Path(args.out) if args.out else Path("/tmp")
    out = out_dir / f"behaviour-{time.strftime('%Y%m%d-%H%M%S')}.json"
    runs = [r.__dict__ | {"checks": [c.__dict__ for c in r.checks]} for r in results]
    out.write_text(json.dumps(runs, ensure_ascii=False, indent=1, default=str))
    print(f"\nfull report: {out}")
    if args.save_baseline:
        rates = baseline.get("rates", {})
        rates.update({k: {"checks": v["checks"], "runs": v["runs"]} for k, v in table.items()})
        BASELINE.write_text(json.dumps({"rates": rates, "updated": time.strftime("%Y-%m-%d")},
                                       ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        # I giri interi accanto ai tassi: chi vede un tasso cambiare legge le
        # risposte di allora senza rifare il giro. Fuori da git (.gitignore): le
        # risposte citano anche quel che c'e' gia' nel workspace dell'emulatore.
        old_runs = json.loads(BASELINE_RUNS.read_text()) if BASELINE_RUNS.exists() else []
        keys = {(r["scenario"], r["model"], r["calibrated"]) for r in runs}
        kept = [r for r in old_runs if (r["scenario"], r["model"], r["calibrated"]) not in keys]
        BASELINE_RUNS.write_text(json.dumps(kept + runs, ensure_ascii=False, indent=1,
                                            default=str) + "\n")
        print(f"baseline updated: {BASELINE}")


if __name__ == "__main__":
    raise SystemExit(main())
