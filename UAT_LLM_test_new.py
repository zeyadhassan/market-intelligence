import requests
import urllib3

# Suppress warnings because verify=False is used for the internal endpoint.
# Prefer installing the server's CA certificate instead when possible.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

url = "https://cbq2-svd-dsgpu2.cbq.com.qa:8443/v1/chat/completions"

# Explicitly bypass environment-configured proxies.
session = requests.Session()
session.trust_env = False

payload = {
    "model": "openai/gpt-oss-120b",
    "messages": [
        {
            "role": "system",
            "content": "You are a helpful AI assistant."
        },
        {
            "role": "user",
            "content": "Who are you?"
        }
    ],
    "temperature": 0.1,
    "max_tokens": 1000,
    "stream": False
}

headers = {
    "Content-Type": "application/json",
    "Accept": "application/json"
}

response = session.post(
    url,
    json=payload,
    headers=headers,
    timeout=120,
    verify=False
)

response.raise_for_status()

data = response.json()
print(data["choices"][0]["message"]["content"])