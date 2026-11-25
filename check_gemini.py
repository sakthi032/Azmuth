import os, httpx
from dotenv import load_dotenv
load_dotenv(".env")
key = os.getenv("GEMINI_API_KEY", "")
print("base url:", os.getenv("GEMINI_BASE_URL"))
print("key prefix:", key[:4], "| length:", len(key))
r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models",
              headers={"x-goog-api-key": key}, timeout=20)
print("status:", r.status_code)
print(r.text[:200].replace(key, "***"))