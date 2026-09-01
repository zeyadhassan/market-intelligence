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


orker_TP1 pid=431) 2026-08-31 13:10:54,089 - INFO - autotuner.py:651 - flashinfer.jit: [Autotuner]: Autotuning process starts ...
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
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ ^C
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ cp -p /apps/srv_mlengineering/nim/docker-compose-extended.yml /apps/srv_mlengineering/nim/docker-compose-extended.yml.before-262k
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ sed -i '/^  nim-qwen3vl:/,/^  nim-gptoss120b:/ {
s|^[[:space:]]*NIM_MAX_MODEL_LEN:.*|      NIM_MAX_MODEL_LEN: "262144"|
s|^[[:space:]]*NIM_PASSTHROUGH_ARGS:.*|      NIM_PASSTHROUGH_ARGS: "--gpu-memory-utilization 0.80 --trust-remote-code --max-num-seqs 4 --max-num-batched-tokens 8192 --enable-chunked-prefill --enforce-eager --enable-auto-tool-choice --tool-call-parser qwen3_coder"|
}' /apps/srv_mlengineering/nim/docker-compose-extended.yml
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ sed -n '/^  nim-qwen3vl:/,/^  nim-gptoss120b:/p' /apps/srv_mlengineering/nim/docker-compose-extended.yml
  nim-qwen3vl:
    image: nvcr.io/nim/nvidia/model-free-nim:latest
    container_name: nim-qwen3vl
    restart: unless-stopped
    security_opt:
      - "label=disable"
    ipc: host
    mem_limit: "96g"
    ports:
      - "8899:8000"
    environment:
      NIM_MODEL_PATH: "/opt/nim/models/qwen3-vl"
      NIM_SERVED_MODEL_NAME: "qwen3-vl-235b-awq"
      NIM_TENSOR_PARALLEL_SIZE: "2"
      NIM_MAX_MODEL_LEN: "262144"
      NIM_PASSTHROUGH_ARGS: "--gpu-memory-utilization 0.80 --trust-remote-code --max-num-seqs 4 --max-num-batched-tokens 8192 --enable-chunked-prefill --enforce-eager --enable-auto-tool-choice --tool-call-parser qwen3_coder"
      PYTHONUNBUFFERED: "1"
      NO_PROXY: "localhost,127.0.0.1,0.0.0.0,host.containers.internal"
      no_proxy: "localhost,127.0.0.1,0.0.0.0,host.containers.internal"
    volumes:
      - "/apps/srv_mlengineering/model-staging/qwen3-vl-235b-awq:/opt/nim/models/qwen3-vl:ro"
      - "/apps/srv_mlengineering/nim-cache:/opt/nim/.cache/ngc"
    devices:
      - "nvidia.com/gpu=2"
      - "nvidia.com/gpu=3"

  nim-gptoss120b:
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ podman-compose -f /apps/srv_mlengineering/nim/docker-compose-extended.yml config >/dev/null && echo "Compose configuration OK"
Compose configuration OK
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ podman-compose -f /apps/srv_mlengineering/nim/docker-compose-extended.yml up -d --no-deps --force-recreate nim-qwen3vl
nim-qwen3vl
nim-qwen3vl
de1f1321fe2460ef540b8ba8956301fbfa4da5170b0530a4e2797b91999dd8ca
WARN[0000] Failed to mount subscriptions, skipping entry in /usr/share/containers/mounts.conf: getting host subscription data: failed to read subscriptions from "/usr/share/rhel/secrets": open /usr/share/rhel/secrets/redhat.repo: permission denied
nim-qwen3vl
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ podman logs --tail 100 -f nim-qwen3vl

=================================================
== NVIDIA Inference Microservice LLM / VLM NIM ==
=================================================

NVIDIA Inference Microservice LLM / VLM NIM Version 2.0.10

Container image Copyright (c) 2016-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.

**GOVERNING DOWNLOAD TERMS:** Use of this container is governed by the [NVIDIA Software License Agreement](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-software-license-agreement/) and [Product-Specific Terms for NVIDIA AI Products](https://www.nvidia.com/en-us/agreements/enterprise-software/product-specific-terms-for-ai-products/).

The use of any models in this container is governed by the [NVIDIA Open Model Agreement](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-agreement/).

You are responsible for ensuring that your use of any provided models complies with all applicable laws.

A copy of the container license can be found under /opt/nim/LICENSE.

Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
LicenseRef-NvidiaProprietary
NVIDIA CORPORATION, its affiliates and licensors retain all intellectual property and proprietary rights in and to this material, related documentation and any modifications thereto. Any use, reproduction, disclosure or distribution of this material and related documentation without an express license agreement from NVIDIA CORPORATION or its affiliates are strictly prohibited.
This distribution includes open source which is archived at the following URL: https://opensource.nvidia.com/oss/teams/nim
For further inquiries or assistance, contact us at oss-requests@nvidia.com
This container will download a model to the /opt/nim/workspace subdirectory in the container to perform inference. Once downloaded, that directory will contain model-specific LICENSE and NOTICE files which you must agree to before using the model.

Starting vLLM v0.26.0
  Model:     qwen3-vl-235b-awq
  Precision: N/A
  TP/PP:     2/1
  CPU:       4 cores
  Port:      8000
  OpenAPI:   http://localhost:8000/docs (interactive)

WARNING 09-01 07:39:18 [argparse_utils.py:422] Found duplicate keys --middleware
(APIServer pid=73) WARNING 09-01 07:39:36 [vllm.py:1163] Enforce eager set, disabling torch.compile and CUDAGraphs. This is equivalent to setting -cc.mode=none -cc.cudagraph_mode=none
(APIServer pid=73) WARNING 09-01 07:39:36 [vllm.py:1213] Inductor compilation was disabled by user settings, optimizations settings that are only active during inductor compilation will be ignored.
(APIServer pid=73) [transformers] The `use_fast` parameter is deprecated and will be removed in a future version. Use `backend="torchvision"` instead of `use_fast=True`, or `backend="pil"` instead of `use_fast=False`.
(EngineCore pid=321) WARNING 09-01 07:39:55 [multiproc_executor.py:1070] Reducing Torch parallelism from 24 threads to 1 to avoid unnecessary CPU contention. Set OMP_NUM_THREADS in the external environment to tune this value as needed.
(Worker pid=429) WARNING 09-01 07:40:15 [symm_mem.py:106] SymmMemCommunicator: symmetric memory multicast operations are not supported.
(Worker pid=428) WARNING 09-01 07:40:15 [symm_mem.py:106] SymmMemCommunicator: symmetric memory multicast operations are not supported.
(Worker pid=428) ERROR 09-01 07:40:16 [multiproc_executor.py:901] WorkerProc failed to start.
(Worker pid=428) ERROR 09-01 07:40:16 [multiproc_executor.py:901] Traceback (most recent call last):
(Worker pid=428) ERROR 09-01 07:40:16 [multiproc_executor.py:901]   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/executor/multiproc_executor.py", line 868, in worker_main
(Worker pid=428) ERROR 09-01 07:40:16 [multiproc_executor.py:901]     worker = WorkerProc(*args, **kwargs)
(Worker pid=428) ERROR 09-01 07:40:16 [multiproc_executor.py:901]              ^^^^^^^^^^^^^^^^^^^^^^^^^^^
(Worker pid=428) ERROR 09-01 07:40:16 [multiproc_executor.py:901]   File "/usr/local/lib/python3.12/dist-packages/vllm/tracing/otel.py", line 178, in sync_wrapper
(Worker pid=428) ERROR 09-01 07:40:16 [multiproc_executor.py:901]     return func(*args, **kwargs)
(Worker pid=428) ERROR 09-01 07:40:16 [multiproc_executor.py:901]            ^^^^^^^^^^^^^^^^^^^^^
(Worker pid=428) ERROR 09-01 07:40:16 [multiproc_executor.py:901]   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/executor/multiproc_executor.py", line 629, in __init__
(Worker pid=428) ERROR 09-01 07:40:16 [multiproc_executor.py:901]     self.worker.init_device()
(Worker pid=428) ERROR 09-01 07:40:16 [multiproc_executor.py:901]   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/worker/worker_base.py", line 331, in init_device
(Worker pid=428) ERROR 09-01 07:40:16 [multiproc_executor.py:901]     self.worker.init_device()  # type: ignore
(Worker pid=428) ERROR 09-01 07:40:16 [multiproc_executor.py:901]     ^^^^^^^^^^^^^^^^^^^^^^^^^
(Worker pid=428) ERROR 09-01 07:40:16 [multiproc_executor.py:901]   File "/usr/local/lib/python3.12/dist-packages/vllm/tracing/otel.py", line 178, in sync_wrapper
(Worker pid=428) ERROR 09-01 07:40:16 [multiproc_executor.py:901]     return func(*args, **kwargs)
(Worker pid=428) ERROR 09-01 07:40:16 [multiproc_executor.py:901]            ^^^^^^^^^^^^^^^^^^^^^
(Worker pid=428) ERROR 09-01 07:40:16 [multiproc_executor.py:901]   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/worker/gpu_worker.py", line 389, in init_device
(Worker pid=428) ERROR 09-01 07:40:16 [multiproc_executor.py:901]     self.requested_memory = request_memory(init_snapshot, self.cache_config)
(Worker pid=428) ERROR 09-01 07:40:16 [multiproc_executor.py:901]                             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
(Worker pid=428) ERROR 09-01 07:40:16 [multiproc_executor.py:901]   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/worker/utils.py", line 435, in request_memory
(Worker pid=428) ERROR 09-01 07:40:16 [multiproc_executor.py:901]     raise ValueError(
(Worker pid=428) ERROR 09-01 07:40:16 [multiproc_executor.py:901] ValueError: Free memory on device cuda:0 (110.9/140.55 GiB) on startup is less than desired GPU memory utilization (0.8, 112.44 GiB). Decrease GPU memory utilization or reduce GPU memory used by other processes.
(Worker pid=429) [transformers] The `use_fast` parameter is deprecated and will be removed in a future version. Use `backend="torchvision"` instead of `use_fast=True`, or `backend="pil"` instead of `use_fast=False`.
[rank0]:[W901 07:40:17.112767298 ProcessGroupNCCL.cpp:1575] Warning: WARNING: destroy_process_group() was not called before program exit, which can leak resources. For more info, please see https://pytorch.org/docs/stable/distributed.html#shutdown (function operator())
[rank1]:[W901 07:40:19.968570826 TCPStore.cpp:125] [c10d] recvValue failed on SocketImpl(fd=64, addr=[localhost]:35986, remote=[localhost]:45489): Failed to recv, got 0 bytes. Connection was likely closed. Did the remote server shutdown or crash?
Exception raised from recvBytes at /pytorch/torch/csrc/distributed/c10d/Utils.hpp:682 (most recent call first):
frame #0: c10::Error::Error(c10::SourceLocation, std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >) + 0x9d (0x7fb9f4f7305d in /usr/local/lib/python3.12/dist-packages/torch/lib/libc10.so)
frame #1: <unknown function> + 0x6a914bd (0x7fb95eab24bd in /usr/local/lib/python3.12/dist-packages/torch/lib/libtorch_cpu.so)
frame #2: c10d::TCPStore::check(std::vector<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >, std::allocator<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > > > const&) + 0x273 (0x7fb95eab0413 in /usr/local/lib/python3.12/dist-packages/torch/lib/libtorch_cpu.so)
frame #3: c10d::ProcessGroupNCCL::HeartbeatMonitor::runLoop() + 0x4a5 (0x7fb94129c8c5 in /usr/local/lib/python3.12/dist-packages/torch/lib/libtorch_cuda.so)
frame #4: <unknown function> + 0xecdb4 (0x7fb9c566edb4 in /lib/x86_64-linux-gnu/libstdc++.so.6)
frame #5: <unknown function> + 0x9caa4 (0x7fb9f6161aa4 in /lib/x86_64-linux-gnu/libc.so.6)
frame #6: <unknown function> + 0x129c6c (0x7fb9f61eec6c in /lib/x86_64-linux-gnu/libc.so.6)

[rank1]:[W901 07:40:19.971184476 ProcessGroupNCCL.cpp:1826] [PG ID 0 PG GUID 0 Rank 1] Failed to check the "should dump" flag on TCPStore, (maybe TCPStore server has shut down too early), with error: Failed to recv, got 0 bytes. Connection was likely closed. Did the remote server shutdown or crash?
(Worker_TP1 pid=429) WARNING 09-01 07:40:19 [vllm.py:1163] Enforce eager set, disabling torch.compile and CUDAGraphs. This is equivalent to setting -cc.mode=none -cc.cudagraph_mode=none
(Worker_TP1 pid=429) WARNING 09-01 07:40:19 [vllm.py:1213] Inductor compilation was disabled by user settings, optimizations settings that are only active during inductor compilation will be ignored.
[rank1]:[W901 07:40:20.971331164 TCPStore.cpp:106] [c10d] sendBytes failed on SocketImpl(fd=64, addr=[localhost]:35986, remote=[localhost]:45489): Broken pipe
Exception raised from sendBytes at /pytorch/torch/csrc/distributed/c10d/Utils.hpp:653 (most recent call first):
frame #0: c10::Error::Error(c10::SourceLocation, std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >) + 0x9d (0x7fb9f4f7305d in /usr/local/lib/python3.12/dist-packages/torch/lib/libc10.so)
frame #1: <unknown function> + 0x6a90931 (0x7fb95eab1931 in /usr/local/lib/python3.12/dist-packages/torch/lib/libtorch_cpu.so)
frame #2: c10d::TCPStore::check(std::vector<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >, std::allocator<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > > > const&) + 0x24d (0x7fb95eab03ed in /usr/local/lib/python3.12/dist-packages/torch/lib/libtorch_cpu.so)
frame #3: c10d::ProcessGroupNCCL::HeartbeatMonitor::runLoop() + 0x4a5 (0x7fb94129c8c5 in /usr/local/lib/python3.12/dist-packages/torch/lib/libtorch_cuda.so)
frame #4: <unknown function> + 0xecdb4 (0x7fb9c566edb4 in /lib/x86_64-linux-gnu/libstdc++.so.6)
frame #5: <unknown function> + 0x9caa4 (0x7fb9f6161aa4 in /lib/x86_64-linux-gnu/libc.so.6)
frame #6: <unknown function> + 0x129c6c (0x7fb9f61eec6c in /lib/x86_64-linux-gnu/libc.so.6)

[rank1]:[W901 07:40:20.972647577 ProcessGroupNCCL.cpp:1826] [PG ID 0 PG GUID 0 Rank 1] Failed to check the "should dump" flag on TCPStore, (maybe TCPStore server has shut down too early), with error: Broken pipe
(EngineCore pid=321) WARNING 09-01 07:40:21 [multiproc_executor.py:441] [shutdown] Executor: workers still running after grace period; sending SIGTERM count=1
[rank1]:[W901 07:40:21.972762452 TCPStore.cpp:106] [c10d] sendBytes failed on SocketImpl(fd=64, addr=[localhost]:35986, remote=[localhost]:45489): Broken pipe
Exception raised from sendBytes at /pytorch/torch/csrc/distributed/c10d/Utils.hpp:653 (most recent call first):
frame #0: c10::Error::Error(c10::SourceLocation, std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >) + 0x9d (0x7fb9f4f7305d in /usr/local/lib/python3.12/dist-packages/torch/lib/libc10.so)
frame #1: <unknown function> + 0x6a90931 (0x7fb95eab1931 in /usr/local/lib/python3.12/dist-packages/torch/lib/libtorch_cpu.so)
frame #2: c10d::TCPStore::check(std::vector<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >, std::allocator<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > > > const&) + 0x24d (0x7fb95eab03ed in /usr/local/lib/python3.12/dist-packages/torch/lib/libtorch_cpu.so)
frame #3: c10d::ProcessGroupNCCL::HeartbeatMonitor::runLoop() + 0x4a5 (0x7fb94129c8c5 in /usr/local/lib/python3.12/dist-packages/torch/lib/libtorch_cuda.so)
frame #4: <unknown function> + 0xecdb4 (0x7fb9c566edb4 in /lib/x86_64-linux-gnu/libstdc++.so.6)
frame #5: <unknown function> + 0x9caa4 (0x7fb9f6161aa4 in /lib/x86_64-linux-gnu/libc.so.6)
frame #6: <unknown function> + 0x129c6c (0x7fb9f61eec6c in /lib/x86_64-linux-gnu/libc.so.6)

[rank1]:[W901 07:40:21.974091162 ProcessGroupNCCL.cpp:1826] [PG ID 0 PG GUID 0 Rank 1] Failed to check the "should dump" flag on TCPStore, (maybe TCPStore server has shut down too early), with error: Broken pipe
[rank1]:[W901 07:40:22.974245661 TCPStore.cpp:106] [c10d] sendBytes failed on SocketImpl(fd=64, addr=[localhost]:35986, remote=[localhost]:45489): Broken pipe
Exception raised from sendBytes at /pytorch/torch/csrc/distributed/c10d/Utils.hpp:653 (most recent call first):
frame #0: c10::Error::Error(c10::SourceLocation, std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >) + 0x9d (0x7fb9f4f7305d in /usr/local/lib/python3.12/dist-packages/torch/lib/libc10.so)
frame #1: <unknown function> + 0x6a90931 (0x7fb95eab1931 in /usr/local/lib/python3.12/dist-packages/torch/lib/libtorch_cpu.so)
frame #2: c10d::TCPStore::check(std::vector<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >, std::allocator<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > > > const&) + 0x24d (0x7fb95eab03ed in /usr/local/lib/python3.12/dist-packages/torch/lib/libtorch_cpu.so)
frame #3: c10d::ProcessGroupNCCL::HeartbeatMonitor::runLoop() + 0x4a5 (0x7fb94129c8c5 in /usr/local/lib/python3.12/dist-packages/torch/lib/libtorch_cuda.so)
frame #4: <unknown function> + 0xecdb4 (0x7fb9c566edb4 in /lib/x86_64-linux-gnu/libstdc++.so.6)
frame #5: <unknown function> + 0x9caa4 (0x7fb9f6161aa4 in /lib/x86_64-linux-gnu/libc.so.6)
frame #6: <unknown function> + 0x129c6c (0x7fb9f61eec6c in /lib/x86_64-linux-gnu/libc.so.6)

[rank1]:[W901 07:40:22.975559651 ProcessGroupNCCL.cpp:1826] [PG ID 0 PG GUID 0 Rank 1] Failed to check the "should dump" flag on TCPStore, (maybe TCPStore server has shut down too early), with error: Broken pipe
(EngineCore pid=321) WARNING 09-01 07:40:25 [multiproc_executor.py:451] [shutdown] Executor: workers still running after SIGTERM; sending SIGKILL count=0
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330] EngineCore failed to start.
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330] Traceback (most recent call last):
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/engine/core.py", line 1299, in run_engine_core
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]     engine_core = EngineCoreProc(*args, engine_index=dp_rank, **kwargs)
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]   File "/usr/local/lib/python3.12/dist-packages/vllm/tracing/otel.py", line 178, in sync_wrapper
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]     return func(*args, **kwargs)
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]            ^^^^^^^^^^^^^^^^^^^^^
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/engine/core.py", line 1065, in __init__
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]     super().__init__(
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/engine/core.py", line 125, in __init__
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]     self.model_executor = executor_class(vllm_config)
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]                           ^^^^^^^^^^^^^^^^^^^^^^^^^^^
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/executor/multiproc_executor.py", line 108, in __init__
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]     super().__init__(vllm_config)
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]   File "/usr/local/lib/python3.12/dist-packages/vllm/tracing/otel.py", line 178, in sync_wrapper
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]     return func(*args, **kwargs)
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]            ^^^^^^^^^^^^^^^^^^^^^
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/executor/abstract.py", line 109, in __init__
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]     self._init_executor()
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/executor/multiproc_executor.py", line 201, in _init_executor
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]     self.workers = WorkerProc.wait_for_ready(unready_workers)
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/executor/multiproc_executor.py", line 765, in wait_for_ready
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330]     raise e from None
(EngineCore pid=321) ERROR 09-01 07:40:25 [core.py:1330] Exception: WorkerProc initialization failed due to an exception in a background process. See stack trace for root cause.
(EngineCore pid=321) Process EngineCore:
(EngineCore pid=321) Traceback (most recent call last):
(EngineCore pid=321)   File "/usr/lib/python3.12/multiprocessing/process.py", line 314, in _bootstrap
(EngineCore pid=321)     self.run()
(EngineCore pid=321)   File "/usr/lib/python3.12/multiprocessing/process.py", line 108, in run
(EngineCore pid=321)     self._target(*self._args, **self._kwargs)
(EngineCore pid=321)   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/engine/core.py", line 1334, in run_engine_core
(EngineCore pid=321)     raise e
(EngineCore pid=321)   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/engine/core.py", line 1299, in run_engine_core
(EngineCore pid=321)     engine_core = EngineCoreProc(*args, engine_index=dp_rank, **kwargs)
(EngineCore pid=321)                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
(EngineCore pid=321)   File "/usr/local/lib/python3.12/dist-packages/vllm/tracing/otel.py", line 178, in sync_wrapper
(EngineCore pid=321)     return func(*args, **kwargs)
(EngineCore pid=321)            ^^^^^^^^^^^^^^^^^^^^^
(EngineCore pid=321)   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/engine/core.py", line 1065, in __init__
(EngineCore pid=321)     super().__init__(
(EngineCore pid=321)   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/engine/core.py", line 125, in __init__
(EngineCore pid=321)     self.model_executor = executor_class(vllm_config)
(EngineCore pid=321)                           ^^^^^^^^^^^^^^^^^^^^^^^^^^^
(EngineCore pid=321)   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/executor/multiproc_executor.py", line 108, in __init__
(EngineCore pid=321)     super().__init__(vllm_config)
(EngineCore pid=321)   File "/usr/local/lib/python3.12/dist-packages/vllm/tracing/otel.py", line 178, in sync_wrapper
(EngineCore pid=321)     return func(*args, **kwargs)
(EngineCore pid=321)            ^^^^^^^^^^^^^^^^^^^^^
(EngineCore pid=321)   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/executor/abstract.py", line 109, in __init__
(EngineCore pid=321)     self._init_executor()
(EngineCore pid=321)   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/executor/multiproc_executor.py", line 201, in _init_executor
(EngineCore pid=321)     self.workers = WorkerProc.wait_for_ready(unready_workers)
(EngineCore pid=321)                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
(EngineCore pid=321)   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/executor/multiproc_executor.py", line 765, in wait_for_ready
(EngineCore pid=321)     raise e from None
(EngineCore pid=321) Exception: WorkerProc initialization failed due to an exception in a background process. See stack trace for root cause.
(APIServer pid=73) Traceback (most recent call last):
(APIServer pid=73)   File "<frozen runpy>", line 198, in _run_module_as_main
(APIServer pid=73)   File "<frozen runpy>", line 88, in _run_code
(APIServer pid=73)   File "/opt/nim/.venv/lib/python3.12/site-packages/nim_llm/start_server.py", line 110, in <module>
(APIServer pid=73)     main()
(APIServer pid=73)   File "/opt/nim/.venv/lib/python3.12/site-packages/nim_llm/start_server.py", line 106, in main
(APIServer pid=73)     run_action(args)
(APIServer pid=73)   File "/opt/nim/.venv/lib/python3.12/site-packages/nim_llm/start_server.py", line 75, in run_action
(APIServer pid=73)     return _run_action(args)
(APIServer pid=73)            ^^^^^^^^^^^^^^^^^
(APIServer pid=73)   File "/opt/nim/.venv/lib/python3.12/site-packages/nim_llm/cli/__init__.py", line 76, in run_action
(APIServer pid=73)     nim_serve(dry_run=args.dry_run)
(APIServer pid=73)   File "/opt/nim/.venv/lib/python3.12/site-packages/nim_llm/cli/actions.py", line 1073, in nim_serve
(APIServer pid=73)     launcher(backend_args, middleware_config)
(APIServer pid=73)   File "/opt/nim/.venv/lib/python3.12/site-packages/nim_llm/backends/base.py", line 94, in _resolve
(APIServer pid=73)     return getattr(module, attr_name)(*args, **kwargs)
(APIServer pid=73)            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
(APIServer pid=73)   File "/opt/nim/.venv/lib/python3.12/site-packages/nim_llm/backends/vllm/lifecycle.py", line 209, in launch_vllm
(APIServer pid=73)     _launch_via_vllm(vllm_args)
(APIServer pid=73)   File "/opt/nim/.venv/lib/python3.12/site-packages/nim_llm/backends/vllm/lifecycle.py", line 177, in _launch_via_vllm
(APIServer pid=73)     main()
(APIServer pid=73)   File "/usr/local/lib/python3.12/dist-packages/vllm/entrypoints/cli/main.py", line 95, in main
(APIServer pid=73)     args.dispatch_function(args)
(APIServer pid=73)   File "/usr/local/lib/python3.12/dist-packages/vllm/entrypoints/cli/serve.py", line 148, in cmd
(APIServer pid=73)     uvloop.run(run_server(args))
(APIServer pid=73)   File "/usr/local/lib/python3.12/dist-packages/uvloop/__init__.py", line 96, in run
(APIServer pid=73)     return __asyncio.run(
(APIServer pid=73)            ^^^^^^^^^^^^^^
(APIServer pid=73)   File "/usr/lib/python3.12/asyncio/runners.py", line 194, in run
(APIServer pid=73)     return runner.run(main)
(APIServer pid=73)            ^^^^^^^^^^^^^^^^
(APIServer pid=73)   File "/usr/lib/python3.12/asyncio/runners.py", line 118, in run
(APIServer pid=73)     return self._loop.run_until_complete(task)
(APIServer pid=73)            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
(APIServer pid=73)   File "uvloop/loop.pyx", line 1518, in uvloop.loop.Loop.run_until_complete
(APIServer pid=73)   File "/usr/local/lib/python3.12/dist-packages/uvloop/__init__.py", line 48, in wrapper
(APIServer pid=73)     return await main
(APIServer pid=73)            ^^^^^^^^^^
(APIServer pid=73)   File "/usr/local/lib/python3.12/dist-packages/vllm/entrypoints/openai/api_server.py", line 759, in run_server
(APIServer pid=73)     await run_server_worker(listen_address, sock, args, **uvicorn_kwargs)
(APIServer pid=73)   File "/usr/local/lib/python3.12/dist-packages/vllm/entrypoints/openai/api_server.py", line 773, in run_server_worker
(APIServer pid=73)     async with build_async_engine_client(
(APIServer pid=73)   File "/usr/lib/python3.12/contextlib.py", line 210, in __aenter__
(APIServer pid=73)     return await anext(self.gen)
(APIServer pid=73)            ^^^^^^^^^^^^^^^^^^^^^
(APIServer pid=73)   File "/usr/local/lib/python3.12/dist-packages/vllm/entrypoints/openai/api_server.py", line 139, in build_async_engine_client
(APIServer pid=73)     async with build_async_engine_client_from_engine_args(
(APIServer pid=73)   File "/usr/lib/python3.12/contextlib.py", line 210, in __aenter__
(APIServer pid=73)     return await anext(self.gen)
(APIServer pid=73)            ^^^^^^^^^^^^^^^^^^^^^
(APIServer pid=73)   File "/usr/local/lib/python3.12/dist-packages/vllm/entrypoints/openai/api_server.py", line 175, in build_async_engine_client_from_engine_args
(APIServer pid=73)     async_llm = AsyncLLM.from_vllm_config(
(APIServer pid=73)                 ^^^^^^^^^^^^^^^^^^^^^^^^^^
(APIServer pid=73)   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/engine/async_llm.py", line 217, in from_vllm_config
(APIServer pid=73)     return cls(
(APIServer pid=73)            ^^^^
(APIServer pid=73)   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/engine/async_llm.py", line 146, in __init__
(APIServer pid=73)     self.engine_core = EngineCoreClient.make_async_mp_client(
(APIServer pid=73)                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
(APIServer pid=73)   File "/usr/local/lib/python3.12/dist-packages/vllm/tracing/otel.py", line 178, in sync_wrapper
(APIServer pid=73)     return func(*args, **kwargs)
(APIServer pid=73)            ^^^^^^^^^^^^^^^^^^^^^
(APIServer pid=73)   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/engine/core_client.py", line 132, in make_async_mp_client
(APIServer pid=73)     return AsyncMPClient(*client_args)
(APIServer pid=73)            ^^^^^^^^^^^^^^^^^^^^^^^^^^^
(APIServer pid=73)   File "/usr/local/lib/python3.12/dist-packages/vllm/tracing/otel.py", line 178, in sync_wrapper
(APIServer pid=73)     return func(*args, **kwargs)
(APIServer pid=73)            ^^^^^^^^^^^^^^^^^^^^^
(APIServer pid=73)   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/engine/core_client.py", line 963, in __init__
(APIServer pid=73)     super().__init__(
(APIServer pid=73)   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/engine/core_client.py", line 573, in __init__
(APIServer pid=73)     with launch_core_engines(
(APIServer pid=73)   File "/usr/lib/python3.12/contextlib.py", line 144, in __exit__
(APIServer pid=73)     next(self.gen)
(APIServer pid=73)   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/engine/utils.py", line 1213, in launch_core_engines
(APIServer pid=73)     wait_for_engine_startup(
(APIServer pid=73)   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/engine/utils.py", line 1272, in wait_for_engine_startup
(APIServer pid=73)     raise RuntimeError(
(APIServer pid=73) RuntimeError: Engine core initialization failed. See root cause above. Failed core proc(s): {}
/usr/lib/python3.12/multiprocessing/resource_tracker.py:254: UserWarning: resource_tracker: There appear to be 1 leaked shared_memory objects to clean up at shutdown
  warnings.warn('resource_tracker: There appear to be %d '
Shutting down services...
  Stopping nginx...



______________________________

Lower it to 0.78. This preserves the 262K context and tool-calling configuration.

Run these commands one at a time:

podman stop -t 30 nim-qwen3vl
sed -i '/^  nim-qwen3vl:/,/^  nim-gptoss120b:/ s/--gpu-memory-utilization 0\.80/--gpu-memory-utilization 0.78/' /apps/srv_mlengineering/nim/docker-compose-extended.yml

Confirm the values:

sed -n '/^  nim-qwen3vl:/,/^  nim-gptoss120b:/p' /apps/srv_mlengineering/nim/docker-compose-extended.yml | grep -E 'NIM_MAX_MODEL_LEN|NIM_PASSTHROUGH_ARGS'

You should see:

NIM_MAX_MODEL_LEN: "262144"
--gpu-memory-utilization 0.78
--enable-chunked-prefill
--enable-auto-tool-choice
--tool-call-parser qwen3_coder

Validate Compose:

podman-compose -f /apps/srv_mlengineering/nim/docker-compose-extended.yml config >/dev/null && echo "Compose configuration OK"

Recreate only Qwen:

podman-compose -f /apps/srv_mlengineering/nim/docker-compose-extended.yml up -d --no-deps --force-recreate nim-qwen3vl

Watch startup:

podman logs --tail 100 -f nim-qwen3vl

Wait for:

Application startup complete

Then press Ctrl+C and verify:

podman inspect nim-qwen3vl --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^NIM_(MAX_MODEL_LEN|TENSOR_PARALLEL_SIZE|PASSTHROUGH_ARGS)='

Check its KV-cache capacity:

curl -fsS http://127.0.0.1:8899/metrics | grep 'vllm:cache_config_info'

The kv_cache_size_tokens value must be greater than 262144 for one maximum-length request to fit.

Test directly:

curl -fsS http://127.0.0.1:8899/v1/models | jq

Test through Nginx:

curl -sk --max-time 600 https://127.0.0.1:8443/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-vl-235b-awq","messages":[{"role":"user","content":"Reply only with OK"}],"max_tokens":128}' | jq

The subscription-mount and symmetric-memory messages are warnings, not the failure. The actual failure was the 0.80 GPU-memory target. You do not need to restart Nginx or GPT-OSS.