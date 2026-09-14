#!/bin/sh
# ENFAL Backend — Container-Entrypoint.
# Railway haengt das Volume unter /data root-eigen ein. Als root: Eigentuemer
# korrigieren (nur Abweichungen, kein pauschales chown -R bei jedem Start),
# danach endgueltig auf appuser wechseln. Laeuft der Container bereits als
# Nicht-root (z.B. docker run --user), wird nur das Kommando gestartet.
set -eu

if [ "$(id -u)" = "0" ]; then
    mkdir -p /data
    find /data \( ! -user appuser -o ! -group appuser \) -exec chown appuser:appuser {} +
    exec setpriv --reuid=appuser --regid=appuser --init-groups "$@"
fi

exec "$@"
