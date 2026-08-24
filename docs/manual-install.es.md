# Manual Install — tienda `manual`

> Estado: **v2**, sobre la base oficial **0.7.4** (`cb2eeaa`), rama
> `claude/unifideck-manual-install-v2` (la v1 queda congelada en
> `claude/unifideck-manual-install-v1`).
> English version: [manual-install.md](./manual-install.md).
>
> Permite instalar juegos desde un instalador `.exe`/`.msi` que el usuario ya
> tiene en local: se crea un prefijo de Proton, el instalador se ejecuta dentro
> de gamescope, los ficheros del juego quedan **fuera del prefijo** (unidad
> `U:`), y al terminar se crea el acceso directo con arte y metadatos de
> unifiDB/SteamGridDB. Los juegos aparecen en la pestaña Downloads para poder
> desinstalarlos.

---

## 1. Idea de diseño

Todo se apoya en la fundación existente de Unifideck; no hay maquinaria nueva
de lanzamiento, prefijos ni arte:

| Necesidad | Pieza existente reutilizada |
|---|---|
| Registrar la tienda | Auto-discovery de `StoreRegistry` (`stores/manual/manual_store.py` basta) |
| Lanzar un exe arbitrario con umu/Proton | `generic_launch → _raw_exe_launch` (una tienda desconocida cae ahí sola, con `STORE=none` para umu) |
| Crear el prefijo con el Proton por defecto | `setup_prefix` (la misma ruta canónica del lanzamiento normal) |
| Abrir una ventana en Gaming Mode | El patrón RunGame de las wrapper stores (`wrapper-shortcut-launch.ts` + shortcut temporal `AddShortcut`) — un subproceso del backend **no tiene sesión gamescope** y su ventana jamás aparece |
| AppID determinista + shortcut + `games.map` | `generate_app_id(launcher, "manual:<id>")`, patrón de `ensure_auth_shortcut`, `mark_installed` |
| Metadatos y arte | Fases normales del sync: unifiDB **por título**, SteamGridDB, CDN de Steam |
| Desinstalar desde Downloads | `uninstall_game(app_id)` → `registry.get_store("manual").uninstall_game()` |

Decisiones clave:

1. **Los ficheros del juego viven fuera del prefijo.** El prefijo
   (`~/.local/share/unifideck/prefixes/<game_id>`) es desechable: se puede
   regenerar o forzar otro Proton sin perder el juego. Wine expone la carpeta
   real del juego como unidad `U:` mediante un symlink en
   `<prefijo>/dosdevices/u:` → `~/Games/Manual/<game_id>` (en los
   prefijos de umu la raíz ES el prefijo Wine — umu crea `pfx` como
   symlink a `.`). La letra es `U:` (de "Unifideck") y no `D:` porque el
   mountmgr de Wine asigna letras a los dispositivos extraíbles desde la D
   hacia arriba — en máquinas cuyo lector de SD aparece como `/dev/sda`
   reclama `d::` y borra un symlink `d:` ajeno al arrancar. El mapeo se
   re-asegura **en cada lanzamiento** (idempotente), así que sobrevive a una
   regeneración del prefijo.

2. **"Instalar jugando" sin intervención.** El shortcut se crea primero con
   `games.map` apuntando al **instalador**; el frontend hace `RunGame`
   automáticamente al recibir el evento del backend. El asistente corre bajo
   Steam/gamescope con Steam Input y teclado en pantalla. Al salir el proceso,
   se pide el exe real y `games.map` se re-apunta.

3. **Una tienda sin autenticación.** `store_info.auth_method = "none"` — ese
   flag ya existía en `StoreInfo` sin consumidores; ahora `StoreConnections`
   lo usa para no pintar el botón Authenticate. Todos los métodos de auth de
   la tienda son no-ops honestos.

---

## 2. Flujo de funcionamiento (end to end)

```
Ajustes → MANUAL INSTALL → "Select exe"
  │  openFilePicker (.exe/.msi), arranca en $HOME
  ▼
ManualInstallTitleModal  (título prerellenado desde el nombre del fichero)
  │  RPC manual_install_start(installer_path, title)
  ▼
BACKEND (ManualInstallRPCMixin):
  1. valida instalador y título; deriva game_id = slug(título)-crc32(ruta)
  2. crea ~/Games/Manual/<game_id>/
  3. guarda el registro {status: "installing"} en manual_games.json
  4. escribe el shortcut en shortcuts.vdf (LaunchOptions "manual:<id>")
     + fila de games.map: exe = INSTALADOR, work_dir = carpeta del juego
  5. emite MANUAL_INSTALL_LAUNCH_REQUESTED {store_game_id}
  6. emite ARTWORK_REQUEST (el arte empieza a bajar ya, por título)
  7. (el sync de biblioteca se pospone al FIN de la ejecución, vía
     manual_ensure_shortcut — un sync en mitad del flujo re-añadiría la
     fila que Steam acaba de borrar y su propio aviso de "¿reiniciar
     Steam?" saltaría a destiempo)
  ▼
FRONTEND (manual-install-listener, vive fuera del QAM):
  8. get_compat_tool_for_game("manual:<id>") → appid, launcher_path…
  9. RunGame del shortcut (si Steam aún no lo tiene en memoria: shortcut
     TEMPORAL vía AddShortcut — primer arranque tras escribir el vdf)
  ▼
LAUNCHER (proceso bin/unifideck-launcher):
 10. setup_prefix crea/valida el prefijo con el Proton por defecto
 11. ensure_manual_drive: dosdevices/u: → ~/Games/Manual/<game_id>
 12. _raw_exe_launch ejecuta el INSTALADOR bajo umu/Proton
     → el usuario completa el asistente eligiendo la unidad U:
  ▼
FRONTEND:
 13. watchAppStopped detecta que la app terminó
 14. manual_install_status → sigue "installing" → ManualInstallExeModal
 15. el modal LISTA los .exe candidatos (escaneo de la carpeta U: y del
     drive_c del prefijo, filtrando instaladores/redist Y el contenido de
     serie de Wine: windows/, iexplore, wmplayer…) — un toque y listo; queda
     "Browse…" como respaldo y "Later" para posponer
     │  RPC manual_install_finalize(game_id, exe_path)
  ▼
BACKEND:
 16. valida el exe (confinado a la carpeta del juego o a su prefijo)
 17. registro → {status: "ready", exe_path}; si instaló en C:, el
     install_path se re-ancla en la carpeta del exe
 18. escribe .unifideck_manifest.json (descubrimiento)
 19. mark_installed re-apunta games.map al exe real
 20. emite GAME_INSTALLED + sync en background (metadatos/arte restantes)
  ▼
FRONTEND:
 21. toast "juego listo" + SteamRestartModal (el tile solo aparece
     cuando Steam relee shortcuts.vdf)
```

**Play posterior**: Steam → `unifideck-launcher manual:<id>` → `games.map`
resuelve el exe → prefijo `prefixes/<id>` → `generic_launch` bajo umu/Proton.

**Desinstalar** (pestaña Downloads): `uninstall_game(app_id)` →
`ManualStore.uninstall_game` borra la carpeta del juego (rmtree con guardas:
nunca `/`, nunca `$HOME`, profundidad ≥ 3), opcionalmente el prefijo (toggle
del modal), elimina el registro y emite `GAME_UNINSTALLED`. Para la tienda
manual, el manejador de ese evento elimina el shortcut **del todo** — no lo
deja como "Not Installed", porque el juego ya no existe en ninguna biblioteca
y su botón Install no podría funcionar — y el manejador de
`SHORTCUT_REMOVED` limpia el arte de `grid/`. El frontend además lo quita de
la **sesión viva** de Steam vía `SteamClient.Apps.RemoveShortcut`
(centralizado en `useGameActions.uninstall`, el punto por el que pasan todas
las superficies: fila de Downloads, botones de la página de detalle,
GameInfoCompatRow), así que el tile desaparece al momento (biblioteca y
Recientes) sin reiniciar; si estabas en la página de detalle del juego
eliminado, se te devuelve a la biblioteca. Solo si esa eliminación en vivo
fallara se ofrece el reinicio de Steam. Y una guarda importante: la carpeta
del juego solo se borra si está **dentro de `~/Games/Manual`** — una carpeta
propia del usuario (juego añadido ya instalado) jamás se toca. Esa
protección tiene tres capas: (1) el *marker sweep* genérico del RPC de
desinstalación (que borra cualquier directorio con el marcador
`.unifideck_manifest.json`) se **salta** para la tienda manual — llegó a
borrar la carpeta de un juego importado; (2) el manifiesto solo se escribe
en directorios creados por el plugin bajo `~/Games/Manual`, nunca en
carpetas del usuario; y (3) al desinstalar, cualquier marcador residual de
builds antiguos en una carpeta del usuario se elimina (se desactiva la
bomba) dejando el resto intacto.

**IMPORT — añadir un juego ya instalado**: la sección MANUAL INSTALL tiene
dos botones. *Install* lanza el flujo del diagrama; *Import* añade un juego
que ya está instalado: se selecciona su ejecutable, se confirma el título, y
el registro nace `ready` directamente y el juego se **lanza una vez
automáticamente como ejecución de verificación** (`manual_import` emite el
mismo evento de RunGame): se crea su prefijo ahí mismo y el usuario ve que
el juego funciona — las siguientes ejecuciones son instantáneas. Al salir
de esa ejecución se ofrece el reinicio de Steam. Los ficheros se quedan
donde están y
desinstalar olvida el juego (shortcut, registro, prefijo) sin borrar esa
carpeta gestionada por el usuario.

**Protección del "Later"**: si el usuario pospone la selección del exe, el
registro queda `installing` y CUALQUIER ejecución posterior del juego (Play
re-lanza el instalador) termina re-ofreciendo el modal — el listener escucha
tanto el cierre del lanzamiento automático como los eventos `game_stopped`
de juegos manuales, con una guarda anti-duplicados. Además, en la fila de
la pestaña Downloads el botón **Play de un juego pendiente abre el selector
de exe** en vez de re-ejecutar el instalador — el paso pendiente es lo único
que separa al usuario de jugar. Y aunque se pulse "Later", el shortcut ya
está en `shortcuts.vdf`, así que se ofrece igualmente reiniciar Steam para
que el tile aparezca (la pregunta se omite sola si el tile ya está vivo en
la sesión).

**El volcado de Steam y `manual_ensure_shortcut`**: cada `AddShortcut` /
`RemoveShortcut` del shortcut temporal hace que Steam vuelque SU copia en
memoria de `shortcuts.vdf` — que nunca contuvo nuestra fila — borrándola
(era el bug de "el tile no aparece tras reiniciar"). Por eso, al terminar
cada ejecución (y al salir del modal del exe), el frontend espera a que
pase ese volcado (~2,5 s) y llama a `manual_ensure_shortcut`, que re-escribe
la fila para que aterrice DESPUÉS del último volcado y sobreviva al
reinicio. Y si se pulsa Play en Downloads antes de reiniciar (el shortcut
persistente aún no está registrado en la sesión), el lanzamiento va por la
vía del shortcut temporal en vez de un `RunGame` directo que fallaría.

**Instalar en otra ubicación**: el confinamiento del finalize acepta el exe
en la carpeta U:, el prefijo (C:), la carpeta del instalador, y en general
cualquier ruta bajo `$HOME` o `/run/media` (la unidad `Z:` de Wine permite
instalar en cualquier carpeta). Es seguro porque la desinstalación solo
borra directorios dentro de `~/Games/Manual`. Y si el juego quedó DENTRO
del prefijo (instalación en C:), al desinstalar el prefijo se elimina
siempre — dejarlo sería filtrar el juego "desinstalado" en disco.

---

## 3. La tienda `manual` (backend)

### Estado — `py_modules/unifideck/stores/manual/state.py`

Un único JSON es toda la biblioteca:
`~/.local/share/unifideck/manual_games.json` (configurable:
`stores.manual.state_file`). Escritura atómica (tmp + `os.replace`); las filas
corruptas se descartan con warning sin tumbar la carga.

```json
{
  "version": 1,
  "games": [
    {
      "game_id": "dark-forest-1a2b3c4d",
      "title": "Dark Forest",
      "installer_path": "/home/deck/Downloads/setup_dark_forest.exe",
      "install_path": "/home/deck/Games/Manual/dark-forest-1a2b3c4d",
      "exe_path": "",
      "status": "installing",   // "installing" | "ready"
      "added_at": 1755960000.0
    }
  ]
}
```

* `status: "installing"` → `get_library()` expone el juego con
  `exe_path = installer_path`: pulsar Play re-ejecuta el instalador (esa ES la
  acción pendiente) y la fila de `games.map` se mantiene viva entre syncs.
* `game_id = slug(título)[:32] + "-" + crc32(ruta_instalador)`: estable
  (re-añadir el mismo instalador reutiliza el registro), único entre títulos
  iguales de instaladores distintos, y válido para el regex de identificadores
  y como nombre del directorio del prefijo.

### `ManualStore` — `manual_store.py`

`StoreBase` completo: `is_available() = True` siempre; auth no-op;
`get_library()` devuelve cada registro como `Game(installed=True, exe_path,
install_path, metadata.manual_status)`; `install_game/update_game` no aplican
(la cola de descargas no interviene); `get_game_size` mide el directorio;
`uninstall_game` como se describe arriba. `logout()` es no-op deliberado:
"cerrar sesión / borrar cuentas" **no** debe destruir juegos locales.

### Punto crítico de datos

`reconcile` solo escribe fila de `games.map` para juegos `installed` **con**
`exe_path`. La tienda manual siempre devuelve ambos, así que la fila se
reescribe en cada sync — estos juegos no dependen del flujo del
DownloadWorker.

---

## 4. RPC — `py_modules/unifideck/rpc/mixins/manual_install.py`

| RPC | Qué hace |
|---|---|
| `manual_install_start(installer_path, title)` | Pasos 1-7 del flujo. Devuelve `{game_id, app_id, install_path}` |
| `manual_import(exe_path, title)` | Botón IMPORT: registro `ready` directo desde el exe de un juego ya instalado |
| `manual_exe_candidates(game_id)` | Escanea la carpeta U: y el `drive_c` del prefijo y devuelve los `.exe` candidatos para el modal |
| `manual_install_finalize(game_id, exe_path)` | Pasos 16-20. Confina el exe a la carpeta del juego o su prefijo (guarda anti-traversal) |
| `manual_install_status(game_id)` | El registro actual (el frontend decide si pedir el exe tras parar la app) |

El shortcut ad-hoc se escribe con `stores/manual/shortcut.py`
(`ensure_manual_game_shortcut`): lee el vdf **de disco** (Steam machaca la
copia en memoria), añade una entrada con el mismo shape que
`_build_shortcut_entry` del reconcile (mismo appid, mismas LaunchOptions), y
el reconcile del siguiente sync la **adopta** en lugar de duplicarla.

## 5. Disco U: — `stores/manual/drive.py` + hook del launcher

`ensure_manual_drive(prefix_root, target_dir)` crea/repunta el symlink
`<prefijo>/dosdevices/u:`. Nunca destruye un directorio real que ocupe la
letra. Se invoca desde `services/launcher/orchestrator.py`
(`_ensure_manual_drive_mapping`) justo después de `setup_prefix` y antes de
ejecutar el juego/instalador — best-effort: si falla, solo se pierde la
comodidad de la letra U:, nunca el lanzamiento.

## 6. Frontend

| Pieza | Fichero |
|---|---|
| Sección de ajustes | `src/components/settings/ManualInstallSection.tsx` |
| Modal de título | `src/components/modals/ManualInstallTitleModal.tsx` |
| Modal de exe post-instalación | `src/components/modals/ManualInstallExeModal.tsx` |
| Listener RunGame (vive fuera del QAM) | `src/services/manual-install-listener.tsx` (arranca en `definePlugin`, se para en `teardown`) |

El evento `manual_install_launch_requested` está en `WATCHED_EVENTS` **y** en
`IMPERATIVE_EVENTS` (no debe re-dispararse desde el backlog del replay al
recargar — relanzaría el instalador). El picker usa `openFilePicker` con el
contrato de `ChangeExecutableModal`: sin `filter` RegExp (no cruza el puente
JS→Python), `extensions` para el filtro.

`ChangeExecutableModal` ("Change executable…" del menú contextual) también
funciona para juegos manuales: `"manual"` está en `_DIRECT_LAUNCH_STORES` del
mixin de ejecutables, así que el override ES la columna exe de `games.map`.

## 7. Listas cerradas de tiendas ampliadas

Añadir una tienda toca ~15 listas cerradas. Las de este cambio:

* **Backend**: `services/shortcut/launch_options.py` (`STORE_ID_PATTERN` — el
  fallo silencioso nº 1 si se olvida: el reconcile no reconocería los
  shortcuts como propios), `core/types/events.py` (`StoreEnum` + evento
  nuevo), `bootstrap/cache_registry.py`, `config/config_manager.py`
  (fallback), `defaults/config.json`, `config/schema.json`,
  `config/key_presence.py`, `utils/paths.py` (`DEFAULT_INSTALL_DIRS`),
  `scripts/validate_event_schemas.py`, `main.py` (mixin).
* **Frontend**: `src/types/api.ts` (`StoreId`, `StoreInfo.auth_method`),
  `src/types/store.ts` (`STORE_VISUALS`), `StoreIcon.tsx` (icono `FaHdd`),
  `src/lib/library-filters/index.ts` (`StoreSlug` + contadores),
  `src/lib/steam-bridge/tab-container.ts` (pestaña "Manual", visible solo con
  ≥ 1 juego), `UnifiedLibraryView.tsx` (filtro), `rpc-routes.ts`,
  `types/events.ts`, `event-bus-client.ts`.
* **i18n**: bloque `manualInstall.*` + `deckTabs.manual` en los 16 locales;
  `deckTabs.manual` allowlistado para es-ES/pt-BR ("Manual" coincide con el
  inglés por casualidad léxica).

## 8. Límites conocidos

1. **El tile aparece tras reiniciar Steam** — Steam solo lee `shortcuts.vdf`
   al arrancar. El modal final ofrece el reinicio; instalar y elegir el exe
   funcionan sin reiniciar (shortcut temporal).
2. **"Later" en el modal del exe** → sin riesgo: el juego queda pendiente
   y pulsar Play en su fila de Downloads reabre el selector de exe en
   lugar de lanzar nada.
3. **Instalar en C: en vez de U:** funciona (el picker permite navegar al
   prefijo y `install_path` se re-ancla en la carpeta del exe), pero el juego
   vive dentro del prefijo y borrar el prefijo lo borra.
4. **Instaladores que se auto-relanzan** (proceso padre sale y sigue un hijo):
   umu/Proton espera a `wineserver` (`PROTON_VERB=waitforexitandrun`), lo que
   cubre la mayoría de casos; si el modal saliera antes de tiempo, basta
   cerrarlo con "Later" y elegir el exe cuando acabe.
5. Los datos de guardado dentro del prefijo se pierden si se desinstala con
   "borrar prefijo" activado (comportamiento estándar del modal).
6. **Actualizar un juego (parches .exe)** — usa el menú contextual nativo
   **«Change executable…»** (habilitado para juegos manuales), que está
   confinado a la carpeta del juego: **copia antes el `.exe` del parche a
   la carpeta del juego**, selecciónalo ahí, pulsa Play (corre en el
   prefijo del juego con su `U:` mapeada), y al terminar vuelve a
   seleccionar el exe del juego y borra el parche. Un parche fuera de la
   carpeta del juego es rechazado por el confinamiento del override.

## 9. Verificación

* `ruff`, `mypy`, `tsc`, `eslint`, `prettier`, build de rollup, volumetría
  (files/functions/locals/nesting/fanout), `validate_event_schemas`,
  `check_config_keys` y los 4 checks de i18n: **en verde**.
* `tests/unit/test_manual_store.py` (13 tests): estado, mapeo de biblioteca,
  guardas de desinstalación, derivación de ids, confinamiento del finalize,
  creación/reutilización del shortcut, mapeo de la unidad U:, contrato de
  auth. Suite completa: 2428 pass (los fallos restantes de
  battlenet-prefix/AuthDispatcher son pre-existentes del entorno de CI local,
  presentes también en la 0.7.4 limpia).

Pendiente de validar en hardware real: primer lanzamiento (creación de
prefijo + apertura del asistente), visibilidad de U: en el wizard, y el ciclo
completo hasta jugar.
