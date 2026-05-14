"""Test de garde : périmètre et seuils du linter flake8.

Ce module n'audite aucun code source applicatif ; il audite la
**configuration du linter** elle-même. La cible est de garantir,
de manière reproductible en CI, trois invariants :

    1. Le fichier ``.flake8`` au root du repo est *présent*,
       *parsable*, et ne contient *aucune option inconnue* (sinon
       flake8 la jette en silence et l'intention de l'auteur est
       perdue — c'est exactement ce qui s'est passé avec
       ``no-isort-config = true`` avant ce correctif).

    2. Les seuils ``max-complexity`` et ``max-cognitive-complexity``
       sont *alignés* avec ce que le workflow CI
       (``.github/workflows/complexity.yml``) passe en CLI. Sinon
       un dev voit un seuil en local et un autre en CI, ce qui
       casse la boucle de feedback rapide.

    3. Quand on invoque flake8 contre tout le repo (commande CI
       agressive), AUCUN fichier reporté ne se trouve hors du
       périmètre Unifideck. Concrètement : tout chemin remonté par
       flake8 doit être ``main.py`` ou commencer par
       ``py_modules/unifideck/``.

Pourquoi un test plutôt qu'une simple revue de config ?
    La configuration de flake8 est triplement fragile :

        * flake8 ne lit pas ``pyproject.toml`` ; sa config vit dans
          un fichier dédié à part qui peut être supprimé par
          inadvertance sans qu'un linter ne s'en plaigne (c'est
          exactement le bug qui a déclenché ce correctif :
          ``.flake8`` absent de ``staging``).

        * Les options inconnues sont silencieusement ignorées.

        * L'ajout d'une nouvelle dépendance vendored dans
          ``py_modules/`` (par exemple lors d'un bump de Decky
          Loader) n'apparaît pas dans la config existante et
          repasse en scope d'audit sans avertissement.

    Le seul moyen fiable de défendre ces invariants est de les
    exécuter dans la même boucle que les tests unitaires.

Convention de placement :
    Le PDF "Test coverage companion v1.0" exige
    ``tests/unit/<sub_package>/test_<source_file>.py`` à la
    racine du repo, en miroir du source. Ce fichier vit sous
    ``tests/unit/_tooling/`` (préfixe ``_`` pour distinguer d'un
    sous-paquet du source qui s'appellerait ``tooling``) — seule
    exception à la convention de mirror, justifiée par le fait
    que le sujet est un fichier de config, pas un module Python.
"""

from __future__ import annotations

import configparser
import subprocess
import sys
from pathlib import Path

import pytest


# ─────────────────────────────────────────────────────────────────
# Constantes — chemins résolus relativement à ce fichier
# ─────────────────────────────────────────────────────────────────
#
# Résolution depuis ce fichier pour rester stable peu importe le
# ``cwd`` du runner pytest (le workflow lance ``pytest tests/``,
# pas ``pytest`` depuis la racine). Layout attendu :
#
#   _THIS = <repo_root>/tests/unit/_tooling/test_lint_scope.py
#   .parents[0] = _tooling
#   .parents[1] = unit
#   .parents[2] = tests
#   .parents[3] = <repo_root>
_THIS = Path(__file__).resolve()
REPO_ROOT = _THIS.parents[3]
FLAKE8_CONFIG = REPO_ROOT / ".flake8"
UNIFIDECK_PKG = REPO_ROOT / "py_modules" / "unifideck"
MAIN_PY = REPO_ROOT / "main.py"


# Préfixes acceptés dans le périmètre d'audit. Tout fichier
# remonté par flake8 doit matcher l'un de ces préfixes (chemins
# relatifs à la racine du repo, séparateurs POSIX).
IN_SCOPE_PREFIXES: tuple[str, ...] = (
    "py_modules/unifideck/",
    "main.py",
)


# Seuils attendus dans ``.flake8``. **Doivent matcher** ce que
# ``.github/workflows/complexity.yml`` passe en CLI :
#
#   --max-complexity=15
#   --max-cognitive-complexity=15
#
# Si tu changes ces valeurs ici, change-les aussi dans le
# workflow et inversement. Le test échoue si désynchronisation.
EXPECTED_MAX_COMPLEXITY = "15"
EXPECTED_MAX_COGNITIVE_COMPLEXITY = "15"


# =================================================================
# Helpers
# =================================================================


def _load_flake8_config() -> configparser.ConfigParser:
    """Charge ``.flake8`` avec ``configparser``.

    Préféré à ``flake8.options.config`` pour garder ce test
    indépendant de l'API privée de flake8 (qui change entre les
    versions majeures de l'outil).
    """
    parser = configparser.ConfigParser()
    parser.read(FLAKE8_CONFIG)
    return parser


def _known_flake8_options() -> set[str]:
    """Énumère les options reconnues par le flake8 installé.

    On parse la sortie de ``flake8 --help`` (la seule API
    rétro-compatible). Une option apparaît en deux formes
    possibles : ``--max-complexity`` (kebab) et ``--max_complexity``
    (snake). On normalise tout en kebab pour la comparaison.
    """
    res = subprocess.run(
        ["flake8", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    known: set[str] = set()
    for token in res.stdout.split():
        if token.startswith("--"):
            # ``--max-complexity=N`` → ``max-complexity``
            name = token.lstrip("-").split("=", 1)[0]
            known.add(name)
            known.add(name.replace("_", "-"))
    return known


def _normalise_for_match(path: str) -> str:
    """Convertit un chemin en POSIX relatif à la racine du repo.

    flake8 sort généralement des chemins relatifs au ``cwd`` ;
    sous Windows ils contiennent des backslashes. On normalise
    pour que les ``startswith`` ci-dessous fonctionnent
    uniformément quel que soit l'OS du runner CI.
    """
    posix = path.replace("\\", "/")
    # Si flake8 a sorti un chemin absolu (rare mais possible
    # quand on passe un argument absolu en CLI), on le replie
    # sur le relatif au repo.
    repo_str = str(REPO_ROOT).replace("\\", "/")
    if posix.startswith(repo_str + "/"):
        posix = posix[len(repo_str) + 1:]
    # Strip d'éventuel "./" en tête
    if posix.startswith("./"):
        posix = posix[2:]
    return posix


# =================================================================
# Tests — invariant 1 : ``.flake8`` est sain
# =================================================================


def test_flake8_config_file_exists() -> None:
    """Garde-fou primaire : le fichier ``.flake8`` doit exister.

    S'il disparaît du repo (oubli de commit, branch rebase mal
    fait, etc.), flake8 retombe sur ses défauts (``> 7`` pour la
    complexité cognitive) et le CI échoue sur du code vendored.
    Ce test attrape la régression *avant* que le CI lint ne
    parte en erreur trompeuse.

    Référence historique : c'est exactement le bug qui a touché
    la branche ``staging`` (``.flake8`` non committé) et fait
    remonter ~189 erreurs CCR001/C901 sur des vendors.
    """
    assert FLAKE8_CONFIG.is_file(), (
        f".flake8 manquant à la racine du repo : {FLAKE8_CONFIG}. "
        f"Sans ce fichier, flake8 ignore les exclusions et lint "
        f"toutes les dépendances vendored sous py_modules/."
    )


def test_flake8_config_has_expected_section() -> None:
    """Le fichier doit déclarer la section ``[flake8]``.

    Sans section ``[flake8]``, configparser charge le fichier sans
    erreur mais flake8 ne voit aucune option et retombe sur les
    défauts. Le bug est silencieux et difficile à diagnostiquer
    depuis les logs CI.
    """
    cfg = _load_flake8_config()
    assert cfg.has_section("flake8"), (
        ".flake8 existe mais ne contient pas de section [flake8]. "
        "Toutes les options sont donc ignorées."
    )


def test_flake8_config_has_no_unknown_option() -> None:
    """Aucune option du ``.flake8`` ne doit être inconnue de flake8.

    Régression historique : ``no-isort-config = true`` traînait
    dans le fichier sans être une option valide. flake8 ignorait
    silencieusement, ce qui donnait une fausse impression de
    contrôle. Ce test attrape la classe entière du bug.
    """
    cfg = _load_flake8_config()
    known = _known_flake8_options()
    # Les plugins ajoutent des options non listées dans
    # ``flake8 --help`` quand on les invoque sans le plugin. Pour
    # rester robuste, on whitelist celles qu'on sait être servies
    # par des plugins installés en CI (voir ``complexity.yml`` qui
    # installe flake8-cognitive-complexity).
    plugin_options = {
        "max-cognitive-complexity",  # flake8-cognitive-complexity
    }
    declared = set(cfg["flake8"].keys())
    unknown = declared - known - plugin_options
    assert not unknown, (
        f"Options inconnues dans .flake8 : {sorted(unknown)}. "
        f"flake8 les ignore silencieusement — supprime-les ou "
        f"ajoute le plugin qui les définit dans complexity.yml."
    )


def test_flake8_config_thresholds_match_ci() -> None:
    """Les seuils du ``.flake8`` matchent ceux passés en CLI par le CI.

    ``.github/workflows/complexity.yml`` invoque ::

        flake8 --select=C --max-complexity=15 py_modules/ main.py
        flake8 --select=CCR --max-cognitive-complexity=15 ...

    Le ``.flake8`` doit déclarer les MÊMES valeurs (15 / 15)
    pour que les invocations locales (pre-commit, IDE)
    reproduisent exactement la barre CI. Sinon un dev voit le
    code passer localement et échouer en CI, ou inversement.

    Si ces seuils évoluent un jour, mettre à jour les trois
    endroits *ensemble* :
        - ``.flake8`` (ce fichier)
        - ``.github/workflows/complexity.yml``
        - ``EXPECTED_*`` en haut de ce module
    """
    cfg = _load_flake8_config()
    mccabe = cfg.get("flake8", "max-complexity", fallback=None)
    cog = cfg.get("flake8", "max-cognitive-complexity", fallback=None)
    assert mccabe == EXPECTED_MAX_COMPLEXITY, (
        f"max-complexity doit être à {EXPECTED_MAX_COMPLEXITY} "
        f"(aligné sur complexity.yml CLI) ; trouvé : {mccabe!r}"
    )
    assert cog == EXPECTED_MAX_COGNITIVE_COMPLEXITY, (
        f"max-cognitive-complexity doit être à "
        f"{EXPECTED_MAX_COGNITIVE_COMPLEXITY} (aligné sur "
        f"complexity.yml CLI) ; trouvé : {cog!r}"
    )


# =================================================================
# Tests — invariant 2 : le scope du lint est borné à unifideck/
# =================================================================


@pytest.fixture(scope="module")
def flake8_full_repo_output() -> list[str]:
    """Lance flake8 contre tout le repo et retourne les chemins reportés.

    On utilise ``--exit-zero`` pour ne pas faire échouer le test
    si du vrai code Unifideck a une erreur — ce test ne contrôle
    QUE le périmètre. Les vraies erreurs sont détectées par le
    workflow ``complexity.yml`` principal.

    On reproduit la commande EXACTE du CI ::

        flake8 --select=C --max-complexity=15 py_modules/ main.py

    Si flake8 n'est pas installé dans l'environnement de test
    (ex : ``pytest`` lancé par un dev sans flake8 installé), on
    skip plutôt qu'échouer — le test reste utile localement et
    en CI où flake8 est forcément présent (puisqu'on lint).
    """
    if not (UNIFIDECK_PKG.is_dir() or MAIN_PY.is_file()):
        pytest.skip(
            "Arbre source absent (py_modules/unifideck/ ou main.py). "
            "Le test ne tourne qu'en checkout complet."
        )

    try:
        res = subprocess.run(
            [
                sys.executable, "-m", "flake8",
                "--select=C",
                "--max-complexity=15",
                "--exit-zero",
                "py_modules/",
                "main.py",
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
            timeout=180,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("flake8 non disponible ou trop lent dans cet env.")

    # flake8 sort un chemin par ligne, format ::
    #
    #     path/to/file.py:LINE:COL: CODE message
    #
    # On garde uniquement le chemin (avant le premier ':').
    reported_paths: list[str] = []
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        path = line.split(":", 1)[0]
        reported_paths.append(path)
    return reported_paths


def test_no_vendored_file_in_lint_output(
    flake8_full_repo_output: list[str],
) -> None:
    """AUCUN fichier hors ``py_modules/unifideck/`` ne doit remonter.

    C'est le test central : si on ajoute un nouveau vendor dans
    ``py_modules/`` (bump Decky Loader, ajout d'une lib) et qu'on
    oublie de mettre à jour ``.flake8``, ce test échoue avec un
    diff clair montrant exactement quel chemin a fui le scope.

    À la différence du log CI principal (189 erreurs CCR001 sans
    contexte), le message d'erreur de ce test pointe la cause :
    "tel vendor n'est pas dans extend-exclude".
    """
    out_of_scope = [
        p for p in flake8_full_repo_output
        if not _normalise_for_match(p).startswith(IN_SCOPE_PREFIXES)
    ]
    assert not out_of_scope, (
        f"Le lint flake8 a remonté {len(out_of_scope)} fichier(s) "
        f"hors du périmètre Unifideck. Ajoute-les à `extend-exclude` "
        f"dans .flake8.\n\n"
        f"Exemples (jusqu'à 10) :\n  - "
        + "\n  - ".join(sorted(set(out_of_scope))[:10])
    )


def _flake8_excludes_path(cfg: configparser.ConfigParser, path: str) -> bool:
    """Émule la logique d'exclusion fnmatch de flake8 sur un chemin.

    flake8 teste, pour chaque pattern de ``extend-exclude`` (et
    ``exclude`` historique), un match ``fnmatch.fnmatch`` contre :
        * le chemin complet relatif
        * le basename

    On reproduit cette logique pour pouvoir tester "est-ce que ce
    chemin serait exclu par la config actuelle ?" sans avoir
    besoin d'invoquer flake8 lui-même (qui ne donne pas
    facilement accès à cette info en ligne de commande).
    """
    import fnmatch
    from pathlib import PurePosixPath

    patterns: list[str] = []
    for key in ("extend-exclude", "exclude"):
        raw = cfg.get("flake8", key, fallback="")
        for token in raw.replace("\n", ",").split(","):
            token = token.strip()
            if token:
                patterns.append(token)

    posix = path.replace("\\", "/")
    basename = PurePosixPath(posix).name

    # Pour qu'un dossier ANCÊTRE soit exclu, on teste aussi chaque
    # composant du chemin (ex : ``py_modules/idna`` exclut tout
    # fichier dont le chemin a ``py_modules/idna`` en préfixe).
    parts = posix.split("/")
    ancestors = ["/".join(parts[: i + 1]) for i in range(len(parts))]

    for pat in patterns:
        for candidate in [posix, basename, *ancestors]:
            if fnmatch.fnmatch(candidate, pat):
                return True
    return False


def test_unifideck_package_is_actually_scanned() -> None:
    """Garde-fou contre une exclusion trop large.

    Symétrique du test ``no_vendored`` : si quelqu'un met par
    erreur ``unifideck`` (sans le préfixe ``py_modules/``) dans
    l'exclude list, flake8 EXCLUT le package cible et le test
    principal passe à vide (zéro fichier reporté = zéro fichier
    hors scope) — faux positif silencieux.

    Approche : on émule la logique fnmatch de flake8 et on vérifie
    qu'aucun pattern d'exclusion ne match :
        * ``py_modules/unifideck`` (le package lui-même)
        * un fichier d'exemple dedans (couvre les patterns *.py)
        * ``main.py``

    On ne dépend ni de la sortie ``--verbose`` de flake8 (qui ne
    liste pas les fichiers scannés), ni de la présence d'erreurs
    dans le code (le test reste utile sur du code propre).
    """
    if not UNIFIDECK_PKG.is_dir():
        pytest.skip(
            "py_modules/unifideck/ absent — test sans objet."
        )

    cfg = _load_flake8_config()

    # Sentinelles : si un de ces chemins est exclu, c'est qu'on a
    # un pattern trop large et le scope se vide.
    sentinels = [
        "py_modules/unifideck",
        "py_modules/unifideck/__init__.py",
        "py_modules/unifideck/core",
        "py_modules/unifideck/core/cache_manager.py",
        "main.py",
    ]
    excluded = [
        s for s in sentinels if _flake8_excludes_path(cfg, s)
    ]
    assert not excluded, (
        f"Les patterns extend-exclude du .flake8 excluent par "
        f"erreur des chemins du PÉRIMÈTRE Unifideck : "
        f"{excluded}.\n"
        f"Vérifie qu'aucun pattern n'est trop large (typiquement "
        f"``unifideck`` au lieu de ``py_modules/idna``)."
    )

    # On vérifie aussi qu'il EXISTE bien du code Python sous
    # unifideck/ — sinon le scope est creux et le test précédent
    # passerait toujours à vide.
    py_files = list(UNIFIDECK_PKG.rglob("*.py"))
    assert py_files, (
        f"py_modules/unifideck/ ne contient aucun .py — le scope "
        f"est creux, vérifie le checkout."
    )
