import requests
 
# url = "http://127.0.0.1:11434/api/generate"
# url = "https://apidev.cbq.com.qa/ollama/api/generate"  # Kong gateway (previous)
url = "https://10.1.94.110:8443/ollama/api/generate"  # Direct Nginx endpoint (DEV)
 
# Explicitly disable proxies
proxies = {
    "http": None,
    "https": None
}
 
 
payload = {
    "model": "llama3:70b",
    "prompt": "Who are you",
    "stream": False,
    "options":  {"temperature": 0.1}
}
 
headers = {
    "Content-Type": "application/json",
    "Authorization":  "Basic b2xsYW1hOk9sbGFtYSMxMjM="
}
 
auth = ("ollama", "Ollama#123")
 
 
response = requests.post(
    url,
    json=payload,
    headers=headers,
    auth=auth,
    proxies=proxies,
    timeout=60,
    verify=False  
)
response.raise_for_status()
 
data = response.json()
print(data["response"])
