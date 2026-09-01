$env:HTTP_PROXY = "http://proxy2.cbq.com.qa:3128"
$env:HTTPS_PROXY = $env:HTTP_PROXY
$env:NO_PROXY = "localhost,127.0.0.1,::1,.cbq.com.qa,10.0.0.0/8,host.containers.internal"

.\run.cmd --no-browser