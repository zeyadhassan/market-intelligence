Check the current context setting:

sed -n '/^  nim-qwen3vl:/,/^  nim-gptoss120b:/p' /apps/srv_mlengineering/nim/docker-compose-extended.yml

To change it from 8K to 32K, run:

cp -p /apps/srv_mlengineering/nim/docker-compose-extended.yml /apps/srv_mlengineering/nim/docker-compose-extended.yml.before-32k
sed -i '/^  nim-qwen3vl:/,/^  nim-gptoss120b:/ {
s/NIM_MAX_MODEL_LEN: "8192"/NIM_MAX_MODEL_LEN: "32768"/
s/--max-num-seqs 8/--max-num-seqs 4/
}' /apps/srv_mlengineering/nim/docker-compose-extended.yml

Validate:

podman-compose -f /apps/srv_mlengineering/nim/docker-compose-extended.yml config >/dev/null && echo "Compose configuration OK"

Recreate only Qwen with the new 32K configuration:

podman-compose -f /apps/srv_mlengineering/nim/docker-compose-extended.yml up -d --no-deps --force-recreate nim-qwen3vl

Watch startup:

podman logs --tail 100 -f nim-qwen3vl

Once ready, verify:

podman inspect nim-qwen3vl --format 'status={{.State.Status}} restart={{.HostConfig.RestartPolicy.Name}} compose_service={{index .Config.Labels "io.podman.compose.service"}}'
podman logs nim-qwen3vl 2>&1 | grep -iE 'max model len|GPU KV cache size|maximum concurrency|chunked prefill|error|traceback' | tail -50
curl -sk --max-time 300 https://127.0.0.1:8443/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"qwen3-vl-235b-awq","messages":[{"role":"user","content":"Reply only with OK"}],"max_tokens":128}' | jq

Nginx does not need restarting because the Qwen endpoint remains 10.1.94.110:8899