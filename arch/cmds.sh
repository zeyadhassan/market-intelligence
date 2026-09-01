That only means the log text does not contain those exact phrases. Check the actual running configuration first:

podman inspect nim-qwen3vl --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^NIM_(MAX_MODEL_LEN|PASSTHROUGH_ARGS|TENSOR_PARALLEL_SIZE)='

Then query vLLM metrics:

curl -fsS http://127.0.0.1:8899/metrics | grep -iE 'cache_config_info|num_gpu_blocks|block_size|cache_usage' | head -30

If that returns nothing, try:

curl -fsS http://127.0.0.1:8899/v1/metrics | grep -iE 'cache_config_info|num_gpu_blocks|block_size|cache_usage' | head -30

Finally, inspect broader startup logs without the narrow filter:

podman logs --tail 300 nim-qwen3vl 2>&1 | grep -iE 'cache|block|token|concurr|model.len|chunk|memory' | tail -100

If that is also blank, show the last unfiltered lines:

podman logs --tail 100 nim-qwen3vl

Paste the outputs. Do not switch to 262K yet—we need either the KV block count or the startup error/capacity information first.

__________________________________________________________________________

1. Check current capacity
podman logs nim-qwen3vl 2>&1 | grep -iE 'GPU KV cache size|maximum concurrency|max.model.len|chunked prefill' | tail -50

Also confirm that the required runtime options exist:

podman exec nim-qwen3vl python -m vllm.entrypoints.openai.api_server --help 2>&1 | grep -w -- '--enable-chunked-prefill'
podman exec nim-qwen3vl python -m vllm.entrypoints.openai.api_server --help 2>&1 | grep -w qwen3_coder

Proceed only if:

qwen3_coder is listed;
--enable-chunked-prefill is supported;
GPU KV cache size is at least approximately 262144 tokens.

If KV capacity is below 262,144, use 131,072 first rather than forcing 262K.

2. Back up Compose
cp -p /apps/srv_mlengineering/nim/docker-compose-extended.yml /apps/srv_mlengineering/nim/docker-compose-extended.yml.before-262k
3. Apply 262K, chunked prefill, and tool calling together

This uses one concurrent sequence initially. A 262K request can consume most of the available KV cache.

sed -i '/^  nim-qwen3vl:/,/^  nim-gptoss120b:/ {
s|^[[:space:]]*NIM_MAX_MODEL_LEN:.*|      NIM_MAX_MODEL_LEN: "262144"|
s|^[[:space:]]*NIM_PASSTHROUGH_ARGS:.*|      NIM_PASSTHROUGH_ARGS: "--gpu-memory-utilization 0.80 --trust-remote-code --max-num-seqs 1 --max-num-batched-tokens 8192 --enable-chunked-prefill --enforce-eager --enable-auto-tool-choice --tool-call-parser qwen3_coder"|
}' /apps/srv_mlengineering/nim/docker-compose-extended.yml

Chunked prefill allows the 262K prompt to be processed in smaller scheduler chunks governed by --max-num-batched-tokens 8192; it does not reduce the total context window. vLLM chunked-prefill documentation

Verify the result:

sed -n '/^  nim-qwen3vl:/,/^  nim-gptoss120b:/p' /apps/srv_mlengineering/nim/docker-compose-extended.yml

The important lines should be:

NIM_MAX_MODEL_LEN: "262144"
NIM_PASSTHROUGH_ARGS: "--gpu-memory-utilization 0.80 --trust-remote-code --max-num-seqs 1 --max-num-batched-tokens 8192 --enable-chunked-prefill --enforce-eager --enable-auto-tool-choice --tool-call-parser qwen3_coder"
4. Validate and recreate only Qwen
podman-compose -f /apps/srv_mlengineering/nim/docker-compose-extended.yml config >/dev/null && echo "Compose configuration OK"
podman-compose -f /apps/srv_mlengineering/nim/docker-compose-extended.yml up -d --no-deps --force-recreate nim-qwen3vl

Watch startup:

podman logs --tail 100 -f nim-qwen3vl

Press Ctrl+C after startup completes.

5. Confirm the actual capacity
podman logs nim-qwen3vl 2>&1 | grep -iE 'GPU KV cache size|maximum concurrency|max.model.len|chunked prefill|error|traceback' | tail -100
curl -fsS http://127.0.0.1:8899/v1/models | jq

Then perform a normal gateway smoke test:

curl -sk --max-time 600 https://127.0.0.1:8443/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"qwen3-vl-235b-awq","messages":[{"role":"user","content":"Reply only with OK"}],"max_tokens":128}' | jq
If Qwen fails to start

Restore the previous configuration:

cp -p /apps/srv_mlengineering/nim/docker-compose-extended.yml.before-262k /apps/srv_mlengineering/nim/docker-compose-extended.yml
podman-compose -f /apps/srv_mlengineering/nim/docker-compose-extended.yml up -d --no-deps --force-recreate nim-qwen3vl

Do not immediately raise --gpu-memory-utilization to 0.90 or add FP8 KV cache. Those should be separate experiments if 262K does not fit.

One more consideration: Nginx’s default request-body limit and the application’s existing 120-second timeout may block genuine 250K requests even if the model starts successfully. Check them before sending a long prompt:

grep -nE 'client_max_body_size|client_body_buffer_size|proxy_(connect|send|read)_timeout' /apps/srv_mlengineering/nim/nginx/nginx_router_hybrid_extended.conf

A 262K context being configured does not guarantee that a 250K request will be fast—long prefills can take minutes, especially on the vGPU setup.