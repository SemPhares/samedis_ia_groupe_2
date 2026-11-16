#!/usr/bin/env python3
"""Ramène les dépendances d'une session à une seule liste de versions figées.

Usage, depuis la racine du dépôt :
    python .github/quality_guard/dependencies.py 01-agriculture
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

LOG = logging.getLogger("quality_guard.dependencies")
OUTPUT = Path("reports") / "dependencies.txt"
# -r fichier, -rfichier, --requirement=fichier, et de même pour -c / --constraint.
INCLUDE = re.compile(
    r"^(?P<kind>--requirement|--constraint|-r|-c)[\s=]*(?P<path>[^\s#]+)"
)
EDITABLE = re.compile(r"^(?:--editable|-e)[\s=]*(?P<path>[^\s#]+)")


class ExportError(RuntimeError):
    """Les dépendances déclarées n'ont pas pu être lues."""


def _uv(*args: str) -> list[str]:
    """Lance uv et renvoie les lignes produites.

    Raises:
        ExportError: Si uv échoue (verrou périmé, dépendance introuvable…).
    """
    try:
        proc = subprocess.run(
            ["uv", *args],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as exc:
        raise ExportError(
            "uv introuvable : pip install -r .github/quality_guard/requirements.txt"
        ) from exc
    if proc.returncode != 0:
        errors = [line for line in proc.stderr.splitlines() if "warning:" not in line]
        raise ExportError(f"uv {args[0]} : " + " ".join(errors)[-500:])
    return proc.stdout.splitlines()


def from_uv_lock(session: Path) -> list[str]:
    """Versions figées par ``uv.lock``, groupes de développement compris."""
    return _uv(
        "export",
        "--directory",
        str(session),
        "--frozen",
        "--all-groups",
        "--no-hashes",
        "--no-header",
        "--no-annotate",
        "--no-emit-project",
        "--format",
        "requirements-txt",
    )


def from_poetry_lock(path: Path) -> list[str]:
    """Versions figées par ``poetry.lock`` (paquets venant d'un index uniquement)."""
    packages = tomllib.loads(path.read_text(encoding="utf-8")).get("package", [])
    return [
        f"{p['name']}=={p['version']}"
        for p in packages
        if p.get("source", {}).get("type") in (None, "legacy")
    ]


def from_pipfile_lock(path: Path) -> list[str]:
    """Versions figées par ``Pipfile.lock`` (sections ``default`` et ``develop``)."""
    lock: dict[str, dict[str, dict[str, Any]]] = json.loads(
        path.read_text(encoding="utf-8")
    )
    return [
        f"{name}{spec['version']}"
        for section in ("default", "develop")
        for name, spec in lock.get(section, {}).items()
        if "version" in spec
    ]


def from_pipfile(path: Path) -> list[str]:
    """Dépendances déclarées dans un ``Pipfile`` qui n'a pas encore de verrou."""
    pipfile = tomllib.loads(path.read_text(encoding="utf-8"))
    lines = []
    for section in ("packages", "dev-packages"):
        for name, spec in pipfile.get(section, {}).items():
            version = spec if isinstance(spec, str) else spec.get("version", "*")
            if isinstance(spec, dict) and "version" not in spec:
                continue  # dépendance git ou locale : pas auditable
            lines.append(name if version == "*" else f"{name}{version}")
    return lines


def from_requirements(path: Path) -> list[str]:
    """Contenu de ``requirements.txt`` et de ``requirements-dev.txt`` s'il existe."""
    seen: set[Path] = set()
    files = [path, path.with_name("requirements-dev.txt")]
    return [
        line
        for file in files
        if file.exists()
        for line in _read_requirements(file, seen)
    ]


def _read_requirements(path: Path, seen: set[Path]) -> list[str]:
    """Lignes d'un fichier requirements, fichiers inclus par ``-r`` compris.

    Args:
        path: Fichier à lire.
        seen: Fichiers déjà lus, pour ne pas les recopier deux fois
            (``requirements-dev.txt`` inclut souvent ``requirements.txt``).

    Returns:
        Les lignes utiles, sans commentaires ; un projet éditable (``-e .``)
        est remplacé par les dépendances de son ``pyproject.toml``.

    Raises:
        ExportError: Si un fichier inclus n'existe pas, ou si une dépendance
            éditable n'est pas un dossier local avec un ``pyproject.toml``.
    """
    path = path.resolve()
    if path in seen:
        return []
    seen.add(path)
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if editable := EDITABLE.match(line):
            lines += _editable(path.parent, editable["path"])
            continue
        option = INCLUDE.match(line)
        if option is None:
            lines.append(line)
            continue
        target = path.parent / option["path"]
        if not target.is_file():
            raise ExportError(f"{path.name} inclut {option['path']}, introuvable")
        if option["kind"] in ("-r", "--requirement"):
            lines += _read_requirements(target, seen)
        else:
            lines.append(f"-c {target.resolve().as_posix()}")
    return lines


def _editable(base: Path, target: str) -> list[str]:
    """Dépendances d'un projet installé en mode éditable (``-e .``, ``-e ./lib[dev]``).

    Ignorer la ligne ferait échapper ses dépendances à l'installation et à
    l'audit : seul un dossier local avec un ``pyproject.toml`` est accepté.

    Args:
        base: Dossier du fichier requirements qui contient la ligne.
        target: Cible de ``-e``, telle qu'écrite.

    Returns:
        Les dépendances du projet, au format requirements.

    Raises:
        ExportError: Si la cible est un dépôt git, une URL ou un dossier sans
            ``pyproject.toml``.
    """
    project = base / target.split("[")[0]
    if "://" in target or not (project / "pyproject.toml").is_file():
        raise ExportError(
            f"dépendance éditable non prise en charge : -e {target} "
            "(seul un dossier local avec un pyproject.toml l'est ; "
            "sinon, déclarer la dépendance avec une version)"
        )
    return from_pyproject(project / "pyproject.toml")


def from_pyproject(path: Path) -> list[str]:
    """Dépendances de ``[project]`` résolues par uv quand aucun verrou n'existe."""
    project = tomllib.loads(path.read_text(encoding="utf-8")).get("project", {})
    if not project.get("dependencies") and not project.get("optional-dependencies"):
        return []
    return _uv(
        "pip",
        "compile",
        str(path),
        "--all-extras",
        "--no-header",
        "--no-annotate",
        "--quiet",
    )


def collect(session: Path) -> tuple[str, list[str]]:
    """Comme :func:`find`, mais un fichier mal formé devient une ``ExportError`` lisible.

    Args:
        session: Dossier de la session.

    Returns:
        Le fichier source retenu et les lignes au format requirements.

    Raises:
        ExportError: Si le fichier trouvé ne peut pas être lu.
    """
    try:
        return find(session)
    except (tomllib.TOMLDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ExportError(f"fichier de dépendances mal formé : {exc}") from exc


def find(session: Path) -> tuple[str, list[str]]:
    """Trouve le gestionnaire utilisé par la session et liste ses dépendances.

    Args:
        session: Dossier de la session (répertoire courant du contrôle).

    Returns:
        Le fichier source retenu (ou ``"aucun"``) et les lignes au format requirements.

    Raises:
        ExportError: Si le fichier trouvé ne peut pas être lu.
    """
    if (session / "uv.lock").exists():
        return "uv.lock", from_uv_lock(session)
    if (session / "poetry.lock").exists():
        return "poetry.lock", from_poetry_lock(session / "poetry.lock")
    if (session / "Pipfile.lock").exists():
        return "Pipfile.lock", from_pipfile_lock(session / "Pipfile.lock")
    requirements = session / "requirements.txt"
    if requirements.exists() and (lines := from_requirements(requirements)):
        return "requirements.txt", lines
    if (session / "pyproject.toml").exists():
        return "pyproject.toml", from_pyproject(session / "pyproject.toml")
    if (session / "Pipfile").exists():
        return "Pipfile", from_pipfile(session / "Pipfile")
    return "aucun", []


def export(session: Path) -> tuple[str, Path]:
    """Écrit ``reports/dependencies.txt`` dans la session.

    Args:
        session: Dossier de la session.

    Returns:
        Le fichier source retenu et le chemin du fichier écrit.

    Raises:
        ExportError: Si le fichier trouvé ne peut pas être lu (``dependencies.txt``
            est alors laissé vide).
    """
    output = session / OUTPUT
    output.parent.mkdir(exist_ok=True)
    output.write_text("", encoding="utf-8")
    source, lines = collect(session)
    output.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
    return source, output


def main(argv: list[str]) -> int:
    """Exporte les dépendances du dossier de session passé en argument."""
    logging.basicConfig(format="%(message)s", level=logging.INFO, stream=sys.stdout)
    if len(argv) != 1 or not Path(argv[0]).is_dir():
        LOG.error("Usage : python .github/quality_guard/dependencies.py 01-agriculture")
        return 1
    try:
        source, output = export(Path(argv[0]))
    except ExportError as exc:
        LOG.error("::error title=Dépendances::%s", exc)
        return 1
    count = sum(1 for line in output.read_text(encoding="utf-8").splitlines() if line)
    LOG.info("Dépendances lues dans %s : %d paquet(s) → %s", source, count, output)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
