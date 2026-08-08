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

## Pendiente — estado a 2026-08-08

Diagnosticado y verificado en dispositivo, pero **sin implementar**. `staging`
está en `01ea75a`, con los PR #1–#4 mergeados.

### 1. Idioma de los juegos de Epic que ignoran `-epiclocale`

Síntoma reproducido: Overcooked 2 (`epic:Potoo`) arranca en inglés con
`ui.locale = es-ES`. **No es una regresión del PR #1** — ese arreglo funciona.
El log lo prueba: `resolved language es from config locale es-ES`, y
`--language es` llega a legendary, que lo traduce a `-epiclocale=es`. Dying
Light lo respeta; Overcooked 2 no, porque es Unity y lee el idioma del sistema
operativo, no los parámetros de Epic.

El hueco: `launcher/proton/language_setup/` tiene `amazon.py` y `ubisoft.py`,
que llaman a `registry_io._apply_windows_locale` para escribir
`[Control Panel\International]` en el `user.reg` del prefijo. **Para Epic no
existe**, así que Epic tiene una palanca donde los otros tienen dos.

Trabajo: añadir `language_setup/epic.py` calcado de `amazon.py` (~12 líneas) y
llamarlo desde `handlers/epic.py::epic_launch`. `es-ES` ya está en
`LOCALE_MAP` (`00000c0a`, `ESN`, Spain), no hay tabla nueva que mantener.

Dos avisos para Carles antes de empezar: no garantiza español si el juego no
trae los textos, y cambia el prefijo entero (igual que ya se hace con Amazon y
Ubisoft).

De paso, comprobar `handlers/generic.py::_amazon_launch`: construye
`ConfigManager` sin `user_path`, que es el mismo patrón que causaba el bug del
PR #1. Puede que Amazon tampoco lea la elección del usuario.

### 2. Selector de Proton propio en Unifideck

Motivo: el pin de `proton_settings.json` **no se puede cambiar ni quitar desde
la interfaz**. Una vez fijado, el juego queda anclado a esa versión; para
soltarlo hay que editar el JSON a mano. Elegir «Steam Linux Runtime» no sirve:
la captura lo ignora a propósito.

- Paso 1 — mostrar la versión fijada en la fila de metadatos
  (`components/play/PlayMeta.tsx`, junto a «Espacio requerido · Última vez
  jugado»). Código propio, sin parchear Steam.
- Paso 2 — convertirlo en desplegable con «Predeterminado» + los Protones
  instalados, escribiendo el mismo `proton_settings.json` vía la RPC
  `save_proton_setting` (un `tool_name` vacío borra la entrada). Hace falta una
  RPC nueva que enumere los Protones instalados.

Descartado tras valorarlo con Carles: inyectar el aviso **bajo la casilla de
Steam**. El diálogo de propiedades no es una ruta, así que `routerHook.addPatch`
no sirve y habría que enganchar el modal; es la superficie más frágil de todas
y sería solo informativa.

### Ya verificado — no volver a investigarlo

- Steam **sí** guarda la elección en `config.vdf`. Que el desplegable salte a
  «Steam Linux Runtime» al elegir una versión es cosmética de su interfaz; el
  fichero guarda `proton_9`, `GE-Proton7-55`, etc.
- `SpecifyCompatTool` tarda ~700 ms en verse reflejado en `config.vdf`, así que
  dos capturas seguidas pueden leer el valor viejo. Inocuo: se reescribe el
  mismo valor.
- El candado de `useGameActions` solo colapsa pulsaciones que **se solapan**;
  las secuenciales hacen su propia captura. Inocuo: Steam deduplica `RunGame` y
  solo corre un launcher.
- Leer `config.vdf` al lanzar dejando el ajuste puesto en Steam **no funciona**
  y no hay que reintentarlo: con la casilla marcada, Steam ejecuta el launcher
  a través de Wine y no llega a arrancar nada capaz de leer nada.
