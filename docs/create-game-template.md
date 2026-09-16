# Add a game

Every game in PossumDocker is one YAML file: a **template**. It describes the form shown when a server is
created, the Docker image that runs the game, its ports, and what the panel can do with it (edit its config,
make backups, colour its console). Adding a game means writing a template, not code.

There are two ways to add one:

- **For everyone:** add the template to the repository and open a pull request. See
  [Contributing a game](#contributing-a-game).
- **Only on your server:** put the file in `data/templates/`. See [Your own templates](#your-own-templates).

## How a template works

1. **The form.** `fields` become the inputs in the "create server" dialog: a version, a map, a password.
2. **Install.** When the server is created, the panel creates its data volume and, if the template has an
   `install` step, runs a one-off container that downloads the game into it. The server ends up stopped
   and ready to start.
3. **Run.** Every start creates the game container again from the `runtime` section, filled in with the
   server's current values. The game's files live in the volume, so nothing is lost.
4. **Everything else.** `config_files`, `backup` and `console` tell the panel which files players' settings
   are in, what to back up and how to colour the log.

Almost every string in `install` and `runtime` is a [Jinja](https://jinja.palletsprojects.com/) template
filled in with the server's values: `"itzg/minecraft-server:{{ version }}"`.

The runtime is ordinary Docker. A game that has a good community image (such as `itzg/minecraft-server`
or `factoriotools/factorio`) is the easiest start: the template only passes the right environment
variables. A game without one can use `steamcmd/steamcmd` with an install script, like Counter-Strike 1.6
and Valheim.

## A complete example

A small, real template: [factorio.yaml](https://github.com/omka-1337/PossumDocker/blob/releases/templates/factorio.yaml).

```yaml
id: factorio
name: Factorio
description: Headless server; a new world is generated on the first start.
color: "#b8651f"
steam_appid: 427520            # icon and cover art from Steam

fields:
  - id: version
    label: Version
    type: select
    default: stable
    options:
      - { value: stable, label: Stable }
      - { value: latest, label: Latest (experimental) }
    editable: true
    on_change: restart

ports:
  - { name: game, protocol: udp, default_host: 34197 }

runtime:
  image: "factoriotools/factorio:{{ version }}"
  env:
    PORT: "{{ ports.game }}"
    SAVE_NAME: world
  stop:
    command: /quit             # typed into the console to stop gracefully
    timeout: 60
  data_path: /factorio         # where the server's volume is mounted

config_files:
  - id: server_settings
    label: Server settings
    path: config/server-settings.json
    format: json
    hints:
      max_players: { label: Max players, type: number, min: 0 }

backup:
  paths: [saves, config, mods]
```

The other bundled templates show the rest:

| Template | Shows |
|---|---|
| [minecraft-java.yaml](https://github.com/omka-1337/PossumDocker/blob/releases/templates/minecraft-java.yaml) | versions fetched at runtime, fields that depend on each other, an install step with the same image, console colours |
| [cs16.yaml](https://github.com/omka-1337/PossumDocker/blob/releases/templates/cs16.yaml) | an install script with steamcmd, a command line built from fields, a `cvars` config |
| [valheim.yaml](https://github.com/omka-1337/PossumDocker/blob/releases/templates/valheim.yaml) | two ports that move together, parts of the volume mounted at several paths, a game without console commands |
| [minecraft-bedrock.yaml](https://github.com/omka-1337/PossumDocker/blob/releases/templates/minecraft-bedrock.yaml) | two templates shown as one game with an edition switch |

## Reference

Unknown keys are an error, so a typo is caught instead of silently ignored.

### Top level

| Key | Required | |
|---|---|---|
| `id` | yes | Lowercase letters, digits and `-`. Also the file name: `factorio.yaml`. Never change it once servers use it. |
| `name` | yes | Shown everywhere: "Minecraft: Java Edition". |
| `description` | | One line under the name in the game picker. |
| `icon` | | An SVG, PNG or WebP file relative to the template, e.g. `icons/cs16.svg`. The fallback when art from the web can't be loaded. |
| `color` | | Background of the icon tile, `"#rrggbb"`. |
| `steam_appid` | | For games on Steam: the icon and cover art come from Steam. |
| `art` | | `icon` and `cover`: `https://` links to art, used before Steam's. |
| `group` | | Shows several templates as one game with a switch. See [Groups](#groups). |
| `fields` | | The create-server form. See [Fields](#fields). |
| `ports` | | See [Ports](#ports). |
| `install` | | The one-off install container. See [Install](#install). |
| `runtime` | yes | The game container. See [Runtime](#runtime). |
| `config_files` | | Files the settings tab can edit. See [Config files](#config-files). |
| `backup` | | See [Backups](#backups). |
| `console` | | See [Console](#console). |

### Fields

Every field has:

| Key | |
|---|---|
| `id` | Lowercase letters, digits and `_`, starting with a letter. The name of the value in Jinja. |
| `label` | The input's label. |
| `type` | `string`, `number`, `boolean`, `select` or `secret`. |
| `help` | A hint under the input. |
| `help_url` | A link next to it, e.g. the EULA. |
| `required` | The server can't be created without a value. |
| `visible_if` | Shown only when earlier fields have one of the listed values: `{ loader: [fabric, forge] }`. A hidden field has no value. |
| `editable` | Can be changed in the settings tab after the server is created. |
| `on_change` | What changing it costs: `none`, `restart` (the panel offers a restart) or `reinstall` (the install step runs again; the server must be stopped). |

Per type:

| Type | Keys |
|---|---|
| `string` | `default`, `min_length`, `max_length` (256), `pattern` (a regular expression the whole value must match) |
| `number` | `default`, `min`, `max`. Whole numbers only. |
| `boolean` | `default`, `must_be` (e.g. `true` for an EULA: the server can't be created until it's ticked) |
| `select` | `default`, and either `options` (a list of `{ value, label }`, or plain strings) or `options_from` (see below) |
| `secret` | `generate: true` makes a random value, `length` (24), `hidden: true` never shows it (an RCON password only the panel uses) |

**Options fetched at runtime.** `options_from` names a *provider*: code in the panel that returns the options,
cached for an hour. `depends_on` lists earlier fields whose values the provider gets.

| Provider | Returns | Depends on |
|---|---|---|
| `minecraft.versions` | Minecraft releases for a server type | `loader`: `vanilla`, `paper`, `fabric`, `forge`, `neoforge` |
| `minecraft.loader_versions` | Fabric, Forge or NeoForge builds | `loader`, `version` |

A new provider is Python code: add it to `panel/app/games/providers.py` in your pull request.

### Ports

```yaml
ports:
  - { name: game, protocol: udp, default_host: 2456 }
  - { name: query, protocol: udp, default_host: 2457, follows: game }
```

| Key | |
|---|---|
| `name` | Used in Jinja: `{{ ports.game }}`. |
| `protocol` | `tcp` or `udp`. |
| `default_host` | The port the first server gets. The next server of the game gets the next free one. |
| `container` | The port inside the container. Leave it out to use the same port inside as outside. Games that tell a server list their own port (Valheim, Factorio, Source games) need that. |
| `follows` | Keeps this port at the same distance from an earlier one: the second Valheim server gets 2458 and 2459. |

### Install

A container that runs once when the server is created (and again on reinstall), with the server's volume
at `runtime.data_path`. It must exit with code 0.

| Key | |
|---|---|
| `image` | The image. |
| `script` | A shell script relative to the template, run with `sh`, e.g. `install/cs16.sh`. |
| `entrypoint`, `command` | Instead of a script: what to run. |
| `env` | Added to `runtime.env`, so the installer knows the version too. |

Downloads fail sometimes: retry in the script, and check that the game's files are really there before
exiting with 0 (see [cs16.sh](https://github.com/omka-1337/PossumDocker/blob/releases/templates/install/cs16.sh)).
Without an `install` step the panel only downloads the runtime image; the game then has to download what it
needs on its first start.

### Runtime

| Key | |
|---|---|
| `image` | Must render to a full image name: an unknown value is an error, not an empty tag. |
| `entrypoint`, `command` | Lists; each item stays one argument, never split by a shell. An item that renders empty is left out, so an optional flag works: `"{{ '' if vac else '-insecure' }}"`. |
| `env` | A value that renders empty from a field (a hidden one) is left unset; a literal `""` is passed as empty. |
| `stop.command` | Typed into the console to stop the game gracefully, e.g. `stop` or `quit`. Without it, the game gets SIGTERM. |
| `stop.timeout` | Seconds to wait before forcing it (30). |
| `data_path` | Where the server's volume is mounted (`/data`). |
| `mounts` | Instead of one mount: parts of the volume at several paths, `{ subpath: config, path: /config }`. Paths in `config_files` and `backup` are then relative to the volume. |
| `resources.memory_mb` | The default memory limit in MB, a Jinja expression: Minecraft's is its heap plus room for Java. |
| `resources.cpus` | The default CPU limit, e.g. `"2"`. |
| `resources.disk_mb` | The default disk space limit in MB. Backups get twice this. |

The game's standard input is its console: the panel types commands there. A game started by a wrapper
script must `exec` the real server so it gets that input and the stop signal.

**Values in Jinja:**

| Name | |
|---|---|
| a field's `id` | Its value. Booleans are `True`/`False`: use `{{ public \| lower }}` for `true`/`false`. |
| `server.id`, `server.name` | The server's id and the name its owner gave it. |
| `ports.<name>` | The host port. |
| `minecraft_java_tag(version)` | The `itzg/minecraft-server` tag with a Java that runs that Minecraft version. |

Templates run in a sandbox: they can't reach Python internals.

### Config files

Settings the settings tab can edit, read from the game's own file: whatever keys the game wrote are shown.

| Key | |
|---|---|
| `id`, `label` | |
| `path` | Relative to the volume (or to `data_path` without `mounts`). |
| `format` | `properties` (Java, Minecraft), `cvars` (`key "value"` lines, GoldSrc/Source) or `json` (top-level values and one level down, such as `visibility.public`). |
| `managed` | Keys the panel sets itself (ports, RCON): shown, not editable. |
| `hints` | Nicer inputs for known keys: `label`, `type` (`string`, `number`, `boolean`, `select`), `help`, `options`, `min`, `max`, and `true_value`/`false_value` for booleans (`"1"`/`"0"` in cvars). |

Keys without a hint are shown as plain text; a key the file doesn't have can only be added if it has a hint.

### Backups

| Key | |
|---|---|
| `paths` | Only these files and folders, relative to the volume. Without it, the whole volume. A restore replaces exactly these. |
| `exclude` | For a whole-volume backup: top-level names (globs) left out, like caches the game downloads again. A restore keeps them. |
| `before`, `after` | Console commands around the backup while the server runs, e.g. `save-off` and `save-on`. |
| `wait` | Seconds after `before`, so the game finishes writing (5). |

Back up what players make and admins change (worlds, configs, plugins), not the game itself.

### Console

| Key | |
|---|---|
| `highlight` | `{ pattern, color }` rules; the first match colours the whole line. Colors: `red`, `yellow`, `green`, `blue`, `magenta`, `cyan`, `gray`. Patterns are regular expressions that work in both Python and JavaScript. |
| `continuation` | Lines that belong to the previous one (a stack trace) and keep its colour. |
| `commands` | `false` for games that read nothing from their console: the command input is hidden. |

### Groups

```yaml
group: { id: minecraft, name: Minecraft, variant: Java, order: 0 }
```

Templates with the same group `id` are one tile in the game picker, with a switch between their `variant`s
in `order`. They must use the same `name`.

## Testing a template

1. Run the panel from a clone of the repository (`./possum start` builds it from your code when you aren't
   on a release).
2. Put the file in `templates/` (a game for everyone) or `data/templates/` (your own).
3. `./possum check` shows what's wrong with your templates in `data/templates/` (the panel has to be
   running). A mistake in a template in `templates/` stops the panel from starting, with the reason in
   `./possum logs`.
4. `./possum restart` loads the changes.
5. Create a server and go through it: install, start, join the game, change a setting, make and restore a
   backup, stop it. The install log is on the server's page while it installs; the console shows what the
   game prints.

## Contributing a game

1. Fork the repository and work on a branch from `dev`.
2. Add `templates/<id>.yaml`, and `templates/install/<id>.sh` if it needs a script.
3. For an icon of your own, add it to `templates/icons/` and a row to
   [templates/icons/README.md](https://github.com/omka-1337/PossumDocker/blob/releases/templates/icons/README.md)
   with its source and license. Art from Steam or from an `art` link isn't stored in the repository.
4. Test it as above, then run the panel's tests: `cd panel && .venv/bin/pytest` loads every bundled template.
5. Open a pull request to `dev` saying which game it is, which image it uses and what you tested (joined
   the game, backup and restore, stopping).

A good template:

- uses an image that is maintained and widely used, pinned to a version or to a stable tag, never to
  something you built yourself;
- stops the game gracefully (`stop.command`) so worlds aren't lost;
- backs up players' work and admins' changes, not the game files;
- asks only what's needed to start; everything else is in the game's config file;
- has sensible `resources` defaults.

## Your own templates

Games only you need can stay on your server. Put the template in `data/templates/` next to `./possum`
(with a Docker Compose install: in `templates/` inside the folder mounted at `/data`). Icons and install
scripts go there too, with paths relative to the template.

A template there with the `id` of a bundled one replaces it: copy `templates/minecraft-java.yaml` to
`data/templates/` to change how Minecraft servers are set up. It then stays as you left it, also when
PossumDocker is updated.

A broken template in `data/templates/` is skipped (the reason is in `./possum logs`) instead of stopping
the panel. `./possum check` shows the problems without restarting.
