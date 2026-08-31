cd /apps/srv_mlengineering/nim

rg --files . | rg 'docker-compose.*extended.*\.(yml|yaml)$|nginx_router_hybrid_extended\.conf$|router_hybrid_extended\.js$'

AI_COMPOSE_FILE=/apps/srv_mlengineering/nim/docker-compose-extended.yml
AI_NGINX_FILE=/apps/srv_mlengineering/nim/nginx/nginx_router_hybrid_extended.conf
AI_ROUTER_FILE=/apps/srv_mlengineering/nim/nginx/router_hybrid_extended.js

ls -l "$AI_COMPOSE_FILE" "$AI_NGINX_FILE" "$AI_ROUTER_FILE"

podman --version
podman-compose --version 2>&1 || true

podman-compose -f "$AI_COMPOSE_FILE" config --services

rg -n \
'nim-qwen7b|nim-llama70b|nim-qwen3vl|nim-gptoss120b|nginx-reverseproxy|depends_on:|restart:|nvidia\.com/gpu|8889|8890|8897|8899' \
"$AI_COMPOSE_FILE"

rg -n -C 4 \
'nim-qwen7b|nim-llama70b|nim-qwen3vl|nim-gptoss120b|backend_qwen|backend_llama|backend_gptoss|8889|8890|8897|8899' \
"$AI_NGINX_FILE"

rg -n -C 3 \
'qwen|llama|gpt-oss|internalRedirect|ollama' \
"$AI_ROUTER_FILE"


for AI_CONTAINER in \
  nginx-reverseproxy \
  nim-qwen7b \
  nim-llama70b \
  nim-qwen3vl \
  nim-gptoss120b
do
  podman inspect "$AI_CONTAINER" \
    --format 'name={{.Name}} status={{.State.Status}} network={{.HostConfig.NetworkMode}} restart={{.HostConfig.RestartPolicy.Name}} compose_service={{index .Config.Labels "com.docker.compose.service"}} podman_compose_service={{index .Config.Labels "io.podman.compose.service"}}' \
    2>&1
done

for AI_PORT in 8889 8890 8897 8899
do
  printf 'PORT %s: ' "$AI_PORT"
  curl -fsS --max-time 3 "http://127.0.0.1:${AI_PORT}/v1/models" |
    jq -r '.data[].id' 2>/dev/null || printf 'DOWN\n'
done