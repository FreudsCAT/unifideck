# Notas de trabajo — Unifideck (fork de FreudsCAT)

## Antes de compilar en la Steam Deck: comprobar pip

**Recordárselo SIEMPRE a Carles antes de darle instrucciones de compilar.**

SteamOS no trae `pip` en `/usr/bin/python3`. Sin él, `build-plugin.sh` no
vendoriza nada y produce un plugin que arranca pero se queda en «Comprobando
versión… Cargando…» y sin tiendas — falta `aiohttp` (lo importa
`services/updater/service.py`), `cryptography` (autenticación de las tiendas) y
`jsonschema`.

```
curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
python3 /tmp/get-pip.py --user
python3 -m pip --version
```

Y después de compilar, comprobar que de verdad se vendorizaron:

```
ls py_modules/ | grep -E "aiohttp|cryptography|jsonschema"
```

**No fiarse del mensaje del script.** `build-plugin.sh:285-296` evalúa
`python3 -m pip install … | tail -20`, y el estado de salida de una tubería es
el del último comando (`tail`), siempre 0. Imprime «✓ Python deps vendored»
aunque pip no exista. El aviso correcto (`[WARN] Missing vendored deps…`) viene
después, de una comprobación aparte, y es fácil que pase desapercibido.

## Otras trampas de la build, ya verificadas en dispositivo

- **`Extraction failed` al instalar.** `build-plugin.sh:1014` hace `unzip` sin
  `sudo` sobre `~/homebrew/plugins`, que pertenece a root. Instalación manual:
  parar `plugin_loader`, `sudo rm -rf` del destino, `sudo unzip -d`,
  `sudo chown -R deck:deck`, arrancar `plugin_loader`.
- **`skipping non-regular file "bin/umu/umu/umu_run.py"`** — inofensivo. Es el
  único symlink del repo y apunta a `umu-run` en la misma carpeta; nada abre
  `umu_run.py` por ruta.
- **Panic de la CLI de Decky descargando gogdl/legendary.** Son los
  `remote_binary` de `package.json:102-112`. La CLI hace `panic!` en vez de
  reintentar, así que un corte de TLS tumba la build. Reintentar suele bastar.
- **El zip del CI (`build-plugin.yml`) no es instalable.** Es una verificación
  de compilación: no vendoriza las dependencias de Python ni descarga
  `bin/gogdl`. La vía buena es `./build-plugin.sh prod`, que es la que el autor
  documenta (README:161). Decidido no arreglar el workflow.

## Puertas de calidad del proyecto

`ruff`, `mypy`, `tsc`, `eslint`, `prettier`, y `scripts/volumetry_check.py`
(tope de 550 líneas por fichero, 80 por función). `pytest` corre con `-W error`
y **falla si hay tests saltados**, así que nada puede quedarse en `skip`.

`tests.yml` solo se dispara con cambios en `py_modules/**`, `main.py`,
`tests/**`, `pyproject.toml` o `requirements*.txt`. Un PR que solo toque
`src/**` verá 4 checks en vez de 7: los otros no fallaron, no llegaron a
ejecutarse.
