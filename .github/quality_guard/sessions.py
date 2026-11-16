#!/usr/bin/env python3
"""Liste les dossiers de session que Quality Guard doit contrôler.

Le dépôt d'un groupe sert pour toutes les sessions des Samedis de l'IA : un dossier
par session à la racine (``01-agriculture``, ``02-education``…). Seules les sessions
dont le code a changé sont contrôlées. Une modification de l'outillage (``.github/quality_guard``,
workflow) ou un lancement manuel les contrôle toutes ; les README, ``docs/`` et ``notebooks/``
ne déclenchent rien.

Le résultat est écrit en JSON (``["01-agriculture"]``) dans ``$GITHUB_OUTPUT``,
sous la clé ``list``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

LOG = logging.getLogger("quality_guard.sessions")
SESSION = re.compile(r"^\d{2}-[A-Za-z0-9_-]+$")
NO_CODE = re.compile(r"(\.md$|(^|/)(docs|notebooks)/)")
ZERO = "0" * 40
TOOLING = (".github/quality_guard/", ".github/workflows/quality-guard.yml")


def all_sessions() -> list[str]:
    """Dossiers de session présents à la racine du dépôt."""
    return sorted(
        p.name for p in Path(".").iterdir() if p.is_dir() and SESSION.match(p.name)
    )


def changed_files(base: str) -> list[str] | None:
    """Fichiers modifiés depuis ``base`` (``None`` si la comparaison est impossible).

    Args:
        base: Commit de référence (dernier commit avant le push).

    Returns:
        Les chemins modifiés, ou ``None`` pour tout contrôler.
    """
    if not base or base == ZERO:
        return None
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.splitlines() if proc.returncode == 0 else None


def sessions_to_check(files: list[str] | None, existing: list[str]) -> list[str]:
    """Sessions dont le code a changé, ou toutes si l'outillage a changé.

    Args:
        files: Chemins modifiés, ou ``None`` pour tout contrôler.
        existing: Sessions présentes dans le dépôt.

    Returns:
        Les sessions à contrôler, triées.
    """
    if files is None or any(f.startswith(TOOLING) for f in files):
        return existing
    touched = {f.split("/", 1)[0] for f in files if "/" in f and not NO_CODE.search(f)}
    return sorted(touched & set(existing))


def main() -> int:
    """Écrit la liste des sessions à contrôler dans ``$GITHUB_OUTPUT``."""
    logging.basicConfig(format="%(message)s", level=logging.INFO, stream=sys.stdout)
    manual = os.environ.get("EVENT") == "workflow_dispatch"
    files = None if manual else changed_files(os.environ.get("BASE", ""))
    selected = sessions_to_check(files, all_sessions())
    LOG.info(
        "Sessions à contrôler : %s",
        ", ".join(selected) or "aucune (pas de code modifié)",
    )
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"list={json.dumps(selected)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
