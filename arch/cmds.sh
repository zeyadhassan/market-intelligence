(.venv) PS C:\Users\zehassan\source\repos\market-intelligence> .\run.cmd
Configuration ready: C:\Users\zehassan\source\repos\market-intelligence\deploy\app.env

Checking the model gateways ...
Checking OpenAI-compatible chat endpoint ...
Chat endpoint OK
Checking NVIDIA NIM embedding endpoint ...
2026-09-01 15:07:39 [info     ] embed.start                    component=retrieval.embedders.openai_compatible model=nvidia/llama-3.2-nv-embedqa-1b-v2 n_texts=1
2026-09-01 15:07:39 [info     ] embed.done                     component=retrieval.embedders.openai_compatible n_texts=1 total_tokens=5
NVIDIA NIM embedding endpoint OK (2048 dimensions)

Starting the product ...
Local product configuration is complete.
mode: shadow
access: built-in local analyst
models: extraction, reasoning, embedding, reranker, entailment
>>>> Executing external compose provider "C:\\Users\\zehassan\\source\\repos\\market-intelligence\\.venv\\Scripts\\podman-compose.EXE". Please see podman-compose(1) for how to disable this message. <<<<

Trying to pull docker.io/library/neo4j:5.26-community...
Trying to pull docker.io/pgvector/pgvector:pg16...
Trying to pull docker.io/axllent/mailpit:v1.27...
Error: unable to copy from source docker://axllent/mailpit:v1.27: initializing source docker://axllent/mailpit:v1.27: pinging container registry registry-1.docker.io: Get "https://registry-1.docker.io/v2/": Proxy Authentication Required
Error: unable to copy from source docker://pgvector/pgvector:pg16: initializing source docker://pgvector/pgvector:pg16: pinging container registry registry-1.docker.io: Get "https://registry-1.docker.io/v2/": Proxy Authentication Required
Error: unable to copy from source docker://neo4j:5.26-community: initializing source docker://neo4j:5.26-community: pinging container registry registry-1.docker.io: Get "https://registry-1.docker.io/v2/": Proxy Authentication Required
1b074ba61f486b5fd0a01344eac1283e12f590f4b62e8dac360ba1c49d3b4100
Trying to pull docker.io/pgvector/pgvector:pg16...
Error: unable to copy from source docker://pgvector/pgvector:pg16: initializing source docker://pgvector/pgvector:pg16: pinging container registry registry-1.docker.io: Get "https://registry-1.docker.io/v2/": Proxy Authentication Required
Trying to pull docker.io/library/neo4j:5.26-community...
Error: unable to copy from source docker://neo4j:5.26-community: initializing source docker://neo4j:5.26-community: pinging container registry registry-1.docker.io: Get "https://registry-1.docker.io/v2/": Proxy Authentication Required
Trying to pull docker.io/axllent/mailpit:v1.27...
Error: unable to copy from source docker://axllent/mailpit:v1.27: initializing source docker://axllent/mailpit:v1.27: pinging container registry registry-1.docker.io: Get "https://registry-1.docker.io/v2/": Proxy Authentication Required
Error: no container with name or ID "deploy_postgres_1" found: no such container
Error: no container with name or ID "deploy_neo4j_1" found: no such container
Error: no container with name or ID "deploy_mailpit_1" found: no such container
Error: executing C:\Users\zehassan\source\repos\market-intelligence\.venv\Scripts\podman-compose.EXE --file C:\Users\zehassan\source\repos\market-intelligence\deploy\compose.yml up --detach: exit status 125
podman infrastructure error: Command '('C:\\Users\\zehassan\\AppData\\Local\\Programs\\Podman\\podman.EXE', 'compose', '--file', 'C:\\Users\\zehassan\\source\\repos\\market-intelligence\\deploy\\compose.yml', 'up', '--detach')' returned non-zero exit status 125.
product startup error: Command '('C:\\Users\\zehassan\\source\\repos\\market-intelligence\\.venv\\Scripts\\python.exe', 'C:\\Users\\zehassan\\source\\repos\\market-intelligence\\deploy\\podman_infra.py', 'app-up')' returned non-zero exit status 1.