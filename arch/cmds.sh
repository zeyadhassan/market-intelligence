The Nginx configuration is correct. The 502 occurs because the current embedding container uses network=pasta; the bridge-networked Nginx container cannot reach that published port through 10.1.94.110:8896.

The jq error only happened because Nginx returned an HTML 502 page instead of JSON.

Proceed with replacing the manual container with the Compose-managed bridge container.

Confirm your credential remains exported:

test -n "${NGC_API_KEY:-}" && echo "NGC_API_KEY available" || echo "STOP: NGC_API_KEY missing"

Stop the manual container:

podman stop -t 120 llama-3.2-nv-embedqa

Rename it as a rollback copy:

podman rename llama-3.2-nv-embedqa llama-3.2-nv-embedqa-manual-backup

Start the Compose service:

podman-compose -f /apps/srv_mlengineering/nim/docker-compose-extended.yml \
  up -d --no-deps nim-llama-embedqa

Check its status:

podman ps -a --filter name=llama-3.2-nv-embedqa \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

Watch startup:

podman logs --tail 100 -f llama-3.2-nv-embedqa

Once the service is ready, press Ctrl+C.

Verify that it is now Compose-managed and bridge-networked:

podman inspect llama-3.2-nv-embedqa \
  --format 'status={{.State.Status}} restart={{.HostConfig.RestartPolicy.Name}} network={{.HostConfig.NetworkMode}} compose_service={{index .Config.Labels "io.podman.compose.service"}}'

Expected:

status=running restart=unless-stopped network=bridge compose_service=nim-llama-embedqa

Test it directly:

curl -fsS http://127.0.0.1:8896/v1/models | jq

Then test the gateway while capturing the HTTP status:

curl -sk --max-time 300 \
  -o /tmp/embed-gateway-response.json \
  -w 'HTTP status: %{http_code}\n' \
  https://127.0.0.1:8443/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{
    "input":["What services does Commercial Bank provide?"],
    "model":"nvidia/llama-3.2-nv-embedqa-1b-v2",
    "input_type":"query",
    "modality":"text",
    "encoding_format":"float"
  }'

Expected:

HTTP status: 200

Display only the useful response information:

jq '{model,dimensions:(.data[0].embedding|length),usage}' /tmp/embed-gateway-response.json

No additional Nginx reload should be necessary because its upstream remains 10.1.94.110:8896.

If it still returns 502, run:

podman logs --tail 30 nginx-reverseproxy

and:

podman port llama-3.2-nv-embedqa

Leave llama-3.2-nv-embedqa-manual-backup stopped for now as a rollback copy.