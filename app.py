import requests  # intentionally missing

def fetch():
    return requests.get("https://example.com").status_code
