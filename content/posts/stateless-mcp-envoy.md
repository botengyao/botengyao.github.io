---
title: "Stateless MCP in Envoy 1.40"
date: 2026-10-20T12:00:00+08:00
draft: false
tags: ["envoy", "mcp", "ai-gateway", "protocols"]
summary: "MCP's 2026-07-28 revision makes the protocol stateless, so MCP traffic now looks a lot like ordinary HTTP. Envoy 1.40 builds on that: route MCP requests by tool name, stop malformed or spoofed requests at the proxy, control who can call which tool, and turn any REST API into an MCP server using only config."
---

AI agents use the [Model Context Protocol](https://modelcontextprotocol.io) (MCP) to discover and call tools. The revision published on 2026-07-28 changes MCP in a way that matters to anyone running it at scale: **MCP is now stateless.** There's no connection handshake and no session to keep track of. Each request carries everything a server needs to handle it.

That makes MCP traffic look a lot like ordinary HTTP traffic, which is exactly what Envoy is built for. Envoy 1.40 supports the new revision and lets you:

- **route MCP requests by tool name**, using standard Envoy route configuration;
- **stop malformed or spoofed requests at the proxy**, before they reach your servers;
- **control who can call which tool**, with Envoy's RBAC and external authorization filters;
- **turn an existing REST API into an MCP server**, with config alone and no new code.

## What changed in MCP

Before 2026-07-28, a client opened a session with an `initialize` handshake. The server returned a session ID, and every later request had to reach the server that held that session. Behind a load balancer, that meant sticky sessions.

The new revision removes that step. Every request now includes the protocol version and the client's capabilities. Routing information is also copied into HTTP headers, so a proxy can read it without parsing the JSON body. A tool call now looks like this:

```http
POST /mcp HTTP/1.1
Content-Type: application/json
Accept: application/json, text/event-stream
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tools/call
Mcp-Name: get_weather

{"jsonrpc":"2.0","id":3,"method":"tools/call",
 "params":{"name":"get_weather","arguments":{"city":"Paris"},
  "_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28",
           "io.modelcontextprotocol/clientCapabilities":{}}}}
```

Three headers carry the important routing facts:

- `MCP-Protocol-Version`: the protocol version this request uses;
- `Mcp-Method`: what the client wants to do (`tools/call`);
- `Mcp-Name`: which tool it wants (`get_weather`).

## Why this is a good fit for Envoy

Stateless requests can go to any replica, so you can scale MCP servers the way you scale any other HTTP service. Envoy can spread the load, retry, and fail over without worrying about sessions.

Envoy 1.40 also understands MCP itself, through two extensions. Each one works on its own, so you pick the one that fits your backend:

- **The MCP filter** (`envoy.filters.http.mcp`) goes in front of MCP servers. It reads each MCP request, checks that it is well formed, and exposes the method and tool name to the rest of Envoy for routing, access control and logging.
- **The MCP REST bridge** (`envoy.filters.http.mcp_json_rest_bridge`) goes in front of REST APIs and presents them as an MCP server.

You can run each one in its own filter chain, for example one listener for your MCP servers and another for your REST APIs, or combine them in one chain. Here are the two paths:

<figure>
<svg width="100%" viewBox="0 0 720 380" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>Two ways to put Envoy 1.40 in front of MCP traffic</title>
<desc>An MCP client sends requests to Envoy. In front of an MCP server, the MCP filter checks each request and routes it by tool name. In front of a REST API, the REST bridge answers discovery calls itself and turns tool calls into REST calls. Each works on its own, and each rejects bad requests with an MCP error before they reach the backend.</desc>
<defs>
<marker id="fgA" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#57606a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker>
</defs>
<rect x="1" y="1" width="718" height="378" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<rect x="16" y="170" width="100" height="64" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="66" y="198" font-family="system-ui,sans-serif" font-size="12.5" font-weight="600" fill="#1f2328" text-anchor="middle">MCP client</text>
<text x="66" y="216" font-family="ui-monospace,monospace" font-size="10" fill="#57606a" text-anchor="middle">POST /mcp</text>
<rect x="140" y="20" width="430" height="344" rx="10" fill="#ffffff" stroke="#0F6E56" stroke-width="1.5"/>
<text x="158" y="44" font-family="system-ui,sans-serif" font-size="13" font-weight="600" fill="#085041">Envoy 1.40</text>
<rect x="176" y="58" width="376" height="128" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="190" y="80" font-family="system-ui,sans-serif" font-size="12.5" font-weight="600" fill="#085041">MCP filter</text>
<text x="190" y="96" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a">in front of an MCP server</text>
<text x="190" y="120" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#0F6E56">✓</text><text x="204" y="120" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328">version is supported</text>
<text x="190" y="138" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#0F6E56">✓</text><text x="204" y="138" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328">MCP headers are present</text>
<text x="190" y="156" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#0F6E56">✓</text><text x="204" y="156" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328">headers match the body</text>
<text x="190" y="174" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#0F6E56">✓</text><text x="204" y="174" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328">capabilities are declared</text>
<text x="380" y="120" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#085041">→ routes by tool name</text>
<text x="380" y="156" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#B22B2B">✗ rejects bad requests</text>
<text x="394" y="174" font-family="system-ui,sans-serif" font-size="10.5" fill="#B22B2B">with an MCP error</text>
<text x="364" y="208" font-family="system-ui,sans-serif" font-size="11" font-style="italic" fill="#57606a" text-anchor="middle">or</text>
<rect x="176" y="220" width="376" height="128" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="190" y="242" font-family="system-ui,sans-serif" font-size="12.5" font-weight="600" fill="#085041">REST bridge</text>
<text x="190" y="258" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a">in front of a REST API</text>
<text x="190" y="282" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328">answers server/discover</text>
<text x="190" y="300" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328">answers tools/list</text>
<text x="190" y="318" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328">turns tools/call into</text>
<text x="190" y="335" font-family="ui-monospace,monospace" font-size="10" fill="#085041">GET /v1/weather/Paris</text>
<text x="380" y="282" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#0F6E56">✓</text><text x="394" y="282" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328">headers match the body</text>
<text x="380" y="318" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#B22B2B">✗ rejects bad requests</text>
<text x="394" y="336" font-family="system-ui,sans-serif" font-size="10.5" fill="#B22B2B">with an MCP error</text>
<rect x="596" y="92" width="108" height="60" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="650" y="119" font-family="system-ui,sans-serif" font-size="12.5" font-weight="600" fill="#1f2328" text-anchor="middle">MCP server</text>
<text x="650" y="137" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">speaks MCP</text>
<rect x="596" y="254" width="108" height="60" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="650" y="281" font-family="system-ui,sans-serif" font-size="12.5" font-weight="600" fill="#1f2328" text-anchor="middle">REST API</text>
<text x="650" y="299" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">no MCP code</text>
<polyline points="116,202 152,202 152,122 172,122" fill="none" stroke="#57606a" stroke-width="1.4" marker-end="url(#fgA)"/>
<polyline points="152,202 152,284 172,284" fill="none" stroke="#57606a" stroke-width="1.4" marker-end="url(#fgA)"/>
<line x1="552" y1="122" x2="592" y2="122" stroke="#57606a" stroke-width="1.4" marker-end="url(#fgA)"/>
<line x1="552" y1="284" x2="592" y2="284" stroke="#57606a" stroke-width="1.4" marker-end="url(#fgA)"/>
</svg>
<figcaption>Put the MCP filter in front of MCP servers, the REST bridge in front of REST APIs, or use both. Each works on its own, and each stops bad requests before they reach your backend.</figcaption>
</figure>

## Route requests by tool name

Each tool can have its own backend. For example, `get_weather` can go to a weather service and every other tool to a default MCP server. The MCP filter writes the method and tool name into Envoy's request metadata, and a normal route can match on that metadata:

```yaml
http_filters:
- name: envoy.filters.http.mcp
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.mcp.v3.Mcp
    attribute_source: HEADERS
    clear_route_cache: true
- name: envoy.filters.http.router
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.router.v3.Router
route_config:
  virtual_hosts:
  - name: tools
    domains: ["*"]
    routes:
    - match:
        prefix: /mcp
        dynamic_metadata:
        - filter: envoy.filters.http.mcp
          path: [{key: params}, {key: name}]
          value: {string_match: {exact: get_weather}}
      route: {cluster: weather}
    - match: {prefix: /mcp}
      route: {cluster: default_mcp}
```

Two settings do the work:

- **`attribute_source: HEADERS`** tells the filter to read the tool name from the `Mcp-Name` header. Envoy can then route without waiting for the request body, so large requests are routed just as fast as small ones.
- **`clear_route_cache: true`** makes Envoy pick the route again after the filter has added the tool name.

The filter also handles the spec's encoding rules. A tool name that isn't plain ASCII is sent in Base64 form (`=?base64?…?=`), and the filter decodes it before routing. Both of these requests reach the weather backend:

```text
mcp-name: get_weather                  -> routed to weather
mcp-name: =?base64?Z2V0X3dlYXRoZXI=?=  -> routed to weather
```

`HEADERS` is the fastest mode, and it relies on your MCP servers to confirm that the headers match the body, which every 2026-07-28 server must do. If the route is also a security decision, for example when different tools have different access rules, set `attribute_source` to `BODY` (the default) or `VERIFY`. Envoy then reads the tool name from the body itself.

**Tip:** Match on the filter's metadata, as in the config above, rather than on the raw `mcp-name` header. The metadata holds the decoded name, and in `BODY` or `VERIFY` mode it always reflects what is in the request body.

## Catch bad requests at the proxy

Because routing information now appears twice, once in the headers and once in the body, a client could make the two disagree. For example, it could name an allowed tool in the header and a different tool in the body. The MCP spec requires such requests to be rejected, and Envoy does it at the proxy, so they never reach your servers.

Set `traffic_mode: REJECT_NO_MCP` on the MCP filter, and Envoy checks every 2026-07-28 request:

```console
$ curl -si localhost:10000/mcp ... -H 'mcp-protocol-version: 2026-07-28' \
    -H 'mcp-method: tools/call' -H 'mcp-name: get_weather' \
    -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"delete_everything",...}}'
HTTP/1.1 400 Bad Request

{"jsonrpc":"2.0","error":{"message":"MCP header attributes do not match request body","code":-32020},"id":4}
```

The client gets a standard MCP error (`-32020`, HeaderMismatch), so MCP SDKs can recognize it. Envoy checks that:

- the required headers are present;
- the headers match the body;
- the protocol version in the header matches the one in the body;
- the request declares the client's capabilities.

Not ready to block traffic yet? Leave `traffic_mode` at its default, `PASS_THROUGH`, with `attribute_source` set to `BODY` or `VERIFY`. Envoy then lets every request through, routes on what the body says, and counts header mismatches in the `mcp.header_mismatch` stat, so you can see what enforcement would do before you turn it on.

## Control who can call which tool

Not all tools carry the same risk. Reading the weather is harmless; deleting a record is not. The MCP filter puts the tool name into Envoy's request metadata, so Envoy's existing authorization filters can make per-tool decisions. You don't need a new policy engine. Add the authorization filter after the MCP filter in the same chain.

On the MCP filter, set `traffic_mode: REJECT_NO_MCP` and `attribute_source: VERIFY` (or `BODY`). The tool name then comes from the request body, which is the call the server will actually run. Requests whose headers disagree with the body are rejected before any policy is checked.

### RBAC: rules in config

The RBAC filter can match on the MCP filter's metadata. This rule blocks `delete_record` for any caller without the `admin` role:

```yaml
- name: envoy.filters.http.rbac
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.rbac.v3.RBAC
    rules:
      action: DENY
      policies:
        delete-requires-admin:
          permissions:
          - metadata:
              filter: envoy.filters.http.mcp
              path: [{key: params}, {key: name}]
              value: {string_match: {exact: delete_record}}
          principals:
          - not_id:
              header: {name: x-user-role, string_match: {exact: admin}}
```

Calling each tool with and without the role gives:

```text
tools/call get_weather                        -> 200, reaches the MCP server
tools/call delete_record                      -> 403  RBAC: access denied
tools/call delete_record (x-user-role: admin) -> 200, reaches the MCP server
```

Spoofing the header doesn't get around the rule. A request that names `get_weather` in `Mcp-Name` but `delete_record` in the body is rejected with `-32020` before RBAC runs. A request that claims an older protocol version is still denied, because the tool name comes from the body.

In production, set the role from a verified identity, for example a claim in a JWT validated by Envoy's JWT authentication filter. Never use a value the client sends directly.

### ext_authz: decisions in your own service

When the decision depends on more than static rules, such as user entitlements, tenant limits, or an approval step, use the external authorization filter with a gRPC authorization service. `metadata_context_namespaces` tells Envoy to include the MCP filter's metadata in each check:

```yaml
- name: envoy.filters.http.ext_authz
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.ext_authz.v3.ExtAuthz
    transport_api_version: V3
    grpc_service:
      envoy_grpc: {cluster_name: authz}
    metadata_context_namespaces: [envoy.filters.http.mcp]
```

For a `delete_record` call, the authorization service receives this metadata alongside the usual request attributes:

```json
{
  "method": "tools/call",
  "params": {
    "name": "delete_record",
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientCapabilities": {}
    }
  },
  "id": 1,
  "jsonrpc": "2.0",
  "is_mcp_request": true,
  "status": "mcp_ok"
}
```

The service reads `params.name`, applies its own logic, and answers allow or deny. A small test service that requires the admin role for `delete_record` produced:

```text
tools/call get_weather                        -> 200, reaches the MCP server
tools/call delete_record                      -> 403  tool delete_record requires the admin role
tools/call delete_record (x-user-role: admin) -> 200, reaches the MCP server
```

## Turn a REST API into an MCP server

Many tools you want to give an agent already exist as REST APIs. The MCP REST bridge lets Envoy present them as an MCP server, with no changes to the API:

```yaml
- name: envoy.filters.http.mcp_json_rest_bridge
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.mcp_json_rest_bridge.v3.McpJsonRestBridge
    server_info:
      max_supported_protocol_version: "2026-07-28"
      server_discovery_cache_config:
        ttl: 3600s
    tool_config:
      tool_list_local: {}
      tools:
      - name: get_weather
        http_rule:
          get: /v1/weather/{city}
        tool_list_config:
          description: Current weather for a city.
          input_schema: '{"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}'
```

This config defines one tool, `get_weather`. When an agent calls it with `{"city": "Paris"}`, Envoy sends `GET /v1/weather/Paris` to your API and wraps the JSON response in an MCP result:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [{"type": "text", "text": "{\"city\":\"Paris\",\"temp_c\":18}"}],
    "isError": false,
    "resultType": "complete"
  }
}
```

Envoy also takes care of the rest of the new protocol for you:

- it answers `tools/list` from the config, so agents can discover the tool;
- it answers `server/discover`, the new call clients use to learn what a server supports;
- it checks the MCP headers against the body itself, so it doesn't need the MCP filter in front of it.

A `server/discover` call returns the supported protocol versions, the server's capabilities, and a hint that clients may cache the answer for an hour:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "supportedVersions": ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "2026-07-28"],
    "capabilities": {"tools": {"listChanged": false}},
    "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "mcp.example.com", "version": "1.0.0"}},
    "ttlMs": 3600000,
    "cacheScope": "public",
    "resultType": "complete"
  }
}
```

## Serve old and new clients together

Not every client will move to the new revision at once, and you don't have to choose between them. One Envoy listener serves both:

- Clients on 2026-07-28 get the stateless behavior described above.
- Clients on older versions keep their `initialize` handshake and sessions, and work exactly as before.

You decide when to adopt the new revision with one setting, `max_supported_protocol_version`. On the REST bridge, setting it to `"2026-07-28"` turns the new behavior on. On the MCP filter, it caps the versions you accept. A client that asks for a version you don't support gets a clear error listing the versions you do:

```json
{"jsonrpc":"2.0","id":null,"error":{"code":-32022,"message":"Unsupported MCP protocol version: 2027-01-01",
 "data":{"requested":"2027-01-01","supported":["2024-11-05","2025-03-26","2025-06-18","2025-11-25","2026-07-28"]}}}
```

## Try it

Save this as `envoy.yaml`. It runs both extensions in one filter chain to show them together, though each also works on its own. A fixed response stands in for the weather API:

```yaml
static_resources:
  listeners:
  - name: mcp
    address:
      socket_address: {address: 0.0.0.0, port_value: 10000}
    filter_chains:
    - filters:
      - name: envoy.filters.network.http_connection_manager
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
          stat_prefix: mcp
          http_filters:
          - name: envoy.filters.http.mcp
            typed_config:
              "@type": type.googleapis.com/envoy.extensions.filters.http.mcp.v3.Mcp
              traffic_mode: REJECT_NO_MCP
              attribute_source: VERIFY
              max_supported_protocol_version: "2026-07-28"
          - name: envoy.filters.http.mcp_json_rest_bridge
            typed_config:
              "@type": type.googleapis.com/envoy.extensions.filters.http.mcp_json_rest_bridge.v3.McpJsonRestBridge
              server_info:
                max_supported_protocol_version: "2026-07-28"
                server_discovery_cache_config:
                  ttl: 3600s
              tool_config:
                tool_list_local: {}
                tools:
                - name: get_weather
                  http_rule:
                    get: /v1/weather/{city}
                  tool_list_config:
                    description: Current weather for a city.
                    input_schema: '{"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}'
          - name: envoy.filters.http.router
            typed_config:
              "@type": type.googleapis.com/envoy.extensions.filters.http.router.v3.Router
          route_config:
            virtual_hosts:
            - name: weather
              domains: ["*"]
              routes:
              - match: {prefix: /v1/weather/}
                direct_response:
                  status: 200
                  body: {inline_string: '{"city":"Paris","temp_c":18}'}
                response_headers_to_add:
                - header: {key: content-type, value: application/json}
              - match: {prefix: /}
                direct_response: {status: 404}
```

Start Envoy:

```bash
docker run --rm -p 10000:10000 envoyproxy/envoy:v1.40.0 --config-yaml "$(cat envoy.yaml)"
```

Call the tool:

```bash
curl -s localhost:10000/mcp \
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
  -H 'mcp-protocol-version: 2026-07-28' -H 'mcp-method: tools/call' -H 'mcp-name: get_weather' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_weather","arguments":{"city":"Paris"},"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}'
```

Then change `mcp-name` to another tool, or remove the `mcp-name` header entirely, and watch Envoy reject the request.

The MCP extensions are marked alpha in 1.40. Try them out, and share feedback on [GitHub](https://github.com/envoyproxy/envoy/issues).

## Learn more

- [MCP 2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog) and [Streamable HTTP transport](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
- [SEP-2575: Make MCP stateless](https://modelcontextprotocol.io/seps/2575-stateless-mcp)
- [Envoy 1.40.0 release notes](https://www.envoyproxy.io/docs/envoy/v1.40.0/version_history/v1.40/v1.40.0) and [MCP filter docs](https://www.envoyproxy.io/docs/envoy/v1.40.0/configuration/http/http_filters/mcp_filter)
- Envoy authorization: [RBAC filter](https://www.envoyproxy.io/docs/envoy/v1.40.0/configuration/http/http_filters/rbac_filter) and [external authorization filter](https://www.envoyproxy.io/docs/envoy/v1.40.0/configuration/http/http_filters/ext_authz_filter)
