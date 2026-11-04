#!/bin/sh
# Stop only this user's rootless daemon tree.
set -eu
pkill -TERM -f '/usr/bin/dockerd-rootless.sh' 2>/dev/null || true
sleep 3
pkill -KILL -f '/usr/bin/dockerd-rootless.sh' 2>/dev/null || true
pkill -KILL -f 'rootlesskit.*dockerd-rootless' 2>/dev/null || true
