cd /apps/srv_mlengineering/nim

rg --files . | rg 'docker-compose.*extended.*\.(yml|yaml)$|nginx_router_hybrid_extended\.conf$|router_hybrid_extended\.js$'
[srv_mlengineering@cbq2-svd-dsgpu2 nim]$ find . -type f | grep -E \
'docker-compose.*extended\.(yml|yaml)$|nginx_router_hybrid_extended\.conf$|router_hybrid_extended\.js$'
./nginx/nginx_router_hybrid_extended.conf
./nginx/router_hybrid_extended.js
./docker-compose-extended.yml

AI_COMPOSE_FILE=/apps/srv_mlengineering/nim/docker-compose-extended.yml
AI_NGINX_FILE=/apps/srv_mlengineering/nim/nginx/nginx_router_hybrid_extended.conf
AI_ROUTER_FILE=/apps/srv_mlengineering/nim/nginx/router_hybrid_extended.js

ls -l "$AI_COMPOSE_FILE" "$AI_NGINX_FILE" "$AI_ROUTER_FILE"

podman --version
podman-compose --version 2>&1 || true

podman-compose -f "$AI_COMPOSE_FILE" config --services

rg -n \
'nim-qwen7b|nim-llama70b|nim-qwen3vl|nim-gptoss120b|nginx-reverseproxy|depends_on:|restart:|nvidia\.com/gpu|8889|8890|8897|8899' \
"$AI_COMPOSE_FILE"

rg -n -C 4 \
'nim-qwen7b|nim-llama70b|nim-qwen3vl|nim-gptoss120b|backend_qwen|backend_llama|backend_gptoss|8889|8890|8897|8899' \
"$AI_NGINX_FILE"

rg -n -C 3 \
'qwen|llama|gpt-oss|internalRedirect|ollama' \
"$AI_ROUTER_FILE"

docker-compose.*extended\.(yml|yaml)$|nginx_router_hybrid_extended\.conf$|router_hybrid_extended\.js$'
./nginx/nginx_router_hybrid_extended.conf
./nginx/router_hybrid_extended.js
./docker-compose-extended.yml
[srv_mlengineering@cbq2-svd-dsgpu2 nim]$ ^C
[srv_mlengineering@cbq2-svd-dsgpu2 nim]$ AI_COMPOSE_FILE=/apps/srv_mlengineering/nim/docker-compose-extended.yml
AI_NGINX_FILE=/apps/srv_mlengineering/nim/nginx/nginx_router_hybrid_extended.conf
AI_ROUTER_FILE=/apps/srv_mlengineering/nim/nginx/router_hybrid_extended.js
[srv_mlengineering@cbq2-svd-dsgpu2 nim]$ ls -l "$AI_COMPOSE_FILE" "$AI_NGINX_FILE" "$AI_ROUTER_FILE"
-rw-r-----. 1 srv_mlengineering srv_mlengineering 5436 Aug 19 13:24 /apps/srv_mlengineering/nim/docker-compose-extended.yml
-rw-r-----. 1 srv_mlengineering srv_mlengineering 2763 Aug 31 14:38 /apps/srv_mlengineering/nim/nginx/nginx_router_hybrid_extended.conf
-rw-r-----. 1 srv_mlengineering srv_mlengineering 1239 Aug 31 11:22 /apps/srv_mlengineering/nim/nginx/router_hybrid_extended.js
[srv_mlengineering@cbq2-svd-dsgpu2 nim]$ podman-compose -f "$AI_COMPOSE_FILE" config --services
nginx
ollama-node-1
nim-qwen7b
nim-llama70b
nim-gptoss120b
[srv_mlengineering@cbq2-svd-dsgpu2 nim]$ rg -n \
'nim-qwen7b|nim-llama70b|nim-qwen3vl|nim-gptoss120b|nginx-reverseproxy|depends_on:|restart:|nvidia\.com/gpu|8889|8890|8897|8899' \
"$AI_COMPOSE_FILE"
-bash: rg: command not found
[srv_mlengineering@cbq2-svd-dsgpu2 nim]$ grep -nE \
'nim-qwen7b|nim-llama70b|nim-qwen3vl|nim-gptoss120b|nginx-reverseproxy|depends_on:|restart:|nvidia\.com/gpu|8889|8890|8897|8899' \
"$AI_COMPOSE_FILE"
14:    container_name: nginx-reverseproxy
15:    restart: unless-stopped
24:    depends_on:
26:      - nim-qwen7b
27:      - nim-llama70b
28:      - nim-gptoss120b
36:    restart: unless-stopped
43:      NO_PROXY: "localhost,127.0.0.1,0.0.0.0,host.containers.internal,ollama1,nim-gemma,nim-qwen7b,nim-llama70b"
46:      no_proxy: "localhost,127.0.0.1,0.0.0.0,host.containers.internal,ollama1,nim-gemma,nim-qwen7b,nim-llama70b"
61:      - "nvidia.com/gpu=0"
66:  nim-qwen7b:
68:    container_name: nim-qwen7b
69:    restart: unless-stopped
71:      - "8889:8000"
76:      NO_PROXY: "localhost,127.0.0.1,0.0.0.0,host.containers.internal,ollama1,nim-gemma,nim-qwen7b,nim-llama70b"
79:      no_proxy: "localhost,127.0.0.1,0.0.0.0,host.containers.internal,ollama1,nim-gemma,nim-qwen7b,nim-llama70b"
87:      - "nvidia.com/gpu=2"
91:  nim-llama70b:
93:    container_name: nim-llama70b
94:    restart: unless-stopped
96:      - "8890:8000"
101:      NO_PROXY: "localhost,127.0.0.1,0.0.0.0,host.containers.internal,ollama1,nim-gemma,nim-qwen7b,nim-llama70b"
104:      no_proxy: "localhost,127.0.0.1,0.0.0.0,host.containers.internal,ollama1,nim-gemma,nim-qwen7b,nim-llama70b"
116:      - "nvidia.com/gpu=3"
120:  nim-gptoss120b:
122:    container_name: nim-gptoss120b
123:    restart: unless-stopped
125:      - "8897:8000"
130:      NO_PROXY: "localhost,127.0.0.1,0.0.0.0,host.containers.internal,ollama1,nim-gemma,nim-qwen7b,nim-llama70b,nim-gptoss120b"
133:      no_proxy: "localhost,127.0.0.1,0.0.0.0,host.containers.internal,ollama1,nim-gemma,nim-qwen7b,nim-llama70b,nim-gptoss120b"
149:      - "nvidia.com/gpu=1"
[srv_mlengineering@cbq2-svd-dsgpu2 nim]$ grep -nC 4 -E \
'nim-qwen7b|nim-llama70b|nim-qwen3vl|nim-gptoss120b|backend_qwen|backend_llama|backend_gptoss|8889|8890|8897|8899' \
"$AI_NGINX_FILE"
20-   #     server nim-gemma:8000;
21-   #     keepalive 32;
22-   # }
23-
24:    upstream backend_qwen7b {
25:        server 10.1.94.110:8889;
26-        keepalive 32;
27-    }
28-
29:   # upstream backend_llama70b {
30:   #     server nim-llama70b:8000;
31-   #     keepalive 32;
32-   # }
33-
34:    upstream backend_gptoss120b {
35:        server 10.1.94.110:8897;
36-        keepalive 32;
37-    }
38-
39-
40:    upstream backend_qwen3vl {
41:        server 10.1.94.110:8899;
42-        keepalive 32;
43-    }
44-
45-log_format upstream_time '$proxy_add_x_forwarded_for - $remote_user [$time_local] '
--
82-        }
83-
84-        location @_backend_ollama1 { proxy_pass http://backend_ollama1; }
85-        # location @_backend_gemma { proxy_pass http://backend_gemma; }
86:        location @_backend_qwen7b { proxy_pass http://backend_qwen7b; }
87:       # location @_backend_llama70b { proxy_pass http://backend_llama70b; }
88:        location @_backend_qwen3vl { proxy_pass http://backend_qwen3vl; }
89:        location @_backend_gptoss120b { proxy_pass http://backend_gptoss120b; }
90-   }
91-}
srv_mlengineering@cbq2-svd-dsgpu2 nim]$ grep -nC 3 -E \
'qwen|llama|gpt-oss|internalRedirect|ollama' \
"$AI_ROUTER_FILE"
1-function get_backend(r) {
2-    if (r.method === 'GET') {
3:        r.internalRedirect("@_backend_ollama1");
4-        return;
5-    }
6-
--
16-        return;
17-    }
18-
19:    if (model.indexOf("qwen3-vl") !== -1) {
20:        r.internalRedirect("@_backend_qwen3vl");
21-        return;
22-    }
23-
24-    if (model.indexOf("gemma-4") !== -1 || model.indexOf("gemma-4-31b-it") !== -1) {
25:        r.internalRedirect("@_backend_gemma");
26-        return;
27-    }
28-
29:    if (model.indexOf("qwen-2.5-7b-instruct") !== -1) {
30:        r.internalRedirect("@_backend_qwen7b");
31-        return;
32-    }
33-
34:    if (model.indexOf("gpt-oss-120b") !== -1) {
35:        r.internalRedirect("@_backend_gptoss120b");
36-        return;
37-    }
38-
39:    // if (model.indexOf("llama-3.3-70b-instruct") !== -1) {
40:    //     r.internalRedirect("@_backend_llama70b");
41-    //     return;
42-    // }
43-
44:    // Default fallback to Ollama
45:    r.internalRedirect("@_backend_ollama1");
46-    return;
47-}
48-

for AI_CONTAINER in \
  nginx-reverseproxy \
  nim-qwen7b \
  nim-llama70b \
  nim-qwen3vl \
  nim-gptoss120b
do
  podman inspect "$AI_CONTAINER" \
    --format 'name={{.Name}} status={{.State.Status}} network={{.HostConfig.NetworkMode}} restart={{.HostConfig.RestartPolicy.Name}} compose_service={{index .Config.Labels "com.docker.compose.service"}} podman_compose_service={{index .Config.Labels "io.podman.compose.service"}}' \
    2>&1
done
name=nginx-reverseproxy status=running network=bridge restart=unless-stopped compose_service=nginx podman_compose_service=nginx
name=nim-qwen7b status=exited network=bridge restart=unless-stopped compose_service=nim-qwen7b podman_compose_service=nim-qwen7b
name=nim-llama70b status=exited network=bridge restart=unless-stopped compose_service=nim-llama70b podman_compose_service=nim-llama70b
name=nim-qwen3vl status=running network=pasta restart=no compose_service= podman_compose_service=
name=nim-gptoss120b status=running network=bridge restart=unless-stopped compose_service=nim-gptoss120b podman_compose_service=nim-gptoss120b

for AI_PORT in 8889 8890 8897 8899
do
  printf 'PORT %s: ' "$AI_PORT"
  curl -fsS --max-time 3 "http://127.0.0.1:${AI_PORT}/v1/models" |
    jq -r '.data[].id' 2>/dev/null || printf 'DOWN\n'
done


PORT 8889: curl: (7) Failed to connect to 127.0.0.1 port 8889: Connection refused
PORT 8890: curl: (7) Failed to connect to 127.0.0.1 port 8890: Connection refused
PORT 8897: openai/gpt-oss-120b
PORT 8899: qwen3-vl-235b-awq




podman-compose -f "$AI_COMPOSE_FILE" config >/tmp/docker-compose-extended.validated.yml &&
printf 'Compose validation: PASS\n'

podman-compose -f "$AI_COMPOSE_FILE" config --services

podman exec nginx-reverseproxy nginx -t


grep -nE \
'nim-qwen7b|nim-llama70b|nim-qwen3vl|nim-gptoss120b|depends_on:|8889|8890|8897|8899' \
"$AI_COMPOSE_FILE"

grep -nC 2 -E \
'backend_qwen|backend_llama|backend_gptoss|8889|8890|8897|8899' \
"$AI_NGINX_FILE"

grep -nC 2 -E \
'qwen|llama|gpt-oss' \
"$AI_ROUTER_FILE"


podman exec nginx-reverseproxy nginx -s reload


if podman container exists nim-qwen3vl-manual-backup; then
  printf 'STOP: nim-qwen3vl-manual-backup already exists\n'
else
  printf 'Backup container name is available\n'
fi


podman stop -t 120 nim-qwen3vl
podman rename nim-qwen3vl nim-qwen3vl-manual-backup

podman-compose -f "$AI_COMPOSE_FILE" up -d nim-qwen3vl

podman logs --tail 100 -f nim-qwen3vl

curl -fsS http://127.0.0.1:8899/v1/models | jq

podman-compose -f "$AI_COMPOSE_FILE" up -d nginx

____________________________________________________________

'nim-qwen7b|nim-llama70b|nim-qwen3vl|nim-gptoss120b|depends_on:|8889|8890|8897|8899' \
"$AI_COMPOSE_FILE"
24:    depends_on:
26:      - nim-gptoss120b
27:      - nim-qwen3vl
42:      NO_PROXY: "localhost,127.0.0.1,0.0.0.0,host.containers.internal,ollama1,nim-gemma,nim-qwen7b,nim-llama70b"
45:      no_proxy: "localhost,127.0.0.1,0.0.0.0,host.containers.internal,ollama1,nim-gemma,nim-qwen7b,nim-llama70b"
65:  nim-qwen7b:
67:    container_name: nim-qwen7b
70:      - "8889:8000"
75:      NO_PROXY: "localhost,127.0.0.1,0.0.0.0,host.containers.internal,ollama1,nim-gemma,nim-qwen7b,nim-llama70b"
78:      no_proxy: "localhost,127.0.0.1,0.0.0.0,host.containers.internal,ollama1,nim-gemma,nim-qwen7b,nim-llama70b"
90:  nim-llama70b:
92:    container_name: nim-llama70b
95:      - "8890:8000"
100:      NO_PROXY: "localhost,127.0.0.1,0.0.0.0,host.containers.internal,ollama1,nim-gemma,nim-qwen7b,nim-llama70b"
103:      no_proxy: "localhost,127.0.0.1,0.0.0.0,host.containers.internal,ollama1,nim-gemma,nim-qwen7b,nim-llama70b"
120:  nim-qwen3vl:
122:    container_name: nim-qwen3vl
129:      - "8899:8000"
146:  nim-gptoss120b:
148:    container_name: nim-gptoss120b
151:      - "8897:8000"
156:      NO_PROXY: "localhost,127.0.0.1,0.0.0.0,host.containers.internal,ollama1,nim-gemma,nim-qwen7b,nim-llama70b,nim-gptoss120b"
159:      no_proxy: "localhost,127.0.0.1,0.0.0.0,host.containers.internal,ollama1,nim-gemma,nim-qwen7b,nim-llama70b,nim-gptoss120b"
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ grep -nC 2 -E \
'backend_qwen|backend_llama|backend_gptoss|8889|8890|8897|8899' \
"$AI_NGINX_FILE"
22-   # }
23-
24:    upstream backend_qwen7b {
25:        server 10.1.94.110:8889;
26-        keepalive 32;
27-    }
28-
29:    upstream backend_llama70b {
30:        server 10.1.94.110:8890;
31-        keepalive 32;
32-    }
33-
34:    upstream backend_gptoss120b {
35:        server 10.1.94.110:8897;
36-        keepalive 32;
37-    }
38-
39-
40:    upstream backend_qwen3vl {
41:        server 10.1.94.110:8899;
42-        keepalive 32;
43-    }
--
84-        location @_backend_ollama1 { proxy_pass http://backend_ollama1; }
85-        # location @_backend_gemma { proxy_pass http://backend_gemma; }
86:        location @_backend_qwen7b { proxy_pass http://backend_qwen7b; }
87:        location @_backend_llama70b { proxy_pass http://backend_llama70b; }
88:        location @_backend_qwen3vl { proxy_pass http://backend_qwen3vl; }
89:        location @_backend_gptoss120b { proxy_pass http://backend_gptoss120b; }
90-   }
91-}
[srv_mlengineering@cbq2-svd-dsgpu2 ~]$ grep -nC 2 -E \
'qwen|llama|gpt-oss' \
"$AI_ROUTER_FILE"
1-function get_backend(r) {
2-    if (r.method === 'GET') {
3:        r.internalRedirect("@_backend_ollama1");
4-        return;
5-    }
--
17-    }
18-
19:    if (model.indexOf("qwen3-vl") !== -1) {
20:        r.internalRedirect("@_backend_qwen3vl");
21-        return;
22-    }
--
27-    }
28-
29:    if (model.indexOf("qwen-2.5-7b-instruct") !== -1) {
30:        r.internalRedirect("@_backend_qwen7b");
31-        return;
32-    }
33-
34:    if (model.indexOf("gpt-oss-120b") !== -1) {
35-        r.internalRedirect("@_backend_gptoss120b");
36-        return;
37-    }
38-
39:    if (model.indexOf("llama-3.3-70b-instruct") !== -1) {
40:        r.internalRedirect("@_backend_llama70b");
41-        return;
42-    }
43-
44:    // Default fallback to Ollama
45:    r.internalRedirect("@_backend_ollama1");
46-    return;
47-}
