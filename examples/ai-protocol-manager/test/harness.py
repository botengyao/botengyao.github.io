"""Runs a config from configs/ on a real Envoy, against the mock providers.

The config is loaded as written, with only what a local run must change:
  - listener and admin ports: free local ports;
  - static cluster endpoints: the mock, keeping the provider's name as the endpoint's hostname
    (the `pdp` cluster: the PDP's port);
  - the trusted CA: the mock's CA;
  - DFP DNS caches: a c-ares resolver that answers every name with 127.0.0.1.
"""

import http.client
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(EXAMPLES))
SYSTEM_CA = "/etc/ssl/certs/ca-certificates.crt"

CREDENTIALS = {
    "OPENAI_API_KEY": "test-openai-key",
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GEMINI_API_KEY": "test-gemini-key",
    "VERTEX_ACCESS_TOKEN": "test-vertex-token",
}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_certs(directory):
    """A throwaway CA, and a certificate it signs for every provider host the configs name."""
    ca, ca_key = f"{directory}/ca.pem", f"{directory}/ca.key"
    cert, key = f"{directory}/server.pem", f"{directory}/server.key"
    sans = "DNS:api.openai.com,DNS:api.anthropic.com,DNS:generativelanguage.googleapis.com,DNS:*.googleapis.com"
    run = lambda *args: subprocess.run(args, check=True, capture_output=True)
    run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=mock CA",
        "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign",
        "-keyout", ca_key, "-out", ca)
    run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=mock provider", "-keyout", key,
        "-out", f"{directory}/server.csr")
    with open(f"{directory}/ext.cnf", "w") as f:
        f.write(f"subjectAltName={sans}\nextendedKeyUsage=serverAuth\n")
    run("openssl", "x509", "-req", "-in", f"{directory}/server.csr", "-CA", ca, "-CAkey", ca_key,
        "-CAcreateserial", "-days", "1", "-extfile", f"{directory}/ext.cnf", "-out", cert)
    return ca, cert, key


def walk(node, visit):
    if isinstance(node, dict):
        visit(node)
        for value in node.values():
            walk(value, visit)
    elif isinstance(node, list):
        for value in node:
            walk(value, visit)


def localize(config, mock_port, dns_port, ca, pdp_port):
    resources = config["static_resources"]
    for listener in resources["listeners"]:
        listener["address"]["socket_address"] = {"address": "127.0.0.1", "port_value": free_port()}
    admin_port = free_port()
    config["admin"] = {"address": {"socket_address": {"address": "127.0.0.1", "port_value": admin_port}}}
    for cluster in resources["clusters"]:
        port = pdp_port if cluster["name"] == "pdp" else mock_port
        for locality in cluster.get("load_assignment", {}).get("endpoints", []):
            for lb_endpoint in locality["lb_endpoints"]:
                endpoint = lb_endpoint["endpoint"]
                address = endpoint["address"]["socket_address"]["address"]
                endpoint["address"]["socket_address"] = {"address": "127.0.0.1", "port_value": port}
                # Keeps the provider's name for auto_host_rewrite, SNI and certificate checks.
                endpoint.setdefault("hostname", address)

    def visit(node):
        if node.get("trusted_ca") == {"filename": SYSTEM_CA}:
            node["trusted_ca"] = {"filename": ca}
        cache = node.get("dns_cache_config")
        if isinstance(cache, dict) and "typed_dns_resolver_config" not in cache:
            cache["typed_dns_resolver_config"] = {
                "name": "envoy.network.dns_resolver.cares",
                "typed_config": {
                    "@type": "type.googleapis.com/envoy.extensions.network.dns_resolver.cares.v3.CaresDnsResolverConfig",
                    "resolvers": [{"socket_address": {"address": "127.0.0.1", "port_value": dns_port}}],
                },
            }

    walk(config, visit)
    return admin_port


class Envoy:

    def __init__(self, config_name, mock, ca, workdir, pdp_port=0):
        with open(os.path.join(EXAMPLES, "configs", config_name)) as f:
            config = yaml.safe_load(f)
        self.admin_port = localize(config, mock.port, mock.dns_port, ca, pdp_port)
        self.port = config["static_resources"]["listeners"][0]["address"]["socket_address"]["port_value"]
        self.config_path = os.path.join(workdir, config_name)
        with open(self.config_path, "w") as f:
            yaml.safe_dump(config, f, sort_keys=False)
        self.access_log = os.path.join(workdir, config_name + ".access.log")
        self.log = os.path.join(workdir, config_name + ".envoy.log")
        binary = os.environ.get("ENVOY", os.path.join(REPO, "bazel-bin/source/exe/envoy-static"))
        if not os.path.exists(binary):
            raise RuntimeError(f"no envoy-static at {binary}; set ENVOY (see README.md)")
        env = dict(os.environ, **CREDENTIALS)
        self.process = subprocess.Popen(
            [binary, "-c", self.config_path, "--use-dynamic-base-id", "--log-level", "info",
             "--component-log-level", "ai_protocol_manager:debug,filter:debug",
             "--file-flush-interval-msec", "50"],
            stdout=open(self.access_log, "w"), stderr=open(self.log, "w"), env=env)
        deadline = time.time() + 30
        while True:
            if self.process.poll() is not None:
                raise RuntimeError(f"envoy exited: {open(self.log).read()[-3000:]}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.admin_port}/ready", timeout=1) as r:
                    if r.status == 200:
                        break
            except OSError:
                pass
            if time.time() > deadline:
                raise RuntimeError("envoy did not become ready")
            time.sleep(0.1)

    def stop(self):
        self.process.terminate()
        self.process.wait(timeout=10)

    def post(self, path, body, headers=None):
        """Sends a request and returns (status, headers, body bytes)."""
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request("POST", path, data, dict({"content-type": "application/json"}, **(headers or {})))
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, payload

    def access_logs(self, count):
        """Waits for `count` access log lines and returns them parsed."""
        deadline = time.time() + 10
        while True:
            with open(self.access_log) as f:
                lines = [json.loads(line) for line in f if line.startswith("{")]
            if len(lines) >= count or time.time() > deadline:
                return lines

    def counter(self, name):
        return sum(self.counters(f"^{name}$").values())

    def counters(self, pattern):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.admin_port}/stats?filter={pattern}") as r:
            lines = r.read().decode().strip().splitlines()
        return {k: int(v) for k, v in (line.split(": ") for line in lines)}


def sse_events(payload):
    """The events of an SSE body as (name, data) pairs; data is parsed JSON when it is JSON."""
    events = []
    for block in payload.decode().replace("\r\n", "\n").split("\n\n"):
        name, data = None, None
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if data is None:
            continue
        try:
            data = json.loads(data)
        except ValueError:
            pass
        events.append((name, data))
    return events


def tokens(record):
    """input/output/total of an envoy.data.ai.v3.TokenUsage record printed by %TYPED_CEL%."""
    return tuple(int(record[k]) if k in record else None for k in ("input_tokens", "output_tokens", "total_tokens"))


def temp_dir():
    return tempfile.mkdtemp(prefix="aipm-examples-")
