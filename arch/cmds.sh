1. Back up Compose
cp -p /apps/srv_mlengineering/nim/docker-compose-extended.yml /apps/srv_mlengineering/nim/docker-compose-extended.yml.before-262k
2. Update the context configuration
sed -i '/^  nim-qwen3vl:/,/^  nim-gptoss120b:/ {
s|^[[:space:]]*NIM_MAX_MODEL_LEN:.*|      NIM_MAX_MODEL_LEN: "262144"|
s|^[[:space:]]*NIM_PASSTHROUGH_ARGS:.*|      NIM_PASSTHROUGH_ARGS: "--gpu-memory-utilization 0.80 --trust-remote-code --max-num-seqs 4 --max-num-batched-tokens 8192 --enable-chunked-prefill --enforce-eager --enable-auto-tool-choice --tool-call-parser qwen3_coder"|
}' /apps/srv_mlengineering/nim/docker-compose-extended.yml
3. Confirm the new values
sed -n '/^  nim-qwen3vl:/,/^  nim-gptoss120b:/p' /apps/srv_mlengineering/nim/docker-compose-extended.yml

Look for:

NIM_MAX_MODEL_LEN: "262144"
NIM_PASSTHROUGH_ARGS: "--gpu-memory-utilization 0.80 --trust-remote-code --max-num-seqs 4 --max-num-batched-tokens 8192 --enable-chunked-prefill --enforce-eager --enable-auto-tool-choice --tool-call-parser qwen3_coder"
4. Validate Compose
podman-compose -f /apps/srv_mlengineering/nim/docker-compose-extended.yml config >/dev/null && echo "Compose configuration OK"

Do not continue if this reports an error.

5. Recreate only Qwen
podman-compose -f /apps/srv_mlengineering/nim/docker-compose-extended.yml up -d --no-deps --force-recreate nim-qwen3vl
6. Watch it load
podman logs --tail 100 -f nim-qwen3vl

Press Ctrl+C after:

Application startup complete
7. Confirm the active configuration
podman inspect nim-qwen3vl --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^NIM_(MAX_MODEL_LEN|PASSTHROUGH_ARGS|TENSOR_PARALLEL_SIZE)='

Expected:

NIM_MAX_MODEL_LEN=262144
NIM_TENSOR_PARALLEL_SIZE=2
8. Confirm capacity and health
curl -fsS http://127.0.0.1:8899/metrics | grep 'vllm:cache_config_info'
curl -fsS http://127.0.0.1:8899/v1/models | jq
9. Test through Nginx
curl -sk --max-time 600 https://127.0.0.1:8443/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"qwen3-vl-235b-awq","messages":[{"role":"user","content":"Reply only with OK"}],"max_tokens":128}' | jq

If startup fails, restore:

cp -p /apps/srv_mlengineering/nim/docker-compose-extended.yml.before-262k /apps/srv_mlengineering/nim/docker-compose-extended.yml
podman-compose -f /apps/srv_mlengineering/nim/docker-compose-extended.yml up -d --no-deps --force-recreate nim-qwen3vl