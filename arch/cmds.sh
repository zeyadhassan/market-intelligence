podman exec nginx-reverseproxy nginx -t

curl -fsS http://127.0.0.1:8897/v1/models | jq -r '.data[].id'
curl -fsS http://127.0.0.1:8899/v1/models | jq -r '.data[].id'

curl -sk --max-time 300 \
  https://127.0.0.1:8443/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"qwen3-vl-235b-awq",
    "messages":[{"role":"user","content":"Reply only with OK"}],
    "max_tokens":16
  }' | jq

  curl -sk --max-time 300 \
  https://127.0.0.1:8443/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"openai/gpt-oss-120b",
    "messages":[{"role":"user","content":"Reply only with OK"}],
    "max_tokens":16
  }' | jq

  podman inspect nim-qwen3vl \
  --format 'status={{.State.Status}} restart={{.HostConfig.RestartPolicy.Name}} compose_service={{index .Config.Labels "io.podman.compose.service"}}'

podman ps -a --format 'table {{.Names}}\t{{.Status}}' |
grep -E 'nim-qwen3vl|nim-gptoss120b|nim-qwen7b|nim-llama70b'