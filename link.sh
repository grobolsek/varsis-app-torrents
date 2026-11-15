#!/bin/sh
# For local development: link this plugin into a Varsis checkout, so the app run from there
# (uvicorn --reload, npm run dev) includes it and picks up edits made here.
#   ./link.sh [path to varsis-app]      default: ../varsis-app
#   ./link.sh --remove [path]           take the links out again
set -e
here=$(cd "$(dirname "$0")" && pwd)
if [ "$1" = "--remove" ]; then remove=1; shift; fi
core=$(cd "${1:-$here/../varsis-app}" && pwd)
for pair in "backend/torrents:backend/app/plugins/torrents" "frontend/torrents:frontend/src/plugins/torrents"; do
  from=$here/${pair%%:*}
  to=$core/${pair##*:}
  if [ -e "$to" ] && [ ! -L "$to" ]; then
    echo "$to exists and is not a link; not touching it" >&2
    exit 1
  fi
  rm -f "$to"
  if [ -z "$remove" ]; then
    ln -s "$from" "$to"
    echo "linked $to -> $from"
  else
    echo "removed $to"
  fi
done
