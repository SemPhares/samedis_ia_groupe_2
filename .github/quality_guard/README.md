# Quality Guard

**Chaque push sur `main` lance un contrôle qualité automatique de votre code.** Il ne bloque rien : il indique ce qui est à corriger.

## Où voir le résultat

Onglet **Actions** du dépôt, ou l'icône à côté du dernier commit : ✅ tout va bien, ❌ quelque chose est à corriger. Cliquez dessus pour voir le tableau de votre session.

Seules les sessions dont le code a changé sont contrôlées. Les README, `docs/` et `notebooks/` ne lancent rien.

## Ce qui donne un ❌

| Contrôle | Pour corriger |
|---|---|
| Le code n'est pas formaté avec **black** | `black .` dans le dossier de la session |
| Un test échoue (**pytest**) | `pytest -x` |
| Un problème de sécurité dans le code (**bandit**) : `eval`, `shell=True`, mot de passe en dur… | corriger la ligne indiquée |
| Une faille connue dans une dépendance (**pip-audit**) | monter la version indiquée dans votre fichier de dépendances |
| Le fichier de dépendances est illisible (`pyproject.toml`, `Pipfile`… mal écrit) | corriger le fichier indiqué |

Formatage et sécurité portent sur **tout le code Python de la session**, où qu'il soit, sauf `notebooks/` et `docs/`.

Le tableau affiche aussi des objectifs en ⚠️ : code rangé dans `src/`, imports triés, et pour le code de `src/` docstrings, typage, complexité, couverture de tests. Ce sont de bonnes pratiques à viser, sans obligation.

## Vos dépendances

Gérez-les avec l'outil de votre choix, dans le dossier de la session : **uv** (`uv.lock`), **Poetry** (`poetry.lock`), **Pipenv** (`Pipfile.lock`) ou **pip** (`requirements.txt`). Quality Guard reconnaît le fichier, installe les dépendances avant les tests et vérifie leurs failles. Pensez à versionner le fichier de verrouillage (`uv.lock`, `poetry.lock`, `Pipfile.lock`).

Avec pip, les lignes `-r autre-fichier.txt` et `-c contraintes.txt` sont suivies, et `-e .` est remplacé par les dépendances du `pyproject.toml` visé. Une dépendance éditable venant de git ou d'une URL n'est pas prise en charge : déclarez-la avec une version.

## Vérifier avant de pousser

```bash
pip install -r .github/quality_guard/requirements.txt
cd 01-agriculture && black . && cd ..
python .github/quality_guard/qg.py 01-agriculture
```

Facultatif, pour formater et vérifier la sécurité à chaque commit : `pip install pre-commit && pre-commit install -c .github/quality_guard/pre-commit-config.yaml`.

## Règles du dépôt

- Une session = un dossier `NN-filiere`, avec le code dans `src/` et les tests dans `tests/`. Du code rangé ailleurs est signalé en ⚠️, sans bloquer : il reste contrôlé par black et bandit, mais échappe aux objectifs de `src/`.
- Les dépendances dans le dossier de la session, avec l'outil de votre choix (voir plus haut).
- **Python 3.14**, la version de la CI (3.14.10).
- Aucun secret dans le code : un fichier `.env`, jamais versionné.
- Ne modifiez pas le dossier `.github/`.
