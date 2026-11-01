# AddonixWoW

Search and install [CurseForge](https://www.curseforge.com/wow) addons for **World of Warcraft: Forever** from the Omarchy bar.

- Search CurseForge, filtered to files built for WoW Forever.
- Install in one click, with integrity checks and safe archive extraction.
- See installed addons and open the AddOns folder.

## Requirements

- Omarchy with the shell plugin system, and `python3`.
- Battle.net and WoW Forever installed through [Faugus Launcher](https://github.com/Faugus/faugus-launcher). For any other install, set `addonsPath` (see below).
- Your own CurseForge API key. [Apply for one in the CurseForge console](https://console.curseforge.com/); the API does not work without it.

## Install

```bash
omarchy plugin add https://github.com/aledx18/addonixwow.git --enable
```

Open the widget and paste your key. The key is checked with CurseForge, then saved to `~/.config/addonixwow/config.json` with `0600` permissions.

Remove it with:

```bash
omarchy plugin remove io.github.aledx18.addonixwow
```

Removing the plugin keeps your addons and `~/.config/addonixwow/`.

## Configuration

`~/.config/addonixwow/config.json` (all fields optional):

| Field | Purpose |
| --- | --- |
| `apiKey` | CurseForge API key. The `CURSEFORGE_API_KEY` environment variable takes priority. |
| `addonsPath` | AddOns folder to use instead of auto-detection. |
| `gameVersionTypeId` | Override for the CurseForge game version, in case Forever cannot be detected. |

## How it finds your game

It reads `default-prefix` from `~/.config/faugus-launcher/config.json`, then checks `~/Faugus`, looking for `<prefix>/drive_c/Program Files*/World of Warcraft/_forever_/Interface/AddOns`. Support for other launchers is added as a new entry in `DETECTORS` in `backend/addonix.py`.

## Limitations

- Some authors block third-party downloads, including popular addons like DBM and Details!. Those addons show a link to their CurseForge page instead.
- CurseForge can block `/v1/mods/search` for a key while allowing other endpoints. The widget then lists only featured Forever addons and filters them by your text.
- There are no updates or uninstall from the widget yet.

## Development

```bash
python3 -m unittest discover -s tests
omarchy plugin validate .
qmllint -I "$OMARCHY_PATH/shell" BarWidget.qml Panel.qml
```

Tests run the backend against a local fake of the CurseForge API.

## License

MIT
