"""One test class per config in configs/, one test per scenario, each on a real Envoy against the
mock providers."""

import json
import os
import subprocess
import time
import unittest

import harness
from harness import sse_events, tokens
from mock_llm import MockProviders

WORKDIR = harness.temp_dir()
CA, CERT, KEY = harness.make_certs(WORKDIR)

QUESTION = [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Say hello."}]
CLAUDE_CODE_REQUEST = {
    "model": "claude-sonnet-4-5",
    "max_tokens": 1024,
    "stream": True,
    "system": [{"type": "text", "text": "You are Claude Code.", "cache_control": {"type": "ephemeral"}}],
    "messages": [{"role": "user", "content": [{"type": "text", "text": "Say hello."}]}],
    "tools": [{"name": "Read", "description": "Reads a file.",
               "input_schema": {"type": "object", "properties": {"path": {"type": "string"}},
                                "required": ["path"]}}],
}
GENAI_REQUEST = {
    "systemInstruction": {"parts": [{"text": "Be brief."}]},
    "contents": [{"role": "user", "parts": [{"text": "Say hello."}]}],
    "generationConfig": {"maxOutputTokens": 256},
}
VERTEX = "/v1/projects/my-project/locations/us-east5/publishers"
GEMINI_API_PATH = "/v1beta/models/gemini-2.5-flash:generateContent"
USAGE = {"OPENAI_CHAT_COMPLETIONS": (11, 7, 18), "OPENAI_RESPONSES": (11, 7, 18),
         "ANTHROPIC_MESSAGES": (12, 6, 18), "GEMINI_GENERATE_CONTENT": (9, 5, 14)}


class ExampleTest(unittest.TestCase):
    config = None

    def setUp(self):
        self.mock = MockProviders(CERT, KEY)
        self.mock.start()
        self.envoy = harness.Envoy(self.config, self.mock, CA, WORKDIR, **self.envoy_args())

    def envoy_args(self):
        return {}

    def tearDown(self):
        try:
            # A response the transcoder cannot convert is forwarded untranslated, so count those too.
            fallbacks = self.envoy.counters(r"transcoder\.(failed|unresolved)$")
            self.assertEqual(fallbacks, {k: 0 for k in fallbacks})
        finally:
            self.envoy.stop()
            self.mock.stop()

    def chat(self, path, model, stream=False, headers=None, **fields):
        body = dict({"model": model, "messages": QUESTION, "stream": stream}, **fields)
        return self.envoy.post(path, body, headers)

    def last_log(self, count):
        return self.envoy.access_logs(count)[count - 1]

    def assert_usage(self, log, api):
        self.assertEqual(log["token_usage"]["llm_protocol"], api)
        self.assertEqual(tokens(log["token_usage"]), USAGE[api])

    def assert_openai_reply(self, status, payload, model, text, stream):
        self.assertEqual(status, 200, payload)
        if not stream:
            reply = json.loads(payload)
            self.assertEqual(reply["object"], "chat.completion")
            self.assertEqual(reply["model"], model)
            self.assertEqual(reply["choices"][0]["message"]["content"], text)
            return
        events = sse_events(payload)
        self.assertEqual(events[-1][1], "[DONE]")
        chunks = [data for _, data in events[:-1]]
        self.assertTrue(all(c["object"] == "chat.completion.chunk" for c in chunks), chunks)
        self.assertEqual("".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"]),
                         text)

    def assert_anthropic_reply(self, status, payload, model, text, stream):
        self.assertEqual(status, 200, payload)
        if not stream:
            reply = json.loads(payload)
            self.assertEqual((reply["type"], reply["model"]), ("message", model))
            self.assertEqual(reply["content"][0]["text"], text)
            return
        events = sse_events(payload)
        names = [name for name, _ in events]
        self.assertEqual((names[0], names[-1]), ("message_start", "message_stop"))
        self.assertEqual(events[0][1]["message"]["model"], model)
        self.assertEqual("".join(d["delta"]["text"] for n, d in events if n == "content_block_delta"), text)

    def assert_gemini_reply(self, status, payload, text, stream):
        self.assertEqual(status, 200, payload)
        chunks = [d for _, d in sse_events(payload)] if stream else [json.loads(payload)]
        self.assertEqual("".join(p["text"] for c in chunks for p in c["candidates"][0]["content"]["parts"]),
                         text)


class ObserveTest(ExampleTest):
    """configs/observe.yaml: native APIs, nothing rewritten."""
    config = "observe.yaml"

    def test_openai_chat(self):
        sent = b'{"model": "gpt-4o-mini",   "messages": [{"role": "user", "content": "Say hello."}]}'
        status, _, payload = self.envoy.post("/v1/chat/completions", sent, {
            "authorization": "Bearer the-client-key", "accept-encoding": "gzip"})
        self.assert_openai_reply(status, payload, "gpt-4o-mini", "Hello from OpenAI.", stream=False)

        upstream = self.mock.only_request()
        self.assertEqual(upstream.host, "api.openai.com")
        self.assertEqual(upstream.headers["authorization"], "Bearer the-client-key")
        self.assertNotIn("accept-encoding", upstream.headers)
        self.assertEqual(upstream.raw, sent)

        log = self.last_log(1)
        self.assertEqual(log["model"], "gpt-4o-mini")
        self.assertEqual(log["request_info"]["message_count"], 1)
        self.assert_usage(log, "OPENAI_CHAT_COMPLETIONS")

    def test_openai_chat_stream_needs_include_usage(self):
        status, _, payload = self.chat("/v1/chat/completions", "gpt-4o-mini", stream=True,
                                       headers={"authorization": "Bearer k"},
                                       stream_options={"include_usage": True})
        self.assert_openai_reply(status, payload, "gpt-4o-mini", "Hello from OpenAI.", stream=True)
        self.chat("/v1/chat/completions", "gpt-4o-mini", stream=True, headers={"authorization": "Bearer k"})

        with_usage, without_usage = self.envoy.access_logs(2)
        self.assert_usage(with_usage, "OPENAI_CHAT_COMPLETIONS")
        self.assertIsNone(without_usage["token_usage"])
        self.assertEqual(self.envoy.counter("ai_protocol_manager.token_usage_missing"), 1)

    def test_openai_responses(self):
        for stream in (False, True):
            status, _, payload = self.envoy.post("/v1/responses", {
                "model": "gpt-4o-mini", "input": "Say hello.", "stream": stream}, {"authorization": "Bearer k"})
            self.assertEqual(status, 200, payload)
        for log in self.envoy.access_logs(2):
            self.assertEqual(log["request_info"]["message_count"], 1)
            self.assert_usage(log, "OPENAI_RESPONSES")
        # No payload schema for the Responses API yet: forwarded unvalidated.
        self.assertEqual(self.envoy.counter("ai_protocol_manager.schema_validation.skipped"), 2)

    def test_anthropic_stream(self):
        status, _, payload = self.envoy.post("/v1/messages", CLAUDE_CODE_REQUEST, {
            "x-api-key": "the-client-key", "anthropic-version": "2023-06-01"})
        self.assert_anthropic_reply(status, payload, "claude-sonnet-4-5", "Hello from Claude.", stream=True)
        self.assertEqual(self.mock.only_request().headers["x-api-key"], "the-client-key")
        log = self.last_log(1)
        self.assertEqual(log["request_info"]["tool_count"], 1)
        self.assert_usage(log, "ANTHROPIC_MESSAGES")

    def test_gemini_model_from_the_path(self):
        status, _, payload = self.envoy.post(
            "/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse", GENAI_REQUEST, {"x-goog-api-key": "k"})
        self.assert_gemini_reply(status, payload, "Hello from Gemini.", stream=True)
        log = self.last_log(1)
        self.assertEqual(log["model"], "gemini-2.5-flash")
        self.assertEqual((log["request_info"]["stream"], log["request_info"]["max_output_tokens"]), (True, "256"))
        self.assert_usage(log, "GEMINI_GENERATE_CONTENT")

    def test_bad_requests_stop_at_envoy(self):
        bad = [("/v1/chat/completions", b'{"model": "gpt-4o-mini",'),
               ("/v1/chat/completions", {"model": "gpt-4o-mini", "messages": "hi"}),
               ("/v1/messages", {"model": "claude-sonnet-4-5", "messages": QUESTION[1:]}),
               (GEMINI_API_PATH, {"contents": "hi"})]
        for path, body in bad:
            status, _, _ = self.envoy.post(path, body)
            self.assertEqual(status, 400, path)
        self.assertEqual(self.mock.requests, [])
        self.assertEqual(self.envoy.counter("ai_protocol_manager.request_parse_error"), 1)
        self.assertEqual(self.envoy.counter("ai_protocol_manager.schema_validation.invalid"), 3)


class GatewayTest(ExampleTest):
    """configs/gateway.yaml: any client API to any backend, one route each."""
    config = "gateway.yaml"

    def test_openai_to_claude(self):
        for stream in (False, True):
            self.mock.requests.clear()
            status, _, payload = self.chat("/claude/v1/chat/completions", "claude-sonnet-4-5", stream,
                                           headers={"authorization": "Bearer the-client-key"})
            self.assert_openai_reply(status, payload, "claude-sonnet-4-5", "Hello from Claude.", stream)
            upstream = self.mock.only_request()
            self.assertEqual((upstream.host, upstream.path), ("api.anthropic.com", "/v1/messages"))
            self.assertEqual(upstream.headers["x-api-key"], "test-anthropic-key")
            self.assertNotIn("authorization", upstream.headers)
            self.assertEqual(upstream.body["system"], "Be brief.")
            self.assertIn("max_tokens", upstream.body)
        for log in self.envoy.access_logs(2):
            self.assertEqual(log["upstream_path"], "/v1/messages")
            self.assert_usage(log, "ANTHROPIC_MESSAGES")

    def test_retry_resends_the_transcoded_body(self):
        self.mock.failures = [503]
        status, _, payload = self.chat("/claude/v1/chat/completions", "claude-sonnet-4-5")
        self.assert_openai_reply(status, payload, "claude-sonnet-4-5", "Hello from Claude.", stream=False)
        first, second = self.mock.requests
        self.assertEqual((first.path, second.path), ("/v1/messages", "/v1/messages"))
        self.assertEqual(first.raw, second.raw)
        log = self.last_log(1)
        self.assertEqual(log["attempts"], 2)
        self.assert_usage(log, "ANTHROPIC_MESSAGES")

    def test_genai_client_to_claude(self):
        for stream, method in ((False, "generateContent"), (True, "streamGenerateContent?alt=sse")):
            self.mock.requests.clear()
            status, _, payload = self.envoy.post(f"/claude/v1beta/models/gemini-2.5-flash:{method}", GENAI_REQUEST)
            self.assert_gemini_reply(status, payload, "Hello from Claude.", stream)
            upstream = self.mock.only_request()
            self.assertEqual(upstream.path, "/v1/messages")
            self.assertEqual(upstream.body["model"], "claude-sonnet-4-5")
            self.assertEqual(upstream.body["max_tokens"], 256)
            self.assertEqual(upstream.body.get("stream", False), stream)

    def test_openai_to_gemini(self):
        paths = {False: GEMINI_API_PATH, True: "/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse"}
        for stream in (False, True):
            self.mock.requests.clear()
            status, _, payload = self.chat("/gemini/v1/chat/completions", "gemini-2.5-flash", stream)
            self.assert_openai_reply(status, payload, "gemini-2.5-flash", "Hello from Gemini.", stream)
            upstream = self.mock.only_request()
            self.assertEqual(upstream.path, paths[stream])
            self.assertEqual(upstream.headers["x-goog-api-key"], "test-gemini-key")
        for log in self.envoy.access_logs(2):
            self.assert_usage(log, "GEMINI_GENERATE_CONTENT")

    def test_claude_code_to_gemini(self):
        status, _, payload = self.envoy.post("/gemini/v1/messages?beta=true", CLAUDE_CODE_REQUEST,
                                             {"x-api-key": "the-client-key"})
        self.assert_anthropic_reply(status, payload, "claude-sonnet-4-5", "Hello from Gemini.", stream=True)
        upstream = self.mock.only_request()
        self.assertEqual(upstream.path, "/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse")
        self.assertNotIn("x-api-key", upstream.headers)
        log = self.last_log(1)
        self.assertEqual((log["client_model"], log["backend_model"]), ("claude-sonnet-4-5", "gemini-2.5-flash"))
        self.assert_usage(log, "GEMINI_GENERATE_CONTENT")

    def test_claude_code_to_openai(self):
        for stream in (False, True):
            self.mock.requests.clear()
            body = dict(CLAUDE_CODE_REQUEST, stream=stream)
            status, _, payload = self.envoy.post("/openai/v1/messages", body)
            self.assert_anthropic_reply(status, payload, "claude-sonnet-4-5", "Hello from OpenAI.", stream)
            upstream = self.mock.only_request()
            self.assertEqual((upstream.host, upstream.path), ("api.openai.com", "/v1/chat/completions"))
            self.assertEqual(upstream.body["model"], "gpt-4o-mini")
            self.assertEqual(upstream.body["tools"][0]["function"]["name"], "Read")

    def test_openai_to_claude_on_vertex(self):
        for stream, method in ((False, "rawPredict"), (True, "streamRawPredict")):
            self.mock.requests.clear()
            status, _, payload = self.chat("/vertex/claude/v1/chat/completions", "claude-sonnet-4-5@20250929", stream)
            self.assert_openai_reply(status, payload, "claude-sonnet-4-5@20250929", "Hello from Claude.", stream)
            upstream = self.mock.only_request()
            self.assertEqual(upstream.host, "us-east5-aiplatform.googleapis.com")
            self.assertEqual(upstream.path, f"{VERTEX}/anthropic/models/claude-sonnet-4-5@20250929:{method}")
            self.assertEqual(upstream.headers["authorization"], "Bearer test-vertex-token")
            self.assertNotIn("model", upstream.body)
            self.assertEqual(upstream.body["anthropic_version"], "vertex-2023-10-16")

    def test_openai_to_gemini_on_vertex(self):
        status, _, payload = self.chat("/vertex/gemini/v1/chat/completions", "gemini-2.5-flash", stream=True)
        self.assert_openai_reply(status, payload, "gemini-2.5-flash", "Hello from Gemini.", stream=True)
        self.assertEqual(self.mock.only_request().path,
                         f"{VERTEX}/google/models/gemini-2.5-flash:streamGenerateContent?alt=sse")

    def test_claude_code_on_vertex_to_gemini_on_vertex(self):
        path = "/v1/projects/acme/locations/us-east5/publishers/anthropic/models/claude-sonnet-4-5@20250929"
        body = dict(CLAUDE_CODE_REQUEST, anthropic_version="vertex-2023-10-16")
        del body["model"]
        status, _, payload = self.envoy.post(f"{path}:streamRawPredict", body)
        self.assert_anthropic_reply(status, payload, "claude-sonnet-4-5@20250929", "Hello from Gemini.", stream=True)
        upstream = self.mock.only_request()
        self.assertEqual(upstream.path, "/v1/projects/acme/locations/us-east5/publishers/google/models/"
                         "gemini-2.5-flash:streamGenerateContent?alt=sse")
        self.assertNotIn("anthropic_version", upstream.body)


class MultiBackendTest(ExampleTest):
    """configs/multi-backend.yaml: split, failover and by-model routing across APIs."""
    config = "multi-backend.yaml"
    claude_path = f"{VERTEX}/anthropic/models/claude-sonnet-4-5@20250929:rawPredict"
    gemini_path = f"{VERTEX}/google/models/gemini-2.5-flash:generateContent"

    def test_split(self):
        texts = set()
        for _ in range(20):
            status, _, payload = self.chat("/split/v1/chat/completions", "any")
            self.assertEqual(status, 200, payload)
            texts.add(json.loads(payload)["choices"][0]["message"]["content"])
        self.assertEqual(texts, {"Hello from Claude.", "Hello from Gemini."})
        self.assertEqual({r.path for r in self.mock.requests}, {self.claude_path, self.gemini_path})
        for log in self.envoy.access_logs(20):
            self.assert_usage(log, log["backend_api"])

    def test_failover_moves_to_another_api(self):
        self.mock.failures = [503]
        status, _, payload = self.chat("/failover/v1/chat/completions", "any", headers={"authorization": "Bearer c"})
        self.assert_openai_reply(status, payload, "any", "Hello from Gemini.", stream=False)
        first, second = self.mock.requests
        self.assertEqual((first.path, second.path), (self.claude_path, self.gemini_path))
        self.assertEqual(first.body["anthropic_version"], "vertex-2023-10-16")
        self.assertIn("contents", second.body)
        self.assertEqual(second.headers["authorization"], "Bearer test-vertex-token")
        log = self.last_log(1)
        self.assertEqual((log["attempts"], log["backend_api"]), (2, "GEMINI_GENERATE_CONTENT"))
        self.assert_usage(log, "GEMINI_GENERATE_CONTENT")

    def test_failover_without_failure_stays_on_claude(self):
        status, _, payload = self.chat("/failover/v1/chat/completions", "any", stream=True)
        self.assert_openai_reply(status, payload, "any", "Hello from Claude.", stream=True)
        self.assertEqual(self.mock.only_request().path, self.claude_path.replace("rawPredict", "streamRawPredict"))

    def test_failover_for_claude_code(self):
        self.mock.failures = [503]
        status, _, payload = self.envoy.post("/failover/v1/messages", CLAUDE_CODE_REQUEST)
        self.assert_anthropic_reply(status, payload, "claude-sonnet-4-5", "Hello from Gemini.", stream=True)
        self.assertEqual(self.mock.requests[1].path,
                         f"{VERTEX}/google/models/gemini-2.5-flash:streamGenerateContent?alt=sse")

    def test_fallback_across_providers_swaps_keys(self):
        self.mock.failures = [503]
        status, _, payload = self.chat("/fallback/v1/chat/completions", "any")
        self.assert_openai_reply(status, payload, "any", "Hello from Gemini.", stream=False)
        first, second = self.mock.requests
        self.assertEqual((first.host, first.path), ("api.anthropic.com", "/v1/messages"))
        self.assertEqual(first.headers["x-api-key"], "test-anthropic-key")
        self.assertEqual((second.host, second.path), ("generativelanguage.googleapis.com", GEMINI_API_PATH))
        self.assertEqual(second.headers["x-goog-api-key"], "test-gemini-key")
        self.assertNotIn("x-api-key", second.headers)
        self.assertNotIn("anthropic-version", second.headers)
        self.assert_usage(self.last_log(1), "GEMINI_GENERATE_CONTENT")

    def test_route_by_model(self):
        for model, path in (("claude-sonnet-4-5", self.claude_path), ("gemini-2.5-flash", self.gemini_path)):
            status, _, payload = self.chat("/v1/chat/completions", model)
            self.assertEqual(status, 200, payload)
            self.assertEqual(self.mock.requests[-1].path, path)
        status, _, payload = self.chat("/v1/chat/completions", "gpt-4o")
        self.assertEqual((status, payload), (404, b"no backend serves this model"))
        self.assertEqual(len(self.mock.requests), 2)


class EgressTest(ExampleTest):
    """configs/egress.yaml: DFP, no per-provider config, usage read on the cluster."""
    config = "egress.yaml"

    def send(self, host, path, body, headers):
        # Host carries the mock's port: the local run cannot listen on 443.
        sent = json.dumps(body).encode()
        status, _, payload = self.envoy.post(path, sent, dict(headers, host=f"{host}:{self.mock.port}",
                                                              **{"accept-encoding": "gzip"}))
        return sent, status, payload

    def test_any_provider(self):
        cases = [
            ("api.openai.com", "/v1/chat/completions", {"model": "gpt-4o-mini", "messages": QUESTION},
             {"authorization": "Bearer k"}, "OPENAI_CHAT_COMPLETIONS"),
            ("api.anthropic.com", "/v1/messages",
             {"model": "claude-sonnet-4-5", "max_tokens": 64, "messages": QUESTION[1:]},
             {"x-api-key": "k", "anthropic-version": "2023-06-01"}, "ANTHROPIC_MESSAGES"),
            ("generativelanguage.googleapis.com", GEMINI_API_PATH, GENAI_REQUEST, {"x-goog-api-key": "k"},
             "GEMINI_GENERATE_CONTENT"),
        ]
        for host, path, body, headers, _ in cases:
            sent, status, payload = self.send(host, path, body, headers)
            self.assertEqual(status, 200, payload)
            upstream = self.mock.requests[-1]
            self.assertEqual((upstream.host, upstream.raw), (f"{host}:{self.mock.port}", sent))
            self.assertNotIn("accept-encoding", upstream.headers)
        for log, (_, _, _, _, api) in zip(self.envoy.access_logs(3), cases):
            self.assertEqual(log["request_info"]["input_llm_protocol"], api)
            self.assert_usage(log, api)
        self.assertEqual(self.envoy.counter("ai_protocol_manager.schema_validation.llm_protocol_detected"), 3)

    def test_detected_api_is_validated(self):
        _, status, _ = self.send("api.anthropic.com", "/v1/messages",
                                 {"model": "claude-sonnet-4-5", "messages": QUESTION[1:]}, {})
        self.assertEqual(status, 400)
        self.assertEqual(self.mock.requests, [])


class PdpTest(ExampleTest):
    """configs/pdp.yaml with pdp/: the routing decision outside Envoy."""
    config = "pdp.yaml"

    def setUp(self):
        self.pdp_port = harness.free_port()
        self.pdp_log = os.path.join(WORKDIR, "pdp.log")
        self.pdp_binary = os.environ.get("PDP", os.path.join(WORKDIR, "pdp"))
        if not os.path.exists(self.pdp_binary):
            subprocess.run(["go", "build", "-o", self.pdp_binary, "."], check=True,
                           cwd=os.path.join(harness.EXAMPLES, "pdp"))
        super().setUp()

    def envoy_args(self):
        # The PDP adds the mock's port to the host it picks, so it starts once the mock has one.
        self.pdp = subprocess.Popen(
            [self.pdp_binary, "-addr", f"127.0.0.1:{self.pdp_port}", "-budget", "10",
             "-host-suffix", f":{self.mock.port}"],
            stdout=open(self.pdp_log, "w"), stderr=subprocess.STDOUT, env=dict(os.environ, **harness.CREDENTIALS))
        time.sleep(0.5)
        return {"pdp_port": self.pdp_port}

    def tearDown(self):
        super().tearDown()
        self.pdp.terminate()
        self.pdp.wait()

    def pdp_events(self, event):
        with open(self.pdp_log) as f:
            return [r for r in (json.loads(line) for line in f if line.startswith("{")) if r["event"] == event]

    def test_routing_and_budget_loop(self):
        headers = {"x-tenant": "acme", "authorization": "Bearer the-client-key"}

        # 1. "smart" with budget left: Claude.
        status, _, payload = self.chat("/v1/chat/completions", "smart", headers=headers)
        self.assert_openai_reply(status, payload, "smart", "Hello from Claude.", stream=False)
        upstream = self.mock.requests[-1]
        self.assertEqual((upstream.host, upstream.path), (f"api.anthropic.com:{self.mock.port}", "/v1/messages"))
        self.assertEqual(upstream.body["model"], "claude-sonnet-4-5")
        self.assertEqual(upstream.headers["x-api-key"], "test-anthropic-key")
        for internal in ("x-ai-api", "x-ai-binding", "x-ai-model", "x-ai-host", "authorization"):
            self.assertNotIn(internal, upstream.headers)

        # 2. The PDP was charged 18 tokens against a budget of 10: "smart" is now Gemini.
        status, _, payload = self.chat("/v1/chat/completions", "smart", stream=True, headers=headers)
        self.assert_openai_reply(status, payload, "smart", "Hello from Gemini.", stream=True)
        self.assertEqual(self.mock.requests[-1].path, "/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse")

        # 3. A client cannot pick the host itself: the PDP overwrites every x-ai-* header.
        status, _, _ = self.chat("/v1/chat/completions", "fast", headers=dict(headers, **{"x-ai-host": "evil.example"}))
        self.assertEqual(status, 200)
        self.assertEqual(self.mock.requests[-1].host, f"generativelanguage.googleapis.com:{self.mock.port}")

        # 4. The PDP refuses a model it does not serve, before any provider is called.
        count = len(self.mock.requests)
        status, _, payload = self.chat("/v1/chat/completions", "gpt-5", headers=headers)
        self.assertEqual(status, 400, payload)
        self.assertEqual(len(self.mock.requests), count)

        decisions = self.pdp_events("decide")
        self.assertEqual([d["backend"] for d in decisions],
                         ["ANTHROPIC_MESSAGES", "GEMINI_GENERATE_CONTENT", "GEMINI_GENERATE_CONTENT", ""])
        self.assertGreater(decisions[0]["estimated_input_tokens"], 0)
        usage = self.pdp_events("usage")
        self.assertEqual([(u["input"], u["output"], u["total"]) for u in usage], [(12, 6, 18), (9, 5, 14), (9, 5, 14)])
        self.assertEqual(usage[-1]["spent"], 46)

        logs = self.envoy.access_logs(4)
        self.assertEqual([log["backend_api"] for log in logs[:3]],
                         ["ANTHROPIC_MESSAGES", "GEMINI_GENERATE_CONTENT", "GEMINI_GENERATE_CONTENT"])
        self.assertEqual([log["client_model"] for log in logs[:3]], ["smart", "smart", "fast"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
