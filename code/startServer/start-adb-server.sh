#!/usr/bin/env bash
# 拉起本机产品 Agent，并用 adb reverse 把手机接到该进程。
set -euo pipefail
PORT="${PORT:-17890}"
FILE_PORT="${FILE_PORT:-17891}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
AGENT_ROOT="$(cd "${ROOT}/../agent" && pwd)"

ENV_FILE="${ROOT}/env.sh"
if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  echo "loaded ${ENV_FILE}"
else
  echo "no ${ENV_FILE} (DeepSeek/OpenAI keys will be empty unless already exported)"
fi

# main.py authenticates Codex before initializing either server listener.

if ! command -v adb >/dev/null 2>&1; then
  echo "adb not found. Install Android platform-tools and add them to PATH."
  exit 1
fi

free_port() {
  local pids
  pids="$(pgrep -f "${AGENT_ROOT}/main.py" || true)"
  if [[ -n "${pids}" ]]; then
    echo "stopping previous agent: ${pids}"
    # shellcheck disable=SC2086
    kill ${pids} 2>/dev/null || true
    sleep 0.4
  fi
  for port in "${PORT}" "${FILE_PORT}"; do
    pids="$(lsof -nP -t -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -n "${pids}" ]]; then
      echo "port ${port} still in use by ${pids}, force stop"
      # shellcheck disable=SC2086
      kill -9 ${pids} 2>/dev/null || true
    fi
  done
  sleep 0.2
}

free_port

adb start-server
# The Agent owns a watchdog for its full lifetime. USB reconnects and adb
# daemon restarts can erase reverse mappings even while `adb devices` is OK.
ADB_ARGS=(--adb-reverse)
if [[ -n "${ANDROID_SERIAL:-}" ]]; then
  ADB_ARGS+=(--adb-serial "${ANDROID_SERIAL}")
fi
echo "phone should connect to 127.0.0.1:${PORT}"
echo "starting product agent from ${AGENT_ROOT}"
cd "${AGENT_ROOT}"
exec python3 "${AGENT_ROOT}/main.py" --port "${PORT}" --file-port "${FILE_PORT}" --runtime "${AUTO_PROCEDURE_RUNTIME:-apk}" "${ADB_ARGS[@]}"
