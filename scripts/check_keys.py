import os
from pathlib import Path
from dotenv import load_dotenv

p = Path(".env")
print(".env found:", p.resolve() if p.exists() else "NO (not in this folder)")
load_dotenv(p)
for k in ["GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY", "NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY"]:
    v = os.getenv(k, "")
    print(f"{k:22}", f"set ({len(v)} chars)" if v else "NOT SET")