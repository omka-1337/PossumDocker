#!/bin/sh
# Installs HLDS + Counter-Strike into /data. Runs inside steamcmd/steamcmd.
set -eu

# App 90 is known to stop half-way and report success; retry until the binary is there.
attempt=1
while [ "$attempt" -le 5 ]; do
    echo "steamcmd attempt $attempt"
    steamcmd +force_install_dir /data +login anonymous \
        +app_set_config 90 mod cstrike +app_update 90 validate +quit || true
    if [ -f /data/hlds_linux ] && [ -d /data/cstrike ]; then
        echo "hlds installed"
        exit 0
    fi
    attempt=$((attempt + 1))
done

echo "hlds install failed after 5 attempts" >&2
exit 1
