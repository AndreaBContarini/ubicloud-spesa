#!/usr/bin/env python3
"""Stima la spesa Ubicloud dai job GitHub Actions girati sui runner ubicloud-*.

Ubicloud non espone la fatturazione via API: si ricostruisce dai job.
Ogni job fatturato = minuti arrotondati per eccesso x prezzo al minuto del tipo di runner.
Solo libreria standard. Variabili d'ambiente:
  GH_TOKEN   token con lettura Actions sui repo dell'organizzazione
  GH_ORG     organizzazione GitHub da scansionare
  START_DATE prima data da considerare (YYYY-MM-DD), usata solo al primo giro
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
API = "https://api.github.com"

# Listino ufficiale: github.com/ubicloud/ubicloud config/billing_rates/github.yml (attivo dal 2026-09-01), $/minuto.
RATES = {
    "standard-2": 0.00125, "standard-4": 0.0025, "standard-8": 0.005,
    "standard-16": 0.01, "standard-30": 0.01875, "standard-60": 0.0375,
    "premium-2": 0.002, "premium-4": 0.004, "premium-8": 0.008,
    "premium-16": 0.016, "premium-30": 0.03,
}
LABEL_RE = re.compile(r"^ubicloud(?:-(standard|premium)-(\d+))?(-arm)?(?:-ubuntu-\d+)?$")
RERUN_WINDOW = timedelta(days=7)  # i rerun tengono la data di creazione originale
# Con l'opzione "premium" accesa in console, Ubicloud fattura premium anche i label standard: dal job non si vede.
# Qui i periodi in cui era accesa (UTC, inizio incluso, fine esclusa). Se la si riaccende, aggiungere una riga.
PREMIUM_WINDOWS = [("2026-11-26T00:00:00Z", "2026-11-26T10:54:00Z")]


def gh(path: str, params: dict | None = None) -> dict | list:
    url = path if path.startswith("http") else API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ubicloud-spesa",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def paged(path: str, key: str | None, params: dict) -> list:
    out, page = [], 1
    while True:
        body = gh(path, {**params, "per_page": 100, "page": page})
        items = body[key] if key else body
        out.extend(items)
        if len(items) < 100:
            return out
        page += 1


def family(labels: list[str]) -> str | None:
    for label in labels:
        m = LABEL_RE.match(label)
        if m:
            tier, cpus = m.group(1) or "standard", m.group(2) or "2"
            return f"{tier}-{cpus}" + ("-arm" if m.group(3) else "")
    return None


def rate(fam: str) -> float:
    return RATES[fam.removesuffix("-arm")]  # arm costa come x64


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def load(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def main() -> int:
    org = os.environ["GH_ORG"]
    DATA.mkdir(exist_ok=True)
    state = load(DATA / "state.json", {"last_scan": None, "seen_runs": {}})
    now = datetime.now(timezone.utc)
    if state["last_scan"]:
        since = parse(state["last_scan"]) - RERUN_WINDOW
    else:
        since = datetime.fromisoformat(os.environ.get("START_DATE", now.strftime("%Y-%m-01"))).replace(tzinfo=timezone.utc)

    months: dict[str, dict] = {}

    def month_file(key: str) -> dict:
        if key not in months:
            months[key] = load(DATA / f"{key}.json", {"jobs": {}})
        return months[key]

    repos = [r["name"] for r in paged(f"/orgs/{org}/repos", None, {"type": "all", "sort": "pushed"})
             if not r["archived"] and parse(r["pushed_at"]) >= since]
    new_jobs = 0
    for repo in repos:
        try:
            runs = paged(f"/repos/{org}/{repo}/actions/runs", "workflow_runs",
                         {"created": f">={since.strftime('%Y-%m-%dT%H:%M:%SZ')}"})
        except urllib.error.HTTPError as err:
            print(f"salto {repo}: HTTP {err.code}", file=sys.stderr)
            continue
        for run in runs:
            if run["status"] != "completed":
                continue  # si riprende al prossimo giro
            key = f"{repo}/{run['id']}"
            if state["seen_runs"].get(key, {}).get("attempt") == run["run_attempt"]:
                continue
            for job in paged(f"/repos/{org}/{repo}/actions/runs/{run['id']}/jobs", "jobs", {"filter": "all"}):
                fam = family(job["labels"])
                if not fam or not job["runner_name"] or not job["started_at"] or not job["completed_at"]:
                    continue  # non Ubicloud, o saltato senza macchina
                start, end = parse(job["started_at"]), parse(job["completed_at"])
                if fam.startswith("standard-") and any(parse(a) <= start < parse(b) for a, b in PREMIUM_WINDOWS):
                    fam = fam.replace("standard-", "premium-", 1)
                minutes = max(1, math.ceil((end - start).total_seconds() / 60))
                month_file(start.strftime("%Y-%m"))["jobs"][str(job["id"])] = {
                    "repo": repo, "workflow": run["name"], "job": job["name"],
                    "event": run["event"], "branch": run["head_branch"],
                    "run_id": run["id"], "attempt": job["run_attempt"],
                    "conclusion": job["conclusion"], "runner": fam,
                    "started": job["started_at"], "minutes": minutes,
                    "cost": round(minutes * rate(fam), 6),
                }
                new_jobs += 1
            state["seen_runs"][key] = {"attempt": run["run_attempt"], "created": run["created_at"]}

    cutoff = now - RERUN_WINDOW - timedelta(days=2)
    state["seen_runs"] = {k: v for k, v in state["seen_runs"].items() if parse(v["created"]) >= cutoff}
    state["last_scan"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    for key, content in months.items():
        save(DATA / f"{key}.json", content)
    save(DATA / "state.json", state)
    all_months = sorted(p.stem for p in DATA.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].json"))
    save(DATA / "meta.json", {"updated_at": state["last_scan"], "months": all_months, "rates": RATES})
    print(f"{len(repos)} repo controllati, {new_jobs} job Ubicloud registrati")
    return 0


if __name__ == "__main__":
    sys.exit(main())
