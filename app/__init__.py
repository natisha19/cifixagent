import requests  

import flask # intentionally missing dependency
from utils import helper 
def fetch():
    return requests.get("https://example.com").status_code
