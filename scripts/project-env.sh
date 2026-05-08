#!/usr/bin/env bash
# Source this file before running local development commands.

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "Source this script instead of executing it: source scripts/project-env.sh" >&2
  exit 64
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_PARENT="$(cd "$PROJECT_ROOT/.." && pwd)"

if [[ -z "${RECIPE_SITE_HOME:-}" ]]; then
  if [[ "$(basename "$PROJECT_PARENT")" == "recipe-site-home" ]]; then
    export RECIPE_SITE_HOME="$PROJECT_PARENT"
  else
    export RECIPE_SITE_HOME="$PROJECT_ROOT/.home"
  fi
fi

export HOME="$RECIPE_SITE_HOME"
export XDG_CACHE_HOME="$HOME/.cache"
export XDG_CONFIG_HOME="$HOME/.config"
export XDG_DATA_HOME="$HOME/.local/share"
export XDG_STATE_HOME="$HOME/.local/state"
export PYTHONPATH="${PYTHONPATH:-src}"
export PYTHONUSERBASE="$HOME/.local"
export UV_CACHE_DIR="$XDG_CACHE_HOME/uv"
export UV_LINK_MODE=copy
export PIP_CACHE_DIR="$XDG_CACHE_HOME/pip"
export DOCKER_CONFIG="$XDG_CONFIG_HOME/docker"
export PATH="$HOME/.local/bin:$PATH"

mkdir -p "$HOME" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME"
