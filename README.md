# [Nom de l'équipe] · Samedis de l'IA

> Groupe [n°]

## Un seul dépôt pour les six mois

Ce dépôt accompagne votre groupe pendant **tout le cycle des Samedis de l'IA, soit six mois et six sessions**. Chaque session porte sur une filière différente et aboutit à **un cas d'usage différent**, avec sa propre solution et sa propre démo.

Chaque session a donc **son dossier**, à la racine du dépôt, numéroté dans l'ordre :

```
01-agriculture/     première session : agriculture
02-<filière>/       deuxième session, créée au début du cycle suivant
…
06-<filière>/
```

Un dossier de session est un projet complet et indépendant : son cas d'usage, son code, ses tests, ses dépendances, sa présentation. 
Le dossier `.github/` à la racine contient le contrôle qualité, commun à toutes les sessions.

## L'équipe

| Nom | Rôle | GitHub |
|---|---|---|
| | | |

Mentor : [nom]

## Les sessions

| N° | Session | Cas d'usage  |
|---|---|---|
| 01 | [Agriculture](01-agriculture/) | [le cas d'usage en une phrase] | |

Ajoutez une ligne à chaque nouvelle session.


## Démarrer une nouvelle session

1. Créez le dossier `NN-filiere` (ex. `02-education`, sans accent ni espace : sinon il n'est pas contrôlé) en recopiant la structure de `01-agriculture` : `README.md`, les dépendances (`requirements.txt`, ou uv, Poetry, Pipenv), `src/`, `tests/`, `docs/`.
2. Remplissez son `README.md` avec le cas d'usage de la session.
3. Ajoutez la session au tableau ci-dessus.

Les sessions déjà terminées ne bougent plus : leur version présentée en plénière est marquée par un tag (`agriculture-v1.0`, `education-v1.0`…).


## Qualité

Chaque merge vers `main` passe par Quality Guard, qui ne contrôle que les sessions modifiées : voir [`.github/quality_guard/README.md`](.github/quality_guard/README.md).
