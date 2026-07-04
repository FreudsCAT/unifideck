"""Regression test for sanitize_frozen_loader_env.

The install-time prefix warmup builds the Proton launch plan inside the Decky
plugin process, whose PyInstaller-frozen PluginLoader exports
``LD_LIBRARY_PATH=/tmp/_MEIxxxx`` (an old bundled libcrypto). umu-run runs under
the SYSTEM python, so that path made its ``import ssl`` fail
(``libcrypto.so.3: version 'OPENSSL_3.3.0' not found``) and createprefix/
winetricks silently produced empty prefixes. The sanitizer undoes that.
"""
from unifideck.launcher.proton.infrastructure.core import (
    sanitize_frozen_loader_env,
)


def test_drops_pyinstaller_mei_path_without_orig():
    env = {"LD_LIBRARY_PATH": "/tmp/_MEI9q7soT", "PATH": "/usr/bin"}  # noqa: S108
    sanitize_frozen_loader_env(env)
    assert "LD_LIBRARY_PATH" not in env
    assert env["PATH"] == "/usr/bin"


def test_restores_original_when_pyinstaller_saved_it():
    env = {
        "LD_LIBRARY_PATH": "/tmp/_MEIxx",  # noqa: S108
        "LD_LIBRARY_PATH_ORIG": "/usr/lib/real",
        "LD_PRELOAD": "/tmp/_MEIxx/libfoo.so",  # noqa: S108
        "LD_PRELOAD_ORIG": "",
    }
    sanitize_frozen_loader_env(env)
    assert env["LD_LIBRARY_PATH"] == "/usr/lib/real"
    assert "LD_LIBRARY_PATH_ORIG" not in env
    # An empty _ORIG is a real value (PyInstaller used to have nothing set) —
    # restore it verbatim rather than dropping.
    assert env["LD_PRELOAD"] == ""
    assert "LD_PRELOAD_ORIG" not in env


def test_clean_launcher_env_is_untouched():
    # The out-of-process launcher Steam spawns has a clean env — a legit
    # runtime LD_LIBRARY_PATH with no _MEI / no _ORIG must be left alone.
    env = {"LD_LIBRARY_PATH": "/steamrt/lib:/usr/lib", "PATH": "/usr/bin"}
    sanitize_frozen_loader_env(env)
    assert env["LD_LIBRARY_PATH"] == "/steamrt/lib:/usr/lib"


def test_absent_vars_no_crash():
    env = {"PATH": "/usr/bin"}
    sanitize_frozen_loader_env(env)
    assert env == {"PATH": "/usr/bin"}
