#!/usr/bin/env python3
"""Quality Guard pour les dépôts des Samedis de l'IA.

Usage, en local comme en CI, depuis la racine du dépôt :
    pip install -r .github/quality_guard/requirements.txt
    python .github/quality_guard/qg.py 01-agriculture
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from dependencies import ExportError
from dependencies import export as export_dependencies

LOG = logging.getLogger("quality_guard")
CONFIG = Path(__file__).resolve().parent / "config.toml"
REPORTS = Path("reports")
RANKS = "ABCDEF"
MAX_DETAILS = 15
SKIPPED_DIRS = frozenset(
    {"notebooks", "docs", "reports", ".venv", "venv", "__pycache__", ".git"}
)

DEFAULTS: dict[str, Any] = {
    "package": "src",
    "tests": "tests",
    "allowed_deps": "",
    "docstrings": 90.0,
    "coverage": 92.5,
    "unit_coverage": 80.0,
    "typing_quality": 99.0,
    "typing_precision": 90.0,
    "complexity": "B",
    "maintainability": "A",
    "require_tests": False,
    "blocking": [
        "formatting",
        "tests",
        "security",
        "dep-security",
    ],
}


@dataclass(frozen=True)
class Config:
    """Paramètres lus dans ``[tool.quality_guard]``, complétés par les valeurs par défaut."""

    package: Path
    tests: Path
    requirements: Path
    allowed_deps: Path | None
    docstrings: float
    coverage: float
    unit_coverage: float
    typing_quality: float
    typing_precision: float
    complexity: str
    maintainability: str
    require_tests: bool
    blocking: frozenset[str]


@dataclass
class Result:
    """Résultat d'un point de contrôle, tel qu'il apparaît dans le tableau."""

    key: str
    label: str
    value: str
    threshold: str
    ok: bool | None  # None : contrôle non configuré ou non mesurable
    details: list[str] = field(default_factory=list)
    fix: str = ""


def load_config(pyproject: Path) -> Config:
    """Fusionne la section ``[tool.quality_guard]`` avec les valeurs par défaut.

    Args:
        pyproject: Chemin du fichier de configuration de Quality Guard.

    Returns:
        La configuration complète.
    """
    raw = dict(DEFAULTS)
    if pyproject.exists():
        raw.update(
            tomllib.loads(pyproject.read_text(encoding="utf-8"))
            .get("tool", {})
            .get("quality_guard", {})
        )
    allowed = str(raw["allowed_deps"])
    return Config(
        package=Path(str(raw["package"])),
        tests=Path(str(raw["tests"])),
        requirements=Path("requirements.txt"),
        allowed_deps=CONFIG.parent / allowed if allowed else None,
        docstrings=float(raw["docstrings"]),
        coverage=float(raw["coverage"]),
        unit_coverage=float(raw["unit_coverage"]),
        typing_quality=float(raw["typing_quality"]),
        typing_precision=float(raw["typing_precision"]),
        complexity=str(raw["complexity"]).upper(),
        maintainability=str(raw["maintainability"]).upper(),
        require_tests=bool(raw["require_tests"]),
        blocking=frozenset(raw["blocking"]),
    )


def run(*args: str, path: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Lance un outil Python dans l'environnement courant (``python -m outil``).

    Args:
        *args: Module de l'outil puis ses arguments.
        path: Dossier à ajouter au PYTHONPATH, pour que les tests importent le code.

    Returns:
        Le processus terminé, sorties capturées.
    """
    # UTF-8 dans les deux sens : sous Windows, l'encodage par défaut (cp1252)
    # ne sait pas décoder les émojis de black et la lecture de sa sortie plante.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if path is not None:
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, [str(path.resolve()), os.environ.get("PYTHONPATH", "")])
        )
    return subprocess.run(
        [sys.executable, "-m", *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
    )


def python_files(*skip: str) -> list[str]:
    """Fichiers Python de la session, hors notebooks, docs et environnements.

    Formatage et sécurité portent sur tout le code de la session, pas seulement
    sur ``src/``.

    Args:
        *skip: Dossiers à écarter en plus (ex. ``"tests"`` pour bandit).

    Returns:
        Les chemins relatifs à la session, triés.
    """
    ignored = SKIPPED_DIRS.union(skip)
    return sorted(
        str(p) for p in Path(".").rglob("*.py") if not ignored.intersection(p.parts)
    )


def rel(path: str) -> str:
    """Chemin relatif à la racine du dépôt, plus lisible dans le résumé."""
    return os.path.relpath(path)


def pct(value: float) -> str:
    """Formate un pourcentage à la française (``92,5 %``)."""
    return f"{value:.1f} %".replace(".", ",")


# --------------------------------------------------------------------- style


def check_formatting(cfg: Config) -> Result:
    """FORMATTING : le code respecte black, sans configuration."""
    proc = run("black", "--check", "--config", str(CONFIG), *python_files())
    files = [
        rel(line.removeprefix("would reformat "))
        for line in proc.stderr.splitlines()
        if line.startswith("would reformat")
    ]
    ok = proc.returncode == 0
    value = "100 %" if ok else f"{len(files)} fichier(s) à reformater"
    if not ok and not files:  # black n'a pas pu lire le code ou la config
        value, files = (
            "black n'a pas pu s'exécuter",
            proc.stderr.strip().splitlines()[-1:],
        )
    return Result(
        "formatting", "Formatage (black)", value, "100 %", ok, files, "black ."
    )


def check_isort(cfg: Config) -> Result:
    """ISORT : les imports sont triés, profil compatible black."""
    proc = run("isort", "--check-only", "--profile", "black", *python_files())
    files = [
        rel(line.split()[1])
        for line in proc.stderr.splitlines()
        if line.startswith("ERROR:")
    ]
    ok = proc.returncode == 0
    value = "100 %" if ok else f"{len(files)} fichier(s) à trier"
    return Result(
        "isort",
        "Tri des imports (isort)",
        value,
        "100 %",
        ok,
        files,
        "isort --profile black .",
    )


def check_layout(cfg: Config) -> Result:
    """LAYOUT : le code va dans ``src/`` et les tests dans ``tests/`` (conseillé).

    Le code rangé ailleurs reste contrôlé par black et bandit, mais échappe aux
    objectifs de conception (docstrings, typage, complexité), qui visent ``src/``.
    """
    outside = [
        f
        for f in python_files(cfg.tests.name)
        if not Path(f).is_relative_to(cfg.package) and f != "conftest.py"
    ]
    return Result(
        "layout",
        f"Rangement (code dans {cfg.package}/)",
        "100 %" if not outside else f"{len(outside)} fichier(s) hors {cfg.package}/",
        f"tout dans {cfg.package}/ ou {cfg.tests}/",
        not outside,
        outside,
        f"déplacer ces fichiers dans {cfg.package}/ (ou {cfg.tests}/ pour les tests)",
    )


# -------------------------------------------------------- auto-documentation


def check_docstrings(cfg: Config) -> Result:
    """DOCSTRINGS : part des fonctions, classes et méthodes documentées."""
    proc = run(
        "interrogate", "-c", str(CONFIG), "-vv", "--fail-under", "0", str(cfg.package)
    )
    found = re.search(r"actual: ([\d.]+)%", proc.stdout)
    covered = float(found.group(1)) if found else 0.0
    missed, current = [], ""
    for cells in (
        line.strip("| ").split("|")
        for line in proc.stdout.splitlines()
        if line.startswith("| ")
    ):
        name, state = cells[0].strip(), cells[-1].strip()
        current = name if name.endswith(".py") else current
        if state == "MISSED":
            missed.append(f"{current} · {name}")
    return Result(
        "docstrings",
        "Docstrings (interrogate)",
        pct(covered),
        f"≥ {pct(cfg.docstrings)}",
        covered >= cfg.docstrings,
        missed,
        "ajouter une docstring Google (le pourquoi) aux éléments listés",
    )


def logical_lines(cfg: Config) -> int:
    """Nombre de lignes logiques du code (radon raw), base du ratio de typage."""
    data = json.loads(run("radon", "raw", "-j", str(cfg.package)).stdout or "{}")
    return sum(int(v.get("lloc", 0)) for v in data.values() if isinstance(v, dict))


def check_typing(cfg: Config) -> list[Result]:
    """TYPING-QUALITY et TYPING-PRECISION, à partir d'un seul passage de mypy."""
    report_dir = REPORTS / "mypy"
    proc = run(
        "mypy",
        "--config-file",
        str(CONFIG),
        "--txt-report",
        str(report_dir),
        str(cfg.package),
    )
    errors = [line for line in proc.stdout.splitlines() if ": error:" in line]
    lloc = logical_lines(cfg)
    quality = 100.0 * (1 - len(errors) / lloc) if lloc else 100.0
    index = report_dir / "index.txt"
    found = re.search(
        r"\|\s*Total\s*\|\s*([\d.]+)% imprecise",
        index.read_text() if index.exists() else "",
    )
    precision = 100.0 - float(found.group(1)) if found else 0.0
    imprecise = [
        f"{module} — {100 - float(value):.0f} % précis"
        for module, value in re.findall(
            r"\|\s*([\w.]+)\s*\|\s*([\d.]+)% imprecise",
            index.read_text() if index.exists() else "",
        )
        if module != "Total" and 100 - float(value) < cfg.typing_precision
    ]
    return [
        Result(
            "typing-quality",
            "Typage, erreurs (mypy)",
            f"{pct(quality)} · {len(errors)} erreur(s)",
            f"≥ {pct(cfg.typing_quality)}",
            quality >= cfg.typing_quality,
            errors,
            "mypy src",
        ),
        Result(
            "typing-precision",
            "Typage, précision (mypy)",
            pct(precision),
            f"≥ {pct(cfg.typing_precision)}",
            precision >= cfg.typing_precision,
            imprecise,
            "annoter les fonctions des modules listés, éviter Any",
        ),
    ]


# ----------------------------------------------------------------- simplicité


def check_complexity(cfg: Config) -> Result:
    """COMPLEXITY : aucune fonction ni méthode au-delà de la lettre autorisée."""
    data = json.loads(run("radon", "cc", "-j", str(cfg.package)).stdout or "{}")
    limit = RANKS.index(cfg.complexity)
    blocks = [
        (path, b)
        for path, items in data.items()
        if isinstance(items, list)
        for b in items
    ]
    functions = [
        (path, b) for path, b in blocks if b.get("type") in ("function", "method")
    ]
    worst = max((b["rank"] for _, b in functions), default="A")
    offenders = [
        f"{path}:{b['lineno']} {b['name']} — rang {b['rank']} (complexité {b['complexity']})"
        for path, b in functions
        if RANKS.index(b["rank"]) > limit
    ]
    return Result(
        "complexity",
        "Complexité (radon cc)",
        f"pire rang : {worst}",
        f"A à {cfg.complexity}",
        not offenders,
        offenders,
        "découper les fonctions signalées",
    )


def check_maintainability(cfg: Config) -> Result:
    """MAINTAINABILITY : chaque fichier au rang autorisé ou mieux."""
    data = json.loads(run("radon", "mi", "-j", str(cfg.package)).stdout or "{}")
    limit = RANKS.index(cfg.maintainability)
    files = {path: v for path, v in data.items() if isinstance(v, dict) and "rank" in v}
    worst = max((v["rank"] for v in files.values()), default="A")
    offenders = [
        f"{path} — rang {v['rank']} (indice {v['mi']:.0f})"
        for path, v in files.items()
        if RANKS.index(v["rank"]) > limit
    ]
    return Result(
        "maintainability",
        "Maintenabilité (radon mi)",
        f"pire rang : {worst}",
        f"A à {cfg.maintainability}",
        not offenders,
        offenders,
        "alléger les fichiers signalés",
    )


# ------------------------------------------------------------------ stabilité


@dataclass(frozen=True)
class PytestRun:
    """Résultats utiles d'un passage de pytest."""

    total: int
    failed: list[str]
    coverage: float


def run_pytest(cfg: Config, tag: str, marker: str | None = None) -> PytestRun:
    """Lance pytest avec couverture et lit les rapports JUnit et JSON.

    Args:
        cfg: Configuration du dépôt.
        tag: Suffixe des fichiers de rapport.
        marker: Expression de sélection ``-m`` (ex. ``"not integration"``).

    Returns:
        Le nombre de tests, les tests en échec et la couverture.
    """
    cov, junit = REPORTS / f"coverage-{tag}.json", REPORTS / f"junit-{tag}.xml"
    args = [
        "pytest",
        "-c",
        str(CONFIG),
        "--rootdir",
        ".",
        "-q",
        "-p",
        "no:cacheprovider",
        f"--cov={cfg.package}",
        f"--cov-report=json:{cov}",
        f"--junitxml={junit}",
    ]
    proc = run(*args, *(["-m", marker] if marker else []), path=cfg.package)
    total, failed = 0, []
    if junit.exists():
        # Rapport écrit à l'instant par pytest sur le runner : pas une donnée externe.
        for case in ET.parse(junit).getroot().iter("testcase"):  # nosec B314
            total += 1
            if case.find("failure") is not None or case.find("error") is not None:
                failed.append(f"{case.get('classname')}::{case.get('name')}")
    if not total and proc.returncode not in (0, 5):  # 5 : aucun test trouvé
        last = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or ["?"]
        total, failed = 1, [f"pytest n'a pas pu démarrer : {last[0]}"]
    coverage = (
        json.loads(cov.read_text())["totals"]["percent_covered"]
        if cov.exists()
        else 0.0
    )
    return PytestRun(total, failed, float(coverage))


def check_tests(cfg: Config) -> list[Result]:
    """TESTS, COVERAGE et UNIT-COVERAGE (les tests d'intégration portent le marqueur ``integration``)."""
    full = run_pytest(cfg, "all")
    unit = run_pytest(cfg, "unit", "not integration")
    passed = full.total - len(full.failed)
    tests_ok: bool | None = not full.failed if full.total else None
    if not full.total and cfg.require_tests:
        tests_ok = False
    return [
        Result(
            "tests",
            "Tests (pytest)",
            (
                f"{passed}/{full.total} verts"
                if full.total
                else "aucun test pour l'instant"
            ),
            "100 % verts",
            tests_ok,
            full.failed,
            "pytest -x   # s'arrête au premier échec",
        ),
        Result(
            "coverage",
            "Couverture totale",
            pct(full.coverage),
            f"≥ {pct(cfg.coverage)}",
            full.coverage >= cfg.coverage,
            fix="pytest --cov=src --cov-report=term-missing",
        ),
        Result(
            "unit-coverage",
            "Couverture unitaire",
            pct(unit.coverage),
            f"≥ {pct(cfg.unit_coverage)}",
            unit.coverage >= cfg.unit_coverage,
            fix='pytest -m "not integration" --cov=src --cov-report=term-missing',
        ),
    ]


def requirement_names(path: Path) -> list[str]:
    """Noms normalisés des paquets déclarés dans un fichier requirements."""
    if not path.exists():
        return []
    lines = (
        line.split("#")[0].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
    )
    names = (
        re.split(r"[\s<>=!~\[;@]", line, maxsplit=1)[0]
        for line in lines
        if line and not line.startswith("-")
    )
    return [re.sub(r"[-_.]+", "-", name).lower() for name in names if name]


def check_security(cfg: Config) -> Result:
    """SECURITY : aucune alerte bandit de gravité moyenne ou haute dans le code (``-ll``)."""
    label, threshold = "Sécurité du code (bandit)", "0 alerte moyenne ou haute"
    fix = "bandit -r . -ll -x ./tests   # puis corriger chaque ligne signalée"
    report = REPORTS / "bandit.json"
    files = python_files(cfg.tests.name)
    if not files:
        return Result("security", label, "aucun code hors tests", threshold, None)
    report.unlink(missing_ok=True)  # pas de rapport périmé d'un passage précédent
    proc = run(
        "bandit",
        *files,
        "-ll",
        "-c",
        str(CONFIG),
        "-f",
        "json",
        "-o",
        str(report),
        "-q",
    )
    # bandit sort en 0 (rien), 1 (alertes) ; tout autre code ou l'absence de
    # rapport veut dire que l'analyse n'a pas eu lieu : ce n'est pas un succès.
    if proc.returncode not in (0, 1) or not report.exists():
        last = (proc.stderr + proc.stdout).strip().splitlines()[-5:]
        return Result(
            "security",
            label,
            "bandit n'a pas pu s'exécuter",
            threshold,
            False,
            last,
            fix,
        )
    data = json.loads(report.read_text())
    found = [
        f"{rel(i['filename'])}:{i['line_number']} {i['test_id']} — {i['issue_text']} ({i['issue_severity'].lower()})"
        for i in data.get("results", [])
    ]
    # Fichier illisible (erreur de syntaxe…) : bandit le saute sans alerte.
    skipped = [
        f"{rel(e['filename'])} — non analysé : {e['reason']}"
        for e in data.get("errors", [])
    ]
    value = "aucune alerte" if not found else f"{len(found)} alerte(s)"
    if skipped:
        value += f", {len(skipped)} fichier(s) non analysé(s)"
    return Result(
        "security",
        label,
        value,
        threshold,
        not found and not skipped,
        found + skipped,
        fix,
    )


def check_dep_security(cfg: Config, export_error: str | None = None) -> Result:
    """DEP-SECURITY : aucune faille connue dans les dépendances déclarées (pip-audit).

    Args:
        cfg: Configuration, avec la liste des dépendances exportées.
        export_error: Message si les dépendances de la session n'ont pas pu être lues.

    Returns:
        Le résultat du contrôle.
    """
    if export_error:
        return Result(
            "dep-security",
            "Failles (pip-audit)",
            "dépendances illisibles",
            "0 faille",
            False,
            [export_error],
            "corriger le fichier de dépendances (uv.lock, pyproject.toml, Pipfile…)",
        )
    if not requirement_names(cfg.requirements):
        return Result(
            "dep-security",
            "Failles (pip-audit)",
            "aucune dépendance déclarée",
            "0 faille",
            True,
        )
    report = REPORTS / "pip-audit.json"
    proc = run(
        "pip_audit",
        "-r",
        str(cfg.requirements),
        "--format",
        "json",
        "--output",
        str(report),
        "--progress-spinner",
        "off",
    )
    if proc.returncode not in (0, 1) or not report.exists():
        return Result(
            "dep-security",
            "Failles (pip-audit)",
            "audit impossible",
            "0 faille",
            False,
            proc.stderr.splitlines()[-5:],
            "vérifier le fichier de dépendances de la session",
        )
    deps = json.loads(report.read_text()).get("dependencies", [])
    # pip-audit peut renvoyer la même faille plusieurs fois pour un paquet.
    vulns = list(
        dict.fromkeys(
            f"{d['name']} {d.get('version', '')} — {v['id']} (corrigé en {', '.join(v.get('fix_versions', [])) or '?'})"
            for d in deps
            for v in d.get("vulns", [])
        )
    )
    value = "aucune faille" if not vulns else f"{len(vulns)} faille(s)"
    return Result(
        "dep-security",
        "Failles (pip-audit)",
        value,
        "0 faille",
        not vulns,
        vulns,
        "monter les versions indiquées (requirements.txt, pyproject.toml, Pipfile…)",
    )


def check_dep_compliance(cfg: Config) -> Result:
    """DEP-COMPLIANCE : dépendances limitées à une liste blanche, si elle est configurée."""
    if cfg.allowed_deps is None or not cfg.allowed_deps.exists():
        return Result(
            "dep-compliance",
            "Dépendances autorisées",
            "liste blanche non configurée",
            "100 %",
            None,
        )
    allowed = set(requirement_names(cfg.allowed_deps))
    outside = [
        name for name in requirement_names(cfg.requirements) if name not in allowed
    ]
    value = "100 %" if not outside else f"{len(outside)} hors liste"
    return Result(
        "dep-compliance",
        "Dépendances autorisées",
        value,
        "100 %",
        not outside,
        outside,
        "demander l'ajout au mentor",
    )


# ------------------------------------------------------------------- synthèse


def status(result: Result, blocking: frozenset[str]) -> str:
    """Pictogramme du statut : ✅ conforme, ❌ bloquant, ⚠️ à améliorer, ➖ non mesuré."""
    if result.ok is None:
        return "➖"
    if result.ok:
        return "✅"
    return "❌" if result.key in blocking else "⚠️"


def details(result: Result, blocking: frozenset[str]) -> list[str]:
    """Bloc dépliable qui liste les écarts d'un contrôle et la façon de les corriger."""
    shown = [f"- `{d}`" for d in result.details[:MAX_DETAILS]]
    hidden = len(result.details) - MAX_DETAILS
    more = [f"- … et {hidden} autre(s)"] if hidden > 0 else []
    fix = [f"Pour corriger : `{result.fix}`"] if result.fix else []
    summary = f"<details><summary>{status(result, blocking)} {result.label}</summary>"
    return [summary, "", *shown, *more, "", *fix, "</details>", ""]


def render(results: list[Result], blocking: frozenset[str]) -> str:
    """Construit le résumé Markdown : tableau, verdict, puis détails des écarts."""
    rows = [
        f"| {r.label} | {r.value} | {r.threshold} | {'oui' if r.key in blocking else 'non'} | {status(r, blocking)} |"
        for r in results
    ]
    failed = [r for r in results if r.ok is False and r.key in blocking]
    verdict = (
        "✅ **Tous les contrôles obligatoires passent.**"
        if not failed
        else f"❌ **{len(failed)} contrôle(s) obligatoire(s) en échec.**"
    )
    lines = [
        "## Quality Guard",
        "",
        "| Contrôle | Mesure | Seuil | Obligatoire | Statut |",
        "|---|---|---|---|---|",
        *rows,
        "",
        verdict,
        "",
    ]
    for r in results:
        if r.ok is False:
            lines += details(r, blocking)
    return "\n".join(lines)


def annotate(results: list[Result], blocking: frozenset[str], session: str) -> None:
    """Remonte les écarts dans l'onglet Actions (commandes GitHub Actions)."""
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    for r in results:
        if r.ok is False:
            level = "error" if r.key in blocking else "warning"
            sys.stdout.write(
                f"::{level} title=Quality Guard · {session}::{r.label} : {r.value} (seuil {r.threshold})\n"
            )


SESSION = re.compile(r"^\d{2}-[A-Za-z0-9_-]+$")


def sessions_found() -> list[str]:
    """Dossiers de session présents à la racine du dépôt (``01-agriculture``…)."""
    return sorted(
        p.name for p in Path(".").iterdir() if p.is_dir() and SESSION.match(p.name)
    )


def run_checks(cfg: Config) -> list[Result]:
    """Lance tous les contrôles d'une session (répertoire courant).

    Args:
        cfg: Configuration de Quality Guard.

    Returns:
        Les résultats, dans l'ordre du tableau.
    """
    export_error = None
    try:
        source, deps = export_dependencies(Path("."))
        LOG.info("Dépendances lues dans : %s", source)
    except ExportError as exc:
        export_error, deps = str(exc), REPORTS / "dependencies.txt"
    cfg = replace(cfg, requirements=deps)
    # Les objectifs de conception visent src/ : tant qu'il est vide, ils ne sont pas affichés.
    design = []
    if any(cfg.package.rglob("*.py")):
        design = [
            check_docstrings(cfg),
            *check_typing(cfg),
            check_complexity(cfg),
            check_maintainability(cfg),
        ]
    return [
        check_formatting(cfg),
        check_isort(cfg),
        check_layout(cfg),
        *design,
        *check_tests(cfg),
        check_security(cfg),
        check_dep_security(cfg, export_error),
        check_dep_compliance(cfg),
    ]


def publish(results: list[Result], blocking: frozenset[str], session: str) -> None:
    """Affiche le tableau, l'ajoute au résumé du job et annote les écarts."""
    summary = render(results, blocking).replace(
        "## Quality Guard", f"## Quality Guard · {session}", 1
    )
    LOG.info(summary)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(summary + "\n")
    annotate(results, blocking, session)


def main(argv: list[str]) -> int:
    """Contrôle un dossier de session et renvoie 1 si un contrôle bloquant échoue.

    Args:
        argv: Arguments de la ligne de commande ; le premier est le dossier de session.

    Returns:
        Le code de sortie : 0 si tous les contrôles bloquants passent, 1 sinon.
    """
    if isinstance(sys.stdout, io.TextIOWrapper):
        # Le résumé contient des émojis : cp1252 (Windows, sortie redirigée) les refuse.
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(format="%(message)s", level=logging.INFO, stream=sys.stdout)
    if len(argv) != 1 or not Path(argv[0]).is_dir():
        LOG.error(
            "Indiquez le dossier de session à contrôler, par exemple : python %s 01-agriculture",
            ".github/quality_guard/qg.py",
        )
        LOG.error("Sessions trouvées : %s", ", ".join(sessions_found()) or "aucune")
        return 1
    session = Path(argv[0]).name
    os.chdir(argv[0])
    if not python_files():
        LOG.info("%s : pas encore de code Python, rien à contrôler.", session)
        return 0
    cfg = load_config(CONFIG)
    REPORTS.mkdir(exist_ok=True)
    results = run_checks(cfg)
    publish(results, cfg.blocking, session)
    return 1 if any(r.ok is False and r.key in cfg.blocking for r in results) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
