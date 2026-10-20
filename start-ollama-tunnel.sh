#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# start-ollama-tunnel.sh
# Starts Ollama (with external access enabled) + ngrok tunnel,
# then auto-updates OLLAMA_URL on Fly.io so bulk analysis works.
#
# Run this once before doing bulk analysis. Keep it open.
# Usage:  bash start-ollama-tunnel.sh
# ─────────────────────────────────────────────────────────────

APP="stockanalyzer-api"
OLLAMA_PORT=11434
NGROK="/tmp/ngrok-latest/ngrok.exe"
NGROK_LOG="/tmp/ngrok-ollama.log"

# ── 1. Stop old Ollama & restart with ORIGINS=* ─────────────
echo "▶ Starting Ollama with external access enabled..."
taskkill //F //IM "ollama app.exe" > /dev/null 2>&1 || true
taskkill //F //IM "ollama.exe"     > /dev/null 2>&1 || true
sleep 2

cmd //c "set OLLAMA_ORIGINS=* && set OLLAMA_HOST=0.0.0.0 && start /B ollama serve" > /dev/null 2>&1 &
sleep 4

if ! curl -s --max-time 5 "http://localhost:${OLLAMA_PORT}/api/tags" > /dev/null 2>&1; then
  echo "✗ Ollama failed to start. Make sure Ollama is installed."
  exit 1
fi
echo "  ✓ Ollama running (ORIGINS=* enabled)"

# ── 2. Start ngrok tunnel ────────────────────────────────────
echo ""
echo "▶ Starting ngrok tunnel..."
kill $(cat /tmp/ngrok-ollama.pid 2>/dev/null) 2>/dev/null || true
rm -f "$NGROK_LOG"

$NGROK http "$OLLAMA_PORT" --log "$NGROK_LOG" --log-format json > /dev/null 2>&1 &
echo $! > /tmp/ngrok-ollama.pid
sleep 6

# ── 3. Get the public URL ────────────────────────────────────
TUNNEL_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null \
  | python3 -c "import sys,json; t=json.load(sys.stdin)['tunnels']; print(t[0]['public_url'] if t else '')" 2>/dev/null)

if [ -z "$TUNNEL_URL" ]; then
  echo "✗ Could not get ngrok URL. ngrok log:"
  tail -5 "$NGROK_LOG"
  exit 1
fi
echo "  ✓ Tunnel: $TUNNEL_URL"

# ── 4. Verify tunnel reaches Ollama ─────────────────────────
echo ""
echo "▶ Verifying tunnel..."
TEST=$(curl -s --max-time 10 "$TUNNEL_URL/api/tags" 2>/dev/null)
if echo "$TEST" | grep -q '"models"'; then
  echo "  ✓ Tunnel verified — Ollama is reachable"
else
  echo "  ⚠ Verification inconclusive — continuing anyway"
fi

# ── 5. Update Fly.io secret ──────────────────────────────────
echo ""
echo "▶ Updating OLLAMA_URL on Fly.io ($APP)..."
fly secrets set "OLLAMA_URL=${TUNNEL_URL}" --app "$APP" 2>&1 | grep -E "✔|update succeeded|Error"

# ── 6. Wait for backend to restart & confirm ────────────────
echo ""
echo "▶ Waiting for backend restart..."
for i in $(seq 1 15); do
  sleep 2
  STATUS=$(curl -s --max-time 5 "https://${APP}.fly.dev/api/ollama/status" 2>/dev/null)
  if echo "$STATUS" | grep -q '"running":true'; then
    echo "  ✓ Backend connected to Ollama!"
    echo "  $STATUS"
    break
  fi
done

# ── 7. Done ──────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════"
echo "✓ All set! Go run Bulk Analyze:"
echo "  https://stockanalyzer-web.fly.dev/"
echo ""
echo "  Tunnel: $TUNNEL_URL"
echo "  Keep this window open while analysis runs."
echo "  Press Ctrl+C to stop when done."
echo "═══════════════════════════════════════════════════════"

cleanup() {
  echo ""
  echo "Stopping ngrok..."
  kill $(cat /tmp/ngrok-ollama.pid 2>/dev/null) 2>/dev/null
  echo "Done. Run this script again next time before bulk analysis."
  exit 0
}
trap cleanup INT TERM

wait
