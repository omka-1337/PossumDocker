#!/bin/sh
# Downloads the Valheim dedicated server (Steam app 896660). Runs inside steamcmd/steamcmd.
# Layout matches lloesche/valheim-server, which mounts server/ at /opt/valheim: its updater keeps
# the Steam download in dl/server and runs the copy in server/.
set -eu

download=/data/server/dl/server
install=/data/server/server
mkdir -p /data/config "$download" "$install"

attempt=1
while [ "$attempt" -le 3 ]; do
    echo "steamcmd attempt $attempt"
    steamcmd +force_install_dir "$download" +login anonymous +app_update 896660 validate +quit || true
    if [ -f "$download/valheim_server.x86_64" ]; then
        break
    fi
    attempt=$((attempt + 1))
done

if [ ! -f "$download/valheim_server.x86_64" ]; then
    echo "valheim server download failed after 3 attempts" >&2
    exit 1
fi

# The image would copy it over on first start anyway; doing it now makes that start quick.
cp -a "$download/." "$install/"
rm -rf "$install/steamapps"
echo "valheim server installed"
