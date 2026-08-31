cd /apps/srv_mlengineering/nim

python3 <<'PY'
from pathlib import Path
import re
import shutil

compose_path = Path("/apps/srv_mlengineering/nim/docker-compose-extended.yml")
nginx_path = Path("/apps/srv_mlengineering/nim/nginx/nginx_router_hybrid_extended.conf")
router_path = Path("/apps/srv_mlengineering/nim/nginx/router_hybrid_extended.js")

paths = [compose_path, nginx_path, router_path]

for path in paths:
    backup = Path(str(path) + ".bak-before-qwen3-compose")
    if backup.exists():
        raise SystemExit(f"Backup already exists; no changes made: {backup}")

for path in paths:
    shutil.copy2(path, Path(str(path) + ".bak-before-qwen3-compose"))

compose = compose_path.read_text()

# Remove the legacy models from nginx's dependencies.
for service in ("nim-qwen7b", "nim-llama70b"):
    needle = f"      - {service}\n"
    if compose.count(needle) != 1:
        raise SystemExit(f"Expected exactly one dependency line: {needle!r}")
    compose = compose.replace(needle, "", 1)

# Add Qwen 235B alongside the existing GPT-OSS dependency.
gpt_dependency = "      - nim-gptoss120b\n"
if compose.count(gpt_dependency) != 1:
    raise SystemExit("Could not uniquely locate the GPT-OSS dependency")
compose = compose.replace(
    gpt_dependency,
    gpt_dependency + "      - nim-qwen3vl\n",
    1,
)

# Add the Compose service for the currently manual Qwen 235B container.
service_marker = "\n  nim-gptoss120b:\n"
if compose.count(service_marker) != 1:
    raise SystemExit("Could not uniquely locate the nim-gptoss120b service")

if "\n  nim-qwen3vl:\n" in compose:
    raise SystemExit("nim-qwen3vl is already defined in Compose")

qwen_service = """
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
      NIM_MAX_MODEL_LEN: "8192"
      NIM_PASSTHROUGH_ARGS: "--gpu-memory-utilization 0.80 --trust-remote-code --max-num-seqs 8 --max-num-batched-tokens 8192 --enforce-eager"
      PYTHONUNBUFFERED: "1"
      NO_PROXY: "localhost,127.0.0.1,0.0.0.0,host.containers.internal"
      no_proxy: "localhost,127.0.0.1,0.0.0.0,host.containers.internal"
    volumes:
      - "/apps/srv_mlengineering/model-staging/qwen3-vl-235b-awq:/opt/nim/models/qwen3-vl:ro"
      - "/apps/srv_mlengineering/nim-cache:/opt/nim/.cache/ngc"
    devices:
      - "nvidia.com/gpu=2"
      - "nvidia.com/gpu=3"

"""

compose = compose.replace(
    service_marker,
    "\n" + qwen_service + "  nim-gptoss120b:\n",
    1,
)

nginx = nginx_path.read_text()

# Enable Llama using the host port so Nginx starts even while Llama is stopped.
llama_upstream_pattern = re.compile(
    r'(?m)^[ \t]*#\s*upstream backend_llama70b \{[ \t]*\n'
    r'^[ \t]*#\s*server nim-llama70b:8000;[ \t]*\n'
    r'^[ \t]*#\s*keepalive 32;[ \t]*\n'
    r'^[ \t]*#\s*\}[ \t]*\n'
)

nginx, count = llama_upstream_pattern.subn(
    "    upstream backend_llama70b {\n"
    "        server 10.1.94.110:8890;\n"
    "        keepalive 32;\n"
    "    }\n",
    nginx,
    count=1,
)
if count != 1:
    raise SystemExit("Could not uniquely enable the Llama upstream")

nginx, count = re.subn(
    r'(?m)^[ \t]*#\s*location @_backend_llama70b '
    r'\{ proxy_pass http://backend_llama70b; \}[ \t]*$',
    "        location @_backend_llama70b { proxy_pass http://backend_llama70b; }",
    nginx,
    count=1,
)
if count != 1:
    raise SystemExit("Could not uniquely enable the Llama location")

router = router_path.read_text()

llama_js_pattern = re.compile(
    r'(?m)^[ \t]*// if \(model\.indexOf\("llama-3\.3-70b-instruct"\) !== -1\) \{[ \t]*\n'
    r'^[ \t]*//[ \t]*r\.internalRedirect\("@_backend_llama70b"\);[ \t]*\n'
    r'^[ \t]*//[ \t]*return;[ \t]*\n'
    r'^[ \t]*// \}[ \t]*$'
)

router, count = llama_js_pattern.subn(
    '    if (model.indexOf("llama-3.3-70b-instruct") !== -1) {\n'
    '        r.internalRedirect("@_backend_llama70b");\n'
    '        return;\n'
    '    }',
    router,
    count=1,
)
if count != 1:
    raise SystemExit("Could not uniquely enable the Llama JavaScript route")

# Preserve the original inodes used by bind mounts.
for path, content in (
    (compose_path, compose),
    (nginx_path, nginx),
    (router_path, router),
):
    with path.open("r+", encoding="utf-8") as handle:
        handle.seek(0)
        handle.write(content)
        handle.truncate()

print("Files patched successfully.")
for path in paths:
    print(f"Backup: {path}.bak-before-qwen3-compose")
PY