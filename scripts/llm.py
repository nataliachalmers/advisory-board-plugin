"""Thin client for a local Ollama model (fully private synthesis).

If Ollama isn't installed/running, callers can catch LLMUnavailable and fall
back to retrieval-only output. Install later with:  brew install ollama  (or
download from ollama.com), then:  ollama pull llama3.1:8b
"""
import json
import os
import urllib.error
import urllib.request

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
# 3B default: this machine has 8GB RAM, so an 8B model swaps/OOMs while
# transcription runs. For higher-quality synthesis when the machine is idle:
#   ./bin/ollama pull llama3.1:8b  &&  export ADVISORY_LLM=llama3.1:8b
DEFAULT_MODEL = os.environ.get("ADVISORY_LLM", "llama3.2:3b")


class LLMUnavailable(RuntimeError):
    pass


def available():
    try:
        urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=2)
        return True
    except Exception:
        return False


def generate(prompt, system=None, model=None, temperature=0.2):
    payload = {
        "model": model or DEFAULT_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if system:
        payload["system"] = system
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL + "/api/generate", data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.load(r)["response"].strip()
    except urllib.error.URLError as e:
        raise LLMUnavailable(
            f"Ollama not reachable at {OLLAMA_URL}. Install/start it and "
            f"`ollama pull {model or DEFAULT_MODEL}`. ({e})")
