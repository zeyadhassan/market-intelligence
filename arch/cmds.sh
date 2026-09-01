The model works correctly and produces 2,048-dimensional embeddings. We’ll:

Make it Compose-managed on GPU 0.
Keep it out of Nginx depends_on, so it cannot block gateway restarts.
Route POST /v1/embeddings directly to port 8896.
Leave router_hybrid_extended.js unchanged.

Run each command separately.

1. Prepare and back up
cd /apps/srv_mlengineering/nim
cp -p docker-compose-extended.yml docker-compose-extended.yml.before-embedqa
cp -p nginx/nginx_router_hybrid_extended.conf nginx/nginx_router_hybrid_extended.conf.before-embedqa

Ensure the NGC credential is available without displaying it:

test -n "${NGC_API_KEY:-}" && echo "NGC_API_KEY is available" || echo "NGC_API_KEY is not currently exported"

If it is not exported and ~/.ngc_key is your existing secure key file:

export NGC_API_KEY="$(tr -d '\r\n' < ~/.ngc_key)"
test -n "${NGC_API_KEY:-}" && echo "NGC_API_KEY is now available"
2. Add the embedding service to Compose

Paste this entire command:

sed -i '/^  nim-qwen3vl:/i\
  nim-llama-embedqa:\
    image: nvcr.io/nim/nvidia/llama-3.2-nv-embedqa-1b-v2:1.10\
    container_name: llama-3.2-nv-embedqa\
    restart: unless-stopped\
    security_opt:\
      - "label=disable"\
    ipc: host\
    ports:\
      - "8896:8000"\
    environment:\
      NGC_API_KEY: "${NGC_API_KEY}"\
      NO_PROXY: "localhost,127.0.0.1,::1,.cbq.com.qa,10.0.0.0/8"\
      no_proxy: "localhost,127.0.0.1,::1,.cbq.com.qa,10.0.0.0/8"\
    volumes:\
      - "/apps/srv_mlengineering/nim-cache:/opt/nim/.cache"\
    devices:\
      - "nvidia.com/gpu=0"\
\
' docker-compose-extended.yml

Confirm the new block:

sed -n '/^  nim-llama-embedqa:/,/^  nim-qwen3vl:/p' docker-compose-extended.yml

Validate Compose:

podman-compose -f docker-compose-extended.yml config >/dev/null && echo "Compose configuration OK"

Confirm the service exists:

podman-compose -f docker-compose-extended.yml config --services | grep nim-llama-embedqa

Do not continue if Compose reports an unset NGC_API_KEY or YAML error.

3. Add the Nginx embedding route

This command modifies the bind-mounted Nginx file in place, preserving its inode:

python3 -c 'p="/apps/srv_mlengineering/nim/nginx/nginx_router_hybrid_extended.conf"; f=open(p,"r+"); s=f.read(); a1="    upstream backend_qwen7b {"; a2="        location @_backend_ollama1"; u="    upstream backend_llama_embed {\n        server 10.1.94.110:8896;\n        keepalive 32;\n    }\n\n"; l="        location = /v1/embeddings {\n            proxy_pass http://backend_llama_embed;\n            proxy_http_version 1.1;\n            proxy_set_header Host $host;\n            proxy_set_header Connection \"\";\n            proxy_connect_timeout 10s;\n            proxy_send_timeout 300s;\n            proxy_read_timeout 300s;\n        }\n\n"; assert "upstream backend_llama_embed" not in s, "embedding upstream already exists"; assert a1 in s, "upstream insertion point not found"; assert a2 in s, "location insertion point not found"; s=s.replace(a1,u+a1,1).replace(a2,l+a2,1); f.seek(0); f.write(s); f.truncate(); f.close()'

Confirm the host file:

grep -nC 4 -E 'backend_llama_embed|/v1/embeddings' nginx/nginx_router_hybrid_extended.conf

Confirm the running container sees it:

podman exec nginx-reverseproxy grep -nC 4 -E 'backend_llama_embed|/v1/embeddings' /etc/nginx/nginx.conf

Validate Nginx:

podman exec nginx-reverseproxy nginx -t

Continue only if it reports:

syntax is ok
test is successful

Reload Nginx:

podman exec nginx-reverseproxy nginx -s reload
4. Test through the gateway before changing the container
curl -sk --max-time 300 https://127.0.0.1:8443/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{
    "input":["What services does Commercial Bank provide?"],
    "model":"nvidia/llama-3.2-nv-embedqa-1b-v2",
    "input_type":"query",
    "modality":"text",
    "encoding_format":"float"
  }' | jq '{model,dimensions:(.data[0].embedding|length),usage}'

Expected:

{
  "model": "nvidia/llama-3.2-nv-embedqa-1b-v2",
  "dimensions": 2048
}
5. Replace the manual container with the Compose service

Stop the manual container:

podman stop -t 120 llama-3.2-nv-embedqa

Preserve it as a rollback:

podman rename llama-3.2-nv-embedqa llama-3.2-nv-embedqa-manual-backup

Start the Compose service:

podman-compose -f docker-compose-extended.yml up -d --no-deps nim-llama-embedqa

Watch startup:

podman logs --tail 100 -f llama-3.2-nv-embedqa

When it is ready, press Ctrl+C and test directly:

curl -fsS http://127.0.0.1:8896/v1/models | jq

Confirm Compose ownership:

podman inspect llama-3.2-nv-embedqa \
  --format 'status={{.State.Status}} restart={{.HostConfig.RestartPolicy.Name}} compose_service={{index .Config.Labels "io.podman.compose.service"}} network={{.HostConfig.NetworkMode}}'

Expected:

status=running restart=unless-stopped compose_service=nim-llama-embedqa

Test through Nginx again:

curl -sk --max-time 300 https://127.0.0.1:8443/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"input":["Test from the gateway"],"model":"nvidia/llama-3.2-nv-embedqa-1b-v2","input_type":"query","modality":"text","encoding_format":"float"}' |
jq '{model,dimensions:(.data[0].embedding|length),usage}'
6. Test from another PC

From an internal PC that can reach the server:

curl -k --max-time 300 https://10.1.94.110:8443/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"input":["External gateway test"],"model":"nvidia/llama-3.2-nv-embedqa-1b-v2","input_type":"query","modality":"text","encoding_format":"float"}'

Use:

input_type: "passage" when indexing documents.
input_type: "query" when embedding a search question.

One security concern: ports 8896 and 8443 are currently bound on all host interfaces, and the gateway reportedly has no authentication. Client networks should be allowed to reach 8443, while direct backend port 8896 should ideally be blocked by the network firewall.