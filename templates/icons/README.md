# Game icons

Shown next to each game in the panel.

## Games on Steam

Set `steam_appid` in the template (e.g. `steam_appid: 10` for Counter-Strike). The panel downloads the game's
icon and header art from Steam's CDN on first use and caches them in `data/cache/steam/`. That art belongs to its
publishers and is **not** stored in this repository. Browsers only talk to the panel, never to Steam.

## Bundled icons

A template can also point to a file here: `icon: icons/<file>.svg` and `color: "#rrggbb"` for its tile.
It is used for games that aren't on Steam, and as the fallback when Steam art can't be fetched.

| File | Source | License |
|---|---|---|
| `cs16.svg` | [Simple Icons](https://simpleicons.org/?q=counter-strike) (`counterstrike`), fill changed to white | CC0-1.0 |
| `minecraft-java.svg` | Original drawing made for this project | AGPL-3.0, like the rest of the repository |

Game names and logos are trademarks of their owners (Counter-Strike: Valve; Minecraft: Mojang/Microsoft).
They are used only to show which game a server runs; this project is not affiliated with or endorsed by them.
Mojang does not allow its logo in third-party products, so Minecraft gets a neutral block instead.

When adding a bundled icon, prefer an entry from Simple Icons or the game's official brand/press kit that allows
this use, and add a row here.
