# Launch Options Guide

Unifideck lets you customize how your games launch by adding extra options to the Steam shortcut. This covers things like performance overlays, frame generation, trainers, debug tools, and game-specific tweaks — all without needing to modify any config files by hand.

## Where to Set It

1. Open your **Steam Library**
2. Right-click the game → **Properties**
3. Under **Shortcut** → **Launch Options**

You'll normally see just the game's identifier here, e.g.:

```
epic:Salt
```

or, if Proton (Force Compatibility) is enabled:

```
/home/deck/homebrew/plugins/Unifideck/bin/unifideck-launcher "epic:Salt" #%command%
```

Don't touch the `store:game_id` part or the launcher path — Unifideck needs those to identify and launch the game. Everything you add goes **after** the game ID.

---

## What You Can Add

### 1 — Environment Variables

These are `NAME=value` settings that get passed to the game at launch. You can add any number of them after the game ID.

```
gog:1103900211 MANGOHUD=1 DXVK_HUD=fps
```

Unifideck automatically recognises any `ALL_CAPS_NAME=value` style entry (letters, numbers, and underscores in the name) and exports it for the game. You don't need to stick to a predefined list — common examples:

| What you type                                              | What it does                                                                                                                |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `MANGOHUD=1`                                               | Turns on the MangoHud overlay                                                                                               |
| `mangohud=1`                                               | Same — lowercase is fine too                                                                                                |
| `DXVK_HUD=fps,frametime`                                   | Shows DXVK frame stats                                                                                                      |
| `PROTON_ENABLE_NVAPI=1`                                    | Enables Nvidia API emulation                                                                                                |
| `PROTON_REMOTE_DEBUG_CMD="/path/to/trainer.exe"`           | Runs a trainer alongside the game                                                                                           |
| `PRESSURE_VESSEL_FILESYSTEMS_RW=/home/deck/Games/Trainers` | Grants the game access to a folder                                                                                          |
| `STEAM_COMPAT_MOUNTS=/mnt/games`                           | Mounts extra storage inside the game container                                                                              |
| `LSFG=1`                                                   | Enables [LSFG frame generation](https://www.lsfg.app/) (AI-powered framegen / lossless scaling, requires `fgmod` installed) |
| `PROTON=GE-Proton10-10`                                    | Forces a specific Proton version                                                                                            |

> [!NOTE]
> Values with spaces must be quoted, e.g. `PROTON_REMOTE_DEBUG_CMD="/home/deck/Games/Trainers/My Trainer.exe"`

### 2 — Wrapper Programs

A wrapper runs _before_ the game, wrapping around it. Put the wrapper path before the game ID:

```
gog:1103900211 /usr/bin/gamemoderun
```

Or with tilde shorthand:

```
epic:Salt ~/fgmod/fgmod
```

The game still launches normally — the wrapper just gets to intercept or modify the process.

## Combining Options

You can freely mix all three types in a single launch options field. Order doesn't matter for environment variables:

```
gog:1103900211 MANGOHUD=1 PRESSURE_VESSEL_FILESYSTEMS_RW=/home/deck/Games/Trainers PROTON_REMOTE_DEBUG_CMD="/home/deck/Games/Trainers/My Trainer.exe"
```

Or with a wrapper and game argument:

```
epic:Salt ~/fgmod/fgmod MANGOHUD=1
```

**Frame generation with LSFG** — run `fgmod` as a wrapper and enable LSFG:

```
epic:Salt ~/fgmod/fgmod LSFG=1
```

> [!NOTE] > `fgmod` must be installed separately. See the [LSFG guide](https://www.lsfg.app/) for setup instructions. Once installed, this works for Epic, GOG, and Amazon games alike.

Unifideck sorts everything out:

- `store:game_id` → identifies the game
- `ALL_CAPS=value` entries → exported as environment variables
- Paths or commands before the game ID → run as wrappers around the game

> [!CAUTION]
> Do **not** add `%command%` to your launch options. Unlike native Steam games, Unifideck doesn't use Steam's process substitution — the launcher handles everything internally. Adding `%command%` will cause it to be passed as a literal argument and the game will not start correctly.

---

## How Proton / Force Compatibility Interacts

When you enable **Force Compatibility** (Proton) through Steam's Properties:

- Unifideck saves your Proton version choice to its own config
- It temporarily rewrites the launch options to a bypass format (so Steam doesn't try to run the launcher itself through Wine)
- When you open the game page, it restores the simple format
- **Your custom parameters are always preserved** through this process

You can change Proton version at any time without losing anything you've added to Launch Options.

---

## Troubleshooting

**Parameters seem to disappear** — Make sure you're on the latest plugin version. Custom parameters are preserved across syncs, installs, and Proton toggles.

**A variable isn't being picked up** — Check that the name is in `ALL_CAPS_WITH_UNDERSCORES` format with no spaces around the `=`. Values with spaces must be quoted: `VAR="value with spaces"`.

**Wrapper isn't running** — Make sure the path is correct and the file is executable. Tilde paths (`~/my/script.sh`) work fine.

**Game doesn't start at all** — Don't remove the `store:game_id` from the launch options, and don't modify the launcher path or `#%command%` when Proton is active. Unifideck manages those automatically.
