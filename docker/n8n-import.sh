#!/bin/sh
# One-off container: import the backend credential + workflows into n8n and publish them.
# Runs before n8n starts. Re-imports ONLY when the workflow files changed (a hash is remembered),
# so edits made in the n8n editor (e.g. selecting a Gmail credential) survive restarts.
# Force a re-import with FORCE_IMPORT=true.
set -e

mkdir -p /tmp/wf
for f in /workflows/*.json; do
  # Workflows point at the local dev backend; inside Docker the backend is http://backend:8000
  sed "s#http://127.0.0.1:8000/api/v1#${BACKEND_URL}#g" "$f" > "/tmp/wf/$(basename "$f")"
done

HASH=$(cat /tmp/wf/*.json | sha256sum | cut -d' ' -f1)
MARK=/home/node/.n8n/.wa-agent-import
if [ -f "$MARK" ] && [ "$(cat "$MARK")" = "$HASH" ] && [ "${FORCE_IMPORT:-false}" != "true" ]; then
  echo "[n8n-import] workflows unchanged - nothing to do"
  exit 0
fi

# The credential exists only for a moment in /tmp of this throw-away container; n8n stores it encrypted.
printf '[{"id":"WaAgentBackendK1","name":"WA Agent Backend (internal key)","type":"httpHeaderAuth","data":{"name":"X-Internal-Api-Key","value":"%s"}}]' \
  "$BACKEND_INTERNAL_API_KEY" > /tmp/cred.json
n8n import:credentials --input=/tmp/cred.json
rm -f /tmp/cred.json

n8n import:workflow --separate --input=/tmp/wf
for id in WaAgentErrors001 WaAgentLeadNoti1 WaAgentHandoff01 WaAgentFollowUp1 WaAgentIncoming1 WaAgentErrDrill1; do
  n8n publish:workflow --id="$id"
done

echo "$HASH" > "$MARK"
echo "[n8n-import] done"
