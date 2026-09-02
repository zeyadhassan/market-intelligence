On the server:
git pull origin main
For the 407, either have the proxy authorize the server or configure authenticated proxy URLs in deploy/app.env:
FI_INTEL_SOURCE_HTTP_PROXY=http://USER:URL_ENCODED_PASSWORD@proxy2.cbq.com.qa:3128
FI_INTEL_SOURCE_HTTPS_PROXY=http://USER:URL_ENCODED_PASSWORD@proxy2.cbq.com.qa:3128
For temporary shadow/UAT testing of the certificate problem:
FI_INTEL_SOURCE_TLS_VERIFY=false
This is intentionally rejected in pilot/production; the permanent solution is installing the corporate CA inside the container.
Then rebuild and force every source to run:
.venv/bin/python deploy/podman_infra.py app-up
.venv/bin/python deploy/podman_infra.py source-check
Finally collect the complete diagnostics:
.venv/bin/python deploy/podman_infra.py logs --no-follow --tail 500
Paste the source-check output if anything remains. The forced check is important because several DNS failures shown on the page were older durable o