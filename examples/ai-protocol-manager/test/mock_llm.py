"""Mock LLM providers behind one TLS port, and a DNS server that points every name at it.

The mock answers as OpenAI, Anthropic, Gemini, and Claude and Gemini on Vertex AI do, chosen by the
request path, and refuses what those APIs refuse (a missing credential, a field the API does not
know, a model in the body where the path carries it), so a wrong transcode fails the test. Every
request is recorded for the test to inspect.
"""

import json
import re
import socket
import socketserver
import ssl
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OPENAI_USAGE = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
ANTHROPIC_USAGE = {"input_tokens": 12, "output_tokens": 6}
GEMINI_USAGE = {"promptTokenCount": 9, "candidatesTokenCount": 5, "totalTokenCount": 14}

GEMINI_PATH = re.compile(r"^/v1beta/models/(?P<model>[^/:]+):(?P<method>generateContent|streamGenerateContent)$")
VERTEX_CLAUDE_PATH = re.compile(r"^/v1/projects/(?P<project>[^/]+)/locations/(?P<location>[^/]+)"
                                r"/publishers/anthropic/models/(?P<model>[^/:]+):(?P<method>rawPredict|streamRawPredict)$")
VERTEX_GEMINI_PATH = re.compile(r"^/v1/projects/(?P<project>[^/]+)/locations/(?P<location>[^/]+)"
                                r"/publishers/google/models/(?P<model>[^/:]+):(?P<method>generateContent|streamGenerateContent)$")

ANTHROPIC_FIELDS = {
    "model", "messages", "max_tokens", "system", "stream", "temperature", "top_p", "top_k",
    "stop_sequences", "tools", "tool_choice", "metadata", "thinking", "service_tier"
}
OPENAI_FIELDS = {
    "model", "messages", "stream", "stream_options", "max_tokens", "max_completion_tokens",
    "temperature", "top_p", "n", "stop", "tools", "tool_choice", "parallel_tool_calls",
    "response_format", "seed", "user", "metadata", "logprobs", "top_logprobs", "presence_penalty",
    "frequency_penalty", "logit_bias", "reasoning_effort", "service_tier", "store"
}
GEMINI_FIELDS = {
    "contents", "systemInstruction", "system_instruction", "generationConfig", "generation_config",
    "tools", "toolConfig", "tool_config", "safetySettings", "safety_settings", "cachedContent"
}


class Refused(Exception):

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Recorded:

    def __init__(self, handler, raw):
        self.method = handler.command
        self.path = handler.path
        self.headers = {k.lower(): v for k, v in handler.headers.items()}
        self.host = self.headers.get("host", "")
        self.raw = raw
        try:
            self.body = json.loads(raw) if raw else None
        except ValueError:
            self.body = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length", "0")))
        request = Recorded(self, raw)
        self.server.mock.requests.append(request)
        if self.server.mock.failures:
            code = self.server.mock.failures.pop(0)
            return self.send_json(code, {"error": {"message": "injected failure"}})
        try:
            self.answer(request)
        except Refused as refused:
            self.send_json(refused.code, {"error": {"message": str(refused)}})

    def answer(self, r):
        path, _, query = r.path.partition("?")
        if not isinstance(r.body, dict):
            raise Refused(400, "body is not a JSON object")
        if path == "/v1/chat/completions":
            self.require(r, "authorization", "Bearer ")
            unknown = set(r.body) - OPENAI_FIELDS
            if unknown:
                raise Refused(400, f"Unrecognized request arguments supplied: {sorted(unknown)}")
            return self.openai(r)
        if path == "/v1/responses":
            self.require(r, "authorization", "Bearer ")
            return self.openai_responses(r)
        if path == "/v1/messages":
            self.require(r, "x-api-key", "")
            self.require(r, "anthropic-version", "2023-06-01")
            self.check_anthropic(r.body, model_in_body=True)
            return self.anthropic(r.body["model"], bool(r.body.get("stream")))
        if m := GEMINI_PATH.match(path):
            self.require(r, "x-goog-api-key", "")
            return self.gemini(r, m, query)
        if m := VERTEX_CLAUDE_PATH.match(path):
            self.require(r, "authorization", "Bearer ")
            self.check_anthropic(r.body, model_in_body=False)
            if r.body.get("anthropic_version") != "vertex-2023-10-16":
                raise Refused(400, "anthropic_version must be vertex-2023-10-16")
            stream = m["method"] == "streamRawPredict"
            if bool(r.body.get("stream")) != stream:
                raise Refused(400, "stream does not match the method")
            return self.anthropic(m["model"], stream)
        if m := VERTEX_GEMINI_PATH.match(path):
            self.require(r, "authorization", "Bearer ")
            return self.gemini(r, m, query)
        raise Refused(404, f"no such method: {r.path}")

    def require(self, r, header, prefix):
        if not r.headers.get(header, "").startswith(prefix) or not r.headers.get(header):
            raise Refused(401, f"missing or wrong {header}")

    def check_anthropic(self, body, model_in_body):
        unknown = set(body) - ANTHROPIC_FIELDS - ({"anthropic_version"} if not model_in_body else set())
        if unknown:
            raise Refused(400, f"{sorted(unknown)}: Extra inputs are not permitted")
        if model_in_body != ("model" in body):
            raise Refused(400, "model must be in the body" if model_in_body else "model is in the path")
        for required in ("messages", "max_tokens"):
            if required not in body:
                raise Refused(400, f"{required}: Field required")

    def openai(self, r):
        model = r.body.get("model", "")
        text = ["Hello", " from OpenAI."]
        if not r.body.get("stream"):
            return self.send_json(200, {
                "id": "chatcmpl-1", "object": "chat.completion", "created": 1761400000, "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "".join(text)},
                             "finish_reason": "stop"}],
                "usage": OPENAI_USAGE})
        events = []
        for i, part in enumerate(text):
            delta = {"role": "assistant", "content": part} if i == 0 else {"content": part}
            events.append({"id": "chatcmpl-1", "object": "chat.completion.chunk", "model": model,
                           "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
        events.append({"id": "chatcmpl-1", "object": "chat.completion.chunk", "model": model,
                       "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        if (r.body.get("stream_options") or {}).get("include_usage"):
            events.append({"id": "chatcmpl-1", "object": "chat.completion.chunk", "model": model,
                           "choices": [], "usage": OPENAI_USAGE})
        self.send_sse([(None, e) for e in events] + [(None, "[DONE]")])

    def openai_responses(self, r):
        model = r.body.get("model", "")
        usage = {"input_tokens": OPENAI_USAGE["prompt_tokens"], "output_tokens": OPENAI_USAGE["completion_tokens"],
                 "total_tokens": OPENAI_USAGE["total_tokens"]}
        response = {"id": "resp_1", "object": "response", "model": model, "status": "completed",
                    "output": [{"type": "message", "role": "assistant",
                                "content": [{"type": "output_text", "text": "Hello from OpenAI."}]}],
                    "usage": usage}
        if not r.body.get("stream"):
            return self.send_json(200, response)
        self.send_sse([("response.created", {"type": "response.created",
                                              "response": dict(response, status="in_progress", output=[], usage=None)}),
                       ("response.output_text.delta", {"type": "response.output_text.delta", "delta": "Hello"}),
                       ("response.output_text.delta", {"type": "response.output_text.delta", "delta": " from OpenAI."}),
                       ("response.completed", {"type": "response.completed", "response": response})])

    def anthropic(self, model, stream):
        text = ["Hello", " from Claude."]
        message = {"id": "msg_01", "type": "message", "role": "assistant", "model": model,
                   "stop_sequence": None}
        if not stream:
            return self.send_json(200, dict(message, content=[{"type": "text", "text": "".join(text)}],
                                            stop_reason="end_turn", usage=ANTHROPIC_USAGE))
        events = [("message_start", {"type": "message_start", "message": dict(
            message, content=[], stop_reason=None,
            usage={"input_tokens": ANTHROPIC_USAGE["input_tokens"], "output_tokens": 1})}),
                  ("content_block_start", {"type": "content_block_start", "index": 0,
                                           "content_block": {"type": "text", "text": ""}}),
                  ("ping", {"type": "ping"})]
        for part in text:
            events.append(("content_block_delta", {"type": "content_block_delta", "index": 0,
                                                   "delta": {"type": "text_delta", "text": part}}))
        events += [("content_block_stop", {"type": "content_block_stop", "index": 0}),
                   ("message_delta", {"type": "message_delta",
                                      "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                      "usage": {"output_tokens": ANTHROPIC_USAGE["output_tokens"]}}),
                   ("message_stop", {"type": "message_stop"})]
        self.send_sse(events)

    def gemini(self, r, m, query):
        unknown = set(r.body) - GEMINI_FIELDS
        if unknown:
            raise Refused(400, f"Invalid JSON payload received. Unknown name {sorted(unknown)}")
        if "contents" not in r.body:
            raise Refused(400, "contents is required")
        model = m["model"]
        text = ["Hello", " from Gemini."]
        if m["method"] == "generateContent":
            return self.send_json(200, {
                "candidates": [{"content": {"role": "model", "parts": [{"text": "".join(text)}]},
                                "finishReason": "STOP"}],
                "usageMetadata": GEMINI_USAGE, "modelVersion": model, "responseId": "resp-1"})
        if query != "alt=sse":
            raise Refused(400, "these tests stream Gemini as SSE")
        chunks = [{"candidates": [{"content": {"role": "model", "parts": [{"text": text[0]}]}}],
                   "modelVersion": model, "responseId": "resp-1"},
                  {"candidates": [{"content": {"role": "model", "parts": [{"text": text[1]}]},
                                   "finishReason": "STOP"}],
                   "usageMetadata": GEMINI_USAGE, "modelVersion": model, "responseId": "resp-1"}]
        self.send_sse([(None, c) for c in chunks])

    def send_json(self, code, doc):
        body = json.dumps(doc).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_sse(self, events):
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        for name, data in events:
            frame = (f"event: {name}\n" if name else "") + "data: " + (
                data if isinstance(data, str) else json.dumps(data)) + "\n\n"
            chunk = frame.encode()
            self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")


class DnsHandler(socketserver.BaseRequestHandler):
    """Answers every A query with 127.0.0.1."""

    def handle(self):
        query, sock = self.request
        end = 12
        while query[end]:
            end += 1 + query[end]
        qtype = struct.unpack("!H", query[end + 1:end + 3])[0]
        answer = b""
        if qtype == 1:
            answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 30, 4) + socket.inet_aton("127.0.0.1")
        header = query[:2] + struct.pack("!HHHHH", 0x8180, 1, 1 if answer else 0, 0, 0)
        sock.sendto(header + query[12:end + 5] + answer, self.client_address)


class MockProviders:

    def __init__(self, cert, key):
        self.requests = []
        # Status codes to answer the next requests with, before answering normally.
        self.failures = []
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http.mock = self
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        context.set_alpn_protocols(["http/1.1"])
        self.http.socket = context.wrap_socket(self.http.socket, server_side=True)
        self.dns = socketserver.ThreadingUDPServer(("127.0.0.1", 0), DnsHandler)
        self.port = self.http.server_address[1]
        self.dns_port = self.dns.server_address[1]

    def start(self):
        for server in (self.http, self.dns):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def stop(self):
        for server in (self.http, self.dns):
            server.shutdown()
            server.server_close()

    def only_request(self):
        assert len(self.requests) == 1, f"{len(self.requests)} upstream requests"
        return self.requests[0]
