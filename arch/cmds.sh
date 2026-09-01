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