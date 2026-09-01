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

______________________________________________________________________________________

[srv_mlengineering@cbq2-svd-dsgpu2 nim]$ podman-compose -f /apps/srv_mlengineering/nim/docker-compose-extended.yml \
  up -d --no-deps nim-llama-embedqa
Error: no container with name or ID "llama-3.2-nv-embedqa" found: no such container
Error: no container with ID or name "llama-3.2-nv-embedqa" found: no such container
c55f6c07b3b957a558bb720bc5befe5344dd81082a479a03e5a61b57a06198c7
WARN[0000] Failed to mount subscriptions, skipping entry in /usr/share/containers/mounts.conf: getting host subscription data: failed to read subscriptions from "/usr/share/rhel/secrets": open /usr/share/rhel/secrets/redhat.repo: permission denied
llama-3.2-nv-embedqa
[srv_mlengineering@cbq2-svd-dsgpu2 nim]$ podman ps -a --filter name=llama-3.2-nv-embedqa \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
NAMES                               STATUS                       PORTS
llama-3.2-nv-embedqa-manual-backup  Exited (137) 31 seconds ago  0.0.0.0:8896->8000/tcp
llama-3.2-nv-embedqa                Up 20 seconds                0.0.0.0:8896->8000/tcp
[srv_mlengineering@cbq2-svd-dsgpu2 nim]$ podman logs --tail 100 -f llama-3.2-nv-embedqa
WARNING 2026-09-01 10:06:30.900 pytorch.py:25] torch not found
I0901 10:06:30.904173 379 model_lifecycle.cc:849] "successfully loaded 'nvidia_llama_3_2_nv_embedqa_1b_v2_batcher_512'"
I0901 10:06:30.925774 379 logging.cc:46] "[MemUsageChange] TensorRT-managed allocation in IExecutionContext creation: CPU +0, GPU +155, now: CPU 0, GPU 1584 (MiB)"
WARNING 2026-09-01 10:06:31.010 pytorch.py:25] torch not found
I0901 10:06:31.013624 379 model_lifecycle.cc:849] "successfully loaded 'nvidia_llama_3_2_nv_embedqa_1b_v2_batcher_8192'"
I0901 10:06:31.118545 379 logging.cc:46] "[MemUsageChange] TensorRT-managed allocation in IExecutionContext creation: CPU +0, GPU +155, now: CPU 0, GPU 1739 (MiB)"
I0901 10:06:31.118582 379 logging.cc:46] "Switching optimization profile from: 0 to 1. Please ensure there are no enqueued operations pending in this context prior to switching profiles"
I0901 10:06:31.525999 379 logging.cc:46] "[MemUsageChange] TensorRT-managed allocation in IExecutionContext creation: CPU +0, GPU +155, now: CPU 0, GPU 2051 (MiB)"
I0901 10:06:31.526033 379 logging.cc:46] "Switching optimization profile from: 0 to 2. Please ensure there are no enqueued operations pending in this context prior to switching profiles"
I0901 10:06:31.920928 379 logging.cc:46] "[MemUsageChange] TensorRT-managed allocation in IExecutionContext creation: CPU +0, GPU +155, now: CPU 0, GPU 2367 (MiB)"
I0901 10:06:31.920963 379 logging.cc:46] "Switching optimization profile from: 0 to 3. Please ensure there are no enqueued operations pending in this context prior to switching profiles"
I0901 10:06:32.587902 379 logging.cc:46] "[MemUsageChange] TensorRT-managed allocation in IExecutionContext creation: CPU +0, GPU +155, now: CPU 0, GPU 2755 (MiB)"
I0901 10:06:32.587937 379 logging.cc:46] "Switching optimization profile from: 0 to 4. Please ensure there are no enqueued operations pending in this context prior to switching profiles"
W0901 10:06:32.874233 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874270 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874278 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874286 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874321 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874339 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874350 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874360 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874371 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874405 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874418 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874427 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874435 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874444 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874459 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874467 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874475 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874484 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874492 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874523 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874534 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874542 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874549 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
W0901 10:06:32.874556 379 logging.cc:43] "The engine contains 5 profiles. ICudaEngine::getTensorFormat(char const* tensorName) only returns results for profile 0. Use ICudaEngine::getTensorFormat(char const* tensorName, int32_t profileIndex) instead to obtain results for profiles 1 to 5."
I0901 10:06:32.874618 379 instance_state.cc:186] "Created instance nvidia_llama_3_2_nv_embedqa_1b_v2_model_0_0 on GPU 0 with stream priority 0 and optimization profile 0[0]; 1[1]; 2[2]; 3[3]; 4[4];"
I0901 10:06:32.876248 379 model_lifecycle.cc:849] "successfully loaded 'nvidia_llama_3_2_nv_embedqa_1b_v2_model'"
I0901 10:06:32.876396 379 server.cc:611]
+------------------+------+
| Repository Agent | Path |
+------------------+------+
+------------------+------+

I0901 10:06:32.876469 379 server.cc:638]
+----------+-----------------------------------------------------------+--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| Backend  | Path                                                      | Config                                                                                                                                                                                     |
+----------+-----------------------------------------------------------+--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| python   | /opt/tritonserver/backends/python/libtriton_python.so     | {"cmdline":{"auto-complete-config":"false","backend-directory":"/opt/tritonserver/backends","min-compute-capability":"6.000000","default-max-batch-size":"4"}}                             |
| tensorrt | /opt/tritonserver/backends/tensorrt/libtriton_tensorrt.so | {"cmdline":{"auto-complete-config":"false","backend-directory":"/opt/tritonserver/backends","min-compute-capability":"6.000000","version-compatible":"true","default-max-batch-size":"4"}} |
+----------+-----------------------------------------------------------+--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+

I0901 10:06:32.876547 379 server.cc:681]
+------------------------------------------------+---------+--------+
| Model                                          | Version | Status |
+------------------------------------------------+---------+--------+
| nvidia_llama_3_2_nv_embedqa_1b_v2              | 1       | READY  |
| nvidia_llama_3_2_nv_embedqa_1b_v2_batcher_128  | 1       | READY  |
| nvidia_llama_3_2_nv_embedqa_1b_v2_batcher_256  | 1       | READY  |
| nvidia_llama_3_2_nv_embedqa_1b_v2_batcher_4096 | 1       | READY  |
| nvidia_llama_3_2_nv_embedqa_1b_v2_batcher_512  | 1       | READY  |
| nvidia_llama_3_2_nv_embedqa_1b_v2_batcher_8192 | 1       | READY  |
| nvidia_llama_3_2_nv_embedqa_1b_v2_model        | 1       | READY  |
+------------------------------------------------+---------+--------+

I0901 10:06:33.035701 379 metrics.cc:889] "Collecting metrics for GPU 0: NVIDIA H200-141C"
I0901 10:06:33.060609 379 metrics.cc:782] "Collecting CPU metrics"
I0901 10:06:33.060710 379 tritonserver.cc:2598]
+----------------------------------+-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| Option                           | Value                                                                                                                                                                                                           |
+----------------------------------+-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| server_id                        | triton                                                                                                                                                                                                          |
| server_version                   | 2.60.0                                                                                                                                                                                                          |
| server_extensions                | classification sequence model_repository model_repository(unload_dependents) schedule_policy model_configuration system_shared_memory cuda_shared_memory binary_tensor_data parameters statistics trace logging |
| model_repository_path[0]         | /opt/nim/tmp/run/triton-model-repository                                                                                                                                                                        |
| model_control_mode               | MODE_NONE                                                                                                                                                                                                       |
| strict_model_config              | 1                                                                                                                                                                                                               |
| model_config_name                |                                                                                                                                                                                                                 |
| rate_limit                       | OFF                                                                                                                                                                                                             |
| pinned_memory_pool_byte_size     | 268435456                                                                                                                                                                                                       |
| cuda_memory_pool_byte_size{0}    | 67108864                                                                                                                                                                                                        |
| min_supported_compute_capability | 6.0                                                                                                                                                                                                             |
| strict_readiness                 | 1                                                                                                                                                                                                               |
| exit_timeout                     | 30                                                                                                                                                                                                              |
| cache_enabled                    | 0                                                                                                                                                                                                               |
+----------------------------------+-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+

I0901 10:06:33.062909 379 grpc_server.cc:2562] "Started GRPCInferenceService at 0.0.0.0:8001"
I0901 10:06:33.063064 379 http_server.cc:4789] "Started HTTPService at 0.0.0.0:8080"
I0901 10:06:33.104236 379 http_server.cc:358] "Started Metrics Service at 0.0.0.0:8002"
W0901 10:06:34.061673 379 metrics.cc:643] "Unable to get power limit for GPU 0. Status:Success, value:0.000000"
W0901 10:06:34.061719 379 metrics.cc:661] "Unable to get power usage for GPU 0. Status:Success, value:0.000000"
W0901 10:06:34.061726 379 metrics.cc:685] "Unable to get energy consumption for GPU 0. Status:Success, value:0"
W0901 10:06:35.076900 379 metrics.cc:643] "Unable to get power limit for GPU 0. Status:Success, value:0.000000"
W0901 10:06:35.076943 379 metrics.cc:661] "Unable to get power usage for GPU 0. Status:Success, value:0.000000"
W0901 10:06:35.076950 379 metrics.cc:685] "Unable to get energy consumption for GPU 0. Status:Success, value:0"
W0901 10:06:36.077357 379 metrics.cc:643] "Unable to get power limit for GPU 0. Status:Success, value:0.000000"
W0901 10:06:36.077394 379 metrics.cc:661] "Unable to get power usage for GPU 0. Status:Success, value:0.000000"
W0901 10:06:36.077401 379 metrics.cc:685] "Unable to get energy consumption for GPU 0. Status:Success, value:0"
^C[srv_mlengineering@cbq2-svd-dsgpu2 nim]podman inspect llama-3.2-nv-embedqa \ \
  --format 'status={{.State.Status}} restart={{.HostConfig.RestartPolicy.Name}} network={{.HostConfig.NetworkMode}} compose_service={{index .Config.Labels "io.podman.compose.service"}}'
status=running restart=unless-stopped network=bridge compose_service=nim-llama-embedqa
[srv_mlengineering@cbq2-svd-dsgpu2 nim]$ curl -fsS http://127.0.0.1:8896/v1/models | jq
{
  "object": "list",
  "data": [
    {
      "id": "nvidia/llama-3.2-nv-embedqa-1b-v2",
      "created": 0,
      "object": "model",
      "owned_by": "organization-owner"
    }
  ]
}
[srv_mlengineering@cbq2-svd-dsgpu2 nim]$ curl -sk --max-time 300 \
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
HTTP status: 200
