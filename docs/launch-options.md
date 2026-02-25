# Launch Options Guide

Unifideck lets you customize how your non-Steam games launch by adding parameters to their Steam shortcut launch options. These parameters control things like performance overlays, debug tools, and game-specific tweaks.

## Where to Find Launch Options

1. Open your **Steam Library**
2. Right-click the game → **Properties**
3. Under **Shortcut** → **Launch Options**

You'll see something like:

```
epic:Salt
```

or if Proton compatibility is enabled:

```
/home/deck/homebrew/plugins/Unifideck/bin/unifideck-launcher "epic:Salt" #%command%
```

## Adding Custom Parameters

You can append parameters after the `store:game_id` portion. Unifideck will preserve them across syncs, installs, and Proton toggles.

### Environment Variables

Add `VAR=value` style parameters to set environment variables for the game:

```
epic:Salt MANGOHUD=1
```

```
gog:1482265668 MANGOHUD=1 DXVK_HUD=compiler
```

### Supported Variables

The launcher recognizes these patterns and exports them as environment variables:

| Variable                     | What it does                                            |
| ---------------------------- | ------------------------------------------------------- |
| `MANGOHUD=1`                 | Enables the MangoHud performance overlay                |
| `LSFG=1`                     | Enables Linux Steam Frame Generation (lossless scaling) |
| `PROTON=proton_9`            | Forces a specific Proton version                        |
| `PROTONPATH=/path/to/proton` | Points to a specific Proton installation                |
| `DXVK_*=value`               | Any DXVK setting (e.g. `DXVK_HUD=compiler`)             |
| `VKD3D_*=value`              | Any VKD3D setting (e.g. `VKD3D_CONFIG=dxr`)             |
| `WINE_*=value`               | Any Wine setting                                        |
| `PROTON_*=value`             | Any Proton setting (e.g. `PROTON_ENABLE_NVAPI=1`)       |

### Examples

**MangoHud overlay:**

```
epic:Salt MANGOHUD=1
```

**Force Proton 9 with MangoHud:**

```
gog:1482265668 PROTON=proton_9 MANGOHUD=1
```

**DXVK debug overlay:**

```
amazon:MyGame DXVK_HUD=fps,frametime
```

## How It Works with Proton / Force Compatibility

When you enable **Force Compatibility** (Proton) through Steam's UI, Unifideck automatically switches your launch options to a bypass format:

```
/path/to/unifideck-launcher "epic:Salt" MANGOHUD=1 #%command%
```

**What's happening here:**

- The launcher path and quoted `store:game_id` tell the launcher which game to run
- Your custom parameters (`MANGOHUD=1`) are preserved between the game ID and `#%command%`
- The `#%command%` is a special trick — Steam replaces `%command%` with the Proton command, but the `#` turns it into a bash comment, so it's ignored
- The launcher handles Proton **internally** via [umu-launcher](https://github.com/Open-Wine-Components/umu-launcher), reading your Proton preference from its own config

**Your custom parameters survive all transitions:**

```
epic:Salt MANGOHUD=1
    ↓ Enable Force Compatibility
/path/to/launcher "epic:Salt" MANGOHUD=1 #%command%
    ↓ Disable Force Compatibility
epic:Salt MANGOHUD=1
```

## Don'ts

- **Don't remove the `store:game_id`** (e.g. `epic:Salt`) — Unifideck needs this to identify the game
- **Don't modify the launcher path or `#%command%`** when Proton is enabled — the plugin manages this automatically
- **Don't use `%command%` without the `#`** — this would cause Steam to run the bash launcher through Wine, which will fail

## Troubleshooting

### My custom parameters disappeared

This was a known issue fixed in v1.x.x. If you're on an older version, update the plugin. Custom parameters are now preserved across:

- Game installs and uninstalls
- Library syncs and force syncs
- Proton compatibility toggles
- Page navigation (no longer reset on every game page visit)

### The game doesn't pick up my environment variable

Make sure you're using the `VAR=value` format (no spaces around `=`). The launcher only recognizes patterns listed in the [Supported Variables](#supported-variables) table. For unlisted variables, you can set them system-wide via `~/.bashrc` instead.
