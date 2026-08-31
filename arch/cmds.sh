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



curl -fsS http://127.0.0.1:8897/v1/models | jq -r '.data[].id'
curl -fsS http://127.0.0.1:8899/v1/models | jq -r '.data[].id'
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
openai/gpt-oss-120b
qwen3-vl-235b-awq
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ curl -sk --max-time 300 \
  https://127.0.0.1:8443/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"qwen3-vl-235b-awq",
    "messages":[{"role":"user","content":"Reply only with OK"}],
    "max_tokens":16
  }' | jq
parse error: Invalid numeric literal at line 1, column 7
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ ^C
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ curl -sk --max-time 300 \
  https://127.0.0.1:8443/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"qwen3-vl-235b-awq",
    "messages":[{"role":"user","content":"Reply only with OK"}],
    "max_tokens":16
  }'
``
<html>
<head><title>502 Bad Gateway</title></head>
<body>
<center><h1>502 Bad Gateway</h1></center>
<hr><center>nginx</center>
</body>
</html>
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ ^C
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ curl -sk --max-time 300 \
  https://127.0.0.1:8443/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"openai/gpt-oss-120b",
    "messages":[{"role":"user","content":"Reply only with OK"}],
    "max_tokens":16
  }'
{"id":"chatcmpl-a47c93077b0e387a","object":"chat.completion","created":1788180297,"model":"openai/gpt-oss-120b","choices":[{"index":0,"message":{"role":"assistant","content":null,"refusal":null,"annotations":null,"audio":null,"function_call":null,"reasoning":"User says \"Reply only with OK\". So we must respond with"},"logprobs":null,"finish_reason":"length","stop_reason":null,"token_ids":null,"routed_experts":null}],"service_tier":null,"system_fingerprint":"vllm-0.26.0-2844599c","usage":{"prompt_tokens":71,"total_tokens":87,"completion_tokens":16,"prompt_tokens_details":null},"prompt_logprobs":null,"prompt_token_ids":null,"prompt_text":null,"kv_transfer_params":null,"ec_transfer_params":null,"metrics":null}[srv_mlengineering@cbq2-svd-dsgpupodman inspect nim-qwen3vl \n3vl \
  --format 'status={{.State.Status}} restart={{.HostConfig.RestartPolicy.Name}} compose_service={{index .Config.Labels "io.podman.compose.service"}}'
status=running restart=no compose_service=
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ podman ps -a --format 'table {{.Names}}\t{{.Status}}' |
grep -E 'nim-qwen3vl|nim-gptoss120b|nim-qwen7b|nim-llama70b'
nim-qwen7b                        Exited (137) 26 hours ago
nim-llama70b                      Exited (0) 26 hours ago
nim-gptoss120b                    Up 26 hours
nim-qwen3vl                       Up 25 hours
