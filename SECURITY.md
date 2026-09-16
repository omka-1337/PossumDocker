# Security

## Reporting a vulnerability

Please don't open a public issue for a security problem. Report it privately instead:
**[Report a vulnerability](https://github.com/omka-1337/PossumDocker/security/advisories/new)**
(the repository's Security tab). Only the maintainer sees the report.

It helps to include what an attacker needs (an account? which permission?), what they can do with it,
and the steps to reproduce. You'll get an answer once the report is read; fixes go into a new release,
and the advisory is published after that release is out.

This is a small project maintained by one person, so there is no bug bounty.

## Supported versions

Only the latest release gets security fixes. Update with `make update`.

## How PossumDocker is meant to be run

Some things are the design, not vulnerabilities:

- **An administrator of the panel controls the machine.** The agent manages Docker, and whoever controls
  Docker can control the host. Only make people administrators you would give root to.
- **The "files" permission runs code.** Plugins and mods uploaded to a server run inside its container. Give
  it only to people you trust with that server.
- **Disk limits are soft.** The panel refuses uploads, unpacking and backups that don't fit, and stops a
  server that grows past its limit within a few minutes, but a game can write more than its limit in between.
- **Templates are trusted.** A game template decides which image runs and with what; only add templates
  you have read.
- **Use HTTPS.** Without it, passwords and the session cookie cross the network in plain text. To reach the
  panel from the internet, put a reverse proxy with a certificate (Caddy, nginx) in front of it and set
  `POSSUM_TRUSTED_PROXIES` in `.env` to the proxy's address.

A way around any of this (for example a user getting a permission they weren't given, or the agent doing
something its specification doesn't allow) is a vulnerability: please report it.
