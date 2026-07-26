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


def test_never_restores_either_var_from_orig():
    # Both loader vars are write-once-never: a _MEI-tainted value is dropped
    # and neither is ever restored from its _ORIG twin.
    #
    # LD_PRELOAD: umu-run/pressure-vessel has its own Steam overlay
    # mechanism, and re-exporting the host's gameoverlayrenderer.so crashes
    # the game process ("WARNING: Keyboard Interrupt").
    #
    # LD_LIBRARY_PATH: umu copies it into STEAM_RUNTIME_LIBRARY_PATH, so a
    # host path shadows the container's libs and the container can't start
    # python3 ("error while loading shared libraries: libz.so.1") -> rc 127.
    env = {
        "LD_LIBRARY_PATH": "/tmp/_MEIxx",  # noqa: S108
        "LD_LIBRARY_PATH_ORIG": "/usr/lib/real",
        "LD_PRELOAD": "/tmp/_MEIxx/libfoo.so",  # noqa: S108
        "LD_PRELOAD_ORIG": "",
    }
    sanitize_frozen_loader_env(env)
    assert "LD_LIBRARY_PATH" not in env
    assert "LD_LIBRARY_PATH_ORIG" not in env
    assert "LD_PRELOAD" not in env
    assert "LD_PRELOAD_ORIG" not in env


def test_never_restores_ld_library_path_on_clean_launcher_env():
    # The regression that broke every GOG/Amazon/Ubisoft launch:
    # bin/unifideck-launcher pops LD_LIBRARY_PATH at process start but NOT
    # its _ORIG twin, so an inherited LD_LIBRARY_PATH_ORIG got promoted
    # straight back and rode umu-run into the pressure-vessel container,
    # where a host library path shadows the container's own libs. Must NOT
    # be promoted. (Observed on plain Steam stable — the origin of the
    # inherited value is not established, only that it must not survive.)
    env = {
        "PATH": "/usr/bin",
        "LD_LIBRARY_PATH_ORIG": (
            "/usr/lib/pressure-vessel/overrides/lib/x86_64-linux-gnu/aliases"
        ),
    }
    sanitize_frozen_loader_env(env)
    assert "LD_LIBRARY_PATH" not in env
    assert "LD_LIBRARY_PATH_ORIG" not in env


def test_never_restores_ld_preload_from_orig_on_clean_launcher_env():
    # The Gaming-Mode-shaped case: no LD_PRELOAD set (already stripped by
    # bin/unifideck-launcher at process start), but an inherited
    # LD_PRELOAD_ORIG with a real overlay path survives. Must NOT be
    # promoted back into LD_PRELOAD.
    env = {
        "PATH": "/usr/bin",
        "LD_PRELOAD_ORIG": "/home/deck/.local/share/Steam/ubuntu12_32/gameoverlayrenderer.so",
    }
    sanitize_frozen_loader_env(env)
    assert "LD_PRELOAD" not in env
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
