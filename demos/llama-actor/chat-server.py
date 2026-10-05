#!/usr/bin/env python3
# Copyright 2026 Ant Weiss
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Stateful chat wrapper calling llama-server (llama.cpp) at /v1/chat/completions.
# MESSAGES is module-level — substrate's gVisor memory snapshot captures it
# across suspend/resume, so the conversation survives.
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json, urllib.request

MESSAGES = [
    {"role": "system", "content": "You are concise. Keep answers to 1-2 sentences."}
]

def llama_chat(messages):
    req = urllib.request.Request(
        "http://127.0.0.1:11434/v1/chat/completions",
        data=json.dumps({
            "model": "qwen",
            "messages": messages,
            "max_tokens": 150,
            "temperature": 0.3,
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive capable, requires Content-Length

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")  # defensive against upstream pooling
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/readyz"):
            self._json(200, {"ready": True, "turns": len(MESSAGES)})
        elif self.path == "/history":
            self._json(200, MESSAGES)
        else:
            body = b'{"error":"not found"}'
            self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

    def do_POST(self):
        if self.path == "/reset":
            del MESSAGES[1:]
            self._json(200, {"turns": len(MESSAGES)}); return
        if self.path != "/chat":
            self._json(404, {"error": "not found"}); return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        user_msg = body.get("message", "").strip()
        if not user_msg:
            self._json(400, {"error": "missing 'message'"}); return
        MESSAGES.append({"role": "user", "content": user_msg})
        try:
            resp = llama_chat(MESSAGES)
        except Exception as e:
            MESSAGES.pop()
            self._json(502, {"error": str(e)}); return
        assistant = resp["choices"][0]["message"]
        MESSAGES.append(assistant)
        self._json(200, {
            "reply": assistant.get("content", ""),
            "turns": len(MESSAGES),
            "tokens": resp.get("usage", {}).get("completion_tokens"),
        })

    def log_message(self, *a): pass

if __name__ == "__main__":
    ThreadingHTTPServer(("", 80), H).serve_forever()
