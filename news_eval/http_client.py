import json
import os
from urllib.error import URLError
from urllib.request import Request, urlopen

HTTP_FAILURES = (OSError, URLError, ValueError, KeyError, TypeError)


def request_json(url, token_env, timeout, payload=None):
    headers = {"Accept": "application/json"}
    token = os.getenv(token_env) if token_env else None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode()
    with urlopen(Request(url, data=body, headers=headers), timeout=timeout) as response:
        return response.status, json.load(response)


def ask_model(config, model, instruction, data):
    _, result = request_json(
        config["base_url"].rstrip("/") + "/chat/completions",
        config.get("token_env"), config.get("timeout", 90),
        {"model": model, "messages": [
            {"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
        ]},
    )
    return json.loads(result["choices"][0]["message"]["content"])
