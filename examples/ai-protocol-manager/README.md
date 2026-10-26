# AI Protocol Manager examples

Configs for Envoy's AI Protocol Manager. Each one is run by an integration test on a real Envoy
against mock providers. Written against Envoy main at `5d3b0dcaf4` (2026-10-26); the AI Protocol
Manager API is work in progress, so expect changes.

- `configs/observe.yaml`: the native OpenAI (Chat Completions, Responses), Anthropic and Gemini
  APIs through one gateway. Validates, describes, reads token usage, forwards bytes unchanged.
- `configs/gateway.yaml`: any client API to any backend, one route each, Vertex AI included.
- `configs/multi-backend.yaml`: weighted split, composite cluster failover, priority group
  failover across providers with their own keys, and routing by model.
- `configs/egress.yaml`: a dynamic forward proxy for any provider. Detects the API and reads token
  usage on the cluster.
- `configs/pdp.yaml` with `pdp/`: an ext_proc decision point picks the backend and settles a token
  budget from the usage Envoy reports.

Walkthrough: https://botengyao.github.io/posts/envoy-ai-protocol-manager-examples/

## Running the tests

You need `uv`, `go` and `openssl`, and an `envoy-static` built from Envoy main. A full build works;
`build_config/extensions_build_config.bzl` is the trimmed set the tests need, which builds in about
8 minutes:

```bash
# In an Envoy checkout:
bazel build -c opt //source/exe:envoy-static \
  --override_repository=+envoy_build_config_ext+envoy_build_config=/path/to/this/build_config
# Here:
ENVOY=/path/to/envoy/bazel-bin/source/exe/envoy-static ./run_tests.sh
```

The tests change only what a local run has to: listener and admin ports, static endpoints (to the
mock, keeping the provider's name as the endpoint hostname for Host, SNI and the certificate check),
the trusted CA, and the DNS resolver of dynamic forward proxy caches. Tests through a dynamic
forward proxy put the mock's port in the Host, since they cannot listen on 443.
