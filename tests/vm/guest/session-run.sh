#!/bin/bash
# Runs a command inside the logged-in desktop session of a test VM, from ssh.
#
# A command started straight from ssh has no display and no session D-Bus, and
# pkexec there fails with "Error creating textual authentication agent"
# instead of showing the desktop's password prompt. So take the environment
# of the desktop shell itself and run in a user scope, the way a launcher
# would start the program.
#
# Usage: session-run.sh COMMAND [ARGS...]   (blocks until COMMAND exits)
set -eu

shell_pid=""
for name in gnome-shell plasmashell cinnamon kwin_wayland cosmic-panel xfce4-session; do
    shell_pid=$(pgrep -u "$(id -u)" -x -o "$name" || true)
    [ -n "$shell_pid" ] && break
done
if [ -z "$shell_pid" ]; then
    echo "session-run: no desktop shell is running" >&2
    exit 1
fi

env_args=()
while IFS= read -r -d '' var; do
    env_args+=("$var")
done < "/proc/$shell_pid/environ"
# A launcher also passes the session's systemd environment, which holds the
# X display and the Wayland one. GNOME Shell's own has neither (it is the
# Wayland server and starts Xwayland itself). Without DISPLAY TuxTalk's
# service fell back to native Wayland, where its floating labels take the
# keyboard: the held-send space "bug" of 2026-10-18. Without WAYLAND_DISPLAY
# it took the session for X11: it typed instead of pasting, and voice editing
# said it needs Wayland, unlike after a login.
while IFS= read -r var; do
    env_args+=("$var")
done < <(systemctl --user show-environment | grep -E '^(DISPLAY|XAUTHORITY|WAYLAND_DISPLAY)=')

exec systemd-run --user --scope --quiet --collect env -i "${env_args[@]}" "$@"
