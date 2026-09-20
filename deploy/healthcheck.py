import os
import urllib.request
from urllib.parse import urlsplit

request = urllib.request.Request(
    "http://127.0.0.1:8090/readyz",
    headers={"Host": urlsplit(os.environ["SSO_BASE_URL"]).netloc, "X-Forwarded-Proto": "https"},
)
with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=4) as response:
    if response.status != 200:
        raise SystemExit(1)
