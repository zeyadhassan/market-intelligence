Paste these commands one at a time:

podman stop -t 120 nim-qwen3vl
podman rename nim-qwen3vl nim-qwen3vl-manual-backup
podman-compose -f /apps/srv_mlengineering/nim/docker-compose-extended.yml up -d nim-qwen3vl

Watch it start:

podman logs --tail 100 -f nim-qwen3vl

When startup completes, press Ctrl+C, then run:

curl -fsS http://127.0.0.1:8899/v1/models | jq
podman inspect nim-qwen3vl --format 'status={{.State.Status}} restart={{.HostConfig.RestartPolicy.Name}} compose_service={{index .Config.Labels "io.podman.compose.service"}}'

Then test through Nginx:

curl -sk --max-time 300 https://127.0.0.1:8443/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"qwen3-vl-235b-awq","messages":[{"role":"user","content":"Reply only with OK"}],"max_tokens":64}' | jq