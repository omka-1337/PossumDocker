# Game icons

Shown next to each game in the panel.

## Art from the web

Nothing below is stored in this repository. The panel downloads it on first use, caches it in
`data/cache/art/` and serves the cached copy; browsers never contact these sites. The images belong to their owners.

- `art.icon` / `art.cover`: any public `https://` link (SVG, PNG, JPEG, WebP, up to 2 MB). Used first.
- `steam_appid`: for games on Steam, the icon and header art come from Steam's CDN. Used when `art` has no link
  for that image or the link fails.

```yaml
art:
  icon: https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/minecraft.svg
  cover: https://cdn2.steamgriddb.com/hero_thumb/043ab21fc5a1607b381ac3896176dac6.jpg
steam_appid: 10
```

## Bundled icons

A template can also point to a file here: `icon: icons/<file>.svg` and `color: "#rrggbb"` for its tile.
It is the fallback when art from the web can't be fetched (offline on first start, a dead link).

| File | Source | License |
|---|---|---|
| `cs16.svg` | [Simple Icons](https://simpleicons.org/?q=counter-strike) (`counterstrike`), fill changed to white | CC0-1.0 |
| `minecraft-java.svg` | Original drawing made for this project | AGPL-3.0, like the rest of the repository |

Game names and logos are trademarks of their owners (Counter-Strike: Valve; Minecraft: Mojang/Microsoft).
They are used only to show which game a server runs; this project is not affiliated with or endorsed by them.
Mojang does not allow its logo in third-party products, so Minecraft gets a neutral block instead.

When adding a bundled icon, prefer an entry from Simple Icons or the game's official brand/press kit that allows
this use, and add a row here.
