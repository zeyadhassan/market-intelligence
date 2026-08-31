Back up Compose:

cp -p /apps/srv_mlengineering/nim/docker-compose-extended.yml /apps/srv_mlengineering/nim/docker-compose-extended.yml.before-32k-tools

Update both settings together:

sed -i '/^  nim-qwen3vl:/,/^  nim-gptoss120b:/ {
s|^[[:space:]]*NIM_MAX_MODEL_LEN:.*|      NIM_MAX_MODEL_LEN: "32768"|
s|^[[:space:]]*NIM_PASSTHROUGH_ARGS:.*|      NIM_PASSTHROUGH_ARGS: "--gpu-memory-utilization 0.80 --trust-remote-code --max-num-seqs 4 --max-num-batched-tokens 8192 --enforce-eager --enable-auto-tool-choice --tool-call-parser qwen3_coder"|
}' /apps/srv_mlengineering/nim/docker-compose-extended.yml

Confirm the final configuration:

sed -n '/^  nim-qwen3vl:/,/^  nim-gptoss120b:/p' /apps/srv_mlengineering/nim/docker-compose-extended.yml

You should see:

NIM_MAX_MODEL_LEN: "32768"
NIM_PASSTHROUGH_ARGS: "--gpu-memory-utilization 0.80 --trust-remote-code --max-num-seqs 4 --max-num-batched-tokens 8192 --enforce-eager --enable-auto-tool-choice --tool-call-parser qwen3_coder"

Validate Compose:

podman-compose -f /apps/srv_mlengineering/nim/docker-compose-extended.yml config >/dev/null && echo "Compose configuration OK"

Recreate only Qwen:

podman-compose -f /apps/srv_mlengineering/nim/docker-compose-extended.yml up -d --no-deps --force-recreate nim-qwen3vl

Watch startup:

podman logs --tail 100 -f nim-qwen3vl

After startup completes, press Ctrl+C and test automatic tool calling through Nginx:

curl -sk --max-time 300 https://127.0.0.1:8443/v1/chat/completions \
-H 'Content-Type: application/json' \
-d '{
  "model":"qwen3-vl-235b-awq",
  "messages":[
    {
      "role":"user",
      "content":"Use the get_weather tool to get the weather in Doha."
    }
  ],
  "tools":[
    {
      "type":"function",
      "function":{
        "name":"get_weather",
        "description":"Get the current weather for a city",
        "parameters":{
          "type":"object",
          "properties":{
            "city":{"type":"string"}
          },
          "required":["city"]
        }
      }
    }
  ],
  "tool_choice":"auto",
  "parallel_tool_calls":false,
  "temperature":0,
  "max_tokens":256
}' | jq '.choices[0] | {finish_reason, message}'

A successful result should contain something like:

{
  "finish_reason": "tool_calls",
  "message": {
    "tool_calls": [
      {
        "type": "function",
        "function": {
          "name": "get_weather",
          "arguments": "{\"city\":\"Doha\"}"
        }
      }
    ]
  }
}

If Qwen fails to start, restore immediately:

cp -p /apps/srv_mlengineering/nim/docker-compose-extended.yml.before-32k-tools /apps/srv_mlengineering/nim/docker-compose-extended.yml
podman-compose -f /apps/srv_mlengineering/nim/docker-compose-extended.yml up -d --no-deps --force