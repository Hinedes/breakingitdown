#!/bin/sh
# Rootless Docker for this WSL host. No systemd PID 1, so this is the service.
set -eu
export XDG_RUNTIME_DIR=/home/iclab/.run
export DOCKER_HOST=unix:///home/iclab/.run/docker.sock
# The default detached-netns mode leaves slirp4netns dead on this WSL kernel.
export DOCKERD_ROOTLESS_ROOTLESSKIT_DETACH_NETNS=false
export DOCKERD_ROOTLESS_ROOTLESSKIT_SLIRP4NETNS_SANDBOX=false
export DOCKERD_ROOTLESS_ROOTLESSKIT_SLIRP4NETNS_SECCOMP=false
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
exec /usr/bin/dockerd-rootless.sh
