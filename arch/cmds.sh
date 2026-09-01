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
OUTPUT:
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ podman inspect nim-qwen3vl --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^NIM_(MAX_MODEL_LEN|PASSTHROUGH_ARGS|TENSOR_PARALLEL_SIZE)='
NIM_MAX_MODEL_LEN=32768
NIM_TENSOR_PARALLEL_SIZE=2
NIM_PASSTHROUGH_ARGS=--gpu-memory-utilization 0.80 --trust-remote-code --max-num-seqs 4 --max-num-batched-tokens 8192 --enforce-eager --enable-auto-tool-choice --tool-call-parser qwen3_coder
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ curl -fsS http://127.0.0.1:8899/metrics | grep -iE 'cache_config_info|num_gpu_blocks|block_size|cache_usage' | head -30
# HELP vllm:kv_cache_usage_perc KV-cache usage. 1 means 100 percent usage.
# TYPE vllm:kv_cache_usage_perc gauge
vllm:kv_cache_usage_perc{engine="0",model_name="qwen3-vl-235b-awq"} 0.0
# HELP vllm:cache_config_info Information of the LLMEngine CacheConfig
# TYPE vllm:cache_config_info gauge
vllm:cache_config_info{_block_size_resolved="True",block_size="16",cache_dtype="auto",calculate_kv_scales="False",enable_prefix_caching="True",engine="0",gpu_memory_utilization="0.8",is_attention_free="False",kv_cache_dtype_skip_layers="[]",kv_cache_max_concurrency="16.8310546875",kv_cache_memory_bytes="None",kv_cache_size_tokens="551520",kv_offloading_backend="native",kv_offloading_size="None",kv_sharing_fast_prefill="False",mamba_block_size="None",mamba_cache_dtype="auto",mamba_cache_mode="none",mamba_page_size_padded="None",mamba_ssm_cache_dtype="auto",num_cpu_blocks="None",num_gpu_blocks="34470",num_gpu_blocks_override="None",prefix_caching_hash_algo="sha256",prefix_match_unit="None",skip_page_size_padded="None",sliding_window="None",user_specified_block_size="False",user_specified_mamba_block_size="False"} 1.0
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ podman logs --tail 300 nim-qwen3vl 2>&1 | grep -iE 'cache|block|token|concurr|model.len|chunk|memory' | tail -100
(Worker pid=430) WARNING 08-31 13:09:52 [symm_mem.py:106] SymmMemCommunicator: symmetric memory multicast operations are not supported.
(Worker pid=431) WARNING 08-31 13:09:52 [symm_mem.py:106] SymmMemCommunicator: symmetric memory multicast operations are not supported.
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ podman logs --tail 100 nim-qwen3vl
(Worker_TP0 pid=430) WARNING 08-31 13:09:56 [vllm.py:1213] Inductor compilation was disabled by user settings, optimizations settings that are only active during inductor compilation will be ignored.
Loading safetensors checkpoint shards:   0% Completed | 0/42 [00:00<?, ?it/s]
Loading safetensors checkpoint shards:   2% Completed | 1/42 [00:00<00:09,  4.16it/s]
Loading safetensors checkpoint shards:   5% Completed | 2/42 [00:00<00:14,  2.75it/s]
Loading safetensors checkpoint shards:   7% Completed | 3/42 [00:01<00:15,  2.47it/s]
Loading safetensors checkpoint shards:  10% Completed | 4/42 [00:01<00:17,  2.20it/s]
Loading safetensors checkpoint shards:  12% Completed | 5/42 [00:02<00:16,  2.19it/s]
Loading safetensors checkpoint shards:  14% Completed | 6/42 [00:02<00:16,  2.18it/s]
Loading safetensors checkpoint shards:  17% Completed | 7/42 [00:03<00:15,  2.20it/s]
Loading safetensors checkpoint shards:  19% Completed | 8/42 [00:03<00:17,  1.97it/s]
Loading safetensors checkpoint shards:  21% Completed | 9/42 [00:04<00:17,  1.85it/s]
Loading safetensors checkpoint shards:  24% Completed | 10/42 [00:04<00:17,  1.84it/s]
Loading safetensors checkpoint shards:  26% Completed | 11/42 [00:05<00:17,  1.75it/s]
Loading safetensors checkpoint shards:  29% Completed | 12/42 [00:05<00:16,  1.87it/s]
Loading safetensors checkpoint shards:  31% Completed | 13/42 [00:06<00:15,  1.83it/s]
Loading safetensors checkpoint shards:  33% Completed | 14/42 [00:06<00:14,  1.87it/s]
Loading safetensors checkpoint shards:  36% Completed | 15/42 [00:07<00:14,  1.91it/s]
Loading safetensors checkpoint shards:  38% Completed | 16/42 [00:07<00:13,  1.99it/s]
Loading safetensors checkpoint shards:  40% Completed | 17/42 [00:08<00:12,  2.06it/s]
Loading safetensors checkpoint shards:  43% Completed | 18/42 [00:08<00:11,  2.08it/s]
Loading safetensors checkpoint shards:  45% Completed | 19/42 [00:09<00:10,  2.13it/s]
Loading safetensors checkpoint shards:  48% Completed | 20/42 [00:09<00:10,  2.06it/s]
Loading safetensors checkpoint shards:  50% Completed | 21/42 [00:10<00:11,  1.89it/s]
Loading safetensors checkpoint shards:  52% Completed | 22/42 [00:10<00:10,  1.99it/s]
Loading safetensors checkpoint shards:  55% Completed | 23/42 [00:11<00:09,  2.05it/s]
Loading safetensors checkpoint shards:  57% Completed | 24/42 [00:11<00:08,  2.09it/s]
Loading safetensors checkpoint shards:  60% Completed | 25/42 [00:12<00:07,  2.13it/s]
Loading safetensors checkpoint shards:  62% Completed | 26/42 [00:12<00:07,  2.16it/s]
Loading safetensors checkpoint shards:  64% Completed | 27/42 [00:13<00:06,  2.17it/s]
Loading safetensors checkpoint shards:  67% Completed | 28/42 [00:13<00:06,  2.18it/s]
Loading safetensors checkpoint shards:  69% Completed | 29/42 [00:14<00:05,  2.21it/s]
Loading safetensors checkpoint shards:  71% Completed | 30/42 [00:14<00:05,  2.20it/s]
Loading safetensors checkpoint shards:  74% Completed | 31/42 [00:15<00:05,  2.08it/s]
Loading safetensors checkpoint shards:  76% Completed | 32/42 [00:15<00:04,  2.14it/s]
Loading safetensors checkpoint shards:  79% Completed | 33/42 [00:15<00:04,  2.16it/s]
Loading safetensors checkpoint shards:  81% Completed | 34/42 [00:16<00:03,  2.16it/s]
Loading safetensors checkpoint shards:  83% Completed | 35/42 [00:16<00:03,  2.19it/s]
Loading safetensors checkpoint shards:  86% Completed | 36/42 [00:17<00:02,  2.19it/s]
Loading safetensors checkpoint shards:  88% Completed | 37/42 [00:17<00:02,  2.17it/s]
Loading safetensors checkpoint shards:  90% Completed | 38/42 [00:18<00:01,  2.05it/s]
Loading safetensors checkpoint shards:  93% Completed | 39/42 [00:18<00:01,  2.09it/s]
Loading safetensors checkpoint shards:  95% Completed | 40/42 [00:19<00:00,  2.08it/s]
Loading safetensors checkpoint shards:  98% Completed | 41/42 [00:19<00:00,  2.09it/s]
Loading safetensors checkpoint shards: 100% Completed | 42/42 [00:20<00:00,  2.40it/s]
Loading safetensors checkpoint shards: 100% Completed | 42/42 [00:20<00:00,  2.10it/s]
(Worker_TP0 pid=430)
(Worker_TP1 pid=431) 2026-08-31 13:10:54,089 - INFO - autotuner.py:651 - flashinfer.jit: [Autotuner]: Autotuning process starts ...
(Worker_TP0 pid=430) 2026-08-31 13:10:54,102 - INFO - autotuner.py:651 - flashinfer.jit: [Autotuner]: Autotuning process starts ...
(Worker_TP1 pid=431) 2026-08-31 13:10:54,518 - INFO - autotuner.py:674 - flashinfer.jit: [Autotuner]: Autotuning process ends
(Worker_TP0 pid=430) 2026-08-31 13:10:54,518 - INFO - autotuner.py:674 - flashinfer.jit: [Autotuner]: Autotuning process ends
(EngineCore pid=323) [transformers] The `use_fast` parameter is deprecated and will be removed in a future version. Use `backend="torchvision"` instead of `use_fast=True`, or `backend="pil"` instead of `use_fast=False`.
(EngineCore pid=323) WARNING 08-31 13:11:06 [vllm.py:1163] Enforce eager set, disabling torch.compile and CUDAGraphs. This is equivalent to setting -cc.mode=none -cc.cudagraph_mode=none
(EngineCore pid=323) WARNING 08-31 13:11:06 [vllm.py:1213] Inductor compilation was disabled by user settings, optimizations settings that are only active during inductor compilation will be ignored.
(APIServer pid=77) WARNING 08-31 13:11:06 [model.py:1546] Default vLLM sampling parameters have been overridden by the model's `generation_config.json`: `{'repetition_penalty': 1.0, 'temperature': 0.7, 'top_k': 20, 'top_p': 0.8}`. If this is not intended, please relaunch vLLM instance with `--generation-config vllm`.
(APIServer pid=77) INFO:     Started server process [77]
(APIServer pid=77) INFO:     Waiting for application startup.
(APIServer pid=77) INFO:     Application startup complete.
(Worker_TP0 pid=430) WARNING 08-31 13:11:25 [jit_monitor.py:135] Triton kernel JIT compilation during inference: _triton_mrope_forward. This causes a latency spike; consider extending warmup to cover this shape/config.
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(Worker_TP0 pid=430) WARNING 08-31 13:17:58 [jit_monitor.py:135] Triton kernel JIT compilation during inference: _bilinear_pos_embed_kernel. This causes a latency spike; consider extending warmup to cover this shape/config.
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(Worker_TP0 pid=430) WARNING 08-31 13:30:24 [jit_monitor.py:135] Triton kernel JIT compilation during inference: rotary_kernel. This causes a latency spike; consider extending warmup to cover this shape/config.
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 400 Bad Request
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 400 Bad Request
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 400 Bad Request
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 400 Bad Request
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 400 Bad Request
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
(APIServer pid=77) INFO:     10.89.0.1:0 - "POST /v1/chat/completions HTTP/1.1" 400 Bad Request
(APIServer pid=77) INFO:     10.89.0.9:0 - "GET /metrics HTTP/1.1" 200 OK

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
