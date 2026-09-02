(.venv) PS C:\Users\zehassan\source\repos\market-intelligence> .venv/bin/python deploy/podman_infra.py app-up
.venv/bin/python : The term '.venv/bin/python' is not recognized as the name of a cmdlet, function, script file, or 
operable program. Check the spelling of the name, or if a path was included, verify that the path is correct and try again.
At line:1 char:1
+ .venv/bin/python deploy/podman_infra.py app-up
+ ~~~~~~~~~~~~~~~~
    + CategoryInfo          : ObjectNotFound: (.venv/bin/python:String) [], CommandNotFoundException
    + FullyQualifiedErrorId : CommandNotFoundException
 
(.venv) PS C:\Users\zehassan\source\repos\market-intelligence> python deploy/podman_infra.py app-up          
podman infrastructure error: Duplicate deploy/app.env setting on line 26: FI_INTEL_SOURCE_HTTP_PROXY
(.venv) PS C:\Users\zehassan\source\repos\market-intelligence> python deploy/podman_infra.py app-up
Local product configuration is complete.
mode: shadow
access: built-in local analyst
models: extraction, reasoning, embedding, reranker, entailment
>>>> Executing external compose provider "C:\\Users\\zehassan\\source\\repos\\market-intelligence\\.venv\\Scripts\\podman-compose.EXE". Please see podman-compose(1) for how to disable this message. <<<<

9b05db12a35460fff0587e009f9326e414a53e9484555547f96d214a2ba98ef7
d3238814e371a990ab3d08a8e9b13936953e448590a6648233270a2e3e8fcf75
8ccf5ebc4812295e1cc59ad9ce2fa9f1d8a4d167cbb8198c716f09d5a848cfec
deploy_postgres_1
deploy_neo4j_1
deploy_mailpit_1
postgres schema is current
STEP 1/11: FROM docker.io/library/python:3.13-slim
STEP 2/11: ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     PIP_DISABLE_PIP_VERSION_CHECK=1
--> Using cache 02055724616f03be24addba7bb333da601c15471de95c62afaf1a4dc322f5789
--> 02055724616f
STEP 3/11: WORKDIR /app
--> Using cache 1a146aaa1bf10e52368f0393000c7e0789b0c63ee67105f4fdb9846aab8371c3
--> 1a146aaa1bf1
STEP 4/11: RUN groupadd --system fi-intel     && useradd --system --gid fi-intel --home-dir /app fi-intel
--> Using cache 733646836bf9a34f654034097945b04809cda8ee2e68baac3a0d177263ee2d4c
--> 733646836bf9
STEP 5/11: COPY pyproject.toml README.md /app/
--> 17db8f1e49c3
STEP 6/11: COPY fi_intel /app/fi_intel
--> 434b1bed8b70
STEP 7/11: COPY evals /app/evals
--> 5c2a40368944
STEP 8/11: RUN python -m pip install --no-cache-dir .
Processing ./.
  Installing build dependencies: started
  Installing build dependencies: finished with status 'error'
  error: subprocess-exited-with-error
  
  × installing build dependencies did not run successfully.
  │ exit code: 1
  ╰─> [7 lines of output]
      WARNING: Retrying (Retry(total=4, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NameResolutionError("HTTPSConnection(host='proxy2.cbq.com.qa', port=3128): Failed to resolve 'proxy2.cbq.com.qa' ([Errno -2] Name or service not known)")': /simple/setuptools/
      WARNING: Retrying (Retry(total=3, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NameResolutionError("HTTPSConnection(host='proxy2.cbq.com.qa', port=3128): Failed to resolve 'proxy2.cbq.com.qa' ([Errno -2] Name or service not known)")': /simple/setuptools/
      WARNING: Retrying (Retry(total=2, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NameResolutionError("HTTPSConnection(host='proxy2.cbq.com.qa', port=3128): Failed to resolve 'proxy2.cbq.com.qa' ([Errno -2] Name or service not known)")': /simple/setuptools/
      WARNING: Retrying (Retry(total=1, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NameResolutionError("HTTPSConnection(host='proxy2.cbq.com.qa', port=3128): Failed to resolve 'proxy2.cbq.com.qa' ([Errno -2] Name or service not known)")': /simple/setuptools/
      WARNING: Retrying (Retry(total=0, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NameResolutionError("HTTPSConnection(host='proxy2.cbq.com.qa', port=3128): Failed to resolve 'proxy2.cbq.com.qa' ([Errno -2] Name or service not known)")': /simple/setuptools/
      ERROR: Could not find a version that satisfies the requirement setuptools>=68 (from versions: none)
      ERROR: No matching distribution found for setuptools>=68
      [end of output]
  
  note: This error originates from a subprocess, and is likely not a problem with pip.
ERROR: Failed to build 'file:///app' when installing build dependencies
Error: building at STEP "RUN python -m pip install --no-cache-dir .": while running runtime: exit status 1

podman infrastructure error: Command '('C:\\Users\\zehassan\\AppData\\Local\\Programs\\Podman\\podman.EXE', 'build', '--file', 'C:\\Users\\zehassan\\source\\repos\\market-intelligence\\deploy\\Containerfile', '--tag', 'localhost/fi-intel:dev', '--build-arg', 'HTTP_PROXY=http://proxy2.cbq.com.qa:3128', '--build-arg', 'HTTPS_PROXY=http://proxy2.cbq.com.qa:3128', '--build-arg', 'NO_PROXY=localhost,127.0.0.1,::1,.cbq.com.qa,10.0.0.0/8,host.containers.internal', 'C:\\Users\\zehassan\\source\\repos\\market-intelligence')' returned non-zero exit status 1.
