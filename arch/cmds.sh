(.venv) PS C:\Users\zehassan\source\repos\market-intelligence> .\run.cmd --no-browser
Configuration ready: C:\Users\zehassan\source\repos\market-intelligence\deploy\app.env

Checking the model gateways ...
Checking OpenAI-compatible chat endpoint ...
Chat endpoint OK
Checking NVIDIA NIM embedding endpoint ...
2026-09-01 15:37:15 [info     ] embed.start                    component=retrieval.embedders.openai_compatible model=nvidia/llama-3.2-nv-embedqa-1b-v2 n_texts=1
2026-09-01 15:37:15 [info     ] embed.done                     component=retrieval.embedders.openai_compatible n_texts=1 total_tokens=5
NVIDIA NIM embedding endpoint OK (2048 dimensions)

Starting the product ...
Local product configuration is complete.
mode: shadow
access: built-in local analyst
models: extraction, reasoning, embedding, reranker, entailment
>>>> Executing external compose provider "C:\\Users\\zehassan\\source\\repos\\market-intelligence\\.venv\\Scripts\\podman-compose.EXE". Please see podman-compose(1) for how to disable this message. <<<<

9b05db12a35460fff0587e009f9326e414a53e9484555547f96d214a2ba98ef7
d3238814e371a990ab3d08a8e9b13936953e448590a6648233270a2e3e8fcf75
8ccf5ebc4812295e1cc59ad9ce2fa9f1d8a4d167cbb8198c716f09d5a848cfec
postgres schema is current
>>>> Executing external compose provider "C:\\Users\\zehassan\\source\\repos\\market-intelligence\\.venv\\Scripts\\podman-compose.EXE". Please see podman-compose(1) for how to disable this message. <<<<

9b05db12a35460fff0587e009f9326e414a53e9484555547f96d214a2ba98ef7
8ccf5ebc4812295e1cc59ad9ce2fa9f1d8a4d167cbb8198c716f09d5a848cfec
d3238814e371a990ab3d08a8e9b13936953e448590a6648233270a2e3e8fcf75
STEP 1/11: FROM docker.io/library/python:3.13-slim
                                                  STEP 1/11: FROM docker.io/library/python:3.13-slim
                                                                                                    STEP 1/11: FROM docker.io/library/python:3.13-slim
    STEP 1/11: FROM docker.io/library/python:3.13-slim
                                                      STEP 1/11: FROM docker.io/library/python:3.13-slim
                                                                                                        STEP 2/11: ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     PIP_DISABLE_PIP_VERSION_CHECK=1
                                                         STEP 2/11: ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     PIP_DISABLE_PIP_VERSION_CHECK=1
          STEP 2/11: ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     PIP_DISABLE_PIP_VERSION_CHECK=1
                                                                                                             STEP 2/11: ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     PIP_DISABLE_PIP_VERSION_CHECK=1
                                                              STEP 2/11: ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     PIP_DISABLE_PIP_VERSION_CHECK=1
               STEP 1/11: FROM docker.io/library/python:3.13-slim
--> 1fb9e309c6e8
--> 963f96046645
--> 02055724616f
--> 743532d834cb
--> 517480cfbcfb
STEP 3/11: WORKDIR /app
STEP 3/11: WORKDIR /app
STEP 3/11: WORKDIR /app
                       STEP 3/11: WORKDIR /app
                                              STEP 3/11: WORKDIR /app
                                                                     STEP 2/11: ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     PIP_DISABLE_PIP_VERSION_CHECK=1
                      --> Using cache 02055724616f03be24addba7bb333da601c15471de95c62afaf1a4dc322f5789
                                                                                                      --> 02055724616f
                                                                                                                      STEP 3/11: WORKDIR /app
                                                                                                                                             --> 1a146aaa1bf1
           --> a9be069d55c8
                           --> Using cache 1a146aaa1bf10e52368f0393000c7e0789b0c63ee67105f4fdb9846aab8371c3
                                                                                                           --> 1a146aaa1bf1
                                                                                                                           STEP 4/11: RUN groupadd --system fi-intel     && useradd --system --gid fi-intel --home-dir /app fi-intel
                                                                                  STEP 4/11: RUN groupadd --system fi-intel     && useradd --system --gid fi-intel --home-dir /app fi-intel
                                         --> 547f019c9538
--> d2637cc10e4d
STEP 4/11: RUN groupadd --system fi-intel     && useradd --system --gid fi-intel --home-dir /app fi-intel
STEP 1/11: FROM docker.io/library/python:3.13-slim
STEP 4/11: RUN groupadd --system fi-intel     && useradd --system --gid fi-intel --home-dir /app fi-intel
STEP 4/11: RUN groupadd --system fi-intel     && useradd --system --gid fi-intel --home-dir /app fi-intel
--> d176c08c6d3f
STEP 2/11: ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     PIP_DISABLE_PIP_VERSION_CHECK=1
STEP 4/11: RUN groupadd --system fi-intel     && useradd --system --gid fi-intel --home-dir /app fi-intel
--> Using cache 02055724616f03be24addba7bb333da601c15471de95c62afaf1a4dc322f5789
--> 02055724616f
STEP 3/11: WORKDIR /app
--> Using cache 1a146aaa1bf10e52368f0393000c7e0789b0c63ee67105f4fdb9846aab8371c3
--> 1a146aaa1bf1
STEP 4/11: RUN groupadd --system fi-intel     && useradd --system --gid fi-intel --home-dir /app fi-intel
--> db5c2cd34824
--> Using cache db5c2cd34824fdf548bf44aecc371d75c86723e8cbe4ce877ab80c0bf17574bc
--> db5c2cd34824
STEP 5/11: COPY pyproject.toml README.md /app/
STEP 5/11: COPY pyproject.toml README.md /app/
--> db5be8313207
--> 39e74883ce93
STEP 5/11: COPY pyproject.toml README.md /app/
--> a305ec1479df
STEP 5/11: COPY pyproject.toml README.md /app/
STEP 5/11: COPY pyproject.toml README.md /app/
--> 733646836bf9
--> 876ee7e1bbfe
STEP 5/11: COPY pyproject.toml README.md /app/
STEP 5/11: COPY pyproject.toml README.md /app/
--> 224bfa1514b7
--> c2f822a2bc26
--> f7ed0fdd1f8d
STEP 6/11: COPY fi_intel /app/fi_intel
STEP 6/11: COPY fi_intel /app/fi_intel
STEP 6/11: COPY fi_intel /app/fi_intel
--> 753d6ac52ed6
--> 75a39dd29bd0
--> f10aa2d49ab1
--> f7e3693a70e8
STEP 6/11: COPY fi_intel /app/fi_intel
STEP 6/11: COPY fi_intel /app/fi_intel
STEP 6/11: COPY fi_intel /app/fi_intel
STEP 6/11: COPY fi_intel /app/fi_intel
--> 184413d2cdd4
--> 706f44e804fd
--> 8423d3717b28
STEP 7/11: COPY evals /app/evals
STEP 7/11: COPY evals /app/evals
STEP 7/11: COPY evals /app/evals
--> c1a7dcc83367
STEP 7/11: COPY evals /app/evals
--> fedd840a17fc
--> 5513b7cd9641
--> ffea4a2e51c5
STEP 7/11: COPY evals /app/evals
STEP 7/11: COPY evals /app/evals
STEP 7/11: COPY evals /app/evals
--> 0fc31fd91b4b
--> e1af7021abfa
--> 151fcab24503
STEP 8/11: RUN python -m pip install --no-cache-dir .
--> 57f84006bcdf
--> ed97340bb727
STEP 8/11: RUN python -m pip install --no-cache-dir .
--> fbd9a34238cd
STEP 8/11: RUN python -m pip install --no-cache-dir .
STEP 8/11: RUN python -m pip install --no-cache-dir .
--> dc9fe9ba250d
STEP 8/11: RUN python -m pip install --no-cache-dir .
STEP 8/11: RUN python -m pip install --no-cache-dir .
STEP 8/11: RUN python -m pip install --no-cache-dir .
Processing ./.
  Installing build dependencies: started
Processing ./.
  Installing build dependencies: started
Processing ./.
  Installing build dependencies: started
Processing ./.
  Installing build dependencies: started
Processing ./.
  Installing build dependencies: started
Processing ./.
  Installing build dependencies: started
Processing ./.
  Installing build dependencies: started
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: finished with status 'error'
  error: subprocess-exited-with-error
  
  × installing build dependencies did not run successfully.
  │ exit code: 1
  ╰─> [7 lines of output]
      WARNING: Retrying (Retry(total=4, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=3, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=2, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=1, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=0, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      ERROR: Could not find a version that satisfies the requirement setuptools>=68 (from versions: none)
      ERROR: No matching distribution found for setuptools>=68
      [end of output]
  
  note: This error originates from a subprocess, and is likely not a problem with pip.
ERROR: Failed to build 'file:///app' when installing build dependencies
  Installing build dependencies: finished with status 'error'
  error: subprocess-exited-with-error
  
  × installing build dependencies did not run successfully.
  │ exit code: 1
  ╰─> [7 lines of output]
      WARNING: Retrying (Retry(total=4, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=3, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=2, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=1, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=0, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      ERROR: Could not find a version that satisfies the requirement setuptools>=68 (from versions: none)
      ERROR: No matching distribution found for setuptools>=68
      [end of output]
  
  note: This error originates from a subprocess, and is likely not a problem with pip.
ERROR: Failed to build 'file:///app' when installing build dependencies
  Installing build dependencies: finished with status 'error'
  error: subprocess-exited-with-error
  
  × installing build dependencies did not run successfully.
  │ exit code: 1
  ╰─> [7 lines of output]
      WARNING: Retrying (Retry(total=4, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=3, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=2, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=1, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=0, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      ERROR: Could not find a version that satisfies the requirement setuptools>=68 (from versions: none)
      ERROR: No matching distribution found for setuptools>=68
      [end of output]
  
  note: This error originates from a subprocess, and is likely not a problem with pip.
ERROR: Failed to build 'file:///app' when installing build dependencies
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: still running...
  Installing build dependencies: finished with status 'error'
  Installing build dependencies: finished with status 'error'
  error: subprocess-exited-with-error
  
  × installing build dependencies did not run successfully.
  │ exit code: 1
  ╰─> [7 lines of output]
      WARNING: Retrying (Retry(total=4, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=3, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=2, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=1, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=0, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      ERROR: Could not find a version that satisfies the requirement setuptools>=68 (from versions: none)
      ERROR: No matching distribution found for setuptools>=68
      [end of output]
  
  note: This error originates from a subprocess, and is likely not a problem with pip.
ERROR: Failed to build 'file:///app' when installing build dependencies
  error: subprocess-exited-with-error
  
  × installing build dependencies did not run successfully.
  │ exit code: 1
  ╰─> [7 lines of output]
      WARNING: Retrying (Retry(total=4, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=3, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=2, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=1, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=0, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      ERROR: Could not find a version that satisfies the requirement setuptools>=68 (from versions: none)
      ERROR: No matching distribution found for setuptools>=68
      [end of output]
  
  note: This error originates from a subprocess, and is likely not a problem with pip.
ERROR: Failed to build 'file:///app' when installing build dependencies
  Installing build dependencies: still running...
  Installing build dependencies: finished with status 'error'
  error: subprocess-exited-with-error
  
  × installing build dependencies did not run successfully.
  │ exit code: 1
  ╰─> [7 lines of output]
      WARNING: Retrying (Retry(total=4, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=3, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=2, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=1, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=0, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      ERROR: Could not find a version that satisfies the requirement setuptools>=68 (from versions: none)
      ERROR: No matching distribution found for setuptools>=68
      [end of output]
  
  note: This error originates from a subprocess, and is likely not a problem with pip.
ERROR: Failed to build 'file:///app' when installing build dependencies
Error: building at STEP "RUN python -m pip install --no-cache-dir .": while running runtime: exit status 1

  Installing build dependencies: finished with status 'error'
  error: subprocess-exited-with-error
  
  × installing build dependencies did not run successfully.
  │ exit code: 1
  ╰─> [7 lines of output]
      WARNING: Retrying (Retry(total=4, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=3, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=2, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=1, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      WARNING: Retrying (Retry(total=0, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError("HTTPSConnection(host='pypi.org', port=443): Failed to establish a new connection: [Errno 101] Network is unreachable")': /simple/setuptools/
      ERROR: Could not find a version that satisfies the requirement setuptools>=68 (from versions: none)
      ERROR: No matching distribution found for setuptools>=68
      [end of output]
  
  note: This error originates from a subprocess, and is likely not a problem with pip.
ERROR: Failed to build 'file:///app' when installing build dependencies
Error: building at STEP "RUN python -m pip install --no-cache-dir .": while running runtime: exit status 1

Error: building at STEP "RUN python -m pip install --no-cache-dir .": while running runtime: exit status 1

Error: building at STEP "RUN python -m pip install --no-cache-dir .": while running runtime: exit status 1

Error: building at STEP "RUN python -m pip install --no-cache-dir .": while running runtime: exit status 1

Error: building at STEP "RUN python -m pip install --no-cache-dir .": while running runtime: exit status 1

Error: building at STEP "RUN python -m pip install --no-cache-dir .": while running runtime: exit status 1

ERROR:podman_compose:Build command failed
ERROR:podman_compose:Prepare images failed
Error: executing C:\Users\zehassan\source\repos\market-intelligence\.venv\Scripts\podman-compose.EXE --file C:\Users\zehassan\source\repos\market-intelligence\deploy\compose.yml --env-file C:\Users\zehassan\source\repos\market-intelligence\deploy\app.env --profile app up --detach --build: exit status 1
podman infrastructure error: Command '('C:\\Users\\zehassan\\AppData\\Local\\Programs\\Podman\\podman.EXE', 'compose', '--file', 'C:\\Users\\zehassan\\source\\repos\\market-intelligence\\deploy\\compose.yml', '--env-file', 'C:\\Users\\zehassan\\source\\repos\\market-intelligence\\deploy\\app.env', '--profile', 'app', 'up', '--detach', '--build')' returned non-zero exit status 1.
product startup error: Command '('C:\\Users\\zehassan\\source\\repos\\market-intelligence\\.venv\\Scripts\\python.exe', 'C:\\Users\\zehassan\\source\\repos\\market-intelligence\\deploy\\podman_infra.py', 'app-up')' returned non-zero exit status 1.
