"""Tests de tooling — audits de la configuration des linters/CI.

Ce sous-paquet contient les tests qui auditent les **fichiers de
configuration** (``.flake8``, hooks pre-commit, scope CI…) plutôt
que le code source du package ``unifideck``. Le préfixe ``_`` du
nom de paquet est délibéré : il indique que ce dossier ne mirror
PAS un sous-paquet du source (cf. convention dans ``tests/unit/``
qui exige ``tests/unit/<sub_package>/test_<source_file>.py``).

Couverture actuelle :
    * ``test_lint_scope`` — invariants sur ``.flake8`` et garde
      contre la fuite des vendors dans le scope flake8.
"""
