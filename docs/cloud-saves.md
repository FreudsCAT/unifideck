# Cloud Saves

Unifideck can sync your game saves to and from each store's cloud for **GOG** and
**Epic** games, so your progress follows you between devices. This page explains
how it works, what the cloud‑save button does, and how to fix the common cases
where a game's saves can't be found automatically.

> **Which games?** Cloud saves apply to **GOG** and **Epic** games launched
> through Unifideck. Steam games use Steam's own cloud (handled by Steam).
> Amazon, Ubisoft, and xCloud titles are not covered by this feature.

---

## How it works

### Finding where a game keeps its saves

The hardest part of cloud saves is knowing **where** a game reads and writes its
save files inside its Windows (Proton) prefix. Unifideck resolves this in order,
using the first method that succeeds:

1. **Your manual override** — a save location you set yourself (see
   [Set a custom save location](#set-a-custom-save-location)).
2. **The store's own metadata** — GOG's cloud‑save configuration / Epic's
   `legendary` metadata. This is the most authoritative source.
3. **Community save‑location database** — paths sourced from
   [PCGamingWiki](https://www.pcgamingwiki.com/) (via the
   [Ludusavi](https://github.com/mtkennerly/ludusavi-manifest) project) and baked
   into Unifideck's game database. This covers the large majority of games.
4. **A live PCGamingWiki lookup** — for newer or niche games the database hasn't
   covered yet.
5. **A best‑effort folder scan** of the prefix as a last resort.

Path templates from these sources (e.g. `%APPDATA%/MyGame/Saves`, or a folder
next to the game's install directory) are resolved against the game's actual
Proton prefix and **your chosen install location** (games can live on the SD card
or an external drive; saves are found wherever the game actually writes them).

### When saves sync

- **Download (cloud → device) happens automatically when you launch a game.**
  This direction is safe: Unifideck takes a snapshot first and never deletes your
  local saves.
- **Upload (device → cloud) is manual by default.** Uploading is the only
  direction that can overwrite what's in the cloud, so Unifideck leaves it under
  your control via the cloud‑save button. You can change this — see
  [Settings](#settings-auto-pull--manual-push).

---

## The cloud‑save button

On a GOG/Epic game's page, a **cloud icon** sits in the button row next to
**Play**. Its appearance tells you the save state at a glance:

| Icon | Meaning |
|------|---------|
| ☁️ Cloud (plain) | Cloud saves supported; nothing needs attention |
| ☁️⬇️ green dot | Cloud saves are available to download |
| ☁️ amber ring | **Save location not found** — sync may not work yet (see below) |
| 🔄 spinning | A sync is in progress |
| ☁️ dimmed | This game has no cloud‑save support |

Selecting the icon opens the **Cloud saves** window, which shows:

- **Local** — how many save files are on this device, their size, and when they
  were last changed.
- **Cloud** — the same for the most recent copy Unifideck has of the cloud saves.
- **Download cloud save** — pull the cloud copy onto this device now.
- **Upload local save** — push this device's saves to the cloud now.

---

## Settings: auto‑pull / manual push

Two settings control automatic syncing (in the plugin config under `cloud`):

| Setting | Default | Effect |
|---------|---------|--------|
| `auto_pull_on_launch` | `true` | Download cloud saves automatically when a game launches. |
| `auto_push_on_stop` | `false` | Upload local saves automatically when a game exits. |

- Leaving the defaults gives you **auto‑download + manual upload** — the safest
  balance, and the recommended setup.
- Set `auto_push_on_stop` to `true` to restore **fully automatic** two‑way sync.
- Set both to `false` to make **everything manual** via the button.

---

## Safety: your cloud saves won't be wiped

Uploading is guarded so a bad local state can't destroy good cloud saves:

- **Empty or settings‑only folders are never uploaded.** If Unifideck would be
  pushing nothing but config files (or an empty folder), the upload is blocked.
- **Regressions are caught.** If files that were there at the last sync have gone
  missing locally, Unifideck stops and asks you which copy to keep instead of
  silently overwriting the cloud.
- **Versioned local backups.** Before each upload, Unifideck snapshots your local
  saves to `~/Save Games Backup/<store>/<game id>/` (the newest few are kept), so
  a mistake is recoverable.

When local and cloud genuinely disagree, you'll get a **conflict prompt** showing
both copies (file counts, sizes, timestamps) and a choice of **Use Cloud** or
**Use Local**.

---

## Troubleshooting

### "Save location not found — sync may not work for this game"

This warning means Unifideck couldn't determine where the game reads/writes its
saves yet. The most common reasons:

**1. You haven't launched the game on this device yet.**
A game's save folder lives inside its Proton prefix, which **doesn't exist until
the first launch**. Until then there's no real location to sync to, and any saves
Unifideck has pulled are held in a temporary staging folder.

> **Fix:** Launch the game once. Unifideck creates the prefix, resolves the real
> save folder, and brings your saves into it. After that the cloud‑save button
> works normally.

**2. The game's save location isn't in our database and couldn't be auto‑detected.**
Some games store saves in unusual places.

> **Fix:** [Set a custom save location](#set-a-custom-save-location) yourself.

### I see "11 files" locally and in the cloud, but download fails

If the game hasn't been launched yet (no prefix), the files you see are real saves
held in a **staging folder**, not the game's actual save directory. Sync can't
complete because there's no valid destination inside a prefix yet. **Launch the
game once**, then use the button.

### Set a custom save location

If auto‑detection fails but you know where the game keeps its saves (for example
after launching it once and checking inside its prefix), open the **Cloud saves**
window and choose **Set save location** to pick the folder. Unifideck saves this
as a per‑game override and always prefers it from then on.

### The cloud icon is dimmed / "No cloud‑save support"

Not every game offers cloud saves on every store. If the store doesn't support
cloud saves for that title, there's nothing to sync — this is expected, not an
error. (For example, many Epic titles have no cloud‑save support at all.)

### My game is on the SD card / a custom location

Fully supported. Game files can be installed anywhere; only the Proton prefix
stays in Unifideck's data folder. Saves are resolved against your actual install
location automatically.

---

## Where things are stored on your device

| What | Location |
|------|----------|
| Game prefixes (where in‑prefix saves live) | `~/.local/share/unifideck/prefixes/<game id>/` |
| Local backups (pre‑upload snapshots) | `~/Save Games Backup/<store>/<game id>/` |
| Staging folder (used before a prefix exists) | `~/.local/share/unifideck/saves/<store>/<game id>/` |

---

## Attribution

Save‑location data is sourced from [PCGamingWiki](https://www.pcgamingwiki.com/)
(licensed CC BY‑NC‑SA 3.0), compiled by the
[Ludusavi](https://github.com/mtkennerly/ludusavi-manifest) project.
