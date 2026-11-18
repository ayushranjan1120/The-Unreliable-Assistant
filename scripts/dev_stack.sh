#!/usr/bin/env bash
# Local dev stack manager (pidfile based; never uses pkill -f). Usage: scripts/dev_stack.sh start|stop|restart|status [api-env...]
# Env: PROFILE (dev-cpu|target), LLM_BACKEND, TIME_SCALE, WORKERS. Re-signs MCP manifests on start (design Gate 13).
cd "$(dirname "$0")/.."
PIDS=/tmp/ua_pids; mkdir -p $PIDS
PY=.venv/bin/python
stop_one() { [ -f $PIDS/$1 ] && kill "$(cat $PIDS/$1)" 2>/dev/null; rm -f $PIDS/$1; }
case "${1:-status}" in
  ui) stop_one ui; nohup .venv/bin/streamlit run app.py --server.headless true --server.port 8501 --browser.gatherUsageStats false > /tmp/ua_ui.log 2>&1 & echo $! > $PIDS/ui
      for i in $(seq 1 30); do curl -s localhost:8501/_stcore/health 2>/dev/null | grep -q ok && { echo "ui up: http://localhost:8501"; exit 0; }; sleep 1; done; echo "ui failed; see /tmp/ua_ui.log"; exit 1 ;;
  stop) stop_one api; stop_one state; stop_one ui; sleep 1; echo stopped ;;
  start)
    $PY -m mcp_servers.manifest "${BUILD_VERSION:-v0}" >/dev/null
    nohup $PY mcp_servers/state_server.py > /tmp/ua_state.log 2>&1 & echo $! > $PIDS/state
    sleep 2
    nohup .venv/bin/uvicorn src.api.main:app --port 8000 --workers "${WORKERS:-1}" --timeout-keep-alive "${KEEPALIVE_S:-75}" --backlog 4096 --log-level warning > /tmp/ua_api.log 2>&1 & echo $! > $PIDS/api
    for i in $(seq 1 60); do curl -s localhost:8000/healthz >/dev/null 2>&1 && { echo "api up"; exit 0; }; sleep 2; done
    echo "api failed to start; see /tmp/ua_api.log"; exit 1 ;;
  restart) "$0" stop; "$0" start ;;
  status) for n in state api ui; do [ -f $PIDS/$n ] && kill -0 "$(cat $PIDS/$n)" 2>/dev/null && echo "$n: running ($(cat $PIDS/$n))" || echo "$n: stopped"; done ;;
esac
