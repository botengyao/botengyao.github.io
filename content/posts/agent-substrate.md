---
title: "Agent Substrate: Running a Million Sleeping Agents on Kubernetes"
date: 2026-07-11T23:30:00+08:00
draft: false
tags: ["kubernetes", "ai-agents", "infrastructure", "distributed-systems"]
summary: "A first-principles tour of the open-source Agent Substrate project — the problem it solves, the four-plane architecture it builds on top of Kubernetes, how nodes, pods, and actors relate, and the design tensions it still has to resolve."
---

*A first-principles tour of Google's open-source Agent Substrate project — the problem it solves, the architecture it builds on top of Kubernetes, and the design tensions it still has to resolve.*

*(Agent Substrate is an early-stage open-source project and not an officially supported Google product. APIs are explicitly unstable. Everything below describes the code as of mid-2026.)*

## The problem: agents are asleep 99% of the time

Think about what an AI coding agent actually does all day. A user asks it to build a todo app. It works hard for forty seconds — writes code, runs tests, starts a dev server. Then the user reads the result, thinks, has lunch, comes back tomorrow. The session is *stateful* — the agent's filesystem, terminal history, even in-memory variables all matter — and it may live for weeks. But it computes for perhaps 1% of that lifetime.

Now run a million of these on Kubernetes the obvious way: one pod per session. Three things break, in order:

1. **Idle pods still bill.** RAM is reserved whether the agent is thinking or sleeping. At 99% idle, you're paying a 100× tax.
2. **The control plane can't hold the objects.** etcd and the kube-apiserver are built for tens of thousands of long-lived resources, not millions of churning ones.
3. **Cold starts are too slow.** Scheduling a pod takes seconds — scheduler convergence, image pull, container start. Fine for a service that runs for days; unacceptable for a workload that wakes up for two seconds.

Neither of the two standard platforms fits this workload shape. Serverless is priced for burstiness but is stateless by design — it forgets your filesystem and your process memory between invocations. Kubernetes remembers everything but bills you for every idle second. Agents are the awkward middle: **stateful like a pet, bursty like a function.**

Agent Substrate's answer is one idea applied ruthlessly: **turn the process into data.**

## The core idea: virtual memory, one level up

Substrate decomposes an agent session (it says "actor" — nothing here requires literal AI) into three independent things:

- **State** — a snapshot of RAM plus filesystem, stored as files in object storage (GCS/S3);
- **Identity** — credentials that follow the session, not the machine;
- **Compute** — any one of a pool of pre-warmed, interchangeable "worker" pods.

An idle actor is just a snapshot file: zero CPU, zero RAM, zero pods. When a request arrives, the actor is restored into any free worker in ~100ms (the project's p95 target) using gVisor's checkpoint/restore, or a Kata + Cloud Hypervisor micro-VM with demand-paged memory. When it goes idle again, it's checkpointed back to storage and the worker is wiped and returned to the pool for the next tenant.

If this sounds familiar, it should — it's an operating system pattern promoted one level up the stack:

| OS concept | Substrate equivalent |
|---|---|
| Physical page frame | Warm worker pod |
| Virtual page | Actor |
| Swap space | Snapshot bucket in GCS/S3 |
| Page fault | Request to a suspended actor |

Kubernetes already virtualizes nodes into pods. Substrate virtualizes pods into actors, and the overcommit math is the same as virtual memory's: the demo multiplexes ~250 stateful sessions across 8 physical pods, and the stated ambition is a billion actors per cluster with a thousand wakeups per second.

## Node, pod, actor: who holds whom

It's worth being precise about the containment hierarchy, because one detail here shapes the whole system.

A **node** hosts many **worker pods** — a `WorkerPool` custom resource declares the warm capacity ("50 gVisor workers"), and a controller reconciles it into an ordinary Deployment. Kubernetes does what it's good at: the scheduler places these empty shells, the kubelet starts and probes them. Each worker pod contains exactly one **sandbox** (a gVisor kernel or a micro-VM), and that sandbox hosts **at most one actor at any moment**. The assignment field in the data model is singular, not a list — this is a design invariant, not a temporary limitation.

So where does "250 actors on 8 pods" come from? **The multiplexing is temporal, not spatial:**

<figure>
<svg width="100%" viewBox="0 0 680 320" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:680px">
<title>Actors time-share worker pods</title>
<desc>A Kubernetes node contains worker pods; each hosts at most one actor at a time. Suspended actors live as snapshots in object storage and are resumed into any idle worker.</desc>
<defs><marker id="arr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#57606a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<rect x="1" y="1" width="678" height="318" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<rect x="30" y="30" width="390" height="240" rx="6" fill="none" stroke="#8c959f" stroke-dasharray="6 4"/>
<text x="44" y="56" font-family="system-ui,sans-serif" font-size="14" font-weight="600" fill="#1f2328">Kubernetes node</text>
<text x="406" y="56" font-family="system-ui,sans-serif" font-size="12" fill="#57606a" text-anchor="end">worker = pre-warmed slot</text>
<rect x="50" y="72" width="350" height="52" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="66" y="103" font-family="system-ui,sans-serif" font-size="14" font-weight="600" fill="#1f2328">Worker pod 1</text>
<rect x="252" y="82" width="132" height="32" rx="6" fill="#EEEDFE" stroke="#534AB7"/>
<text x="318" y="102" font-family="system-ui,sans-serif" font-size="12" fill="#3C3489" text-anchor="middle">actor A (running)</text>
<rect x="50" y="136" width="350" height="52" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="66" y="167" font-family="system-ui,sans-serif" font-size="14" font-weight="600" fill="#1f2328">Worker pod 2</text>
<rect x="252" y="146" width="132" height="32" rx="6" fill="#EEEDFE" stroke="#534AB7"/>
<text x="318" y="166" font-family="system-ui,sans-serif" font-size="12" fill="#3C3489" text-anchor="middle">actor B (running)</text>
<rect x="50" y="200" width="350" height="52" rx="6" fill="#f6f8fa" stroke="#d0d7de"/>
<text x="66" y="231" font-family="system-ui,sans-serif" font-size="14" font-weight="600" fill="#1f2328">Worker pod 3</text>
<rect x="252" y="210" width="132" height="32" rx="6" fill="none" stroke="#8c959f" stroke-dasharray="4 3"/>
<text x="318" y="230" font-family="system-ui,sans-serif" font-size="12" fill="#57606a" text-anchor="middle">idle slot</text>
<rect x="480" y="72" width="170" height="180" rx="6" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="565" y="104" font-family="system-ui,sans-serif" font-size="14" font-weight="600" fill="#085041" text-anchor="middle">Snapshot storage</text>
<text x="565" y="126" font-family="system-ui,sans-serif" font-size="12" fill="#0F6E56" text-anchor="middle">GCS / S3</text>
<text x="565" y="164" font-family="system-ui,sans-serif" font-size="12" fill="#0F6E56" text-anchor="middle">suspended actors C…Z</text>
<text x="565" y="184" font-family="system-ui,sans-serif" font-size="12" fill="#0F6E56" text-anchor="middle">millions, zero compute</text>
<line x1="428" y1="98" x2="472" y2="98" stroke="#57606a" stroke-width="1.5" marker-end="url(#arr)"/>
<text x="450" y="88" font-family="system-ui,sans-serif" font-size="12" fill="#57606a" text-anchor="middle">suspend</text>
<line x1="472" y1="226" x2="428" y2="226" stroke="#57606a" stroke-width="1.5" marker-end="url(#arr)"/>
<text x="450" y="216" font-family="system-ui,sans-serif" font-size="12" fill="#57606a" text-anchor="middle">resume</text>
<text x="340" y="298" font-family="system-ui,sans-serif" font-size="12" fill="#57606a" text-anchor="middle">One worker holds one actor at a time — the multiplexing happens across time, not within a pod</text>
</svg>
<figcaption>Actors rotate through a small pool of capacity-one workers; everyone else sleeps in object storage.</figcaption>
</figure>

At any instant, at most 8 actors are actually running; the other 240+ are files. And the capacity-one invariant pays for itself twice:

- **Isolation stays clean.** An actor never has a same-pod neighbor — the only "neighbor" is the previous tenant of the slot, in time. The security review reduces to "was the worker wiped properly between tenants," not "can co-residents attack each other."
- **Scheduling collapses.** When every slot is identical and holds one tenant, there is nothing to pack and nothing to score. More on this below.

Note what this inverts: in Kubernetes, "how many things run in a pod" is a packing question. In Substrate, the pod is a fixed *page frame* and the actor is the thing that flows — asking "how many actors per pod" is like asking how many virtual pages fit in a physical frame. Always one; the interesting number is how many rotate through it per day.

## The architecture: four planes on top of Kubernetes

Substrate is best understood as four planes, each with a deliberate relationship to the Kubernetes machinery underneath.

**The control plane** is a small gRPC server (`ate-api-server`) backed by Valkey/Redis rather than etcd. It owns the two high-frequency records — Actors and Workers — and orchestrates the resume/suspend workflows. Actors are *not* Kubernetes objects, and that's the single most important scale decision in the project: a billion actors live as cheap rows in a purpose-built store, while etcd holds only the slow-moving configuration.

**The node plane** runs the sandboxes. A per-node supervisor (`atelet`, a DaemonSet) moves snapshots between object storage and the node; inside every worker pod, a small herder process (`ateom`) drives the sandbox runtime — `runsc` checkpoint/restore for gVisor, or a Cloud Hypervisor snapshot for micro-VMs. Notably, the kubelet is not involved: it starts the empty worker shell and probes it, but never learns what program is running inside. Substrate pulls actor images and restores memory itself.

**The network plane** (`atenet`) answers the question Kubernetes has no primitive for: *route a request to a workload that might not be running, and wake it if so.* A CoreDNS instance resolves every actor name — `<actor>.<atespace>.actors.resources.substrate.ate.dev` — to one router address, and an Envoy-based router does the real addressing at layer 7 per request. More on this below.

**The identity plane** issues credentials that survive migration. A session-identity broker mints short-lived JWTs and SPIFFE certificates bound to *app/user/session* — not to any pod — because an actor may run on worker 3 today and worker 7 tomorrow, and its permissions must not care.

The relationship with Kubernetes follows one consistent rule: **each Substrate component replaces exactly one Kubernetes component that fails at actor granularity, and everything else is reused as-is.**

| Kubernetes component | Why it fails at this granularity | Substrate's replacement |
|---|---|---|
| etcd + apiserver | Can't store 10⁹ objects or absorb thousands of writes/sec | `ate-api-server` backed by Valkey/Redis |
| kube-scheduler | Async convergence in seconds vs a 100ms budget | In-memory filter + compare-and-swap claim |
| Services / cluster DNS | Can't materialize per-actor endpoints; no concept of "wake on traffic" | `atenet`: DNS mesh + Envoy router that resumes actors on demand |
| kubelet / containerd | No checkpoint/restore; no "swap the program inside a running pod" | `atelet` (per-node) + `ateom` (in-pod sandbox herder) |
| Workload identity | Identity is pinned to the pod; actors migrate across pods daily | Session-identity broker minting per-session JWTs and certs |

The slow-moving configuration — worker pools, actor templates, pinned sandbox binaries — stays in ordinary CRDs, which buys RBAC, auditing, and `kubectl` for free. In effect, **Substrate treats Kubernetes as an IaaS**: nodes, warm pod shells, and CRD storage in; scheduling, addressing, identity, and image distribution out. A cluster running Substrate remains a perfectly normal cluster for everything else.

## The life of a request

Here's what happens when a request hits a sleeping actor:

```
client ──① DNS──▶ substrate CoreDNS      (answers: the router's IP, always)
   │
   ├──② HTTP───▶ Envoy (atenet router)   holds the request…
   │                │
   │                ├──③ ext_proc──▶ Go process: parse Host, call ResumeActor
   │                │                   │
   │                │                   ├──④ pick a free worker (CAS in Valkey)
   │                │                   └──⑤ atelet pulls snapshot → ateom
   │                │                        runs `runsc restore`  (~100ms)
   │                ◀── worker IP ──────┘
   │                │
   └──◀─⑥ response──┴──▶ forwards the held request to workerIP:80
```

Two details carry the whole trick:

**DNS deliberately knows nothing.** Every actor name resolves to the same router address. Names must be stable — they end up in user configs and caches — while locations change many times a day. So the name→location binding is deferred to request time, at layer 7, where the `Host` header identifies the actor. Materializing per-actor DNS records or Services would just smuggle the scale problem back into the control plane.

**The router can hold a request.** Envoy's external-processing filter pauses the request while a companion Go process looks up the actor and, if it's suspended, triggers the resume. To the client, a sleeping actor is indistinguishable from a slow first byte. This is why the proxy must be L7: only an HTTP-aware hop can park a request for a few hundred milliseconds and then forward it. The forward then goes **directly to the worker's pod IP** — no Service, no Endpoints, nothing created in etcd per actor.

## Scheduling in microseconds

I expected to find a scheduler. There isn't one — no queue, no scoring loop, no controller. "Scheduling" is four synchronous steps inside the resume call:

1. Filter eligible worker pools (sandbox class + label selectors, all from local caches);
2. List free workers from an in-memory cache;
3. **Shuffle randomly and take the first** — no bin-packing, because every worker is an identical, capacity-one slot;
4. Claim it with an optimistic-concurrency write to Valkey. Two concurrent resumes racing for the same worker? One compare-and-swap wins; the loser backs off ~10ms and picks another.

The insight is that the *warm pool* dissolved the scheduling problem before it started: when slots are homogeneous and hold one tenant each, random choice is optimal and placement takes microseconds. The entire 100ms activation budget goes where it should — moving snapshot bytes.

Failure handling follows the same minimalism. Multi-step workflows (resume, suspend) are sequences of idempotent steps with a "client-driven forward recovery" contract: if a step fails, the error goes back to the caller, and the retry fast-forwards through whatever already completed. There is no internal job queue to babysit — the workflow's only durable state is the actor record itself.

This is the project's design signature, visible at every layer: **slow events go through Kubernetes; fast paths close over local memory and Redis.** Key rotations, pool resizing, template changes — apiserver. Per-request routing, per-wakeup placement — never.

## The honest tension list

Substrate is very young, and the most interesting parts are the unresolved ones:

**Streaming versus transparent suspend.** Agent traffic is SSE and WebSockets — long-lived connections. But an actor holding an open connection can't be suspended transparently; connection state lives in the client and the proxy too. I suspect "connection draining as an explicit phase of suspend" ends up in the design, and "transparent" gets an asterisk.

**The router still calls home per request.** Every request — even to a running actor — currently costs a control-plane lookup for the actor's location. The steady-state fix is an event-driven route cache in the router; until then, the control plane is back in the data path through the side door.

**Identity granularity.** Every service mesh assumes workload = pod. Substrate's workload is *finer* than a pod and migrates across pods — which is why it grows its own session-identity broker instead of adopting a mesh. Expect this mismatch to be a recurring source of design work, especially for per-actor network policy, where CNI reprogramming can never keep up with sub-second multiplexing. Policy will have to follow identity, not IPs.

**The density ceiling.** One actor = one sandbox = one *pod* means per-node concurrency is capped by kubelet's pod limit — a few hundred, not the thousands per host that Firecracker-style platforms achieve. Temporal multiplexing makes this mostly irrelevant for low-duty-cycle agents, but if high-activity workloads ever matter, decoupling sandboxes from pods would be the biggest surgery on the roadmap.

## Why this project is worth watching

Strip away the components and Substrate is a bet about the shape of agent workloads: **stateful, long-lived, and almost always idle** — a shape that neither serverless (stateless by design) nor vanilla Kubernetes (billed while idle) serves well. The response is an old idea executed at a new layer: paging, applied to entire sandboxed processes, on top of a platform it respects enough not to fork and distrusts enough to bypass on every hot path.

Whether *this* codebase wins or not, I'd wager the pattern does. The gap between "pod" and "agent session" is real, and someone will fill it.

---

*The project lives at [github.com/agent-substrate/substrate](https://github.com/agent-substrate/substrate) (Apache 2.0), with a demo video, a counter demo you can run on kind, and a weekly community call. It is emphatically not production-ready — which is exactly the stage at which reading a codebase teaches the most.*
