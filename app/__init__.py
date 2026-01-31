import requests  

import flask # intentionally missing dependency
import numpy # intentionally missing dependency

def fetch():
    return requests.get("https://example.com").status_code
