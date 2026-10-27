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

<figure>
<svg width="100%" viewBox="0 0 720 176" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>Observing native LLM APIs</title>
<desc>The client calls its provider through Envoy with its own SDK and key. The AI Protocol Manager validates the body, describes the request, and forwards the client's exact bytes. On the way back it reads token usage from the provider's response without changing it. Both records are published as typed metadata for the access log or ext_proc.</desc>
<defs>
<marker id="aipmD" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#57606a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker>
</defs>
<rect x="1" y="1" width="718" height="174" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<rect x="16" y="36" width="112" height="60" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="72" y="62" font-family="system-ui,sans-serif" font-size="12.5" font-weight="600" fill="#1f2328" text-anchor="middle">Client</text>
<text x="72" y="79" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">own SDK, own key</text>
<rect x="196" y="20" width="328" height="92" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="212" y="42" font-family="system-ui,sans-serif" font-size="12.5" font-weight="600" fill="#085041">AI Protocol Manager</text>
<text x="212" y="62" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328">schema_validation: a bad body gets its 400 here</text>
<text x="212" y="80" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328">request_info: model, stream, token cap, counts</text>
<text x="212" y="98" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328">token_usage: read off the provider's response</text>
<rect x="592" y="36" width="112" height="60" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="648" y="62" font-family="system-ui,sans-serif" font-size="12.5" font-weight="600" fill="#1f2328" text-anchor="middle">Provider</text>
<text x="648" y="79" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">native API</text>
<line x1="128" y1="56" x2="192" y2="56" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmD)"/>
<line x1="524" y1="56" x2="588" y2="56" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmD)"/>
<line x1="592" y1="78" x2="528" y2="78" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmD)"/>
<line x1="196" y1="78" x2="132" y2="78" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmD)"/>
<text x="162" y="49" font-family="system-ui,sans-serif" font-size="10" fill="#57606a" text-anchor="middle">request</text>
<text x="162" y="93" font-family="system-ui,sans-serif" font-size="10" fill="#57606a" text-anchor="middle">response</text>
<text x="558" y="49" font-family="system-ui,sans-serif" font-size="10" fill="#57606a" text-anchor="middle">same bytes</text>
<text x="558" y="93" font-family="system-ui,sans-serif" font-size="10" fill="#57606a" text-anchor="middle">untouched</text>
<line x1="360" y1="112" x2="360" y2="128" stroke="#0F6E56" stroke-width="1.4" stroke-dasharray="4 3" marker-end="url(#aipmD)"/>
<rect x="196" y="132" width="328" height="30" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="360" y="151" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="10" fill="#1f2328" text-anchor="middle">envoy.ai.request_info · envoy.ai.token_usage</text>
<text x="534" y="151" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a">→ access log, ext_proc</text>
</svg>
<figcaption>Observing only reads. The provider gets the client's bytes and key, and Envoy keeps a description of each request and its token count.</figcaption>
</figure>

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

| Base URL | Client | Backend | Needs, besides the two APIs |
|---|---|---|---|
| `/claude/v1` | OpenAI SDK | Claude | backend path |
| `/gemini/v1` | OpenAI SDK | Gemini | nothing |
| `/gemini` | Claude Code | Gemini | model |
| `/openai` | Claude Code | OpenAI-compatible | backend path, model |
| `/claude` | Gemini SDK | Claude | client path, backend path, model |
| `/vertex/claude/v1` | OpenAI SDK | Claude on Vertex AI | backend path with body edits |
| `/vertex/gemini/v1` | OpenAI SDK | Gemini on Vertex AI | backend path |
| `/v1` | Claude Code on Vertex AI | Gemini on Vertex AI | backend path, model |

"Model" is `envoy.ai.model.resolved`, for clients that ask for a model the backend doesn't have. "Path" is a binding, explained below.

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

<figure>
<svg width="100%" viewBox="0 0 720 336" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>One request from the OpenAI SDK to Claude</title>
<desc>The client sends an OpenAI Chat request to /claude/v1/chat/completions with its own key. Envoy drops the client's key and adds Claude's, the first transcoder keeps OpenAI Chat as is, the second writes Anthropic Messages, the binding sets the path to /v1/messages, and the host is rewritten to Claude's. Claude receives a Messages request with a top-level system field and max_tokens. Claude's answer, with its usage, is read for token usage and converted back into an OpenAI chat completion.</desc>
<defs>
<marker id="aipmE" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#57606a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker>
</defs>
<rect x="1" y="1" width="718" height="334" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<text x="16" y="23" font-family="system-ui,sans-serif" font-size="10.5" font-weight="600" fill="#57606a">request</text>
<rect x="16" y="32" width="210" height="180" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="28" y="52" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328">Client sends&#160;<tspan font-weight="400" fill="#57606a">OpenAI Chat</tspan></text>
<text x="28" y="74" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9.5" fill="#1f2328">POST /claude/v1/chat/completions</text>
<text x="28" y="89" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9.5" fill="#1f2328">authorization: Bearer ‹client›</text>
<text x="28" y="111" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9.5" fill="#1f2328">{"model": "claude-sonnet-4-5",</text>
<text x="34" y="126" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9.5" fill="#1f2328">"messages": [system, user]}</text>
<rect x="246" y="32" width="228" height="180" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="258" y="52" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#085041">Envoy</text>
<text x="258" y="74" font-family="system-ui,sans-serif" font-size="10.5" fill="#1f2328">virtual host: drop the client's key</text>
<text x="258" y="92" font-family="system-ui,sans-serif" font-size="10.5" fill="#1f2328">route: add Claude's key and version</text>
<text x="258" y="110" font-family="system-ui,sans-serif" font-size="10.5" fill="#1f2328">transcoder 1: OpenAI Chat as is</text>
<text x="258" y="128" font-family="system-ui,sans-serif" font-size="10.5" fill="#1f2328">transcoder 2: → Anthropic Messages</text>
<text x="258" y="146" font-family="system-ui,sans-serif" font-size="10.5" fill="#1f2328">binding: path → /v1/messages</text>
<text x="258" y="164" font-family="system-ui,sans-serif" font-size="10.5" fill="#1f2328">auto_host_rewrite: Claude's host</text>
<rect x="494" y="32" width="210" height="180" rx="6" fill="#ffffff" stroke="#d0d7de"/>
<text x="506" y="52" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328">Claude receives&#160;<tspan font-weight="400" fill="#57606a">Messages</tspan></text>
<text x="506" y="74" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9.5" fill="#1f2328">POST /v1/messages</text>
<text x="506" y="89" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9.5" fill="#1f2328">host: api.anthropic.com</text>
<text x="506" y="104" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9.5" fill="#1f2328">x-api-key: ‹gateway's›</text>
<text x="506" y="119" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9.5" fill="#1f2328">anthropic-version: 2023-06-01</text>
<text x="506" y="141" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9.5" fill="#1f2328">{"model": "claude-sonnet-4-5",</text>
<text x="512" y="156" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9.5" fill="#1f2328">"system": "Be brief.",</text>
<text x="512" y="171" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9.5" fill="#1f2328">"messages": [user],</text>
<text x="512" y="186" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9.5" fill="#1f2328">"max_tokens": …}</text>
<line x1="226" y1="122" x2="242" y2="122" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmE)"/>
<line x1="474" y1="122" x2="490" y2="122" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmE)"/>
<text x="16" y="236" font-family="system-ui,sans-serif" font-size="10.5" font-weight="600" fill="#57606a">response</text>
<rect x="16" y="244" width="210" height="78" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="28" y="264" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328">Client gets&#160;<tspan font-weight="400" fill="#57606a">OpenAI Chat</tspan></text>
<text x="28" y="284" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9.5" fill="#1f2328">{"object": "chat.completion",</text>
<text x="34" y="299" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9.5" fill="#1f2328">"choices": [{"message": …}],</text>
<text x="34" y="314" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9.5" fill="#1f2328">"usage": {…}}</text>
<rect x="246" y="244" width="228" height="78" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="258" y="264" font-family="system-ui,sans-serif" font-size="10.5" fill="#1f2328">token_usage: 12 in, 6 out</text>
<text x="258" y="282" font-family="system-ui,sans-serif" font-size="10.5" fill="#1f2328">transcoder 2: → OpenAI Chat</text>
<text x="258" y="300" font-family="system-ui,sans-serif" font-size="10.5" fill="#1f2328">transcoder 1: OpenAI Chat as is</text>
<text x="258" y="316" font-family="system-ui,sans-serif" font-size="10.5" font-style="italic" fill="#57606a">JSON and SSE alike</text>
<rect x="494" y="244" width="210" height="78" rx="6" fill="#ffffff" stroke="#d0d7de"/>
<text x="506" y="264" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328">Claude answers&#160;<tspan font-weight="400" fill="#57606a">Messages</tspan></text>
<text x="506" y="284" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9.5" fill="#1f2328">{"content": [text block],</text>
<text x="512" y="299" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9.5" fill="#1f2328">"usage": {"input_tokens": 12,</text>
<text x="569" y="314" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9.5" fill="#1f2328">"output_tokens": 6}}</text>
<line x1="494" y1="283" x2="478" y2="283" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmE)"/>
<line x1="246" y1="283" x2="230" y2="283" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmE)"/>
</svg>
<figcaption>The client and Claude each see only their own API. The key, path, host and body shape change inside Envoy.</figcaption>
</figure>

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
- **`envoy.ai.model.resolved: claude-sonnet-4-5`**, set the same way as for Claude Code below: the SDK asks for a Gemini model.

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

<figure>
<svg width="100%" viewBox="0 0 720 264" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>How a binding renders a Vertex AI request</title>
<desc>The second transcoder writes an Anthropic Messages body with model and stream. The binding's pattern takes the model from the body into the path, and stream picks rawPredict or streamRawPredict. The first mutation removes model from the body, and the second adds anthropic_version. Vertex AI receives a POST to the streamRawPredict path for the model, with anthropic_version, stream, messages and max_tokens in the body.</desc>
<rect x="1" y="1" width="718" height="262" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<text x="16" y="22" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#1f2328">1  transcoder 2 writes Anthropic Messages</text>
<rect x="16" y="30" width="688" height="34" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="28" y="51" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="10.5" fill="#1f2328">{<tspan fill="#0F6E56" font-weight="600">"model": "claude-sonnet-4-5@20250929"</tspan>,&#160;<tspan fill="#9A6700" font-weight="600">"stream": true</tspan>, "messages": […], "max_tokens": …}</text>
<text x="16" y="90" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#1f2328">2  the binding renders the path and edits the body</text>
<rect x="16" y="98" width="688" height="68" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="28" y="119" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="10.5" fill="#1f2328">uri_pattern: /v1/projects/…/publishers/anthropic/models/<tspan fill="#0F6E56" font-weight="600">{model}</tspan>:<tspan fill="#9A6700" font-weight="600">{rawPredict|streamRawPredict}</tspan></text>
<text x="28" y="138" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="10.5" fill="#1f2328">mutations:&#160;<tspan fill="#0F6E56" font-weight="600">{body_field: model}</tspan>,&#160;<tspan fill="#0969DA" font-weight="600">{body_field: anthropic_version, value: vertex-2023-10-16}</tspan></text>
<text x="28" y="157" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a">{model} takes the body's model, and the first mutation removes it from the body. stream picks the method and stays.</text>
<text x="16" y="190" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#1f2328">3  Vertex AI receives</text>
<rect x="16" y="198" width="688" height="52" rx="6" fill="#ffffff" stroke="#0F6E56" stroke-width="1.5"/>
<text x="28" y="219" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="10.5" fill="#1f2328">POST /v1/projects/…/publishers/anthropic/models/<tspan fill="#0F6E56" font-weight="600">claude-sonnet-4-5@20250929</tspan>:<tspan fill="#9A6700" font-weight="600">streamRawPredict</tspan></text>
<text x="28" y="238" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="10.5" fill="#1f2328">{<tspan fill="#0969DA" font-weight="600">"anthropic_version": "vertex-2023-10-16"</tspan>,&#160;<tspan fill="#9A6700" font-weight="600">"stream": true</tspan>, "messages": […], "max_tokens": …}</text>
</svg>
<figcaption>A binding maps between the body and the path. On the client side it works the other way: it reads the model and the method out of the path, as in the Claude Code on Vertex AI route below.</figcaption>
</figure>

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

<figure>
<svg width="100%" viewBox="0 0 720 172" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>A retry with transcoding on the listener</title>
<desc>The AI Protocol Manager on the listener converts the OpenAI Chat request into Anthropic Messages once, before the router. The router buffers the body. Attempt 1 to the anthropic cluster gets a 503. Attempt 2 sends the same bytes, gets a 200, and is the attempt that reports token usage.</desc>
<defs>
<marker id="aipmG" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#57606a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker>
</defs>
<rect x="1" y="1" width="718" height="170" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<rect x="16" y="58" width="88" height="56" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="60" y="83" font-family="system-ui,sans-serif" font-size="12.5" font-weight="600" fill="#1f2328" text-anchor="middle">Client</text>
<text x="60" y="100" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">OpenAI SDK</text>
<rect x="124" y="50" width="164" height="72" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="206" y="74" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#085041" text-anchor="middle">AIPM on the listener</text>
<text x="206" y="94" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328" text-anchor="middle">OpenAI Chat → Messages</text>
<text x="206" y="112" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">once, before the router</text>
<rect x="308" y="50" width="132" height="72" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="374" y="74" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328" text-anchor="middle">router</text>
<text x="374" y="94" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">buffers the body</text>
<text x="374" y="110" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">for a retry</text>
<rect x="468" y="14" width="236" height="64" rx="6" fill="#ffffff" stroke="#d0d7de"/>
<text x="482" y="36" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328">attempt 1 → anthropic</text>
<text x="482" y="53" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="10" fill="#57606a">POST /v1/messages</text>
<text x="482" y="70" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#B22B2B">✗ 503</text>
<rect x="468" y="94" width="236" height="64" rx="6" fill="#ffffff" stroke="#0F6E56"/>
<text x="482" y="116" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328">attempt 2 → anthropic</text>
<text x="482" y="133" font-family="system-ui,sans-serif" font-size="11" fill="#085041">the same bytes</text>
<text x="482" y="150" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#0F6E56">✓ 200, token usage from here</text>
<line x1="104" y1="86" x2="120" y2="86" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmG)"/>
<line x1="288" y1="86" x2="304" y2="86" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmG)"/>
<polyline points="440,76 454,76 454,46 464,46" fill="none" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmG)"/>
<polyline points="440,96 454,96 454,126 464,126" fill="none" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmG)"/>
</svg>
<figcaption>Transcoded once, sent twice. Only the attempt that answered reports token usage.</figcaption>
</figure>

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

<figure>
<svg width="100%" viewBox="0 0 720 288" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>Three values relayed from the picked host to the AI Protocol Manager</title>
<desc>On each attempt, the picked host's envoy.ai metadata holds the backend's API, its binding and its model. header_mutation copies them into the x-ai-api, x-ai-binding and x-ai-model headers using UPSTREAM_METADATA. Lua moves each header into filter state: envoy.ai.llm_protocol.response, envoy.ai.binding.response and envoy.ai.model.resolved. The AI Protocol Manager then writes that API, renders that path and sends that model.</desc>
<defs>
<marker id="aipmH" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#57606a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker>
</defs>
<rect x="1" y="1" width="718" height="286" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<text x="248" y="22" font-family="system-ui,sans-serif" font-size="10.5" font-weight="600" fill="#57606a" text-anchor="middle">the backend's API</text>
<text x="436" y="22" font-family="system-ui,sans-serif" font-size="10.5" font-weight="600" fill="#57606a" text-anchor="middle">how it carries it on HTTP</text>
<text x="618" y="22" font-family="system-ui,sans-serif" font-size="10.5" font-weight="600" fill="#57606a" text-anchor="middle">the model to send</text>
<rect x="16" y="32" width="128" height="44" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="80" y="50" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328" text-anchor="middle">picked host</text>
<text x="80" y="66" font-family="system-ui,sans-serif" font-size="10" fill="#57606a" text-anchor="middle">envoy.ai metadata</text>
<rect x="156" y="32" width="184" height="44" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<rect x="352" y="32" width="168" height="44" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<rect x="532" y="32" width="172" height="44" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="248" y="58" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="10" fill="#1f2328" text-anchor="middle">api: GEMINI_GENERATE_CONTENT</text>
<text x="436" y="58" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="10" fill="#1f2328" text-anchor="middle">binding: "/v1/projects/…"</text>
<text x="618" y="58" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="10" fill="#1f2328" text-anchor="middle">model: gemini-2.5-flash</text>
<rect x="16" y="98" width="128" height="44" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="80" y="116" font-family="system-ui,sans-serif" font-size="11.5" font-weight="600" fill="#1f2328" text-anchor="middle">header_mutation</text>
<text x="80" y="132" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9" fill="#57606a" text-anchor="middle">%UPSTREAM_METADATA%</text>
<rect x="156" y="98" width="184" height="44" rx="6" fill="#ffffff" stroke="#d0d7de"/>
<rect x="352" y="98" width="168" height="44" rx="6" fill="#ffffff" stroke="#d0d7de"/>
<rect x="532" y="98" width="172" height="44" rx="6" fill="#ffffff" stroke="#d0d7de"/>
<text x="248" y="124" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="10" fill="#1f2328" text-anchor="middle">x-ai-api</text>
<text x="436" y="124" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="10" fill="#1f2328" text-anchor="middle">x-ai-binding</text>
<text x="618" y="124" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="10" fill="#1f2328" text-anchor="middle">x-ai-model</text>
<rect x="16" y="164" width="128" height="44" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="80" y="182" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328" text-anchor="middle">Lua</text>
<text x="80" y="198" font-family="system-ui,sans-serif" font-size="10" fill="#57606a" text-anchor="middle">headers → filter state</text>
<rect x="156" y="164" width="184" height="44" rx="6" fill="#ffffff" stroke="#d0d7de"/>
<rect x="352" y="164" width="168" height="44" rx="6" fill="#ffffff" stroke="#d0d7de"/>
<rect x="532" y="164" width="172" height="44" rx="6" fill="#ffffff" stroke="#d0d7de"/>
<text x="248" y="190" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9.5" fill="#1f2328" text-anchor="middle">envoy.ai.llm_protocol.response</text>
<text x="436" y="190" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9.5" fill="#1f2328" text-anchor="middle">envoy.ai.binding.response</text>
<text x="618" y="190" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9.5" fill="#1f2328" text-anchor="middle">envoy.ai.model.resolved</text>
<rect x="16" y="230" width="128" height="44" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="80" y="248" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#085041" text-anchor="middle">AI Protocol Manager</text>
<text x="80" y="264" font-family="system-ui,sans-serif" font-size="10" fill="#57606a" text-anchor="middle">on each attempt</text>
<rect x="156" y="230" width="184" height="44" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<rect x="352" y="230" width="168" height="44" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<rect x="532" y="230" width="172" height="44" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="248" y="256" font-family="system-ui,sans-serif" font-size="11" fill="#085041" text-anchor="middle">writes this API</text>
<text x="436" y="256" font-family="system-ui,sans-serif" font-size="11" fill="#085041" text-anchor="middle">renders this path</text>
<text x="618" y="256" font-family="system-ui,sans-serif" font-size="11" fill="#085041" text-anchor="middle">sends this model</text>
<line x1="80" y1="76" x2="80" y2="94" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmH)"/>
<line x1="80" y1="142" x2="80" y2="160" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmH)"/>
<line x1="80" y1="208" x2="80" y2="226" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmH)"/>
<line x1="248" y1="76" x2="248" y2="94" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmH)"/>
<line x1="248" y1="142" x2="248" y2="160" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmH)"/>
<line x1="248" y1="208" x2="248" y2="226" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmH)"/>
<line x1="436" y1="76" x2="436" y2="94" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmH)"/>
<line x1="436" y1="142" x2="436" y2="160" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmH)"/>
<line x1="436" y1="208" x2="436" y2="226" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmH)"/>
<line x1="618" y1="76" x2="618" y2="94" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmH)"/>
<line x1="618" y1="142" x2="618" y2="160" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmH)"/>
<line x1="618" y1="208" x2="618" y2="226" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmH)"/>
</svg>
<figcaption>On every attempt, three values relay from the host Envoy picked to the AI Protocol Manager. header_mutation can read the host but not set filter state, and Lua the other way round, so they hand off through headers that Lua then removes.</figcaption>
</figure>

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

<figure>
<svg width="100%" viewBox="0 0 720 254" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>Routing by the model in the body</title>
<desc>When the headers arrive the model is unknown, so the catch-all route matches; it declares request, so the body is parsed. The AI Protocol Manager's request_info reads the model into envoy.ai.model.request. set_filter_state with clear_route_cache drops the picked route, and the routes are matched again: a gemini model goes to vertex_gemini, a claude model to vertex_claude, and anything else gets a 404 without calling a provider.</desc>
<defs>
<marker id="aipmI" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#57606a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker>
</defs>
<rect x="1" y="1" width="718" height="252" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<rect x="16" y="16" width="212" height="72" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="28" y="38" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328">1  headers arrive</text>
<text x="28" y="58" font-family="system-ui,sans-serif" font-size="10.5" fill="#1f2328">no model yet, so the catch-all</text>
<text x="28" y="74" font-family="system-ui,sans-serif" font-size="10.5" fill="#1f2328">matches; it declares&#160;<tspan font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="10">request:</tspan></text>
<rect x="254" y="16" width="212" height="72" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="266" y="38" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#085041">2  AI Protocol Manager</text>
<text x="266" y="58" font-family="system-ui,sans-serif" font-size="10.5" fill="#1f2328">request_info reads the model into</text>
<text x="266" y="74" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="10" fill="#1f2328">envoy.ai.model.request</text>
<rect x="492" y="16" width="212" height="72" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="504" y="38" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328">3  set_filter_state</text>
<text x="504" y="58" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="10" fill="#1f2328">clear_route_cache: true</text>
<text x="504" y="74" font-family="system-ui,sans-serif" font-size="10.5" fill="#1f2328">drops the route Envoy picked</text>
<line x1="228" y1="52" x2="250" y2="52" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmI)"/>
<line x1="466" y1="52" x2="488" y2="52" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmI)"/>
<rect x="254" y="116" width="212" height="40" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="360" y="141" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328" text-anchor="middle">4  routes match again</text>
<polyline points="598,88 598,136 470,136" fill="none" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmI)"/>
<rect x="16" y="192" width="212" height="48" rx="6" fill="#ffffff" stroke="#0F6E56"/>
<text x="122" y="212" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="10.5" fill="#1f2328" text-anchor="middle">gemini-* → vertex_gemini</text>
<text x="122" y="229" font-family="system-ui,sans-serif" font-size="10" fill="#57606a" text-anchor="middle">Gemini on Vertex AI</text>
<rect x="254" y="192" width="212" height="48" rx="6" fill="#ffffff" stroke="#0F6E56"/>
<text x="360" y="212" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="10.5" fill="#1f2328" text-anchor="middle">claude-* → vertex_claude</text>
<text x="360" y="229" font-family="system-ui,sans-serif" font-size="10" fill="#57606a" text-anchor="middle">Claude on Vertex AI</text>
<rect x="492" y="192" width="212" height="48" rx="6" fill="#ffffff" stroke="#B22B2B"/>
<text x="598" y="212" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#B22B2B" text-anchor="middle">anything else → 404</text>
<text x="598" y="229" font-family="system-ui,sans-serif" font-size="10" fill="#57606a" text-anchor="middle">no provider is called</text>
<line x1="360" y1="156" x2="360" y2="188" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmI)"/>
<polyline points="320,156 320,172 122,172 122,188" fill="none" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmI)"/>
<polyline points="400,156 400,172 598,172 598,188" fill="none" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmI)"/>
</svg>
<figcaption>Envoy picks a route before the body arrives. Clearing the route cache after AIPM has read the model makes it pick again, and this time the model can decide.</figcaption>
</figure>

- **The catch-all route must also declare `request:`**: Envoy picks a route before the body is read, and only an AI endpoint gets its body parsed.
- **`clear_route_cache: true`**, after AIPM, makes Envoy pick the route again once the model is known. A model no backend serves gets the 404 without calling any provider.

## Egress through a forward proxy

One route, no per-provider config. The request's Host picks the destination: clients set `HTTP_PROXY=http://localhost:10000` and use an `http://` base URL, and Envoy originates TLS. Full config: [egress.yaml](https://github.com/botengyao/botengyao.github.io/blob/main/examples/ai-protocol-manager/configs/egress.yaml).

<figure>
<svg width="100%" viewBox="0 0 720 200" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>Egress through a dynamic forward proxy</title>
<desc>A client with HTTP_PROXY set sends plain HTTP to Envoy. The AI Protocol Manager on the listener detects the API from the path, a header or the body, then validates and describes the request. The dynamic forward proxy resolves the request's Host. The AI Protocol Manager on the cluster reads token usage on each attempt, detecting the API from the response. The cluster's TLS takes its SNI and SAN check from the Host, so one route reaches api.openai.com, api.anthropic.com or generativelanguage.googleapis.com.</desc>
<defs>
<marker id="aipmJ" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#57606a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker>
</defs>
<rect x="1" y="1" width="718" height="198" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<rect x="12" y="58" width="102" height="72" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="63" y="82" font-family="system-ui,sans-serif" font-size="12.5" font-weight="600" fill="#1f2328" text-anchor="middle">Client</text>
<text x="63" y="100" font-family="system-ui,sans-serif" font-size="10" fill="#57606a" text-anchor="middle">HTTP_PROXY set,</text>
<text x="63" y="115" font-family="system-ui,sans-serif" font-size="10" fill="#57606a" text-anchor="middle">http:// base URL</text>
<rect x="124" y="14" width="448" height="172" rx="10" fill="#ffffff" stroke="#0F6E56" stroke-width="1.5"/>
<text x="140" y="36" font-family="system-ui,sans-serif" font-size="13" font-weight="600" fill="#085041">Envoy</text>
<rect x="140" y="52" width="148" height="84" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="214" y="72" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#085041" text-anchor="middle">AIPM</text>
<text x="214" y="92" font-family="system-ui,sans-serif" font-size="10.5" fill="#1f2328" text-anchor="middle">detects the API from</text>
<text x="214" y="108" font-family="system-ui,sans-serif" font-size="10.5" fill="#1f2328" text-anchor="middle">path, header or body,</text>
<text x="214" y="124" font-family="system-ui,sans-serif" font-size="10.5" fill="#1f2328" text-anchor="middle">validates, describes</text>
<rect x="304" y="52" width="104" height="84" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="356" y="72" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328" text-anchor="middle">DFP</text>
<text x="356" y="92" font-family="system-ui,sans-serif" font-size="10.5" fill="#1f2328" text-anchor="middle">resolves the</text>
<text x="356" y="108" font-family="system-ui,sans-serif" font-size="10.5" fill="#1f2328" text-anchor="middle">request's Host</text>
<rect x="424" y="52" width="132" height="84" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="490" y="72" font-family="system-ui,sans-serif" font-size="11.5" font-weight="600" fill="#085041" text-anchor="middle">AIPM on the cluster</text>
<text x="490" y="92" font-family="system-ui,sans-serif" font-size="10.5" fill="#1f2328" text-anchor="middle">token usage on</text>
<text x="490" y="108" font-family="system-ui,sans-serif" font-size="10.5" fill="#1f2328" text-anchor="middle">each attempt, API</text>
<text x="490" y="124" font-family="system-ui,sans-serif" font-size="10.5" fill="#1f2328" text-anchor="middle">from the response</text>
<text x="348" y="164" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">cluster TLS: auto_sni and auto_san_validation follow the Host</text>
<rect x="588" y="40" width="116" height="108" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="646" y="60" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328" text-anchor="middle">the Host</text>
<text x="646" y="80" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9" fill="#1f2328" text-anchor="middle">api.openai.com</text>
<text x="646" y="96" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9" fill="#1f2328" text-anchor="middle">api.anthropic.com</text>
<text x="646" y="112" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9" fill="#1f2328" text-anchor="middle">generativelanguage</text>
<text x="646" y="125" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="9" fill="#1f2328" text-anchor="middle">.googleapis.com</text>
<line x1="114" y1="94" x2="136" y2="94" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmJ)"/>
<line x1="288" y1="94" x2="300" y2="94" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmJ)"/>
<line x1="408" y1="94" x2="420" y2="94" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmJ)"/>
<line x1="556" y1="94" x2="584" y2="94" stroke="#57606a" stroke-width="1.4" marker-end="url(#aipmJ)"/>
</svg>
<figcaption>One route for every provider. The Host decides where the request goes, the listener's AIPM works out which API it is, and the cluster's AIPM reads usage from whichever provider answered.</figcaption>
</figure>

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
