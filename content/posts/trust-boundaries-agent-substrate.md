---
title: "Share Mechanisms Broadly, Share Authority Narrowly"
date: 2026-07-25T14:00:00+08:00
draft: false
tags: ["security", "ai-agents", "infrastructure", "multi-tenancy", "envoy", "distributed-systems", "learning-notes"]
summary: "Learning notes: a sentence I believed about TLS interception was wrong, and working out why settled a separate architecture question — whether the L7 proxy belongs on every node."
---

*These are learning notes rather than settled advice. I am working through how trust boundaries should sit in a multi-tenant agent platform, and writing it down is how I find the holes. Some of it will turn out to be wrong, as the first half of this post demonstrates.*

I spent a while convinced of a sentence that turned out to be wrong. Working out why settled an architecture question I had been circling separately, about where the L7 proxy should run. The two turned out to be the same question, which is why they are in the same post.

## The sentence

The setup is a platform where agent sessions migrate between machines many times a day. A gateway sits in front of them and terminates the agent's TLS connection so it can read the request, work out where that session currently lives, and forward it there. By any honest definition the gateway is a man in the middle. But it never holds a session's private key, and on that basis I wrote something close to:

> The gateway is trusted with availability, and trusted with nothing about identity.

It does not survive contact with what a terminating proxy actually does.

A proxy that terminates TLS and re-originates is not passing your connection along. It is an endpoint on both sides: facing the agent it behaves exactly like the destination server, and facing the destination it behaves exactly like the agent. In between there is a moment where the request exists as plaintext in that process's memory. From there the gateway can read everything, rewrite requests, drop or reorder them, fabricate responses and attribute them to the agent, and — unless client identity is separately signed and bound to each request — impersonate clients to the agents themselves.

<figure>
<svg width="100%" viewBox="0 0 720 336" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>Passthrough versus authorized interception</title>
<desc>In passthrough, one TLS session runs from the agent to the origin and the proxy forwards bytes it cannot read. In authorized interception the proxy terminates one TLS session and originates a second, so the request exists as plaintext inside the proxy.</desc>
<defs>
<marker id="m1g" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#57606a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker>
<marker id="m1r" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#B22B2B" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></marker>
</defs>
<rect x="1" y="1" width="718" height="334" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<text x="24" y="34" font-family="system-ui,sans-serif" font-size="13" font-weight="600" fill="#085041">Passthrough</text>
<line x1="150" y1="62" x2="570" y2="62" stroke="#0F6E56" stroke-width="2"/>
<line x1="150" y1="56" x2="150" y2="68" stroke="#0F6E56" stroke-width="2"/>
<line x1="570" y1="56" x2="570" y2="68" stroke="#0F6E56" stroke-width="2"/>
<text x="360" y="52" font-family="system-ui,sans-serif" font-size="11.5" fill="#0F6E56" text-anchor="middle">one TLS session, encrypted end to end</text>
<rect x="30" y="80" width="115" height="52" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="87" y="111" font-family="system-ui,sans-serif" font-size="12.5" fill="#1f2328" text-anchor="middle">agent</text>
<rect x="300" y="80" width="130" height="52" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="365" y="103" font-family="system-ui,sans-serif" font-size="12.5" font-weight="600" fill="#085041" text-anchor="middle">proxy</text>
<text x="365" y="120" font-family="system-ui,sans-serif" font-size="10.5" fill="#0F6E56" text-anchor="middle">forwards opaque bytes</text>
<rect x="575" y="80" width="115" height="52" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="632" y="111" font-family="system-ui,sans-serif" font-size="12.5" fill="#1f2328" text-anchor="middle">origin</text>
<line x1="149" y1="106" x2="296" y2="106" stroke="#57606a" stroke-width="1.3"/>
<line x1="434" y1="106" x2="571" y2="106" stroke="#57606a" stroke-width="1.3"/>
<line x1="24" y1="162" x2="696" y2="162" stroke="#d0d7de"/>
<text x="24" y="192" font-family="system-ui,sans-serif" font-size="13" font-weight="600" fill="#8C1D1D">Authorized interception</text>
<rect x="30" y="226" width="115" height="52" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="87" y="257" font-family="system-ui,sans-serif" font-size="12.5" fill="#1f2328" text-anchor="middle">agent</text>
<rect x="295" y="216" width="140" height="72" rx="6" fill="#FDF0EF" stroke="#B22B2B"/>
<text x="365" y="240" font-family="system-ui,sans-serif" font-size="12.5" font-weight="600" fill="#8C1D1D" text-anchor="middle">proxy</text>
<text x="365" y="259" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#B22B2B" text-anchor="middle">plaintext lives here</text>
<text x="365" y="276" font-family="system-ui,sans-serif" font-size="10.5" fill="#8C1D1D" text-anchor="middle">endpoint of both sides</text>
<rect x="575" y="226" width="115" height="52" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="632" y="257" font-family="system-ui,sans-serif" font-size="12.5" fill="#1f2328" text-anchor="middle">origin</text>
<line x1="149" y1="252" x2="291" y2="252" stroke="#B22B2B" stroke-width="2" marker-end="url(#m1r)"/>
<text x="220" y="242" font-family="system-ui,sans-serif" font-size="11" fill="#8C1D1D" text-anchor="middle">TLS #1</text>
<line x1="439" y1="252" x2="571" y2="252" stroke="#B22B2B" stroke-width="2" marker-end="url(#m1r)"/>
<text x="505" y="242" font-family="system-ui,sans-serif" font-size="11" fill="#8C1D1D" text-anchor="middle">TLS #2</text>
<text x="360" y="314" font-family="system-ui,sans-serif" font-size="11.5" fill="#57606a" text-anchor="middle">two separate sessions — the proxy terminates one and originates the other</text>
</svg>
<figcaption>The whole argument turns on the bottom row. Interception is not one connection being observed; it is two connections joined by a process that holds your request in the clear.</figcaption>
</figure>

So a per-session certificate prevents exactly one thing. The gateway cannot go somewhere else and claim to be tenant 42's agent. That is worth having, but it is a much smaller claim than the one I made. I had conflated "cannot steal the agent's credential", which is true, with "cannot impersonate the agent at the application boundary", which is not.

The version I would defend now:

> The gateway is trusted for the confidentiality and integrity of the traffic it terminates. What it is denied is credential authority. Compromise it and the attacker controls traffic passing through that gateway, but gains no portable identity and no reach into sessions that never traversed it.

The distinction that survives is not how much power the gateway has over a request in front of it, but whether that power is exportable — usable later, usable elsewhere, usable against sessions it never saw. Scoping credentials per session does nothing to shrink in-path authority. It stops in-path authority from becoming portable authority. That turns out to be the same axis that decides where the proxy should run, which I'll come back to.

If you want the stronger property, transport security cannot give it to you, because the gateway is an endpoint of that transport. You have to sign above it: the client signs method, target identity, body hash, nonce and expiry; the agent verifies that itself rather than taking the gateway's word for it; the agent signs its response bound to the request ID. One detail is easy to miss, which is that replay protection has to survive checkpoint and restore, since restoring an older snapshot silently reopens a replay window you thought was closed. This costs latency and SDK surface, and deciding not to pay for it is reasonable. Describing your system as though you had paid for it is not.

## Three principals

Part of why the bad sentence was easy to write is vagueness about whose identity is being checked. On the single hop from gateway to worker there are three principals, not one, and they are checked in different directions.

| Principal | Its credential proves | Checked by |
|---|---|---|
| Gateway | "I am an authorized platform hop" | The worker, and the client downstream |
| Agent | "This instance represents session X" | The gateway, and peer agents |
| Client | "This request came from principal Y" | The agent, or an authorization service |

The worker validates the gateway's identity; the gateway validates that the server it reached presents the expected session identity; the agent separately validates forwarded client authorization. Collapsing these produces sentences like "the worker validates the session certificate", which has the direction backwards — the session lives on the worker, so it is the thing being authenticated, not the thing authenticating.

<figure>
<svg width="100%" viewBox="0 0 720 306" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>Three principals and the direction each is checked in</title>
<desc>Client, gateway and worker sit in a line. The gateway checks that the worker presents the expected session identity. The worker checks that the gateway is an authorized hop. The agent on the worker checks the forwarded client authorization all the way back to the client.</desc>
<defs>
<marker id="m2p" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#534AB7" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></marker>
<marker id="m2a" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#BC6C1E" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></marker>
<marker id="m2g" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#0F6E56" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></marker>
</defs>
<rect x="1" y="1" width="718" height="304" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<path d="M360 74 Q495 26 630 74" fill="none" stroke="#534AB7" stroke-width="1.8" marker-end="url(#m2p)"/>
<circle cx="495" cy="41" r="11" fill="#534AB7"/>
<text x="495" y="45" font-family="system-ui,sans-serif" font-size="11" font-weight="700" fill="#fff" text-anchor="middle">2</text>
<text x="495" y="20" font-family="system-ui,sans-serif" font-size="11" fill="#3C3489" text-anchor="middle">are you really session X?</text>
<rect x="30" y="78" width="120" height="58" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="90" y="105" font-family="system-ui,sans-serif" font-size="12.5" font-weight="600" fill="#1f2328" text-anchor="middle">client</text>
<text x="90" y="122" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">end user</text>
<rect x="290" y="78" width="140" height="58" rx="6" fill="#f6f8fa" stroke="#57606a"/>
<text x="360" y="105" font-family="system-ui,sans-serif" font-size="12.5" font-weight="600" fill="#1f2328" text-anchor="middle">gateway</text>
<text x="360" y="122" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">terminates TLS</text>
<rect x="570" y="78" width="120" height="58" rx="6" fill="#EEEDFE" stroke="#534AB7"/>
<text x="630" y="105" font-family="system-ui,sans-serif" font-size="12.5" font-weight="600" fill="#3C3489" text-anchor="middle">worker</text>
<text x="630" y="122" font-family="system-ui,sans-serif" font-size="10.5" fill="#5A51C4" text-anchor="middle">agent, session X</text>
<line x1="152" y1="107" x2="286" y2="107" stroke="#57606a" stroke-width="1.3"/>
<line x1="432" y1="107" x2="566" y2="107" stroke="#57606a" stroke-width="1.3"/>
<path d="M630 140 Q495 188 360 140" fill="none" stroke="#BC6C1E" stroke-width="1.8" marker-end="url(#m2a)"/>
<circle cx="495" cy="173" r="11" fill="#BC6C1E"/>
<text x="495" y="177" font-family="system-ui,sans-serif" font-size="11" font-weight="700" fill="#fff" text-anchor="middle">1</text>
<text x="495" y="203" font-family="system-ui,sans-serif" font-size="11" fill="#7A4410" text-anchor="middle">are you an authorized hop?</text>
<path d="M600 146 Q345 268 90 146" fill="none" stroke="#0F6E56" stroke-width="1.8" stroke-dasharray="5 4" marker-end="url(#m2g)"/>
<circle cx="345" cy="238" r="11" fill="#0F6E56"/>
<text x="345" y="242" font-family="system-ui,sans-serif" font-size="11" font-weight="700" fill="#fff" text-anchor="middle">3</text>
<text x="345" y="268" font-family="system-ui,sans-serif" font-size="11" fill="#085041" text-anchor="middle">did this request really come from user Y?</text>
<text x="360" y="292" font-family="system-ui,sans-serif" font-size="11" font-style="italic" fill="#57606a" text-anchor="middle">one hop, three checks, three different directions</text>
</svg>
<figcaption>Arrows point from the party doing the checking toward the party being checked. Checks 1 and 2 run in opposite directions across the same hop, which is what makes them easy to conflate. Check 3 is dashed because transport cannot provide it — it needs evidence signed by the client and verified by the agent.</figcaption>
</figure> Most identity-confusion bugs I have seen are one of these rows quietly doing double duty, usually a host identity standing in for a workload identity, or a tenant ID read from an HTTP header standing in for one bound to the tunnel.

While I am correcting myself: I had also described tampering with a checkpointed snapshot as a man-in-the-middle attack on memory. It is a good image but a bad threat model, because it hides the fact that signatures do not fix all of it. An attacker who swaps generation 50 of a snapshot for generation 42 has not forged anything. Every digest verifies, every signature checks out, and the agent wakes into an authentic version of its own past, one where a revoked credential still worked. Integrity was never violated; freshness was. Snapshot manifests therefore have to bind a monotonic generation, checked against an authority that is not the storage itself.

<figure>
<svg width="100%" viewBox="0 0 720 244" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>Rollback of a validly signed snapshot</title>
<desc>Snapshot generations 42, 46 and 50 all carry valid signatures. An attacker with write access serves generation 42 instead of the current generation 50, and the restore succeeds because every signature verifies.</desc>
<defs>
<marker id="m3r" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#B22B2B" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></marker>
<marker id="m3g" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#8c959f" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></marker>
</defs>
<rect x="1" y="1" width="718" height="242" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<text x="24" y="32" font-family="system-ui,sans-serif" font-size="12" fill="#57606a">snapshot history in object storage</text>
<line x1="40" y1="96" x2="470" y2="96" stroke="#d0d7de" stroke-width="2"/>
<rect x="40" y="70" width="104" height="52" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="92" y="92" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328" text-anchor="middle">gen 42</text>
<text x="92" y="110" font-family="system-ui,sans-serif" font-size="10.5" fill="#0F6E56" text-anchor="middle">signed ✓</text>
<rect x="196" y="70" width="104" height="52" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="248" y="92" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328" text-anchor="middle">gen 46</text>
<text x="248" y="110" font-family="system-ui,sans-serif" font-size="10.5" fill="#0F6E56" text-anchor="middle">signed ✓</text>
<rect x="352" y="70" width="104" height="52" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="404" y="92" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#085041" text-anchor="middle">gen 50</text>
<text x="404" y="110" font-family="system-ui,sans-serif" font-size="10.5" fill="#0F6E56" text-anchor="middle">signed ✓ · current</text>
<rect x="560" y="66" width="130" height="60" rx="6" fill="#EEEDFE" stroke="#534AB7"/>
<text x="625" y="90" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#3C3489" text-anchor="middle">restore</text>
<text x="625" y="108" font-family="system-ui,sans-serif" font-size="10.5" fill="#5A51C4" text-anchor="middle">verify, then wake</text>
<path d="M456 84 Q510 70 556 82" fill="none" stroke="#8c959f" stroke-width="1.4" stroke-dasharray="5 4" marker-end="url(#m3g)"/>
<text x="506" y="60" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">expected</text>
<path d="M92 128 Q330 208 556 116" fill="none" stroke="#B22B2B" stroke-width="2" marker-end="url(#m3r)"/>
<text x="320" y="196" font-family="system-ui,sans-serif" font-size="11.5" font-weight="600" fill="#8C1D1D" text-anchor="middle">attacker with write access serves gen 42 instead</text>
<text x="360" y="226" font-family="system-ui,sans-serif" font-size="11.5" fill="#57606a" text-anchor="middle">the restore succeeds — nothing was forged, so every signature still verifies</text>
</svg>
<figcaption>Signatures answer "what is this?" and cannot answer "is this the newest?". Closing the gap needs a monotonic generation checked against a committed-checkpoint authority that the storage layer does not control.</figcaption>
</figure>

## Where this lands architecturally

Everything above is about how much a TLS-terminating component is trusted. That answers a question which looks unrelated: where it should run. The connection is one sentence. If a gateway is fully trusted for the traffic it terminates, then the population whose traffic it terminates is the population that shares its fate.

The pattern worth borrowing is the one sidecar-less meshes converged on, though not for the reason it usually gets cited. People summarize it as one proxy per node instead of one per pod, which misses the part that matters. The important move is the split: a node-local L4 layer handling identity, capture and secure transport, and a separate L7 layer handling TLS termination, protocol policy and routing.

That split is about function, not about which binary you run. Envoy is entirely capable of the L4 role — TCP proxy and network filters, no HTTP connection manager, no termination of application TLS — and using it in both places keeps one data plane, one configuration language and one operational story across the platform, which is worth a lot in practice. Some meshes ship a smaller purpose-built proxy for the node instead, and the argument for that is a reduced attack surface on the component running on every host. It is a reasonable hardening choice, but a second-order one. What actually constrains the blast radius is what the node-local component is permitted to do: give Envoy an L4-only configuration, no interception CA, and no application TLS termination, and it holds exactly the same narrow authority as any purpose-built alternative. The scope is the control; the binary is an implementation detail.

The second detail is that the tunnel carries the application stream opaquely. Wrap the workload's TCP stream in an HTTP/2 CONNECT tunnel protected by mTLS, which is the usual construction, and if the application inside is speaking HTTPS then that inner session stays encrypted straight through the node layer.

Which gives the correction I find myself repeating most often: mTLS is not MITM. Mesh mTLS protects the link and tells you which workload is talking. Enforcing anything about HTTP paths, tool names, model IDs or token budgets requires plaintext, which means something was explicitly authorized to terminate the application's own TLS. Every "we have mTLS, so we have visibility into agent traffic" claim dies there.

Now the placement argument. A bare-metal node already owns a large failure domain — compromise it and you get every sandbox on it, its local identities, its cached state. That is unavoidable; it is the physics of sharing a host. Authorized TLS termination owns a different failure domain: the plaintext of every tenant whose traffic it decrypts, plus the interception CA material and private keys it holds to do so. Put a full L7 interception proxy on every node and those two domains merge. Every host compromise becomes a plaintext and key compromise for whichever tenants happened to be scheduled there — and placement is a scheduling decision, not a security decision, which means you have handed your trust boundaries to the scheduler, and it will redraw them on every rebalance.

<figure>
<svg width="100%" viewBox="0 0 760 430" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:760px">
<title>Layered agent platform: node-local L4 identity, sharded L7 termination</title>
<desc>Bare-metal hosts run microVM sandboxes and a small node-local L4 agent that binds tenant identity and builds an authenticated tunnel. Tunnels route to tenant-affine Envoy L7 shards, which are the only place application TLS is terminated and interception keys are held.</desc>
<defs>
<marker id="a2" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#57606a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker>
</defs>
<rect x="1" y="1" width="758" height="428" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<rect x="24" y="46" width="220" height="200" rx="8" fill="none" stroke="#8c959f" stroke-dasharray="6 4"/>
<text x="38" y="70" font-family="system-ui,sans-serif" font-size="13" font-weight="600" fill="#1f2328">bare-metal host</text>
<rect x="42" y="84" width="184" height="40" rx="6" fill="#EEEDFE" stroke="#534AB7"/>
<text x="134" y="102" font-family="system-ui,sans-serif" font-size="12" fill="#3C3489" text-anchor="middle">microVM sandbox</text>
<text x="134" y="117" font-family="system-ui,sans-serif" font-size="11" fill="#5A51C4" text-anchor="middle">tenant 42 · agent · execution</text>
<rect x="42" y="132" width="184" height="34" rx="6" fill="#EEEDFE" stroke="#534AB7"/>
<text x="134" y="153" font-family="system-ui,sans-serif" font-size="12" fill="#3C3489" text-anchor="middle">microVM sandbox</text>
<rect x="42" y="180" width="184" height="50" rx="6" fill="#f6f8fa" stroke="#57606a"/>
<text x="134" y="200" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328" text-anchor="middle">node L4 agent</text>
<text x="134" y="216" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">identity · capture · tunnel</text>
<text x="134" y="264" font-family="system-ui,sans-serif" font-size="11" font-style="italic" fill="#0F6E56" text-anchor="middle">no plaintext · no CA keys</text>
<line x1="248" y1="205" x2="322" y2="205" stroke="#57606a" stroke-width="1.5" marker-end="url(#a2)"/>
<text x="285" y="196" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">authenticated</text>
<text x="285" y="222" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">tunnel</text>
<rect x="326" y="46" width="216" height="230" rx="8" fill="#FDF0EF" stroke="#B22B2B"/>
<text x="434" y="70" font-family="system-ui,sans-serif" font-size="13" font-weight="600" fill="#8C1D1D" text-anchor="middle">L7 shard (tenant-affine)</text>
<rect x="344" y="84" width="180" height="46" rx="6" fill="#ffffff" stroke="#B22B2B"/>
<text x="434" y="103" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#1f2328" text-anchor="middle">Envoy</text>
<text x="434" y="119" font-family="system-ui,sans-serif" font-size="10.5" fill="#57606a" text-anchor="middle">terminates app TLS</text>
<rect x="344" y="140" width="180" height="34" rx="6" fill="#ffffff" stroke="#d0d7de"/>
<text x="434" y="161" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328" text-anchor="middle">policy / quota filters</text>
<rect x="344" y="182" width="180" height="34" rx="6" fill="#ffffff" stroke="#d0d7de"/>
<text x="434" y="203" font-family="system-ui,sans-serif" font-size="11" fill="#1f2328" text-anchor="middle">upstream TLS origination</text>
<text x="434" y="240" font-family="system-ui,sans-serif" font-size="11" font-style="italic" fill="#8C1D1D" text-anchor="middle">plaintext + keys live here</text>
<text x="434" y="258" font-family="system-ui,sans-serif" font-size="11" font-style="italic" fill="#8C1D1D" text-anchor="middle">50–100 tenants, bounded</text>
<rect x="576" y="46" width="160" height="40" rx="6" fill="#FFF4E5" stroke="#BC6C1E"/>
<text x="656" y="71" font-family="system-ui,sans-serif" font-size="11.5" fill="#7A4410" text-anchor="middle">certificate svc (SDS)</text>
<rect x="576" y="96" width="160" height="40" rx="6" fill="#FFF4E5" stroke="#BC6C1E"/>
<text x="656" y="121" font-family="system-ui,sans-serif" font-size="11.5" fill="#7A4410" text-anchor="middle">policy service</text>
<rect x="576" y="146" width="160" height="40" rx="6" fill="#FFF4E5" stroke="#BC6C1E"/>
<text x="656" y="171" font-family="system-ui,sans-serif" font-size="11.5" fill="#7A4410" text-anchor="middle">credential broker</text>
<line x1="546" y1="100" x2="572" y2="80" stroke="#BC6C1E" stroke-width="1.3" stroke-dasharray="4 3" marker-end="url(#a2)"/>
<line x1="546" y1="155" x2="572" y2="135" stroke="#BC6C1E" stroke-width="1.3" stroke-dasharray="4 3" marker-end="url(#a2)"/>
<line x1="546" y1="195" x2="572" y2="180" stroke="#BC6C1E" stroke-width="1.3" stroke-dasharray="4 3" marker-end="url(#a2)"/>
<rect x="576" y="216" width="160" height="46" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="656" y="235" font-family="system-ui,sans-serif" font-size="11.5" font-weight="600" fill="#085041" text-anchor="middle">model providers</text>
<text x="656" y="251" font-family="system-ui,sans-serif" font-size="11" fill="#0F6E56" text-anchor="middle">tool servers</text>
<line x1="546" y1="230" x2="572" y2="235" stroke="#57606a" stroke-width="1.5" marker-end="url(#a2)"/>
<line x1="24" y1="310" x2="736" y2="310" stroke="#d0d7de"/>
<text x="380" y="338" font-family="system-ui,sans-serif" font-size="12.5" font-weight="600" fill="#1f2328" text-anchor="middle">Two blast radii, kept apart</text>
<text x="380" y="362" font-family="system-ui,sans-serif" font-size="11.5" fill="#57606a" text-anchor="middle">host compromise → that node's sandboxes and its L4 identity scope</text>
<text x="380" y="382" font-family="system-ui,sans-serif" font-size="11.5" fill="#57606a" text-anchor="middle">shard compromise → that shard's tenants' plaintext and keys</text>
<text x="380" y="406" font-family="system-ui,sans-serif" font-size="11.5" font-weight="600" fill="#8C1D1D" text-anchor="middle">a per-node L7 proxy would make each imply the other</text>
</svg>
<figcaption>Node-local components hold no plaintext and no CA material. Termination is concentrated in tenant-affine shards whose blast radius is a placement decision rather than a scheduling accident.</figcaption>
</figure>

## Ingress and egress are not the same problem

I have been writing "the L7 tier" as though it were one thing. It is two, and almost everything that matters differs between them.

Egress is an agent calling out — a model provider, a tool server, some URL it discovered at runtime. The destination is not yours, frequently is not known until the request arrives, and the client is code you do not trust. This is where interception actually happens: you terminate the agent's TLS to a third-party hostname by presenting a certificate minted under a private CA that only your own sandbox images trust.

Ingress is the outside calling in — a user's browser or application reaching an agent session that may currently be asleep. The destination is yours. You terminate TLS for your own domain using an ordinary public certificate, which is not interception at all, because you own the name.

<figure>
<svg width="100%" viewBox="0 0 720 336" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>Ingress and egress paths and their separate certificate authorities</title>
<desc>On ingress, an external user reaches an edge gateway holding a public certificate for your own domain, which wakes and reaches the agent session. On egress, the agent leaves through the node L4 layer to an egress shard holding a private interception CA that mints certificates for third-party hostnames.</desc>
<defs>
<marker id="m4g" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#57606a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker>
</defs>
<rect x="1" y="1" width="718" height="334" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<text x="24" y="34" font-family="system-ui,sans-serif" font-size="13" font-weight="600" fill="#085041">Ingress — the outside calling in</text>
<rect x="26" y="56" width="118" height="54" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="85" y="79" font-family="system-ui,sans-serif" font-size="12" fill="#1f2328" text-anchor="middle">end user</text>
<text x="85" y="96" font-family="system-ui,sans-serif" font-size="10" fill="#57606a" text-anchor="middle">browser, app</text>
<rect x="238" y="50" width="176" height="66" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="326" y="72" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#085041" text-anchor="middle">edge gateway</text>
<text x="326" y="89" font-family="system-ui,sans-serif" font-size="10" fill="#0F6E56" text-anchor="middle">public cert for your domain</text>
<text x="326" y="105" font-family="system-ui,sans-serif" font-size="10" fill="#0F6E56" text-anchor="middle">holds request, wakes session</text>
<rect x="512" y="56" width="180" height="54" rx="6" fill="#EEEDFE" stroke="#534AB7"/>
<text x="602" y="79" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#3C3489" text-anchor="middle">agent session</text>
<text x="602" y="96" font-family="system-ui,sans-serif" font-size="10" fill="#5A51C4" text-anchor="middle">yours, possibly asleep</text>
<line x1="147" y1="83" x2="234" y2="83" stroke="#57606a" stroke-width="1.4" marker-end="url(#m4g)"/>
<line x1="417" y1="83" x2="508" y2="83" stroke="#57606a" stroke-width="1.4" marker-end="url(#m4g)"/>
<text x="326" y="140" font-family="system-ui,sans-serif" font-size="10.5" font-style="italic" fill="#57606a" text-anchor="middle">you own the name — this is ordinary termination, not interception</text>
<line x1="24" y1="160" x2="696" y2="160" stroke="#d0d7de"/>
<text x="24" y="190" font-family="system-ui,sans-serif" font-size="13" font-weight="600" fill="#8C1D1D">Egress — the agent calling out</text>
<rect x="26" y="212" width="118" height="54" rx="6" fill="#EEEDFE" stroke="#534AB7"/>
<text x="85" y="235" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#3C3489" text-anchor="middle">agent</text>
<text x="85" y="252" font-family="system-ui,sans-serif" font-size="10" fill="#5A51C4" text-anchor="middle">untrusted code</text>
<rect x="176" y="212" width="104" height="54" rx="6" fill="#f6f8fa" stroke="#57606a"/>
<text x="228" y="235" font-family="system-ui,sans-serif" font-size="11.5" fill="#1f2328" text-anchor="middle">node L4</text>
<text x="228" y="252" font-family="system-ui,sans-serif" font-size="10" fill="#0F6E56" text-anchor="middle">binds identity</text>
<rect x="312" y="206" width="188" height="66" rx="6" fill="#FDF0EF" stroke="#B22B2B"/>
<text x="406" y="228" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#8C1D1D" text-anchor="middle">egress shard</text>
<text x="406" y="245" font-family="system-ui,sans-serif" font-size="10" fill="#B22B2B" text-anchor="middle">private interception CA</text>
<text x="406" y="261" font-family="system-ui,sans-serif" font-size="10" fill="#B22B2B" text-anchor="middle">mints leaf for their hostname</text>
<rect x="536" y="212" width="156" height="54" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="614" y="235" font-family="system-ui,sans-serif" font-size="12" fill="#1f2328" text-anchor="middle">third party</text>
<text x="614" y="252" font-family="system-ui,sans-serif" font-size="10" fill="#57606a" text-anchor="middle">not yours</text>
<line x1="147" y1="239" x2="172" y2="239" stroke="#57606a" stroke-width="1.4" marker-end="url(#m4g)"/>
<line x1="283" y1="239" x2="308" y2="239" stroke="#57606a" stroke-width="1.4" marker-end="url(#m4g)"/>
<line x1="503" y1="239" x2="532" y2="239" stroke="#57606a" stroke-width="1.4" marker-end="url(#m4g)"/>
<text x="360" y="296" font-family="system-ui,sans-serif" font-size="10.5" font-style="italic" fill="#8C1D1D" text-anchor="middle">you own neither the name nor the destination — this is the interception path</text>
<text x="360" y="320" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#1f2328" text-anchor="middle">two paths, two certificate authorities, which must never be the same one</text>
</svg>
<figcaption>The certificate on the ingress path proves you are who you say you are. The certificate on the egress path asserts you are somebody else, and is trusted only because your own sandboxes were told to trust it.</figcaption>
</figure>

Three things follow. **The two paths need separate CA hierarchies**, and merging them is a serious mistake. Your ingress certificate is a public one for a name you own and is revocable through ordinary means. Your egress interception root can mint a certificate for *any* hostname that your sandboxes will accept, which is a far more dangerous capability, and it must never be trusted by anything outside those sandboxes. Keep private interception roots well away from public trust roots.

**Only egress is really MITM.** Ingress is standard edge termination. That said, the correction at the top of this post applies to both — an ingress gateway that terminates TLS is still fully trusted for that traffic, and still cannot be described as trusted with nothing. What is egress-specific is the interception machinery: a private CA, certificates minted per destination hostname, and the requirement that the client trust a root you control.

**The per-node question is really an egress question.** Nobody puts ingress on every node; it lives behind a load balancer at the edge by construction. Egress is the one where placement is genuinely open, because that traffic originates on the node and could be terminated there. So everything below is about the egress tier.

The other asymmetries fall out of the same split. Wake-on-request belongs to ingress, along with the routing invariant that a lookup returns an agent identity and a placement epoch and the gateway forwards nothing until the peer proves both. Confused-deputy risk belongs to egress: an agent that asks the proxy to fetch a link-local or metadata address is exploiting the fact that the proxy has network reach the sandbox does not, which is why destination policy has to be applied to resolved addresses rather than hostnames, and re-applied after every resolution.

## On the node, or at a remote endpoint

This is about the egress tier specifically. The L4 layer stays on the node either way, and it may well be Envoy. The question is only where the *terminating* proxy lives — the one that decrypts application TLS and holds interception keys.

Both candidates for that role are multi-tenant. Both run one Envoy process serving many tenants, so shared-versus-dedicated is not the axis people think it is. What differs is who picks the roommates and what the roommates can lose together.

| | On the node | At a remote endpoint |
|---|---|---|
| Who shares the process | Whoever the scheduler placed here | Whoever you assigned |
| Isolation tiers | Not expressible | Dedicated shards where needed |
| Private keys | On every host | Only in the L7 tier |
| Host compromise costs | Plaintext and keys for local tenants | Sandboxes only |
| Added latency | Effectively zero | One network hop |
| Failure domain | Aligned with the node | Cuts across nodes; needs replicas |
| Scaling | Proxy count tied to node count | Traffic and compute scale separately |
| Certificate working set | Same cert cached on every node | Cached once per shard |
| Rollout | Bad version reaches every host | Canary and drain one shard |

<figure>
<svg width="100%" viewBox="0 0 720 372" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>Where interception keys live under each topology</title>
<desc>With L7 on every node, every host holds interception keys, so the number of key holders equals the host count. With a sharded L7 tier, hosts hold no keys and only a small number of shards do.</desc>
<rect x="1" y="1" width="718" height="370" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<line x1="360" y1="24" x2="360" y2="290" stroke="#d0d7de" stroke-dasharray="5 4"/>
<text x="180" y="40" font-family="system-ui,sans-serif" font-size="13" font-weight="600" fill="#8C1D1D" text-anchor="middle">L7 on every node</text>
<rect x="42" y="60" width="126" height="62" rx="6" fill="#FDF0EF" stroke="#B22B2B"/>
<text x="105" y="82" font-family="system-ui,sans-serif" font-size="11.5" fill="#1f2328" text-anchor="middle">host</text>
<rect x="60" y="90" width="90" height="22" rx="4" fill="#B22B2B"/>
<text x="105" y="105" font-family="system-ui,sans-serif" font-size="10.5" font-weight="600" fill="#fff" text-anchor="middle">keys</text>
<rect x="192" y="60" width="126" height="62" rx="6" fill="#FDF0EF" stroke="#B22B2B"/>
<text x="255" y="82" font-family="system-ui,sans-serif" font-size="11.5" fill="#1f2328" text-anchor="middle">host</text>
<rect x="210" y="90" width="90" height="22" rx="4" fill="#B22B2B"/>
<text x="255" y="105" font-family="system-ui,sans-serif" font-size="10.5" font-weight="600" fill="#fff" text-anchor="middle">keys</text>
<rect x="42" y="134" width="126" height="62" rx="6" fill="#FDF0EF" stroke="#B22B2B"/>
<text x="105" y="156" font-family="system-ui,sans-serif" font-size="11.5" fill="#1f2328" text-anchor="middle">host</text>
<rect x="60" y="164" width="90" height="22" rx="4" fill="#B22B2B"/>
<text x="105" y="179" font-family="system-ui,sans-serif" font-size="10.5" font-weight="600" fill="#fff" text-anchor="middle">keys</text>
<rect x="192" y="134" width="126" height="62" rx="6" fill="#FDF0EF" stroke="#B22B2B"/>
<text x="255" y="156" font-family="system-ui,sans-serif" font-size="11.5" fill="#1f2328" text-anchor="middle">host</text>
<rect x="210" y="164" width="90" height="22" rx="4" fill="#B22B2B"/>
<text x="255" y="179" font-family="system-ui,sans-serif" font-size="10.5" font-weight="600" fill="#fff" text-anchor="middle">keys</text>
<text x="180" y="222" font-family="system-ui,sans-serif" font-size="11.5" fill="#57606a" text-anchor="middle">… every host in the fleet</text>
<text x="180" y="252" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#8C1D1D" text-anchor="middle">key holders = host count</text>
<text x="180" y="272" font-family="system-ui,sans-serif" font-size="11" fill="#8C1D1D" text-anchor="middle">tenant mix chosen by the scheduler</text>
<text x="540" y="40" font-family="system-ui,sans-serif" font-size="13" font-weight="600" fill="#085041" text-anchor="middle">L7 in shards</text>
<rect x="402" y="60" width="126" height="46" rx="6" fill="#f6f8fa" stroke="#0F6E56"/>
<text x="465" y="79" font-family="system-ui,sans-serif" font-size="11.5" fill="#1f2328" text-anchor="middle">host</text>
<text x="465" y="96" font-family="system-ui,sans-serif" font-size="10" fill="#0F6E56" text-anchor="middle">no keys</text>
<rect x="552" y="60" width="126" height="46" rx="6" fill="#f6f8fa" stroke="#0F6E56"/>
<text x="615" y="79" font-family="system-ui,sans-serif" font-size="11.5" fill="#1f2328" text-anchor="middle">host</text>
<text x="615" y="96" font-family="system-ui,sans-serif" font-size="10" fill="#0F6E56" text-anchor="middle">no keys</text>
<rect x="402" y="118" width="126" height="46" rx="6" fill="#f6f8fa" stroke="#0F6E56"/>
<text x="465" y="137" font-family="system-ui,sans-serif" font-size="11.5" fill="#1f2328" text-anchor="middle">host</text>
<text x="465" y="154" font-family="system-ui,sans-serif" font-size="10" fill="#0F6E56" text-anchor="middle">no keys</text>
<rect x="552" y="118" width="126" height="46" rx="6" fill="#f6f8fa" stroke="#0F6E56"/>
<text x="615" y="137" font-family="system-ui,sans-serif" font-size="11.5" fill="#1f2328" text-anchor="middle">host</text>
<text x="615" y="154" font-family="system-ui,sans-serif" font-size="10" fill="#0F6E56" text-anchor="middle">no keys</text>
<rect x="402" y="182" width="276" height="52" rx="6" fill="#FDF0EF" stroke="#B22B2B"/>
<text x="540" y="203" font-family="system-ui,sans-serif" font-size="11.5" font-weight="600" fill="#8C1D1D" text-anchor="middle">L7 shard</text>
<rect x="495" y="210" width="90" height="20" rx="4" fill="#B22B2B"/>
<text x="540" y="224" font-family="system-ui,sans-serif" font-size="10.5" font-weight="600" fill="#fff" text-anchor="middle">keys</text>
<text x="540" y="252" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#085041" text-anchor="middle">key holders = shard count</text>
<text x="540" y="272" font-family="system-ui,sans-serif" font-size="11" fill="#085041" text-anchor="middle">tenant mix chosen by you</text>
<line x1="24" y1="304" x2="696" y2="304" stroke="#d0d7de"/>
<text x="360" y="330" font-family="system-ui,sans-serif" font-size="11.5" fill="#57606a" text-anchor="middle">Failure containment improves as you split into more, smaller units.</text>
<text x="360" y="352" font-family="system-ui,sans-serif" font-size="11.5" font-weight="600" fill="#8C1D1D" text-anchor="middle">Compromise containment gets worse — every extra unit is another key holder.</text>
</svg>
<figcaption>The two properties pull in opposite directions, which is why "a failure only affects one node" and "a compromise only affects one node" are not the same claim.</figcaption>
</figure>

The row that matters most is the first. With a per-node L7 proxy, the set of tenants sharing a decrypting process is decided by bin-packing and re-decided on every rebalance. You cannot promise a regulated customer that their plaintext never sits in the same process as an untrusted tenant's, because you do not control who lands next door and the answer changes hourly. Remote sharding makes tenant mix an explicit and stable placement decision, which is what turns a dedicated isolation tier into something you can actually sell rather than something you hope the scheduler produces.

Certificate economics push the same way and are less obvious. Interception needs a leaf per destination hostname, and per node, every host with a tenant calling a given host fetches and caches its own copy — the same certificate held N times across N hosts, with N times the issuance load and a poor hit rate, since each node's cache is small and starts cold. Concentrate that traffic into a shard serving fifty to a hundred tenants and the certificate is fetched once and reused constantly. Upstream connection pools behave identically: agent egress converges on a handful of model providers and tool servers, so pooling wants concentration, and per-node placement fragments precisely what benefits from being pooled.

I do not want to strawman the other side, because per-node has one real advantage. When a per-node proxy dies it takes egress down for the sandboxes on that node, but those sandboxes already shared that node's fate, so the failure domain is aligned with one that already existed. A remote shard introduces a domain that cuts across nodes, and you pay for that with replicas, health checks and drain procedures. The reason I still come down on the other side is that the per-node arrangement buys its clean failure property by making every host a key holder, which is a cheap availability win traded for an expensive security loss.

Latency deserves a number rather than a shrug. An extra same-zone hop costs a few hundred microseconds. Against a model provider call taking hundreds of milliseconds that is under one percent and genuinely does not matter; against a 2 ms internal RPC it is a quarter of the budget and matters a great deal. Agent egress, which is the traffic you actually want to inspect and meter, is firmly the first kind, so latency should not be allowed to overrule the security argument here. For latency-critical east-west traffic, keep it on the node and do not intercept it.

Per-node L7 is the right answer when the premise of this post does not hold — when every workload shares one trust domain, when the traffic is latency-critical east-west rather than egress, or at the edge where there is no remote tier to route to. The test I would apply is whether the tenants sharing that process would be acceptable roommates no matter how the scheduler shuffles them. If that sentence makes you uncomfortable, you need the remote tier.

For scale, the shape I would start from is one lightweight L4 component per bare-metal host, ten to twenty L7 shards per failure domain, fifty to a hundred tenants per shard, at least two replicas each, and dedicated shards for outsized or regulated tenants. Those are a load-test hypothesis rather than a capacity claim, and I would size by handshake rate, connection count and stream duration rather than request rate, because agent traffic is dominated by long-lived streams that request-rate dashboards hide. The number is not the point anyway. The point is that the affected population is known before an incident rather than discovered during one.

## The principle

One rule generates most of the decisions above. Share mechanisms broadly, share authority narrowly.

Node-local capture, transport machinery, stateless policy binaries and public trust roots should be everywhere; they are mechanism, and duplicating them costs you nothing. Interception CA keys, provider credentials, plaintext access and control-plane mutation rights should be held by as few things as possible; they are authority, and every additional holder is another way to lose them. The same rule explains why a single global interception root is the real blast radius in these systems regardless of how carefully you shard the proxies underneath, and why on-demand certificate selection reduces the working set of private keys in a process without ever removing them.

It also explains why the correction at the top of this post and the placement argument at the bottom are the same argument. Once you accept that a TLS-terminating proxy is fully trusted for the traffic it terminates, where it runs stops being a question about latency and becomes a question about how many tenants you are willing to put inside one trust boundary. For a platform with a thousand tenants the question was never whether one proxy could technically hold a thousand certificates and routes. It probably can. The question is whether one process should own that much authority and that much shared fate.

That is where my thinking currently sits. The part I am least sure of is the revocation story for a workload that migrates faster than any network policy can follow, which I suspect is the next thing I have wrong.
