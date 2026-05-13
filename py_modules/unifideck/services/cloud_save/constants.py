"""Cloud save constants — manifest filename, lockfile name, etc.

OP-17f | py_modules/unifideck/services/cloud_save/constants.py

A small set of string constants used across the cloud_save package.
Centralised here so renaming the manifest filename (for example)
only touches one file.
"""

from __future__ import annotations

MANIFEST_FILE = ".unifideck_sync.json"
