---
title: "Envoy AI Protocol Manager by Example"
date: 2026-10-26T12:00:00+08:00
draft: false
tags: ["envoy", "ai-gateway", "llm", "protocols"]
summary: "How Envoy's AI Protocol Manager handles LLM traffic today, and a set of tested configs: observing native APIs, translating between OpenAI, Claude and Gemini (Vertex AI included), retries and failover across APIs, a forward-proxy egress, and model routing with ext_proc."
---

The AI Protocol Manager (AIPM) is Envoy's filter for LLM API traffic. It parses the request body, runs small AI filters over it, can turn one vendor's API into another's, and reads token usage off the response. It is alpha and moving fast, so this page pins down how it works on Envoy main today (`5d3b0dcaf4`, October 26, 2026) and collects configs for the setups I keep getting asked about.

Every config here runs in an integration test: a real Envoy, mock OpenAI, Anthropic, Gemini and Vertex AI backends that refuse what the real APIs refuse, and an ext_proc server for the routing example. The [configs and tests](https://github.com/botengyao/botengyao.github.io/tree/main/examples/ai-protocol-manager) live next to this blog. The snippets below keep only the part that matters; the linked files are complete.

## How it handles traffic

<figure>
<svg width="100%" viewBox="0 0 720 330" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>How a request moves through the AI Protocol Manager</title>
<desc>A client request reaches Envoy. On a route declared as an AI endpoint, the AI Protocol Manager holds the headers, parses the body, and runs AI filters in order: schema validation, request info, and two transcoders that convert the client's API into OpenAI Chat and then into the backend's API. The body is serialized and passed to the router, which sends it to the provider. On the way back, token usage is read from the provider's bytes, and the transcoders convert the response back to the client's API.</desc>
<defs>
<marker id="aipmA" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#57606a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker>
</defs>
<rect x="1" y="1" width="718" height="328" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<rect x="16" y="112" width="88" height="56" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="60" y="137" font-family="system-ui,sans-serif" font-size="12.5" font-weight="600" fill="#1f2328" text-anchor="middle">Client</text>
<text x="60" y="154" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">any LLM SDK</text>
<rect x="120" y="14" width="480" height="302" rx="10" fill="#ffffff" stroke="#0F6E56" stroke-width="1.5"/>
<text x="136" y="36" font-family="system-ui,sans-serif" font-size="13" font-weight="600" fill="#085041">Envoy</text>
<rect x="136" y="48" width="320" height="196" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="150" y="70" font-family="system-ui,sans-serif" font-size="12.5" font-weight="600" fill="#085041">AI Protocol Manager</text>
<text x="150" y="86" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a">only on routes that declare request:</text>
<text x="150" y="110" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328">1  hold headers, parse the body (bad JSON → 400)</text>
<text x="150" y="130" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328">2  schema_validation</text>
<text x="150" y="150" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328">3  request_info → envoy.ai.request_info</text>
<text x="150" y="170" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328">4  transcoder: client API → OpenAI Chat</text>
<text x="150" y="190" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328">5  transcoder: OpenAI Chat → backend API</text>
<text x="150" y="210" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328">6  serialize, release the headers</text>
<text x="150" y="232" font-family="system-ui,sans-serif" font-size="10.5" font-style="italic" fill="#57606a">use only the AI filters you need</text>
<rect x="472" y="112" width="112" height="56" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="528" y="137" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328" text-anchor="middle">ext_proc, …</text>
<text x="528" y="154" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328" text-anchor="middle">router</text>
<rect x="136" y="260" width="448" height="44" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="150" y="278" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328">response: token usage read from the provider's bytes → envoy.ai.token_usage</text>
<text x="150" y="295" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328">the transcoders convert the response (JSON or SSE) back to the client's API</text>
<rect x="616" y="112" width="88" height="56" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="660" y="137" font-family="system-ui,sans-serif" font-size="12.5" font-weight="600" fill="#1f2328" text-anchor="middle">Provider</text>
<text x="660" y="154" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">backend API</text>
<line x1="104" y1="132" x2="132" y2="132" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmA)"/>
<line x1="456" y1="132" x2="468" y2="132" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmA)"/>
<line x1="584" y1="132" x2="612" y2="132" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmA)"/>
<polyline points="660,168 660,282 588,282" fill="none" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmA)"/>
<polyline points="136,282 60,282 60,172" fill="none" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmA)"/>
</svg>
<figcaption>A declared AI endpoint has its body parsed once and handed to the AI filters. The router and everything after it see a normal request in the backend's API.</figcaption>
</figure>

**A route opts in.** Per-route config with `request:` makes a route an AI endpoint and names the API its clients speak. Every other route passes straight through: no buffering, no parsing.

**The body is parsed before the headers move on.** On an AI endpoint, AIPM holds the headers, offloads the body as it arrives, and parses it as JSON. Bad JSON gets a 400 right there. Then the AI filters run, in order, on the parsed document:

- `schema_validation` checks the body against its API's schema.
- `request_info` publishes the model, the streaming flag, the token cap and the message and tool counts as typed metadata (`envoy.ai.request_info`), and the model as filter state (`envoy.ai.model.request`). With `token_estimation` it also estimates input tokens from the body size.
- `transcoder` converts between APIs. Everything goes through one common form, OpenAI Chat Completions, so you run two instances: the first turns the client's API into the common form, the second turns that into the backend's API. Filters between them only ever see OpenAI Chat.

Then the document is written back into the body and the headers continue down the chain. With `reserialize_body: DISABLE`, the original bytes go out instead, which is what you want when the filters only read.

**The response is handled twice.** Token usage is read from the provider's bytes, in the backend's API, and published when the stream ends as typed metadata (`envoy.ai.token_usage`). The counts mean the same thing for every API: input includes cached tokens, output includes reasoning tokens. Separately, the transcoders convert the response, JSON or SSE, back into the client's API.

**The route decides the backend, and filter state can override it per request.** The route's `response:` names the backend's API. A filter that runs earlier can override it, and more, for a single request:

- `envoy.ai.llm_protocol.request`: the client's API.
- `envoy.ai.llm_protocol.response`: the backend's API.
- `envoy.ai.binding.response`: how the backend carries its API on HTTP. It is either a path pattern such as `/v1beta/models/{model}:{generateContent|streamGenerateContent?alt=sse}`, or a JSON binding that also edits the body.
- `envoy.ai.binding.request`: the same for the client's side.
- `envoy.ai.model.resolved`: the model to send instead of the one the client asked for.

Anything that writes filter state can set these: `set_filter_state`, Lua, or ext_proc through headers.

**It runs on the listener or on the cluster.** In `http_filters` it runs once per request. In a cluster's `http_filters`, or the router's `upstream_http_filters`, it runs once per attempt, after Envoy picks the cluster and host, and still sees the request's filter state.

**Reading the output.** AIPM publishes typed metadata only. An access log reads it with `%TYPED_CEL(metadata.typed_filter_metadata['envoy.ai.token_usage'])%`, and ext_proc receives it with `metadata_options.forwarding_namespaces.typed`.

## The examples

- [Observe native APIs](#observe-native-apis): validate, describe and count tokens, without changing a byte.
- [Any client API, any backend](#any-client-api-any-backend): OpenAI SDK to Claude or Gemini, Claude Code to Gemini or OpenAI, Gemini SDK to Claude, and Vertex AI.
- [Retries](#retries)
- [Several backends](#several-backends-split-failover-by-model): split traffic, fail over to another API with a composite cluster or a cluster specifier, and route by model.
- [Egress through a forward proxy](#egress-through-a-forward-proxy): any provider, no per-provider config.
- [Routing decisions outside Envoy](#routing-decisions-outside-envoy): ext_proc picks the backend and keeps a token budget.
- [Gaps on main](#gaps-on-main): what didn't work and why.

## Observe native APIs

Clients keep their SDKs and their own keys. Envoy validates and describes each request, counts tokens on each response, and forwards everything as is. Full config: [observe.yaml](https://github.com/botengyao/botengyao.github.io/blob/main/examples/ai-protocol-manager/configs/observe.yaml).

```yaml
virtual_hosts:
- name: providers
  domains: ["*"]
  request_headers_to_remove: [accept-encoding]
  routes:
  - match: {path: /v1/messages}
    route: {cluster: anthropic, auto_host_rewrite: true}
    typed_per_filter_config:
      envoy.filters.http.ai_protocol_manager:
        "@type": type.googleapis.com/envoy.extensions.filters.http.ai_protocol_manager.v3.AiProtocolManagerPerRoute
        request: {llm_protocol: ANTHROPIC_MESSAGES}
  # The same for /v1/chat/completions, /v1/responses and /v1beta/models/.
http_filters:
- name: envoy.filters.http.ai_protocol_manager
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.ai_protocol_manager.v3.AiProtocolManager
    request_handling: {reserialize_body: DISABLE}
    response_handling: {token_usage: {}}
    filters:
    - name: envoy.http.ai_filters.schema_validation
      typed_config:
        "@type": type.googleapis.com/envoy.extensions.http.ai_filters.schema_validation.v3.SchemaValidation
    - name: envoy.http.ai_filters.request_info
      typed_config:
        "@type": type.googleapis.com/envoy.extensions.http.ai_filters.request_info.v3.RequestInfo
- name: envoy.filters.http.router
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.router.v3.Router
```

Why each part is there:

- **`request: {llm_protocol: ...}`** makes the route an AI endpoint, and picks the schema to validate against and the usage format to read.
- **`reserialize_body: DISABLE`**: these filters only read, so the provider gets the client's exact bytes.
- **`token_usage: {}`** publishes one usage record per response.
- **`request_headers_to_remove: [accept-encoding]`**: usage can't be read from a gzip body. Envoy then asks the provider for plain bytes.

A request through it logs:

```json
{"model":"gpt-4o-mini","status":200,
 "request_info":{"input_llm_protocol":"OPENAI_CHAT_COMPLETIONS","message_count":1,"model":"gpt-4o-mini"},
 "token_usage":{"extraction_status":"COMPLETE","llm_protocol":"OPENAI_CHAT_COMPLETIONS","model":"gpt-4o-mini",
                "input_tokens":"11","output_tokens":"7","total_tokens":"18","provider_total_tokens":"18"}}
```

Things the tests ran into:

- OpenAI streams carry usage only when the client sets `stream_options.include_usage: true`. Without it there is no record, and `token_usage_missing` counts it.
- The Responses API has usage and request info, but no schema yet, so validation skips it.
- Gemini names the model in the path, and request info reads it from there.
- Malformed JSON, a body that breaks its schema, and an Anthropic request without `max_tokens` all get a 400 without reaching the provider.

## Any client API, any backend

One filter chain serves every route. The base URL picks the backend and the path the client's API, so each pairing is one route. Full config: [gateway.yaml](https://github.com/botengyao/botengyao.github.io/blob/main/examples/ai-protocol-manager/configs/gateway.yaml).

```yaml
http_filters:
- name: envoy.filters.http.set_filter_state     # per-route model overrides
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.set_filter_state.v3.Config
- name: envoy.filters.http.ai_protocol_manager
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.ai_protocol_manager.v3.AiProtocolManager
    request_handling: {}
    response_handling: {token_usage: {}}
    filters:
    - name: envoy.http.ai_filters.request_info
      typed_config:
        "@type": type.googleapis.com/envoy.extensions.http.ai_filters.request_info.v3.RequestInfo
    - name: envoy.http.ai_filters.transcoder      # client's API <-> OpenAI Chat
      typed_config:
        "@type": type.googleapis.com/envoy.extensions.http.ai_filters.transcoder.v3.Transcoder
        request_handling: TO_IR
        response_handling: FROM_IR
    - name: envoy.http.ai_filters.transcoder      # OpenAI Chat <-> backend's API
      typed_config:
        "@type": type.googleapis.com/envoy.extensions.http.ai_filters.transcoder.v3.Transcoder
        request_handling: FROM_IR
        response_handling: TO_IR
- name: envoy.filters.http.router
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.router.v3.Router
```

Credentials belong to the gateway. The virtual host strips whatever the client sent (`authorization`, `x-api-key`, `x-goog-api-key`), and each route adds the backend's key. `most_specific_header_mutations_wins: true` on the route config makes the route's additions run after the virtual host's removals. The other order would strip the key the route just added.

### OpenAI SDK to Claude

`base_url="http://localhost:10000/claude/v1"`:

```yaml
- match: {path: /claude/v1/chat/completions}
  route: {cluster: anthropic, auto_host_rewrite: true}
  request_headers_to_add:
  - header: {key: x-api-key, value: "%ENVIRONMENT(ANTHROPIC_API_KEY)%"}
  - header: {key: anthropic-version, value: "2023-06-01"}
  typed_per_filter_config:
    envoy.filters.http.ai_protocol_manager:
      "@type": type.googleapis.com/envoy.extensions.filters.http.ai_protocol_manager.v3.AiProtocolManagerPerRoute
      request: {llm_protocol: OPENAI_CHAT_COMPLETIONS}
      response: {llm_protocol: ANTHROPIC_MESSAGES, binding: {uri_pattern: /v1/messages}}
```

- **`response.llm_protocol`** tells the second transcoder to write Anthropic Messages, and tells token usage to read Anthropic's format.
- **`binding: {uri_pattern: /v1/messages}`**: the transcoder rewrites the body but leaves the path alone unless a binding names one. Without it, Claude gets a Messages body at `/claude/v1/chat/completions` and answers 404. Only Gemini has a built-in path, because its path carries the model.

### OpenAI SDK to Gemini

```yaml
response: {llm_protocol: GEMINI_GENERATE_CONTENT}
```

This needs no binding: Gemini's built-in one renders `/v1beta/models/gemini-2.5-flash:generateContent`, or `:streamGenerateContent?alt=sse` for `stream: true`, and removes `model` and `stream` from the body. Its key goes in `x-goog-api-key`.

### Claude Code to Gemini, or to any OpenAI-compatible backend

`ANTHROPIC_BASE_URL=http://localhost:10000/gemini`:

```yaml
- match: {path: /gemini/v1/messages}
  route: {cluster: gemini, auto_host_rewrite: true}
  request_headers_to_add:
  - header: {key: x-goog-api-key, value: "%ENVIRONMENT(GEMINI_API_KEY)%"}
  typed_per_filter_config:
    envoy.filters.http.set_filter_state:
      "@type": type.googleapis.com/envoy.extensions.filters.http.set_filter_state.v3.Config
      on_request_headers:
      - object_key: envoy.ai.model.resolved
        format_string: {text_format_source: {inline_string: gemini-2.5-flash}}
    envoy.filters.http.ai_protocol_manager:
      "@type": type.googleapis.com/envoy.extensions.filters.http.ai_protocol_manager.v3.AiProtocolManagerPerRoute
      request: {llm_protocol: ANTHROPIC_MESSAGES}
      response: {llm_protocol: GEMINI_GENERATE_CONTENT}
```

- **`envoy.ai.model.resolved`**: Claude Code asks for Claude models. This replaces the model wherever the backend carries it (here the path), and the response still names the model the client asked for.

For an OpenAI-compatible backend (OpenAI, vLLM, …), the route at `/openai/v1/messages` sends `gpt-4o-mini` with `response: {llm_protocol: OPENAI_CHAT_COMPLETIONS, binding: {uri_pattern: /v1/chat/completions}}`. Claude Code's tools come out as OpenAI functions, and its SSE comes back as Anthropic events.

### Gemini SDK to Claude

`http_options={"base_url": "http://localhost:10000/claude"}`, so the path is `/claude/v1beta/models/gemini-2.5-flash:generateContent`:

```yaml
request:
  llm_protocol: GEMINI_GENERATE_CONTENT
  binding: {uri_pattern: "/claude/v1beta/models/{model}:{generateContent|streamGenerateContent?alt=sse}"}
response: {llm_protocol: ANTHROPIC_MESSAGES, binding: {uri_pattern: /v1/messages}}
```

- **`request.binding`**: Gemini puts the model and the streaming mode in the path. Envoy knows Gemini's own path and Vertex AI's, but not your prefix, so this pattern says where `{model}` sits. `{generateContent|streamGenerateContent?alt=sse}` reads `stream`. `maxOutputTokens` becomes `max_tokens`.

### Vertex AI

OpenAI clients to Claude on Vertex AI:

```yaml
- match: {path: /vertex/claude/v1/chat/completions}
  route: {cluster: vertex, auto_host_rewrite: true}
  request_headers_to_add:
  - header: {key: authorization, value: "Bearer %ENVIRONMENT(VERTEX_ACCESS_TOKEN)%"}
  typed_per_filter_config:
    envoy.filters.http.ai_protocol_manager:
      "@type": type.googleapis.com/envoy.extensions.filters.http.ai_protocol_manager.v3.AiProtocolManagerPerRoute
      request: {llm_protocol: OPENAI_CHAT_COMPLETIONS}
      response:
        llm_protocol: ANTHROPIC_MESSAGES
        binding:
          uri_pattern: "/v1/projects/my-project/locations/us-east5/publishers/anthropic/models/{model}:{rawPredict|streamRawPredict}"
          mutations:
          - {body_field: model}
          - {body_field: anthropic_version, value: vertex-2023-10-16, keep_existing: true}
```

- Claude on Vertex AI speaks Anthropic Messages, but it takes the model in the path and needs `anthropic_version` in the body. The two **`mutations`** remove `model` and add the version. The method follows `stream`.
- Gemini on Vertex AI needs only the pattern (`.../publishers/google/models/{model}:{generateContent|streamGenerateContent?alt=sse}`). Gemini's own edits still apply.
- The token comes from `gcloud auth print-access-token` here. In production, let the gcp_authn filter fetch it.

Claude Code on Vertex AI (`CLAUDE_CODE_USE_VERTEX=1`, `ANTHROPIC_VERTEX_BASE_URL=http://localhost:10000/v1`) to Gemini, in the project and region the client named:

```yaml
- match: {prefix: /v1/projects/}
  route: {cluster: vertex, auto_host_rewrite: true}
  typed_per_filter_config:
    envoy.filters.http.set_filter_state:     # model.resolved: gemini-2.5-flash
      ...
    envoy.filters.http.ai_protocol_manager:
      "@type": type.googleapis.com/envoy.extensions.filters.http.ai_protocol_manager.v3.AiProtocolManagerPerRoute
      request: {llm_protocol: ANTHROPIC_MESSAGES}
      response:
        llm_protocol: GEMINI_GENERATE_CONTENT
        binding: {uri_pattern: "/{version}/projects/{project}/locations/{location}/publishers/google/models/{model}:{generateContent|streamGenerateContent?alt=sse}"}
```

- **No request binding is needed**: Envoy knows `rawPredict` and `streamRawPredict`. It reads the model, the streaming mode, `{version}`, `{project}` and `{location}` from the client's path, and takes `anthropic_version` out of the body.
- **The backend pattern** renders `{model}` from the body (after `model.resolved`), and every other variable from what it read. Access logs can show those values with `%FILTER_STATE(envoy.ai.binding.read:FIELD:project)%`.

I left schema validation out of this chain. It doesn't read bindings yet, so it rejects a Vertex AI body whose model is in the path. The second transcoder validates against the backend's schema before sending anyway.

## Retries

```yaml
route:
  cluster: anthropic
  retry_policy: {retry_on: 5xx, num_retries: 1}
```

With AIPM on the listener, transcoding happens once, before the router. The router buffers the transcoded body and sends the same bytes on the retry, and token usage comes from the attempt that answered. There is nothing AI-specific to configure. Retrying on a backend with a different API needs transcoding on each attempt, which is the next section.

## Several backends: split, failover, by model

Full config: [multi-backend.yaml](https://github.com/botengyao/botengyao.github.io/blob/main/examples/ai-protocol-manager/configs/multi-backend.yaml).

<figure>
<svg width="100%" viewBox="0 0 720 260" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>Transcoding split between the listener and each attempt</title>
<desc>The AI Protocol Manager on the listener converts the client's API to OpenAI Chat once. The router picks a cluster per attempt: a weighted split, a composite cluster, or a priority group. On each attempt, an AI Protocol Manager on the upstream side converts OpenAI Chat into the API of the backend it reached. In the example, attempt 1 goes to Claude on Vertex AI and fails with 503, and attempt 2 goes to Gemini on Vertex AI and succeeds.</desc>
<defs>
<marker id="aipmB" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#57606a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker>
</defs>
<rect x="1" y="1" width="718" height="258" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<rect x="16" y="100" width="88" height="56" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="60" y="125" font-family="system-ui,sans-serif" font-size="12.5" font-weight="600" fill="#1f2328" text-anchor="middle">Client</text>
<text x="60" y="142" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">any API</text>
<rect x="124" y="88" width="164" height="80" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="206" y="112" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#085041" text-anchor="middle">AIPM on the listener</text>
<text x="206" y="132" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328" text-anchor="middle">client API → OpenAI Chat</text>
<text x="206" y="152" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">once per request</text>
<rect x="308" y="88" width="124" height="80" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="370" y="112" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328" text-anchor="middle">router</text>
<text x="370" y="132" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">split, composite,</text>
<text x="370" y="148" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">priority group, retry</text>
<rect x="460" y="20" width="244" height="92" rx="6" fill="#ffffff" stroke="#d0d7de"/>
<text x="474" y="42" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328">attempt 1 → vertex_claude</text>
<text x="474" y="62" font-family="system-ui,sans-serif" font-size="11" fill="#085041">AIPM: OpenAI Chat → Claude</text>
<text x="474" y="80" font-family="ui-monospace,monospace" font-size="10" fill="#57606a">.../anthropic/models/…:rawPredict</text>
<text x="474" y="100" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#B22B2B">✗ 503</text>
<rect x="460" y="144" width="244" height="92" rx="6" fill="#ffffff" stroke="#0F6E56"/>
<text x="474" y="166" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328">attempt 2 → vertex_gemini</text>
<text x="474" y="186" font-family="system-ui,sans-serif" font-size="11" fill="#085041">AIPM: OpenAI Chat → Gemini</text>
<text x="474" y="204" font-family="ui-monospace,monospace" font-size="10" fill="#57606a">.../google/models/…:generateContent</text>
<text x="474" y="224" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#0F6E56">✓ 200, back to the client's API</text>
<line x1="104" y1="128" x2="120" y2="128" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmB)"/>
<line x1="288" y1="128" x2="304" y2="128" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmB)"/>
<polyline points="432,118 446,118 446,66 456,66" fill="none" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmB)"/>
<polyline points="432,138 446,138 446,190 456,190" fill="none" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmB)"/>
</svg>
<figcaption>Transcode the client's side once, and the backend's side on every attempt. A retry that lands on another API is then converted for that API.</figcaption>
</figure>

Split the transcoding in two. The AIPM on the listener converts the client's API into OpenAI Chat. A second AIPM runs on each attempt, after Envoy has picked the cluster and host, and converts OpenAI Chat into whatever that backend speaks. Each endpoint says what it speaks in its metadata:

```yaml
- endpoint: {address: {socket_address: {address: us-east5-aiplatform.googleapis.com, port_value: 443}}}
  metadata:
    filter_metadata:
      envoy.ai:
        api: GEMINI_GENERATE_CONTENT
        model: gemini-2.5-flash
        binding: "/v1/projects/my-project/locations/us-east5/publishers/google/models/{model}:{generateContent|streamGenerateContent?alt=sse}"
```

The router's upstream filters turn that metadata into the AIPM's instructions on every attempt:

```yaml
upstream_http_filters:          # every cluster without filters of its own
- name: envoy.filters.http.header_mutation
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.header_mutation.v3.HeaderMutation
    mutations:
      request_mutations:
      - append: {header: {key: x-ai-api, value: "%UPSTREAM_METADATA(envoy.ai:api)%"}, append_action: OVERWRITE_IF_EXISTS_OR_ADD}
      - append: {header: {key: x-ai-binding, value: "%UPSTREAM_METADATA(envoy.ai:binding)%"}, append_action: OVERWRITE_IF_EXISTS_OR_ADD}
      - append: {header: {key: x-ai-model, value: "%UPSTREAM_METADATA(envoy.ai:model)%"}, append_action: OVERWRITE_IF_EXISTS_OR_ADD}
- name: envoy.filters.http.lua
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.lua.v3.Lua
    default_source_code:
      inline_string: |
        function envoy_on_request(handle)
          local state = handle:streamInfo():filterState()
          for header, key in pairs({["x-ai-api"] = "envoy.ai.llm_protocol.response",
                                    ["x-ai-binding"] = "envoy.ai.binding.response",
                                    ["x-ai-model"] = "envoy.ai.model.resolved"}) do
            state:set(key, key, handle:headers():get(header))
            handle:headers():remove(header)
          end
        end
- name: envoy.filters.http.ai_protocol_manager
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.ai_protocol_manager.v3.AiProtocolManager
    request_handling: {}
    response_handling: {token_usage: {}}
    filters:
    - name: envoy.http.ai_filters.transcoder
      typed_config:
        "@type": type.googleapis.com/envoy.extensions.http.ai_filters.transcoder.v3.Transcoder
        request_handling: FROM_IR
        response_handling: TO_IR
- name: envoy.filters.http.upstream_codec
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.upstream_codec.v3.UpstreamCodec
```

- **Endpoint metadata, not cluster config**: a composite cluster runs its own upstream filters for every sub-cluster, so the only thing that identifies the backend on an attempt is the host that was picked. `%UPSTREAM_METADATA%` reads that host's metadata.
- **header_mutation, then Lua**: `set_filter_state` can't run on a cluster yet, and Lua can't read the host's metadata. `header_mutation` can format `%UPSTREAM_METADATA%`, and Lua can set filter state, so they pass the values along in three headers.
- **Every endpoint sets all three values.** The filter state survives across attempts, so a value one endpoint left out would be the previous attempt's.
- **Token usage on this AIPM** reads the backend that answered. Only the winning attempt publishes.

Then the routes. All of them declare `request: {llm_protocol: OPENAI_CHAT_COMPLETIONS}` (or the client's API) for both AIPMs.

**Split** half the traffic between Claude and Gemini:

```yaml
route:
  weighted_clusters:
    clusters: [{name: vertex_claude, weight: 50}, {name: vertex_gemini, weight: 50}]
  auto_host_rewrite: true
```

**Fail over** to Gemini when Claude fails, using a composite cluster (the first attempt uses the first cluster, the retry uses the next):

```yaml
route: {cluster: vertex_failover, auto_host_rewrite: true, retry_policy: {retry_on: 5xx, num_retries: 1}}
# cluster
- name: vertex_failover
  lb_policy: CLUSTER_PROVIDED
  cluster_type:
    name: envoy.clusters.composite
    typed_config:
      "@type": type.googleapis.com/envoy.extensions.clusters.composite.v3.ClusterConfig
      clusters: [{name: vertex_claude}, {name: vertex_gemini}]
```

In the test, attempt 1 sends `rawPredict` with `anthropic_version` and gets a 503. Attempt 2 sends `generateContent` with `contents`. The OpenAI client gets a normal chat completion, and Claude Code, on its own route, gets Anthropic SSE.

**Fail over across providers with their own keys**, Anthropic first, then Gemini, using the priority group cluster specifier:

```yaml
route:
  inline_cluster_specifier_plugin:
    extension:
      name: envoy.router.cluster_specifier_plugin.priority_group
      typed_config:
        "@type": type.googleapis.com/envoy.extensions.router.cluster_specifiers.priority_group.v3.PriorityGroupClusterSpecifier
        priority_groups:
        - {name: primary, clusters: [{cluster_name: anthropic, weight: 1}]}
        - {name: fallback, clusters: [{cluster_name: gemini, weight: 1}]}
  auto_host_rewrite: true
  retry_policy: {retry_on: 5xx, num_retries: 1, refresh_cluster_on_retry: true}
```

- **`refresh_cluster_on_retry`** makes the router ask the specifier again on the retry, and the attempt then runs the new cluster's filters.
- **Each cluster brings its own key.** A cluster's own `http_filters` replace the router's, so it lists a `header_mutation` that adds its key, followed by the same three filters. It also removes the other provider's key: upstream filters edit the request in place, so a retry starts with whatever the previous attempt added. The test checks that the Gemini attempt carries no `x-api-key`.

**Route by model**: routes can match the model `request_info` reads from the body:

```yaml
- match:
    path: /v1/chat/completions
    filter_state: [{key: envoy.ai.model.request, string_match: {prefix: gemini-}}]
  route: {cluster: vertex_gemini, auto_host_rewrite: true}
# ... claude- to vertex_claude, then:
- match: {path: /v1/chat/completions}
  direct_response: {status: 404, body: {inline_string: "no backend serves this model"}}
```

```yaml
http_filters:
- name: envoy.filters.http.ai_protocol_manager    # request_info, client-side transcoder
  ...
- name: envoy.filters.http.set_filter_state
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.set_filter_state.v3.Config
    clear_route_cache: true
```

- **The catch-all route must also declare `request:`**: Envoy picks a route before the body is read, and only an AI endpoint gets its body parsed.
- **`clear_route_cache: true`**, after AIPM, makes Envoy pick the route again once the model is known. A model no backend serves gets the 404 without calling any provider.

## Egress through a forward proxy

One route, no per-provider config. The request's Host picks the destination: clients set `HTTP_PROXY=http://localhost:10000` and use an `http://` base URL, and Envoy originates TLS. Full config: [egress.yaml](https://github.com/botengyao/botengyao.github.io/blob/main/examples/ai-protocol-manager/configs/egress.yaml).

```yaml
routes:
- match: {prefix: /}
  route: {cluster: egress}
  request_headers_to_remove: [accept-encoding]
  typed_per_filter_config:
    envoy.filters.http.ai_protocol_manager:
      "@type": type.googleapis.com/envoy.extensions.filters.http.ai_protocol_manager.v3.AiProtocolManagerPerRoute
      request: {}
# listener: AIPM (schema_validation, request_info, DISABLE) -> dynamic_forward_proxy -> router
# cluster `egress` (dynamic forward proxy), upstream filters:
http_filters:
- name: envoy.filters.http.ai_protocol_manager
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.ai_protocol_manager.v3.AiProtocolManager
    response_handling: {token_usage: {}}
- name: envoy.filters.http.upstream_codec
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.upstream_codec.v3.UpstreamCodec
```

- **`request: {}`** declares an AI endpoint without naming the API. Schema validation detects it from the path (`/chat/completions`, `/v1/messages`, `:generateContent`), then the `anthropic-version` header, then the body, and validates against it. Request info reads with the API it found. Every body on the route is parsed strictly, so keep this route for LLM traffic.
- **Token usage on the cluster** detects the response's API from its shape. It runs on each attempt, and only the attempt that answered publishes.
- **`auto_sni` and `auto_san_validation`** on the cluster use the Host, so TLS names and checks whichever provider the request went to.

## Routing decisions outside Envoy

A decision point (PDP) behind ext_proc picks the backend for each request and charges each tenant for the tokens it used. Full config: [pdp.yaml](https://github.com/botengyao/botengyao.github.io/blob/main/examples/ai-protocol-manager/configs/pdp.yaml), and the [PDP](https://github.com/botengyao/botengyao.github.io/blob/main/examples/ai-protocol-manager/pdp/main.go) is about 150 lines of Go.

<figure>
<svg width="100%" viewBox="0 0 720 210" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>Model routing with an external decision point</title>
<desc>The request flows from the client through the AI Protocol Manager on the listener, which describes it, to ext_proc, where the PDP decides the backend. set_filter_state turns the PDP's headers into filter state, and the router with the dynamic forward proxy sends the request to the cluster, where a second AI Protocol Manager transcodes it and reads token usage from the provider's response. The usage record goes back to ext_proc on trailers, and the PDP charges the tenant.</desc>
<defs>
<marker id="aipmC" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#57606a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker>
</defs>
<rect x="1" y="1" width="718" height="208" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<rect x="16" y="50" width="84" height="56" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="58" y="75" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328" text-anchor="middle">Client</text>
<text x="58" y="92" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">model: smart</text>
<rect x="116" y="50" width="84" height="56" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="158" y="75" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#085041" text-anchor="middle">AIPM</text>
<text x="158" y="92" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">describe</text>
<rect x="216" y="50" width="84" height="56" rx="6" fill="#ffffff" stroke="#0F6E56" stroke-width="1.5"/>
<text x="258" y="75" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#085041" text-anchor="middle">ext_proc</text>
<text x="258" y="92" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">PDP decides</text>
<rect x="316" y="50" width="84" height="56" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="358" y="75" font-family="system-ui,sans-serif" font-size="11.5" font-weight="600" fill="#1f2328" text-anchor="middle">headers →</text>
<text x="358" y="92" font-family="system-ui,sans-serif" font-size="11.5" font-weight="600" fill="#1f2328" text-anchor="middle">filter state</text>
<rect x="416" y="50" width="84" height="56" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="458" y="75" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328" text-anchor="middle">router</text>
<text x="458" y="92" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">+ DFP</text>
<rect x="516" y="50" width="84" height="56" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="558" y="75" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#085041" text-anchor="middle">AIPM</text>
<text x="558" y="92" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">on the cluster</text>
<rect x="616" y="50" width="88" height="56" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="660" y="75" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328" text-anchor="middle">Claude or</text>
<text x="660" y="92" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328" text-anchor="middle">Gemini</text>
<text x="258" y="34" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">sees the model and a token estimate, not the prompt</text>
<line x1="100" y1="78" x2="112" y2="78" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmC)"/>
<line x1="200" y1="78" x2="212" y2="78" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmC)"/>
<line x1="300" y1="78" x2="312" y2="78" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmC)"/>
<line x1="400" y1="78" x2="412" y2="78" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmC)"/>
<line x1="500" y1="78" x2="512" y2="78" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmC)"/>
<line x1="600" y1="78" x2="612" y2="78" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmC)"/>
<polyline points="558,106 558,150 258,150 258,110" fill="none" stroke="#0F6E56" stroke-width="1.4" stroke-dasharray="4 3" marker-end="url(#aipmC)"/>
<text x="408" y="170" font-family="system-ui,sans-serif" font-size="11" fill="#085041" text-anchor="middle">token usage on the response trailers → the PDP charges the tenant</text>
<text x="408" y="190" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">over budget: the next "smart" request goes to Gemini</text>
</svg>
<figcaption>The PDP decides from a description of the request and settles from the usage Envoy reports. It never handles the prompt or the response body.</figcaption>
</figure>

```yaml
http_filters:
# schema_validation, request_info with token_estimation, reserialize_body: DISABLE
- name: envoy.filters.http.ai_protocol_manager
  ...
- name: envoy.filters.http.ext_proc
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.ext_proc.v3.ExternalProcessor
    grpc_service: {envoy_grpc: {cluster_name: pdp}}
    processing_mode:
      request_header_mode: SEND
      response_header_mode: SKIP
      response_trailer_mode: SEND
    metadata_options:
      forwarding_namespaces:
        typed: [envoy.ai.request_info, envoy.ai.token_usage]
- name: envoy.filters.http.set_filter_state
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.set_filter_state.v3.Config
    on_request_headers:
    - object_key: envoy.ai.llm_protocol.response
      format_string: {text_format_source: {inline_string: "%REQ(x-ai-api)%"}}
    - object_key: envoy.ai.binding.response
      format_string: {text_format_source: {inline_string: "%REQ(x-ai-binding)%"}}
    - object_key: envoy.ai.model.resolved
      format_string: {text_format_source: {inline_string: "%REQ(x-ai-model)%"}}
# the route sets host_rewrite_header: x-ai-host
- name: envoy.filters.http.dynamic_forward_proxy
  ...
- name: envoy.filters.http.router
  ...
# cluster `llm` (dynamic forward proxy), upstream filters:
- name: envoy.filters.http.ai_protocol_manager
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.ai_protocol_manager.v3.AiProtocolManager
    request_handling: {}
    response_handling:
      token_usage: {usage_signal: SYNTHESIZE_TRAILERS}
    filters:
    - name: envoy.http.ai_filters.transcoder
      typed_config:
        "@type": type.googleapis.com/envoy.extensions.http.ai_filters.transcoder.v3.Transcoder
        request_handling: FROM_IR
        response_handling: TO_IR
- name: envoy.filters.http.upstream_codec
  ...
```

- **ext_proc comes after the first AIPM.** `request_info` is published before the headers move on, so the PDP gets the model and the token estimate with the request headers. The body is never sent to it.
- **The PDP answers with headers, and `set_filter_state` turns them into instructions**, since ext_proc can't write filter state. The PDP overwrites all four `x-ai-*` headers on every request, and the route strips them before the request goes upstream. Otherwise a client could send `x-ai-host` and pick the host itself; the test sends one and checks it's ignored.
- **One transcoder instance on the cluster** is enough: these clients already speak OpenAI Chat, the common form.
- **`usage_signal: SYNTHESIZE_TRAILERS`** with `response_trailer_mode: SEND` delivers the usage record at the end of the response, without streaming the body to the PDP.

In the test, the PDP has a 10-token budget. The first `smart` request goes to Claude and costs 18 tokens. The next `smart` request goes to Gemini, and an unknown model gets a 400 from the PDP before any provider is called.

## Gaps on main

These are the things that didn't work as I first wrote them. All of them are on main as of this writing.

- **Schema validation doesn't read bindings.** A Vertex AI body names its model in the path, and validation rejects it with `missing required field: model`. Leave validation out of chains that take Vertex AI clients.
- **`set_filter_state` doesn't run on a cluster.** It isn't registered as an upstream filter, so per-attempt instructions go through `header_mutation` and Lua.
- **`auto_sni` doesn't follow `auto_host_rewrite`.** The SNI is taken from the Host before the rewrite applies, so the TLS check fails. Set `sni` and the SAN check on each cluster, as the configs above do.
- **Upstream filters edit the request in place, and a retry starts from the previous attempt's edits.** That's why clusters remove other providers' keys. It also breaks the client-side transcoder when it runs on the cluster: on a retry it reads the path the first attempt rewrote, and a Gemini SDK client gets a 400 (`missing required field: model`) instead of the retry. Keep the client side on the listener.
- **The Responses API** gets usage and request info, but no schema or transcoding yet.

The AI Protocol Manager is alpha, and all of this will move. If one of these configs stops working on a newer Envoy, the tests will tell you which part changed. Issues and ideas are welcome on [GitHub](https://github.com/envoyproxy/envoy/issues).
