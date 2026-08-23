# Notas de trabajo — Unifideck (fork de FreudsCAT)

## Antes de compilar en la Steam Deck: comprobar pip

**Recordarlo SIEMPRE antes de dar instrucciones de compilar.**

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

## Estado a 2026-08-21

`staging` está en `085a344`: upstream 0.7.3 más los PR #1–#8, más el arreglo
del idioma traído con cherry-pick.

Release de pruebas publicada en el fork: `0.7.3.hotfix` (prerelease, con el
zip adjunto), que junta idioma + Proton forzado + la línea del panel.

### Enviado a upstream

- **PR #422 — resolución del idioma. MERGEADO** el 2026-08-20 (`3f9c191`).
  Entró sin que el autor tocara una línea. Verificado en dispositivo con los
  tres handlers (Epic, Amazon, Ubisoft) y los dos niveles.

### Listo para enviar, sin enviar

- **PR #9 del fork** (`fix/force-compat-capture`) — la captura del Proton
  forzado. Rama sobre el espejo `upstream-staging-mirror`, que quedó anclado a
  `897f8ce`; upstream ya va por `23fba8a`, así que **hay que rebasar** antes de
  abrirlo. Verificado en dispositivo **en aislamiento**: build con solo esos
  siete ficheros sobre 0.7.3 limpio, con `proton_9` y `GE-Proton11-1`, más el
  caso sin elección. Ningún lanzamiento entró en el pressure-vessel.
- **PR #10 del fork** (`fix/update-btn-gamepad-focus`) — el botón Update de la
  pestaña Downloads no tenía estado de foco, así que con el mando parecía
  inactivo aunque el foco sí pasaba por él. Bug de upstream, independiente de
  todo lo demás. Rama sobre el espejo (0.7.3), verificado en dispositivo.
  Esperando a la 0.7.4 para rebasar y enviarlo.
- **PR #8 del fork** (`feat/proton-in-use-badge`) — la línea del panel.
  Depende funcionalmente del anterior: sin la captura, el origen `pin` nunca
  contiene una elección del usuario. Enviar **después** del #9.

## Pendiente

### 1. Comprobar el Proton forzado en la 0.7.4 (ya publicada)

Acordado el 2026-08-21 y reafirmado el 2026-08-23: no adelantar trabajo, ni
rebasar los PR, hasta que la versión salga oficialmente. Revalidado en
`upstream/staging` (`cb2eeaa`, ya con la 0.7.4 mergeada dentro) que **saldrá
con el problema intacto**, salvo que el autor meta algo antes:

- `saveProtonSetting` sigue declarada en `rpc-routes.ts:31` y **sin ningún
  llamante**. Los usos de `SpecifyCompatTool` que hay son de autenticación y
  de los lanzadores envoltorio; ninguno saca la elección de las manos de Steam
  antes de `RunGame`, que es la única palanca posible.
- `_proton_family` sigue con la tabla numérica parada en `proton10`, así que
  `proton_11`, `proton_8`, `proton_7` y `proton_hotfix` caen todos en
  `"other"` y saltar entre ellos no resetea el prefijo.

La comprobación es de un minuto: forzar una versión en Propiedades ›
Compatibilidad y pulsar Jugar. Si no se crea ningún fichero en
`~/.local/share/unifideck/launches/`, es el mismo fallo y toca enviar el #9.

**Publicada el 2026-08-23 (`Release-0.7.4`).** El primer intento de medirlo no
sirvió: `games.map` estaba vacío por el punto 4, así que el nivel 1 del
selector ni se consultaba y el resultado no decía nada del bug. Repetir con un
juego reinstalado, y confirmar que en el log aparece `steam force-compat
lookup: appid=… -> 'proton_9'` antes de dar la prueba por válida.

`upstream/staging` ya va por `cb2eeaa`, así que la base para rebasar los PR
está lista.

**CONFIRMADO el 2026-08-23 sobre la 0.7.4 oficial: el bug sigue.** Prueba
válida, con `games.map` repoblado (Absolute Drift reinstalado) y control
limpio:

```
14:05:26  sin Proton forzado
          steam force-compat lookup: appid=3521017763 -> None
          selected cached latest GE-Proton: GE-Proton11-5
14:06:00  attempt 1 exit code: 0 (ran 20.3s)        ← arrancó

          se fuerza Proton 9 y se lanza …

14:07:14  bundle generado. NINGÚN fichero nuevo en launches/.
```

Cero logs entre ambos momentos: el lanzador no llegó a ejecutarse, que es la
firma exacta del fallo. El nivel 1 sí se consultó, así que la medición es
válida esta vez. **El PR #9 queda justificado sobre la versión publicada.**

### 2. Tres arreglos que perdimos en el merge de 0.7.3

Estaban en el PR #2, se cayeron al resolver conflictos y **no lo detectamos
entonces**. Verificado el 2026-08-21 que siguen ausentes en nuestro `staging`,
y los tres siguen abiertos también en upstream:

- `_proton_family` — sustituir la tabla numérica por una regex sobre la
  versión mayor (`proton[ _-]?(\d+)`), después de las comprobaciones de
  experimental/ge/umu/cachyos para no romperlas, más una cláusula `hotfix`.
- `_handle_proton_change` escribe el marcador de versión **antes** de
  `_ensure_created`, así que un prefijo que no llega a construirse queda
  marcado y nunca se reintenta. Debe sellarse después.
- `_reset_prefix` hace `rmtree(.save_backup)` y solo después comprueba si hay
  `drive_c/users` que copiar: si un reseteo anterior dejó el prefijo a medias,
  el siguiente destruye el backup superviviente. Copiar a un temporal y
  sustituir solo si la copia sale bien.

Buen candidato a un PR pequeño a upstream. Ojo: el autor reescribió
`_proton_family` el 2026-08-13 (`f60e865`) para arreglar el choque entre
nombres de directorio y nombres de pantalla y añadir CachyOS. Hay que
construir encima de **su** versión, no reponer la nuestra.

### 3. Idioma de los juegos de Epic que ignoran `-epiclocale`

Síntoma: Overcooked 2 (`epic:Potoo`) arranca en inglés con `ui.locale = es-ES`.
**No es el bug del PR #1** — ese arreglo funciona y está verificado: el log dice
`language es-ES → -epiclocale=es` y el parámetro llega a la línea de órdenes
real del juego. Dying Light lo respeta; Overcooked 2 no.

**Descartado en dispositivo el 2026-08-09 — no repetirlo:**

- **No es el locale de Windows del prefijo.** Se escribieron a mano las cuatro
  claves de `[Control Panel\International]` (`Locale=00000c0a`,
  `LocaleName=es-ES`, `sLanguage=ESN`, `sCountry=Spain`) en
  `prefixes/Potoo/pfx/user.reg` — exactamente lo que escribiría
  `registry_io._apply_windows_locale` — y el juego **siguió en inglés**. La
  hipótesis de que un juego Unity lee `Application.systemLanguage` no se
  sostiene aquí, así que un `language_setup/epic.py` calcado de `amazon.py`
  **no arreglaría este título**. Podría seguir sirviendo para otros juegos de
  Epic, pero ya no hay motivo para darlo por hecho.
- **No es `write_app_language`.** `stores/epic/install.py:220` ya la llama al
  instalar, y solo fija el valor que legendary usa para `-epiclocale` — la
  misma palanca que el juego ignora.
- **No es que falten los textos.** La copia de GOG del mismo juego
  (`gog:1297999995`) arranca en español, así que la localización existe.

La diferencia real con GOG: `stores/gog/install/progress.py:144` pasa
`--lang` a gogdl, que **descarga los ficheros de ese idioma** y escribe el
`goggame-<id>.info`. legendary no hace nada equivalente; los juegos de Epic
traen todos los idiomas y eligen en tiempo de ejecución.

Siguiente pista, sin explorar: el juego debe guardar su idioma en un ajuste
propio dentro del prefijo. El resolutor de partidas en la nube ya conoce la
ruta — `drive_c/users/steamuser/AppData/LocalLow/Team17/Overcooked2/` — y los
juegos de Unity suelen usar ahí un fichero de preferencias o una clave de
registro bajo `HKCU\Software\Team17`. Mirar qué cambia al elegir idioma dentro
del propio juego es la vía barata.

Lo de `handlers/generic.py::_amazon_launch` ya está: tenía el mismo patrón y
se arregló en el PR #422, con test propio en `test_store_launch_language.py` y
verificado en dispositivo (`[language_setup.amazon] wrote locale=es-ES`).

### 4. `games.map` no se repuebla para juegos ya instalados

Encontrado el 2026-08-23 sobre la 0.7.4 oficial. **Bug de upstream**, causa
localizada en cinco líneas.

`reconcile_phases.py:385-401` mantiene la fila de `games.map` así:

```python
exe = game.exe_path or ""
if game.installed and exe:
    self._games_map[key] = GameMapEntry(...)
```

Los objetos `Game` que llegan de la sincronización de biblioteca **no traen
`exe_path`** — lo resuelve el instalador, no la tienda. Su propio comentario lo
reconoce y da por hecho que la fila «ya existe» de una instalación anterior.

Cuando no existe, nada la crea. Escenarios legítimos donde eso pasa: borrar los
datos del plugin conservando los juegos, reinstalar el plugin en un equipo con
juegos ya instalados, restaurar una copia parcial, mover la instalación a otra
Deck.

**Síntoma, y es silencioso:** la biblioteca aparece completa y sana, pero
`games.map` está vacío, así que el lanzador resuelve `app_id=0`,
`_game_context` deja `steam_app_id=None` y **el nivel 1 del selector no se
consulta en ningún juego** — no aparece siquiera la línea `steam force-compat
lookup` en el log. La versión de Proton que elija el usuario en Propiedades ›
Compatibilidad se ignora en toda la biblioteca, sin error ni aviso.

Reproducido en dispositivo: 669 accesos directos correctos en `shortcuts.vdf`,
`games.map` con solo sus tres líneas de cabecera, y Dying Light arrancando con
`GE-Proton11-5` en vez del `proton_9` elegido. El juego sí arranca porque el
lanzador re-resuelve el ejecutable por búsqueda (`[ExeFinder] Best candidate
(score=400)`); lo que no puede inventarse es el `app_id`.

Comprobación rápida:

```
grep -c "^epic:\|^gog:\|^amazon:\|^ubisoft:" ~/.local/share/unifideck/games.map
```

**Arreglo propuesto:** que la reconciliación, ante un juego marcado como
instalado y sin fila, resuelva el ejecutable con el `ExeFinder` que el
lanzador ya usa y la escriba. Toda la maquinaria existe; solo falta llamarla
desde ahí.

Apaño para el usuario mientras tanto: reinstalar el juego desde Unifideck, que
pasa por `_flip_existing_install` y deja la fila correcta.

### 5. Selector de Proton propio en Unifideck

Motivo: el pin de `proton_settings.json` **no se puede cambiar ni quitar desde
la interfaz**. Una vez fijado, el juego queda anclado a esa versión; para
soltarlo hay que editar el JSON a mano. Elegir «Steam Linux Runtime» no sirve:
la captura lo ignora a propósito.

- Paso 1 — **hecho** en el PR #8, aunque no donde decía esta nota: la versión
  se muestra en la línea de géneros de la ficha
  (`components/info/GameInfoCompatRow.tsx`), no en `PlayMeta.tsx`. La lee la
  RPC nueva `get_effective_proton_tool`, que es de solo lectura.
- Paso 2 — **pendiente**: convertirlo en desplegable con «Predeterminado» + los
  Protones instalados, escribiendo el mismo `proton_settings.json` vía la RPC
  `save_proton_setting` (un `tool_name` vacío borra la entrada). Hace falta una
  RPC nueva que enumere los Protones instalados;
  `ProtonToolsManager.list_known_tools()` (`compatibility/proton_helpers.py`)
  ya hace ese barrido y sirve de base.

Descartado tras valorarlo: inyectar el aviso **bajo la casilla de
Steam**. El diálogo de propiedades no es una ruta, así que `routerHook.addPatch`
no sirve y habría que enganchar el modal; es la superficie más frágil de todas
y sería solo informativa.

## Convivencia con otros plugins que escriben `shortcuts.vdf`

**Tres horas perdidas el 2026-08-22. No repetir el camino.**

Unifideck escribe `shortcuts.vdf` **directamente**. Steam mantiene su propia
copia en memoria, cargada al arrancar la sesión, y la vuelca al fichero cuando
guarda. Todo lo que el plugin escriba después de que la sesión arranque existe
en disco pero no en esa memoria.

Consecuencias visibles, todas comprobadas en dispositivo:

- El diálogo de Propiedades de **Big Picture / Gaming Mode no muestra** las
  opciones de lanzamiento aunque estén en el fichero. Steam **sí las pasa** al
  lanzar: verificado con `request received: epic:04a54ed…` en el log del
  lanzador. Ese diálogo **no es fuente de verdad**; la orden de abajo sí.
- Un juego recién instalado puede salir sin opciones en pantalla, y aparecer
  duplicado: una entrada con arte (la de la sesión) y otra sin arte (la del
  fichero recién leído).
- Editar las opciones **desde Steam de escritorio** hace que Steam reescriba la
  entrada con su propio `appid`. El arte de `grid/` está guardado con el appid
  antiguo, así que la carátula y el tiempo jugado se pierden. Se arregla
  borrando el acceso directo y sincronizando: Unifideck lo recrea con su appid
  determinista y el arte vuelve a enlazar solo. Una sincronización forzada
  **no** basta — `_force_update_shortcut` conserva el appid a propósito.

Comprobar el fichero, que es lo único fiable:

```
python3 -c "
import sys; sys.path.insert(0,'/home/deck/homebrew/plugins/Unifideck/py_modules')
import vdf
p='/home/deck/.steam/steam/userdata/<UID>/config/shortcuts.vdf'
sc=vdf.binary_load(open(p,'rb'))['shortcuts']
ours=[e for e in sc.values() if 'unifideck-launcher' in str(e.get('Exe','')).lower()]
print(len(sc),'entradas,',len(ours),'de Unifideck')
"
```

Ojo: tras cualquier escritura de Steam **todas** las claves quedan en minúscula
(`appname`, no `AppName`), así que esa diferencia de caja **no indica** quién
escribió una entrada — me confundió durante un rato. Léelas siempre con
`e.get("AppName") or e.get("appname")`.

### Los dos plugins instalados, revisados el 2026-08-23

- **Junk Store** (`ebenbruyns/junkstore`) — inofensivo. Solo usa la API de
  Steam (`SetAppLaunchOptions`, `AddShortcut`, `RemoveShortcut`) sobre sus
  propios accesos directos, por `id`. No abre el fichero ni recorre entradas
  ajenas.
- **NonSteamLaunchers** (`moraroy/NonSteamLaunchers-On-Steam-Deck`) — el que
  importa. Escribe por la API (`NSLGameScanner.py:1245-1262`), lo que **obliga
  a Steam a reescribir el fichero desde su memoria**; ese es el mecanismo que
  puede tirar las entradas que Unifideck acababa de escribir. Además compara
  entradas ajenas por subcadena (`NSLGameScanner.py:1167`,
  `launch_options in s.get('LaunchOptions')`), lo que admite falsos positivos.
  **Hace copia antes de tocar nada**, y ahí está la vía de rescate:
  `~/.steam/steam/userdata/<UID>/config/shortcuts.vdf_backups/`.

**Regla práctica:** después de que Unifideck sincronice, reiniciar Steam desde
el menú (no apagar) antes de que NSL haga su barrido.

**Candidato a PR:** el plugin ya detecta la situación —la comprobación
`steam_started_after_shortcuts_write` del bundle de diagnóstico— pero solo la
deja escrita en un informe que nadie mira. Avisarlo en la interfaz, con el
motivo y el remedio, ahorraría exactamente la tarde descrita aquí. Y
`orphan_scan.py:120,131` lee el nombre solo como `AppName`, así que tras una
escritura de Steam ve todas las entradas sin nombre.

## Ya verificado — no volver a investigarlo

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
