The embedding model is already running successfully:

llama-3.2-nv-embedqa
port 8896 → container port 8000

We’ll expose it as:

POST https://10.1.94.110:8443/v1/embeddings

Unlike chat models, it uses /v1/embeddings. NVIDIA also requires input_type to be either query or passage. NVIDIA API documentation

Do not stop or rename the container yet. First, run these commands individually and paste the output so I can reproduce its exact GPU, cache, mounts, and restart configuration safely in Compose.

Check the advertised model ID:

curl -fsS http://127.0.0.1:8896/v1/models | jq

Test the embedding service directly:

curl -fsS http://127.0.0.1:8896/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{
    "input":["What services does Commercial Bank provide?"],
    "model":"nvidia/llama-3.2-nv-embedqa-1b-v2",
    "input_type":"query",
    "modality":"text",
    "encoding_format":"float"
  }' | jq '{model,dimensions:(.data[0].embedding|length),usage}'

Check whether it is already managed by Compose:

podman inspect llama-3.2-nv-embedqa \
  --format 'status={{.State.Status}} restart={{.HostConfig.RestartPolicy.Name}} network={{.HostConfig.NetworkMode}} compose_service={{index .Config.Labels "io.podman.compose.service"}} compose_project={{index .Config.Labels "io.podman.compose.project"}}'

Show the exact image:

podman inspect llama-3.2-nv-embedqa --format 'image={{.Config.Image}}'

Show its mounted directories:

podman inspect llama-3.2-nv-embedqa \
  --format '{{range .Mounts}}{{println .Source " -> " .Destination " options=" .Options}}{{end}}'

Show relevant environment variables without exposing the NGC API key:

podman inspect llama-3.2-nv-embedqa \
  --format '{{range .Config.Env}}{{println .}}{{end}}' |
grep -E '^(NIM_|CUDA_VISIBLE_DEVICES|NVIDIA_VISIBLE_DEVICES|NO_PROXY|no_proxy)='

Show which physical GPU it sees:

podman exec llama-3.2-nv-embedqa \
  nvidia-smi --query-gpu=index,uuid,name,memory.total,memory.used --format=csv

Show the host GPU-to-UUID mapping:

nvidia-smi --query-gpu=index,uuid,name,memory.total,memory.used --format=csv

Finally, show the current Nginx structure:

grep -nE 'upstream |location ' /apps/srv_mlengineering/nim/nginx/nginx_router_hybrid_extended.conf

Once you paste that output, I’ll provide exact pasteable commands to:

Add the embedding service to docker-compose-extended.yml.
Preserve its current GPU and cache configuration.
Route /v1/embeddings directly to 10.1.94.110:8896.
Recreate only the embedding container and Nginx.
Test it from the server and an outside PC.

We won’t route embeddings through router_hybrid_extended.js; a dedicated Nginx /v1/embeddings location is cleaner because there is currently only one embedding backend.
______________________________________________________________________________________________________________________

[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ ^C
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ curl -fsS http://127.0.0.1:8896/v1/models | jq
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
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ curl -fsS http://127.0.0.1:8896/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{
    "input":["What services does Commercial Bank provide?"],
    "model":"nvidia/llama-3.2-nv-embedqa-1b-v2",
    "input_type":"query",
    "modality":"text",
    "encoding_format":"float"
  }' | jq '{model,dimensions:(.data[0].embedding|length),usage}'
{
  "model": "nvidia/llama-3.2-nv-embedqa-1b-v2",
  "dimensions": 2048,
  "usage": {
    "prompt_tokens": 10,
    "total_tokens": 10
  }
}
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ podman inspect llama-3.2-nv-embedqa \
  --format 'status={{.State.Status}} restart={{.HostConfig.RestartPolicy.Name}} network={{.HostConfig.NetworkMode}} compose_service={{index .Config.Labels "io.podman.compose.service"}} compose_project={{index .Config.Labels "io.podman.compose.project"}}'
status=running restart=no network=pasta compose_service= compose_project=
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ podman inspect llama-3.2-nv-embedqa --format 'image={{.Config.Image}}'
image=nvcr.io/nim/nvidia/llama-3.2-nv-embedqa-1b-v2:1.10
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ podman inspect llama-3.2-nv-embedqa \
  --format '{{range .Mounts}}{{println .Source " -> " .Destination " options=" .Options}}{{end}}'
/apps/srv_mlengineering/nim-cache  ->  /opt/nim/.cache  options= [rbind]

[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ podman inspect llama-3.2-nv-embedqa \
  --format '{{range .Config.Env}}{{println .}}{{end}}' |
grep -E '^(NIM_|CUDA_VISIBLE_DEVICES|NVIDIA_VISIBLE_DEVICES|NO_PROXY|no_proxy)='
NVIDIA_VISIBLE_DEVICES=all
NO_PROXY=localhost,127.0.0.1,::1,.cbq.com.qa,10.0.0.0/8
no_proxy=localhost,127.0.0.1,::1,.cbq.com.qa,10.0.0.0/8
NVIDIA_VISIBLE_DEVICES=void
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ podman exec llama-3.2-nv-embedqa \
  nvidia-smi --query-gpu=index,uuid,name,memory.total,memory.used --format=csv
index, uuid, name, memory.total [MiB], memory.used [MiB]
0, GPU-78aaaccb-3af1-11b3-b1f8-3828c251d9f2, NVIDIA H200-141C, 144384 MiB, 45558 MiB
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ nvidia-smi --query-gpu=index,uuid,name,memory.total,memory.used --format=csv
index, uuid, name, memory.total [MiB], memory.used [MiB]
0, GPU-78aaaccb-3af1-11b3-b1f8-3828c251d9f2, NVIDIA H200-141C, 144384 MiB, 45662 MiB
1, GPU-7bdd753d-3af1-11b3-a839-80f10ebdf224, NVIDIA H200-141C, 144384 MiB, 131470 MiB
2, GPU-7eb48ff1-3af1-11b3-9b3a-93ee06a0ff76, NVIDIA H200-141C, 144384 MiB, 137255 MiB
3, GPU-8187d0e1-3af1-11b3-a3da-3990b94501cf, NVIDIA H200-141C, 144384 MiB, 111082 MiB
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ grep -nE 'upstream |location ' /apps/srv_mlengineering/nim/nginx/nginx_router_hybrid_extended.conf
14:    upstream backend_ollama1 {
19:   # upstream backend_gemma {
24:    upstream backend_qwen7b {
29:    upstream backend_llama70b {
34:    upstream backend_gptoss120b {
40:    upstream backend_qwen3vl {
68:        location / {
84:        location @_backend_ollama1 { proxy_pass http://backend_ollama1; }
85:        # location @_backend_gemma { proxy_pass http://backend_gemma; }
86:        location @_backend_qwen7b { proxy_pass http://backend_qwen7b; }
87:        location @_backend_llama70b { proxy_pass http://backend_llama70b; }
88:        location @_backend_qwen3vl { proxy_pass http://backend_qwen3vl; }
89:        location @_backend_gptoss120b { proxy_pass http://backend_gptoss120b; }
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$
