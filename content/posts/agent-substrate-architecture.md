---
title: "Agent Substrate Architecture in Diagrams"
date: 2026-11-15T10:00:00+08:00
draft: false
tags: ["kubernetes", "ai-agents", "infrastructure", "envoy", "architecture", "ai-assisted"]
summary: "A diagram-first map of Agent Substrate as of November 2026: where state lives, the control plane, how a request wakes a suspended actor, ingress and egress through the Envoy gateways, what runs on a node, and the security boundaries."
showtoc: true
---

<div class="sd-defs" aria-hidden="true">
<svg width="0" height="0" aria-hidden="true" focusable="false">
<defs>
<marker id="sd-ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M0,1 L10,5 L0,9 z" class="sd-mk-fg"/></marker>
<marker id="sd-ar-dp" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="10" markerHeight="10" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M0,1 L10,5 L0,9 z" class="sd-mk-dp"/></marker>
<marker id="sd-ar-cp" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M0,1 L10,5 L0,9 z" class="sd-mk-cp"/></marker>
<marker id="sd-ar-k8s" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M0,1 L10,5 L0,9 z" class="sd-mk-k8s"/></marker>
<marker id="sd-ar-sec" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M0,1 L10,5 L0,9 z" class="sd-mk-sec"/></marker>
</defs>
</svg>
</div>

*中文版：[Agent Substrate 架构图解]({{< relref "agent-substrate-architecture-zh.md" >}})*

Agent Substrate multiplexes many actors (agents and other agent-like workloads) onto a small pool of warm worker Pods. Kubernetes runs the slow-moving infrastructure: Pods and nodes. Substrate's own control plane handles the fast part: creating, suspending, resuming and routing actors, which has to happen in about a hundred milliseconds.

I wrote about why the project exists in [an earlier post]({{< relref "agent-substrate.md" >}}). This one is a map. It follows the code at [`a46464663`](https://github.com/agent-substrate/substrate/commit/a46464663fa076221ae059e1fd4a273edd2d8b09) (November 2026) and names the files to read for each part.

In the diagrams, blue is Kubernetes, amber is Substrate's control plane, teal is the traffic path, and a red dashed outline is a sandbox boundary. Wide diagrams scroll sideways on a phone.

*Drafted with an AI agent that read the code and docs at that commit. Identifiers and flows were checked against the code.*

## The core idea: many more actors than workers

Agent-like workloads spend most of their time waiting for input. One Pod per agent means paying for CPU, memory and a Pod slot on the node while it sits idle. Starting a fresh Pod means scheduling, pulling the image and starting containers, which takes seconds.

Substrate starts a set of worker Pods ahead of time. When a request arrives, it restores the actor's snapshot into a free slot on some worker. When the actor goes idle, it is checkpointed to a snapshot and the slot is freed. The next resume may land on a different worker. None of this goes through kube-scheduler or creates a Pod.

The project's targets are p95 activation latency of 100 ms, a billion actors per cluster, and 1,000 wakeups per second.

<figure class="sd-figure wide">
<div class="sd-scroll" style="--minw:760px">
<svg class="sd" viewBox="0 0 960 300" role="img" aria-label="Multiplexing: on the left, many actor records, a few of them RUNNING; on the right, three worker Pods with four slots each. ResumeActor puts an actor into a slot, and SuspendActor takes it out and stores a snapshot.">
<defs>
<pattern id="sd-p-actor" x="30" y="66" width="22" height="22" patternUnits="userSpaceOnUse"><rect x="2" y="2" width="14" height="14" rx="3" class="pat"/></pattern>
</defs>
<text class="t" x="30" y="34">Actor records · in Postgres</text>
<text class="s" x="30" y="52">Mostly SUSPENDED: a row plus a snapshot</text>
<rect x="30" y="66" width="308" height="154" fill="url(#sd-p-actor)"/>
<rect class="run" x="76" y="90" width="14" height="14" rx="3"/>
<rect class="run" x="186" y="134" width="14" height="14" rx="3"/>
<rect class="run" x="274" y="68" width="14" height="14" rx="3"/>
<rect class="run" x="120" y="178" width="14" height="14" rx="3"/>
<rect class="run" x="296" y="156" width="14" height="14" rx="3"/>
<rect class="run" x="230" y="200" width="14" height="14" rx="3"/>
<rect class="run" x="30" y="238" width="14" height="14" rx="3"/>
<text class="s" x="52" y="250">RUNNING · holds a worker slot</text>
<rect class="pat" x="30" y="262" width="14" height="14" rx="3"/>
<text class="s" x="52" y="274">SUSPENDED · uses no compute</text>
<line class="ln-dp" x1="372" y1="120" x2="586" y2="120" marker-end="url(#sd-ar-dp)"/>
<text class="s c tdp" x="480" y="110">ResumeActor: schedule + restore</text>
<line class="ln-cp" x1="588" y1="176" x2="374" y2="176" marker-end="url(#sd-ar-cp)"/>
<text class="s c tcp" x="480" y="196">SuspendActor: checkpoint, free slot</text>
<rect class="box" x="400" y="226" width="160" height="48" rx="8"/>
<text class="t sm c" x="480" y="246">Snapshots</text>
<text class="s c" x="480" y="264">object store + node copy</text>
<text class="t" x="610" y="34">Worker Pods · few, warm</text>
<text class="s" x="610" y="52">Started ahead of time by a WorkerPool</text>
<g>
<rect class="box" x="610" y="70" width="320" height="50" rx="8"/>
<text class="m" x="624" y="100">worker-a</text>
<rect class="slot" x="720" y="80" width="44" height="30" rx="4"/><text class="s c tdp" x="742" y="99">actor</text>
<rect class="slot" x="772" y="80" width="44" height="30" rx="4"/><text class="s c tdp" x="794" y="99">actor</text>
<rect class="slot-free" x="824" y="80" width="44" height="30" rx="4"/>
<rect class="slot-free" x="876" y="80" width="44" height="30" rx="4"/>
</g>
<g>
<rect class="box" x="610" y="132" width="320" height="50" rx="8"/>
<text class="m" x="624" y="162">worker-b</text>
<rect class="slot" x="720" y="142" width="44" height="30" rx="4"/><text class="s c tdp" x="742" y="161">actor</text>
<rect class="slot" x="772" y="142" width="44" height="30" rx="4"/><text class="s c tdp" x="794" y="161">actor</text>
<rect class="slot" x="824" y="142" width="44" height="30" rx="4"/><text class="s c tdp" x="846" y="161">actor</text>
<rect class="slot-free" x="876" y="142" width="44" height="30" rx="4"/>
</g>
<g>
<rect class="box" x="610" y="194" width="320" height="50" rx="8"/>
<text class="m" x="624" y="224">worker-c</text>
<rect class="slot" x="720" y="204" width="44" height="30" rx="4"/><text class="s c tdp" x="742" y="223">actor</text>
<rect class="slot-free" x="772" y="204" width="44" height="30" rx="4"/>
<rect class="slot-free" x="824" y="204" width="44" height="30" rx="4"/>
<rect class="slot-free" x="876" y="204" width="44" height="30" rx="4"/>
</g>
<text class="s" x="610" y="270">Capacity = actor slots + CPU/memory from Pod resources</text>
</svg>
</div>
<figcaption>Six RUNNING actors fill the six occupied slots. Every other actor is just a row and a snapshot. Kubernetes sees three worker Pods and never sees actors move in and out of slots.</figcaption>
</figure>

## Where state lives: two resource models

kube-apiserver and etcd are good at asynchronous reconciliation. They are not built to hold millions of objects or to take thousands of writes a second. So Substrate keeps actor-level state in Postgres, behind the gRPC API of `ate-api-server`. Infrastructure stays as CRDs, so platform teams keep Kubernetes RBAC, auditing and HPA.

<figure class="sd-figure wide">
<div class="sd-scroll" style="--minw:780px">
<svg class="sd" viewBox="0 0 960 400" role="img" aria-label="On the left, the Kubernetes API holds SandboxConfig, WorkerPool, NetworkPolicy, Deployment and Worker Pods. On the right, the Substrate API holds Atespace, ActorTemplate, Actor, Tag, Worker and ActorAssignment. An ActorTemplate selects WorkerPools by label, and each Worker maps to one Worker Pod.">
<text class="t tk8s" x="30" y="30">Kubernetes API · etcd</text>
<text class="s" x="30" y="48">Infrastructure: declarative, changes rarely</text>
<text class="t tcp" x="530" y="30">Substrate API · Postgres</text>
<text class="s" x="530" y="48">Actor lifecycle: many objects, frequent writes</text>
<rect class="k8s" x="30" y="72" width="170" height="50" rx="8"/>
<text class="t c" x="115" y="93">SandboxConfig</text>
<text class="s c" x="115" y="111">CRD · sandbox runtimes</text>
<rect class="k8s" x="250" y="72" width="180" height="50" rx="8"/>
<text class="t c" x="340" y="93">WorkerPool</text>
<text class="s c" x="340" y="111">CRD · pool of warm workers</text>
<line class="ln-k8s" x1="250" y1="97" x2="202" y2="97" marker-end="url(#sd-ar-k8s)"/>
<path class="ln-k8s" d="M290,122 V142 H115 V168" marker-end="url(#sd-ar-k8s)"/>
<line class="ln-k8s" x1="340" y1="122" x2="340" y2="168" marker-end="url(#sd-ar-k8s)"/>
<text class="s" x="348" y="152">created by atecontroller</text>
<rect class="k8s" x="30" y="170" width="170" height="44" rx="8"/>
<text class="t c" x="115" y="190">NetworkPolicy</text>
<text class="s c" x="115" y="206">one per WorkerPool</text>
<rect class="k8s" x="250" y="170" width="180" height="44" rx="8"/>
<text class="t c" x="340" y="197">Deployment</text>
<line class="ln-k8s" x1="340" y1="214" x2="340" y2="290" marker-end="url(#sd-ar-k8s)"/>
<rect class="box" x="258" y="302" width="180" height="56" rx="8"/>
<rect class="k8s" x="250" y="294" width="180" height="56" rx="8"/>
<text class="t c" x="340" y="318">Worker Pod × N</text>
<text class="s c" x="340" y="336">ateworker container</text>
<text class="s" x="30" y="384">Managed with kubectl · WorkerPool has a scale subresource for HPA</text>
<rect class="cp" x="530" y="72" width="170" height="40" rx="8"/>
<text class="t c" x="615" y="97">Atespace</text>
<path class="ln" d="M545,112 V246 M545,143 H565 M545,195 H565 M545,246 H565"/>
<rect class="cp" x="565" y="124" width="160" height="38" rx="8"/>
<text class="t c" x="645" y="148">ActorTemplate</text>
<rect class="cp" x="565" y="174" width="160" height="42" rx="8"/>
<text class="t c" x="645" y="191">Actor</text>
<text class="s c" x="645" y="207">uses one ActorTemplate</text>
<rect class="cp" x="565" y="228" width="160" height="36" rx="8"/>
<text class="t c" x="645" y="251">Tag · snapshot alias</text>
<rect class="cp" x="560" y="300" width="160" height="44" rx="8"/>
<text class="t c" x="640" y="318">Worker</text>
<text class="s c" x="640" y="334">global, Substrate-owned</text>
<line class="ln" x1="720" y1="322" x2="760" y2="322"/>
<rect class="cp" x="760" y="300" width="170" height="44" rx="8"/>
<text class="t c" x="845" y="318">ActorAssignment</text>
<text class="s c" x="845" y="334">one per hosted actor</text>
<path class="ln" d="M800,300 V195 H727" marker-end="url(#sd-ar)"/>
<text class="s" x="806" y="252">actor</text>
<path class="ln ret" d="M565,143 L432,100" marker-end="url(#sd-ar)"/>
<text class="s c halo" x="492" y="111">worker_selector</text>
<line class="ln ret" x1="558" y1="322" x2="434" y2="322" marker-start="url(#sd-ar)" marker-end="url(#sd-ar)"/>
<text class="s c halo" x="496" y="314">1:1 · workersync</text>
</svg>
</div>
<figcaption>The dashed arrows cross between the two APIs: an ActorTemplate selects WorkerPools by label, and atecontroller's workersync turns each worker Pod into a Worker record. Not shown: the EgressPolicy subresource of an Actor, and the global and per-atespace AccessPolicy.</figcaption>
</figure>

| Resource | Stored in | Written by | Changes |
| --- | --- | --- | --- |
| `WorkerPool` | CRD, namespaced | Substrate admin | rarely |
| `SandboxConfig` | CRD, cluster-scoped; lists runtime versions and assets for `gvisor` or `microvm` | Substrate admin | rarely |
| `ActorTemplate` | Postgres, immutable | agent developer (`kubectl ate create actor-template`) | rarely |
| `Actor` | Postgres | the app or platform on top | on every resume and suspend |
| `EgressPolicy` | Postgres, a singleton `default` subresource of an Actor | the app | sometimes |
| `Worker` / `ActorAssignment` | Postgres | Substrate | often |
| `Tag` | Postgres, holds its own copy of a snapshot | the app | rarely |

## The components

Here is the whole system in one picture. The sections after it zoom in on each part. Teal lines are user traffic and amber lines are control-plane calls.

<figure class="sd-figure wide">
<div class="sd-scroll" style="--minw:980px">
<svg class="sd" viewBox="0 0 1180 640" role="img" aria-label="Component topology: a caller's request goes through atenet-router to atunnel on a worker node, then into the actor's sandbox. Outbound actor traffic goes through atunnel to atenet-egress and out to external services. Both gateways call ate-api-server, which uses Postgres and calls atelet. atelet reads and writes object storage through objectstore-plugin.">
<rect class="zone" x="150" y="12" width="880" height="552" rx="14"/>
<text class="t tk8s" x="166" y="32">Kubernetes cluster</text>
<rect class="k8s" x="172" y="72" width="128" height="104" rx="8"/>
<text class="t c" x="236" y="96">kube-apiserver</text>
<text class="s c" x="236" y="118">WorkerPool</text>
<text class="s c" x="236" y="134">SandboxConfig</text>
<text class="s c" x="236" y="150">Deployment / Pod</text>
<rect class="zone" x="316" y="42" width="699" height="150" rx="10"/>
<text class="s" x="330" y="60">ate-system namespace · control plane (--cordon-system-infra-components moves it to dedicated nodes)</text>
<rect class="cp" x="328" y="72" width="150" height="104" rx="8"/>
<text class="t c" x="403" y="96">atecontroller</text>
<text class="s c" x="403" y="118">WorkerPool → Deployment</text>
<text class="s c" x="403" y="134">NetworkPolicy per pool</text>
<text class="s c" x="403" y="150">workersync → Worker</text>
<rect class="cp" x="506" y="72" width="214" height="104" rx="8"/>
<text class="t c" x="613" y="96">ate-api-server</text>
<text class="s c" x="613" y="116">Control API · WorkerService</text>
<text class="s c" x="613" y="132">authn · OpenFGA authz</text>
<text class="s c" x="613" y="148">workflow · scheduler</text>
<text class="s c m" x="613" y="166">sidecar: objectstore-plugin</text>
<rect class="cp" x="748" y="72" width="104" height="104" rx="8"/>
<text class="t c" x="800" y="108">Postgres</text>
<text class="s c" x="800" y="128">Actor / Worker</text>
<text class="s c" x="800" y="144">lease</text>
<rect class="box" x="872" y="72" width="130" height="48" rx="8"/>
<text class="t sm c" x="937" y="92">ate-idp-server</text>
<text class="s c" x="937" y="110">JWKS for actor JWTs</text>
<rect class="box" x="872" y="128" width="130" height="48" rx="8"/>
<text class="t sm c" x="937" y="148">podcertcontroller</text>
<text class="s c" x="937" y="166">component mTLS certs</text>
<line class="ln-k8s" x1="302" y1="124" x2="326" y2="124" marker-start="url(#sd-ar-k8s)" marker-end="url(#sd-ar-k8s)"/>
<line class="ln-cp" x1="478" y1="124" x2="504" y2="124" marker-end="url(#sd-ar-cp)"/>
<line class="ln-cp" x1="720" y1="124" x2="746" y2="124" marker-end="url(#sd-ar-cp)"/>
<rect class="box" x="12" y="322" width="120" height="60" rx="8"/>
<text class="t c" x="72" y="346">Caller</text>
<text class="s c" x="72" y="366">app / platform</text>
<text class="s c m tdp" x="72" y="402">ate-target-actor</text>
<text class="s c" x="72" y="418">header names the actor</text>
<rect class="dp" x="172" y="290" width="172" height="124" rx="8"/>
<text class="t c" x="258" y="314">atenet-router</text>
<text class="s c" x="258" y="336">Envoy · Svc :443 / :8444</text>
<text class="s c" x="258" y="354">ext_proc: resume + route</text>
<text class="s c" x="258" y="372">request parking</text>
<text class="s c m" x="258" y="400">ate-system</text>
<rect class="zone" x="384" y="212" width="442" height="334" rx="10"/>
<text class="s" x="398" y="230">Worker nodes × N</text>
<rect class="cp" x="400" y="242" width="410" height="52" rx="8"/>
<text class="t" x="416" y="264">atelet (DaemonSet)</text>
<text class="s" x="416" y="284">snapshot cache · asset cache · sidecar: objectstore-plugin</text>
<rect class="box" x="410" y="334" width="402" height="200" rx="8"/>
<rect class="box" x="400" y="324" width="402" height="200" rx="8"/>
<text class="t" x="416" y="344">Worker Pod (ateworker-gvisor / -microvm)</text>
<rect class="dp" x="416" y="356" width="128" height="150" rx="6"/>
<text class="t c" x="480" y="378">atunnel</text>
<text class="s c" x="480" y="400">:8443 ingress</text>
<text class="s c" x="480" y="418">egress client</text>
<text class="s c" x="480" y="436">DNS relay :53</text>
<text class="s c" x="480" y="460">runs in ateworker</text>
<rect class="sbx" x="566" y="356" width="222" height="66" rx="6"/>
<text class="t c" x="677" y="382">Actor A · RUNNING</text>
<text class="s c" x="677" y="402">sandbox · own netns · /30 veth</text>
<rect class="sbx" x="566" y="436" width="222" height="66" rx="6"/>
<text class="t c" x="677" y="462">Actor B · RUNNING</text>
<text class="s c" x="677" y="482">sandbox · no creds · default deny</text>
<line class="ln-dp" x1="544" y1="389" x2="564" y2="389" marker-end="url(#sd-ar-dp)"/>
<line class="ln-dp" x1="566" y1="469" x2="546" y2="469" marker-end="url(#sd-ar-dp)"/>
<rect class="dp" x="852" y="290" width="163" height="124" rx="8"/>
<text class="t c" x="933" y="314">atenet-egress</text>
<text class="s c" x="933" y="336">Envoy + ext-proc</text>
<text class="s c" x="933" y="354">sdsmint · MITM certs</text>
<text class="s c" x="933" y="372">Rust policy module</text>
<text class="s c m" x="933" y="400">ate-system</text>
<rect class="box" x="1048" y="322" width="120" height="60" rx="8"/>
<text class="t c" x="1108" y="346">External</text>
<text class="s c" x="1108" y="366">SaaS API / Internet</text>
<rect class="box" x="470" y="578" width="260" height="46" rx="8"/>
<text class="t c" x="600" y="598">Object storage (GCS / S3)</text>
<text class="s c" x="600" y="615">snapshots · sandbox assets</text>
<path class="ln-cp" d="M400,268 H372 V601 H468" marker-end="url(#sd-ar-cp)"/>
<text class="s e tcp halo" x="366" y="480">snapshot upload / download</text>
<line class="ln-dp" x1="132" y1="352" x2="170" y2="352" marker-end="url(#sd-ar-dp)"/>
<text class="s c tdp" x="151" y="344">HTTPS</text>
<line class="gap" x1="352" y1="380" x2="404" y2="380"/>
<line class="ln-dp" x1="344" y1="380" x2="414" y2="380" marker-end="url(#sd-ar-dp)"/>
<text class="s c tdp halo" x="380" y="368">mTLS</text>
<text class="s c tdp halo" x="380" y="398">CONNECT</text>
<path class="ln-dp" d="M480,506 V514 H838 V380 H850" marker-end="url(#sd-ar-dp)"/>
<text class="s tdp" x="844" y="440">mTLS</text>
<text class="s tdp" x="844" y="454">CONNECT</text>
<line class="ln-dp" x1="1015" y1="352" x2="1046" y2="352" marker-end="url(#sd-ar-dp)"/>
<path class="ln-cp" d="M258,290 V204 H560 V178" marker-end="url(#sd-ar-cp)"/>
<text class="s c tcp halo" x="410" y="200">ResumeActor</text>
<path class="ln-cp" d="M933,290 V204 H690 V178" marker-end="url(#sd-ar-cp)"/>
<text class="s c tcp halo" x="812" y="200">GetActor · GetActorEgressPolicy</text>
<line class="ln-cp" x1="600" y1="178" x2="600" y2="240" marker-end="url(#sd-ar-cp)"/>
<text class="s e tcp halo" x="594" y="226">Atelet RPC</text>
<line class="ln-cp" x1="650" y1="240" x2="650" y2="178" marker-end="url(#sd-ar-cp)"/>
<text class="s tcp halo" x="656" y="226">WorkerService</text>
<line class="ln" x1="450" y1="294" x2="450" y2="322" marker-end="url(#sd-ar)"/>
<text class="s m" x="458" y="313">worker.sock</text>
<line class="ln" x1="700" y1="322" x2="700" y2="296" marker-end="url(#sd-ar)"/>
<text class="s m e" x="694" y="313">worker-support.sock</text>
</svg>
</div>
<figcaption>Worker Pods cannot reach the API, so a worker's calls to the control plane (registering, getting certificates, reporting) go through the atelet on its node to WorkerService. The objectstore-plugin sidecar of ate-api-server copies and deletes whole snapshots; that line is not drawn.</figcaption>
</figure>

| Component | Runs as | Does | Code |
| --- | --- | --- | --- |
| ate-api-server | Deployment | Control API, WorkerService, scheduling, workflows, authn and authz | `cmd/ateapi` |
| Postgres | StatefulSet, or Cloud SQL | actor and worker state, leases | `cmd/ateapi/internal/store/atepg` |
| atecontroller | Deployment | WorkerPool to Deployment, a NetworkPolicy per pool, worker Pods to Worker records | `cmd/atecontroller` |
| atelet | DaemonSet | node supervisor: drives ateworker, moves and caches snapshots | `cmd/atelet` |
| ateworker | container in each worker Pod | runs, checkpoints and restores sandboxes; hosts atunnel | `cmd/ateworker-gvisor`, `cmd/ateworker-microvm` |
| atunnel | inside ateworker | ingress endpoint, egress tunnel client, DNS relay | `internal/atunnel` |
| atenet-router | Deployment: Envoy + atenet | ingress: resume, parking, routing; serves xDS | `cmd/atenet/internal/router` |
| atenet-egress | Deployment: Envoy + ext-proc + sdsmint | egress policy enforcement point, credential injection | `cmd/atenet/internal/router/egress` |
| objectstore-plugin | sidecar of atelet and ate-api-server | serves `objectstore.v1` over a Unix socket, backed by GCS or S3 | `cmd/objectstore-plugin` |
| podcertcontroller | Deployment | issues component certificates (`podid`, `servicedns`, `postgres` signers) | `cmd/podcertcontroller` |
| ate-idp-server | Deployment | serves OIDC discovery and JWKS for actor JWTs | `cmd/ate-idp-server` |
| kubectl-ate | CLI | calls the Control API | `cmd/kubectl-ate` |

## The control plane

The control plane handles only frequent, latency-sensitive actor operations. Creating and scaling Pods and provisioning nodes stay with Kubernetes.

<figure class="sd-figure wide">
<div class="sd-scroll" style="--minw:820px">
<svg class="sd" viewBox="0 0 1000 430" role="img" aria-label="Inside ate-api-server: callers pass authentication, then OpenFGA authorization, then reach the Control service and ActorWorkflow. The workflow asks the scheduler for a worker, and the scheduler reads workercache. The workflow calls atelet and objectstore-plugin and reads and writes the store. atelet calls WorkerService.">
<rect class="box" x="10" y="56" width="150" height="40" rx="8"/>
<text class="t sm c" x="85" y="81">kubectl ate / SDK</text>
<rect class="box" x="10" y="108" width="150" height="40" rx="8"/>
<text class="t sm c" x="85" y="133">router ext_proc</text>
<rect class="box" x="10" y="160" width="150" height="40" rx="8"/>
<text class="t sm c" x="85" y="185">egress ext_proc</text>
<rect class="box" x="10" y="330" width="150" height="48" rx="8"/>
<text class="t sm c" x="85" y="350">atelet</text>
<text class="s c" x="85" y="368">for its node's workers</text>
<rect class="zone" x="185" y="16" width="625" height="400" rx="12"/>
<text class="t tcp" x="200" y="38">ate-api-server</text>
<rect class="cp" x="200" y="56" width="130" height="134" rx="8"/>
<text class="t c" x="265" y="82">Authentication</text>
<text class="s c m" x="265" y="100">apiauthn</text>
<text class="s c" x="265" y="128">SPIFFE mTLS</text>
<text class="s c" x="265" y="146">OIDC JWTProvider</text>
<rect class="cp" x="350" y="56" width="130" height="134" rx="8"/>
<text class="t c" x="415" y="82">Authorization</text>
<text class="s c m" x="415" y="100">authz</text>
<text class="s c" x="415" y="128">OpenFGA model.fga</text>
<text class="s c" x="415" y="146">global or atespace</text>
<text class="s c" x="415" y="164">unlisted RPCs denied</text>
<rect class="cp" x="500" y="56" width="130" height="134" rx="8"/>
<text class="t c" x="565" y="82">Control service</text>
<text class="s c m" x="565" y="100">controlapi</text>
<text class="s c" x="565" y="128">resource CRUD</text>
<text class="s c" x="565" y="146">Resume / Suspend</text>
<text class="s c" x="565" y="164">Revert / Delete</text>
<rect class="cp" x="650" y="56" width="140" height="134" rx="8"/>
<text class="t c" x="720" y="82">ActorWorkflow</text>
<text class="s c m" x="720" y="100">workflow_*.go</text>
<text class="s c" x="720" y="128">per-actor lease</text>
<text class="s c" x="720" y="146">runs named steps</text>
<text class="s c" x="720" y="164">a trace span per step</text>
<rect class="cp" x="500" y="236" width="130" height="64" rx="8"/>
<text class="t c" x="565" y="260">Scheduler</text>
<text class="s c" x="565" y="282">power of two choices</text>
<rect class="cp" x="650" y="236" width="140" height="64" rx="8"/>
<text class="t c" x="720" y="260">workercache</text>
<text class="s c" x="720" y="282">in-memory worker view</text>
<rect class="cp" x="200" y="330" width="280" height="70" rx="8"/>
<text class="t c" x="340" y="354">WorkerService</text>
<text class="s c" x="340" y="374">RegisterWorker · mint actor certs / JWTs</text>
<text class="s c" x="340" y="390">report snapshot state · report crashes</text>
<rect class="cp" x="500" y="330" width="290" height="70" rx="8"/>
<text class="t c" x="645" y="354">store (atepg)</text>
<text class="s c" x="645" y="374">Actor · Worker · ActorAssignment</text>
<text class="s c" x="645" y="390">lease · watch</text>
<rect class="box" x="840" y="46" width="150" height="70" rx="8"/>
<text class="t c" x="915" y="68">atelet</text>
<text class="s c" x="915" y="88">Run · Restore · Checkpoint</text>
<text class="s c" x="915" y="104">DetachActor · Terminate</text>
<rect class="box" x="840" y="146" width="150" height="54" rx="8"/>
<text class="t sm c" x="915" y="168">objectstore-plugin</text>
<text class="s c" x="915" y="186">copy / delete snapshots</text>
<rect class="box" x="840" y="340" width="150" height="50" rx="8"/>
<text class="t c" x="915" y="370">Postgres</text>
<line class="ln" x1="160" y1="76" x2="198" y2="88" marker-end="url(#sd-ar)"/>
<line class="ln" x1="160" y1="128" x2="198" y2="128" marker-end="url(#sd-ar)"/>
<line class="ln" x1="160" y1="180" x2="198" y2="168" marker-end="url(#sd-ar)"/>
<line class="ln" x1="160" y1="354" x2="198" y2="362" marker-end="url(#sd-ar)"/>
<line class="ln" x1="330" y1="123" x2="348" y2="123" marker-end="url(#sd-ar)"/>
<line class="ln" x1="480" y1="123" x2="498" y2="123" marker-end="url(#sd-ar)"/>
<line class="ln" x1="630" y1="123" x2="648" y2="123" marker-end="url(#sd-ar)"/>
<line class="ln-cp" x1="790" y1="80" x2="838" y2="80" marker-end="url(#sd-ar-cp)"/>
<line class="ln-cp" x1="790" y1="110" x2="838" y2="166" marker-end="url(#sd-ar-cp)"/>
<path class="ln-cp" d="M700,190 V214 H565 V234" marker-end="url(#sd-ar-cp)"/>
<text class="s c tcp" x="632" y="229">AssignWorker</text>
<line class="ln" x1="630" y1="268" x2="648" y2="268" marker-end="url(#sd-ar)"/>
<line class="ln" x1="720" y1="330" x2="720" y2="302" marker-end="url(#sd-ar)"/>
<text class="s" x="726" y="320">WatchWorkers</text>
<path class="ln-cp" d="M790,160 H800 V350 H792" marker-end="url(#sd-ar-cp)"/>
<line class="ln" x1="480" y1="365" x2="498" y2="365" marker-end="url(#sd-ar)"/>
<line class="ln" x1="790" y1="380" x2="838" y2="380" marker-end="url(#sd-ar)"/>
<text class="s c" x="814" y="374">SQL</text>
</svg>
</div>
<figcaption>All three outside callers go through the same authentication and authorization. atelet calls WorkerService with its node identity. That is a separate set of RPCs that workers use to report on themselves.</figcaption>
</figure>

- **Authenticate, then authorize.** `apiauthn` authenticates every RPC with SPIFFE mTLS or a configured OIDC `JWTProvider`. The embedded OpenFGA model in `authz/model.fga` then authorizes it. An RPC missing from `defaultRPCPermissions` is denied, and a test fails the build if one is missing.
- **One operation per actor at a time.** `ActorWorkflow` takes the actor's lease before it runs any step, so a resume and a suspend of the same actor never run at once. A conflict returns `Aborted`, and the router retries.
- **Scheduling.** `scheduling.Schedule` filters workers by constraints, samples two at random and keeps the less loaded one (power of two choices). Load is the higher of slot utilization and CPU or memory utilization. The constraints are the snapshot's sandbox class and runtime version, the label selectors on the ActorTemplate and the Actor, and `RequiredNodes` when the latest snapshot is still on a node's disk.
- **No database read on the hot path.** `workercache` keeps an in-memory view of workers through the store's `WatchWorkers`, and the scheduler reads only that.
- **Where Workers come from.** atecontroller's `workersync` watches worker Pods and calls `CreateWorker` and `DeleteWorker`. When the worker process starts, it calls `RegisterWorker` through atelet, then `SetWorkerSandboxRuntimes` to report the runtimes it offers. The scheduler only picks registered workers.

## How a request wakes an actor

This is the full path of an HTTP request to a SUSPENDED actor. If the actor is already running, `ResumeActor` returns its current worker and skips the restore steps.

<figure class="sd-figure wide">
<div class="sd-scroll" style="--minw:820px">
<svg class="sd" viewBox="0 0 980 650" role="img" aria-label="ResumeActor sequence: the caller sends to Envoy, Envoy to ext_proc, and ext_proc calls ResumeActor. ate-api-server loads the actor, creates volumes, assigns a worker and attaches volumes, then calls atelet Restore. atelet fetches the snapshot and calls RestoreWorkload on ateworker. When it is ready, the worker IP returns to ext_proc, and Envoy sends the request over an mTLS CONNECT to atunnel, which forwards it to the actor.">
<rect class="box" x="6" y="14" width="116" height="40" rx="8"/><text class="t sm c" x="64" y="39">Caller</text>
<rect class="dp" x="128" y="14" width="114" height="40" rx="8"/><text class="t sm c" x="185" y="39">Envoy</text>
<rect class="dp" x="258" y="14" width="124" height="40" rx="8"/><text class="t sm c" x="320" y="39">atenet ext_proc</text>
<rect class="cp" x="410" y="14" width="130" height="40" rx="8"/><text class="t sm c" x="475" y="39">ate-api-server</text>
<rect class="cp" x="568" y="14" width="114" height="40" rx="8"/><text class="t sm c" x="625" y="39">atelet</text>
<rect class="dp" x="710" y="14" width="130" height="40" rx="8"/><text class="t sm c" x="775" y="39">ateworker / atunnel</text>
<rect class="sbx" x="860" y="14" width="110" height="40" rx="8"/><text class="t sm c" x="915" y="39">Actor</text>
<line class="life" x1="64" y1="54" x2="64" y2="640"/>
<line class="life" x1="185" y1="54" x2="185" y2="640"/>
<line class="life" x1="320" y1="54" x2="320" y2="640"/>
<line class="life" x1="475" y1="54" x2="475" y2="640"/>
<line class="life" x1="625" y1="54" x2="625" y2="640"/>
<line class="life" x1="775" y1="54" x2="775" y2="640"/>
<line class="life" x1="915" y1="54" x2="915" y2="640"/>
<line class="ln-dp" x1="64" y1="86" x2="183" y2="86" marker-end="url(#sd-ar-dp)"/>
<text class="s halo" x="72" y="78">HTTP · ate-target-actor</text>
<line class="ln-dp" x1="185" y1="118" x2="318" y2="118" marker-end="url(#sd-ar-dp)"/>
<text class="s c halo" x="252" y="110">ext_proc headers</text>
<line class="ln-cp" x1="320" y1="150" x2="473" y2="150" marker-end="url(#sd-ar-cp)"/>
<text class="s c halo" x="397" y="142">ResumeActor</text>
<rect class="dp" x="236" y="166" width="150" height="48" rx="6"/>
<text class="s" x="246" y="186">No free worker:</text>
<text class="s" x="246" y="204">park, retry up to 5s</text>
<rect class="cp" x="402" y="166" width="150" height="96" rx="6"/>
<text class="s" x="412" y="186">① LoadActorForResume</text>
<text class="s" x="412" y="204">② CreateVolumes</text>
<text class="s" x="412" y="222">③ AssignWorker</text>
<text class="s" x="412" y="240">④ AttachVolumes</text>
<line class="ln-cp" x1="475" y1="290" x2="623" y2="290" marker-end="url(#sd-ar-cp)"/>
<text class="s c halo" x="550" y="282">⑤ Restore</text>
<rect class="cp" x="560" y="304" width="130" height="40" rx="6"/>
<text class="s c" x="625" y="320">snapshot from node</text>
<text class="s c" x="625" y="336">or object store</text>
<line class="ln" x1="625" y1="368" x2="773" y2="368" marker-end="url(#sd-ar)"/>
<text class="s c m halo" x="700" y="360">RestoreWorkload</text>
<rect class="dp" x="712" y="382" width="126" height="40" rx="6"/>
<text class="s c" x="775" y="398">restore sandbox</text>
<text class="s c" x="775" y="414">start actor network</text>
<line class="ln ret" x1="775" y1="446" x2="627" y2="446" marker-end="url(#sd-ar)"/>
<text class="s c halo" x="700" y="438">ready</text>
<line class="ln-cp ret" x1="625" y1="476" x2="477" y2="476" marker-end="url(#sd-ar-cp)"/>
<text class="s c halo" x="550" y="468">worker IP</text>
<rect class="cp" x="390" y="490" width="170" height="26" rx="6"/>
<text class="s c" x="475" y="507">⑥ FinalizeRunning → RUNNING</text>
<line class="ln-cp ret" x1="475" y1="540" x2="322" y2="540" marker-end="url(#sd-ar-cp)"/>
<text class="s c halo" x="397" y="532">Actor + worker address</text>
<line class="ln-dp ret" x1="320" y1="568" x2="187" y2="568" marker-end="url(#sd-ar-dp)"/>
<text class="s c halo" x="252" y="560">dynamic metadata</text>
<line class="ln-dp" x1="185" y1="598" x2="773" y2="598" marker-end="url(#sd-ar-dp)"/>
<text class="s c halo tdp" x="480" y="590">mTLS CONNECT :8443 (ORIGINAL_DST cluster)</text>
<line class="ln-dp" x1="775" y1="626" x2="913" y2="626" marker-end="url(#sd-ar-dp)"/>
<text class="s c halo" x="845" y="618">to the actor's port</text>
</svg>
</div>
<figcaption>Steps ① to ⑥ are the <code>stepSpan</code> names in <code>workflow_resume.go</code>. The response takes the same path back and is not drawn.</figcaption>
</figure>

## Actor lifecycle and snapshots

<figure class="sd-figure wide">
<div class="sd-scroll" style="--minw:760px">
<svg class="sd" viewBox="0 0 960 290" role="img" aria-label="Actor state machine: CreateActor enters SUSPENDED. ResumeActor moves it to RESUMING, and a finished restore to RUNNING. SuspendActor moves it to SUSPENDING, and a written checkpoint back to SUSPENDED. A crash moves RUNNING to CRASHED, and RevertActor returns it to SUSPENDED.">
<circle class="dot" cx="28" cy="115" r="7"/>
<line class="ln" x1="35" y1="115" x2="68" y2="115" marker-end="url(#sd-ar)"/>
<text class="s" x="8" y="84">CreateActor</text>
<rect class="box" x="70" y="92" width="150" height="46" rx="23"/>
<text class="t m c" x="145" y="120">SUSPENDED</text>
<rect class="box" x="300" y="92" width="150" height="46" rx="23"/>
<text class="t m c" x="375" y="120">RESUMING</text>
<rect class="dp" x="530" y="92" width="150" height="46" rx="23"/>
<text class="t m c" x="605" y="120">RUNNING</text>
<rect class="box" x="760" y="92" width="170" height="46" rx="23"/>
<text class="t m c" x="845" y="120">SUSPENDING</text>
<rect class="crash" x="530" y="222" width="150" height="40" rx="20"/>
<text class="t m c tsec" x="605" y="247">CRASHED</text>
<line class="ln" x1="220" y1="115" x2="298" y2="115" marker-end="url(#sd-ar)"/>
<text class="s c" x="260" y="106">ResumeActor</text>
<line class="ln" x1="450" y1="115" x2="528" y2="115" marker-end="url(#sd-ar)"/>
<text class="s c" x="490" y="106">restored</text>
<line class="ln" x1="680" y1="115" x2="758" y2="115" marker-end="url(#sd-ar)"/>
<text class="s c" x="720" y="106">SuspendActor</text>
<path class="ln" d="M845,92 V52 H145 V90" marker-end="url(#sd-ar)"/>
<text class="s c halo" x="495" y="47">checkpoint written (synchronous mode also waits for the upload)</text>
<line class="ln-sec" x1="605" y1="138" x2="605" y2="220" marker-end="url(#sd-ar-sec)"/>
<text class="s tsec" x="612" y="184">ReportActorCrashed</text>
<path class="ln" d="M530,242 H145 V140" marker-end="url(#sd-ar)"/>
<text class="s c halo" x="338" y="236">RevertActor: back to the last durable snapshot</text>
<text class="s" x="740" y="248">DeleteActor works in any state</text>
</svg>
</div>
<figcaption>RevertActor also accepts RUNNING, and SUSPENDED while the latest snapshot is still on a node. It stops the sandbox, drops any local or partial snapshot, and keeps the last completed external snapshot.</figcaption>
</figure>

A snapshot holds up to three nested layers of state, chosen by `snapshotConfig.preferredFidelity` on the ActorTemplate.

<figure class="sd-figure">
<div class="sd-scroll" style="--minw:420px">
<svg class="sd" viewBox="0 0 520 210" role="img" aria-label="Three snapshot fidelity levels: MEMORY contains ROOTFS, which contains VOLUMES.">
<rect class="dp" x="10" y="10" width="500" height="190" rx="10"/>
<text class="t" x="26" y="34">MEMORY (default)</text>
<text class="s" x="26" y="52">adds the process tree and RAM</text>
<rect class="box" x="30" y="66" width="460" height="124" rx="8"/>
<text class="t" x="46" y="90">ROOTFS</text>
<text class="s" x="46" y="108">adds each container's writable rootfs layer</text>
<rect class="cp" x="50" y="122" width="420" height="56" rx="6"/>
<text class="t" x="66" y="146">VOLUMES</text>
<text class="s" x="66" y="164">contents of DurableDir volumes</text>
</svg>
</div>
<figcaption>Each layer contains the one inside it.</figcaption>
</figure>

- **Asynchronous suspend** is the default. It returns once the local checkpoint is written, with `durableSnapshot` in `PENDING`. atelet uploads in the background and marks it `COMPLETED`. If the node dies before the upload finishes, that state is lost.
- **Synchronous suspend** (`DURABILITY_MODE_SYNCHRONOUS`) returns after the upload.
- **Node-local copy.** After a suspend the snapshot stays on the node (`locality`), and the next resume is scheduled back there to skip the object-store round trip. atelet deletes the local copy after `--snapshot-detach-delay`.
- **Runtime version.** A MEMORY snapshot records the sandbox runtime it was taken on and restores only on a worker that still offers that runtime. If none has room, the resume falls back to ROOTFS and cold-boots.
- **Tags** are immutable aliases in an atespace. Each holds its own copy of the snapshot, and a published tag can be used from other atespaces.

gVisor uses `runsc` checkpoint and restore. microVM runs on Kata and Cloud Hypervisor and restores memory on demand with `userfaultfd`. Its rootfs writes and DurableDir volumes live on the host behind virtio-fs and ship with the snapshot as tar files.

## Ingress: from the caller to the actor

<figure class="sd-figure wide">
<div class="sd-scroll" style="--minw:860px">
<svg class="sd" viewBox="0 0 1040 340" role="img" aria-label="Inside atenet-router: the :8443 listener passes through the ext_proc filter to the ORIGINAL_DST cluster, then over mTLS to atunnel on the worker and on to the actor. The ext_proc filter holds a gRPC stream to the atenet process, which calls ate-api-server. The :8444 listener terminates CONNECT and sends the bytes into an internal listener that runs the same ext_proc filter.">
<rect class="zone" x="160" y="16" width="545" height="318" rx="12"/>
<text class="s" x="176" y="36">atenet-router Pod (Envoy container + atenet container)</text>
<rect class="box" x="8" y="55" width="130" height="60" rx="8"/>
<text class="t sm c" x="73" y="80">Caller</text>
<text class="s c" x="73" y="98">HTTPS · Svc :443</text>
<rect class="box" x="8" y="262" width="130" height="60" rx="8"/>
<text class="t sm c" x="73" y="287">Caller</text>
<text class="s c m" x="73" y="305">CONNECT host:9090</text>
<rect class="dp" x="178" y="55" width="124" height="60" rx="8"/>
<text class="t sm c" x="240" y="80">listener :8443</text>
<text class="s c" x="240" y="98">HTTP conn manager</text>
<rect class="dp" x="330" y="55" width="124" height="60" rx="8"/>
<text class="t sm c" x="392" y="80">ext_proc filter</text>
<text class="s c" x="392" y="98">ate-target-actor</text>
<rect class="dp" x="530" y="55" width="160" height="60" rx="8"/>
<text class="t sm c" x="610" y="80">ORIGINAL_DST cluster</text>
<text class="s c" x="610" y="98">uses dynamic metadata</text>
<rect class="cp" x="480" y="150" width="210" height="96" rx="8"/>
<text class="t sm c" x="585" y="172">atenet (ext_proc server)</text>
<text class="s c m" x="585" y="192">ingress.Handler</text>
<text class="s c" x="585" y="210">flight dedup · parking lot</text>
<text class="s c" x="585" y="228">also Envoy's xDS server</text>
<rect class="dp" x="178" y="262" width="124" height="60" rx="8"/>
<text class="t sm c" x="240" y="287">listener :8444</text>
<text class="s c" x="240" y="305">terminates CONNECT</text>
<rect class="dp" x="330" y="262" width="124" height="60" rx="8"/>
<text class="t sm c" x="392" y="287">internal listener</text>
<text class="s c m" x="392" y="305">main_internal</text>
<rect class="dp" x="740" y="55" width="140" height="60" rx="8"/>
<text class="t sm c" x="810" y="80">atunnel :8443</text>
<text class="s c" x="810" y="98">reads TargetPortHeader</text>
<rect class="sbx" x="910" y="55" width="122" height="60" rx="8"/>
<text class="t sm c" x="971" y="80">Actor</text>
<text class="s c" x="971" y="98">default port 80</text>
<rect class="cp" x="740" y="168" width="140" height="60" rx="8"/>
<text class="t sm c" x="810" y="193">ate-api-server</text>
<text class="s c" x="810" y="211">ResumeActor</text>
<text class="s" x="740" y="266">Host stays the app's authority;</text>
<text class="s" x="740" y="282">it does not pick the actor.</text>
<line class="ln-dp" x1="138" y1="85" x2="176" y2="85" marker-end="url(#sd-ar-dp)"/>
<line class="ln-dp" x1="302" y1="85" x2="328" y2="85" marker-end="url(#sd-ar-dp)"/>
<line class="ln-dp" x1="454" y1="85" x2="528" y2="85" marker-end="url(#sd-ar-dp)"/>
<text class="s c halo" x="491" y="77">allowed</text>
<path class="ln-cp" d="M420,117 V198 H478" marker-start="url(#sd-ar-cp)" marker-end="url(#sd-ar-cp)"/>
<text class="s tcp halo" x="426" y="150">gRPC</text>
<line class="ln-dp" x1="138" y1="292" x2="176" y2="292" marker-end="url(#sd-ar-dp)"/>
<line class="ln-dp" x1="302" y1="292" x2="328" y2="292" marker-end="url(#sd-ar-dp)"/>
<line class="ln-dp" x1="380" y1="262" x2="380" y2="117" marker-end="url(#sd-ar-dp)"/>
<text class="s e tdp halo" x="372" y="196">each tunneled request</text>
<line class="ln-cp" x1="690" y1="198" x2="738" y2="198" marker-end="url(#sd-ar-cp)"/>
<line class="ln-dp" x1="690" y1="85" x2="738" y2="85" marker-end="url(#sd-ar-dp)"/>
<text class="s c tdp" x="714" y="77">mTLS</text>
<line class="ln-dp" x1="880" y1="85" x2="908" y2="85" marker-end="url(#sd-ar-dp)"/>
</svg>
</div>
<figcaption>CONNECT traffic on other ports is terminated and sent back through the same ext_proc path, so each request inside a long-lived tunnel is resumed and routed on its own, even if the actor has moved to another worker. Only HTTP(S) inside the tunnel works today.</figcaption>
</figure>

- **Picking the actor.** The `ate-target-actor: <atespace>/<actor>` header names it. The ingress handler treats every request header as unauthenticated client input.
- **Picking the upstream.** ext_proc puts the worker address in dynamic metadata, not in a header, and Envoy's `ORIGINAL_DST` cluster dials it. The actor's port still travels in a header, `TargetPortHeader`, because atunnel is a separate process and cannot read Envoy's metadata.
- **The hop to the worker** is an mTLS CONNECT to atunnel on port 8443 by default. With `--upstream-connect=false` it is HTTPS to port 443.
- **One resume per hot actor.** Concurrent requests for the same actor join one in-flight `ResumeActor` call in the resumer's flight registry.
- **Graceful drain.** On SIGTERM the router flips `/readyz`, waits for endpoints to update, drains Envoy through its admin API, stops ext_proc, and writes a marker that releases the Envoy container's `preStop` hook. A parked request still gets its full budget.

### Request parking

Multiplexing means a burst can fill the worker pool for a moment, and `ResumeActor` returns `ResourceExhausted`. Another actor usually suspends within milliseconds. So instead of returning 503 right away, the router parks the request and retries with exponential backoff until it succeeds or the budget runs out.

| ResumeActor result | Router | Client sees |
| --- | --- | --- |
| `OK` | routes | the actor's response |
| `Aborted` (concurrent resume) | always retries | the response, once it succeeds |
| `ResourceExhausted` (no free slot) | parks and retries | 503 after the budget |
| `Unavailable` (control plane blip, rolling update) | parks and retries | 503 after the budget |
| `FailedPrecondition` (for example CRASHED) | fails fast | 503 |
| `NotFound` | fails fast | 404 |
| `DeadlineExceeded` | fails fast | 504 |
| `PermissionDenied` / `Unauthenticated` | fails fast | 403 / 401 |

- `--parked-request-budget` (5s by default) is per flight. Requests that join an in-flight resume share what is left of its budget. The budget stops new retries but never cancels a resume already in progress, so a late restore is still served.
- `--parked-request-max` (1024 by default) is the size of the parking lot. When it is full, new parkers get 503 "router at capacity". Setting it to 0 turns parking off.
- Each parked request holds an ext_proc stream, so the circuit breaker on Envoy's ext_proc cluster is the hard limit. `--extproc-max-requests` defaults to twice the lot size (at least 1024), which leaves half for requests to actors that are already running.

## Egress: traffic leaving an actor

All outbound TCP from an actor goes through a policy enforcement point (PEP), `atenet-egress` by default. The one exception is DNS to the sandbox gateway on port 53, which the DNS relay in atunnel answers.

<figure class="sd-figure wide">
<div class="sd-scroll" style="--minw:900px">
<svg class="sd" viewBox="0 0 1080 450" role="img" aria-label="Egress path: nftables redirects the actor's TCP connections to atunnel, which opens an mTLS CONNECT to atenet-egress with the actor's certificate. The outer chain checks the certificate and asks ate-api-server about the actor and its policy. Traffic then goes to one of three inner chains by protocol, and on to the upstream. The DNS relay forwards only queries for hostnames in the policy.">
<rect class="sbx" x="8" y="40" width="170" height="330" rx="10"/>
<text class="t sm tsec" x="22" y="62">Actor sandbox</text>
<text class="s" x="22" y="78">untrusted</text>
<rect class="box" x="22" y="96" width="142" height="56" rx="6"/>
<text class="t sm c" x="93" y="120">actor process</text>
<text class="s c m" x="93" y="138">connect(IP:port)</text>
<rect class="box" x="22" y="270" width="142" height="56" rx="6"/>
<text class="t sm c" x="93" y="294">DNS query</text>
<text class="s c" x="93" y="312">to gateway :53</text>
<rect class="zone" x="200" y="40" width="230" height="330" rx="10"/>
<text class="t sm" x="214" y="62">Worker Pod</text>
<text class="s" x="214" y="78">ateworker · trusted</text>
<rect class="box" x="214" y="96" width="202" height="56" rx="6"/>
<text class="t sm c" x="315" y="118">nftables</text>
<text class="s c" x="315" y="136">TCP REDIRECT · drop the rest</text>
<rect class="dp" x="214" y="170" width="202" height="80" rx="6"/>
<text class="t sm c" x="315" y="192">atunnel</text>
<text class="s c m" x="315" y="210">SO_ORIGINAL_DST</text>
<text class="s c" x="315" y="228">actor key + short-lived cert</text>
<text class="s c" x="315" y="244">cert minted via atelet</text>
<rect class="dp" x="214" y="270" width="202" height="56" rx="6"/>
<text class="t sm c" x="315" y="292">DNS relay</text>
<text class="s c" x="315" y="310">resolves policy hosts only</text>
<text class="s c" x="315" y="348">others get NXDOMAIN</text>
<rect class="zone" x="470" y="20" width="400" height="360" rx="12"/>
<text class="t sm" x="486" y="42">atenet-egress Pod (Envoy)</text>
<rect class="dp" x="486" y="58" width="170" height="306" rx="8"/>
<text class="t sm c" x="571" y="82">Outer CONNECT chain</text>
<text class="s c m" x="571" y="100">egress</text>
<text class="s" x="498" y="130">① check the actor's cert</text>
<text class="s" x="498" y="148">(Actor Identity CA)</text>
<text class="s" x="498" y="178">② ask ate-api-server:</text>
<text class="s" x="498" y="196">exists, same UID, and</text>
<text class="s" x="498" y="214">RUNNING or RESUMING</text>
<text class="s" x="498" y="244">③ load EgressPolicy</text>
<text class="s" x="498" y="262">Rust module stores hash</text>
<text class="s" x="498" y="292">no policy, no tunnel</text>
<rect class="box" x="690" y="58" width="165" height="90" rx="8"/>
<text class="t sm c m" x="772" y="80">egress_cleartext</text>
<text class="s c" x="772" y="100">cleartext HTTP</text>
<text class="s c" x="772" y="118">per request, by Host</text>
<text class="s c" x="772" y="136">denied: 403</text>
<rect class="box" x="690" y="162" width="165" height="100" rx="8"/>
<text class="t sm c m" x="772" y="184">egress_tls_mitm</text>
<text class="s c" x="772" y="204">terminates TLS (sdsmint)</text>
<text class="s c" x="772" y="222">per request, https rules</text>
<text class="s c" x="772" y="240">injects credentials</text>
<rect class="box" x="690" y="276" width="165" height="88" rx="8"/>
<text class="t sm c m" x="772" y="298">tls_passthrough</text>
<text class="s c" x="772" y="318">by SNI, not decrypted</text>
<text class="s c" x="772" y="336">else closed in handshake</text>
<rect class="box" x="910" y="58" width="160" height="306" rx="8"/>
<text class="t sm c" x="990" y="204">Upstream</text>
<text class="s c" x="990" y="222">chosen by policy</text>
<rect class="cp" x="486" y="398" width="170" height="40" rx="8"/>
<text class="t sm c" x="571" y="423">ate-api-server</text>
<line class="ln-dp" x1="164" y1="124" x2="212" y2="124" marker-end="url(#sd-ar-dp)"/>
<text class="s c" x="188" y="116">TCP</text>
<line class="ln-dp" x1="315" y1="152" x2="315" y2="168" marker-end="url(#sd-ar-dp)"/>
<line class="ln-dp" x1="164" y1="298" x2="212" y2="298" marker-end="url(#sd-ar-dp)"/>
<text class="s c" x="188" y="290">:53</text>
<line class="ln-dp" x1="416" y1="210" x2="484" y2="210" marker-end="url(#sd-ar-dp)"/>
<text class="s c tdp halo" x="450" y="200">mTLS</text>
<text class="s c tdp halo" x="450" y="226">CONNECT</text>
<line class="ln-dp" x1="656" y1="103" x2="688" y2="103" marker-end="url(#sd-ar-dp)"/>
<line class="ln-dp" x1="656" y1="212" x2="688" y2="212" marker-end="url(#sd-ar-dp)"/>
<line class="ln-dp" x1="656" y1="320" x2="688" y2="320" marker-end="url(#sd-ar-dp)"/>
<line class="ln-dp" x1="855" y1="103" x2="908" y2="103" marker-end="url(#sd-ar-dp)"/>
<line class="ln-dp" x1="855" y1="212" x2="908" y2="212" marker-end="url(#sd-ar-dp)"/>
<line class="ln-dp" x1="855" y1="320" x2="908" y2="320" marker-end="url(#sd-ar-dp)"/>
<line class="ln-cp" x1="571" y1="364" x2="571" y2="396" marker-end="url(#sd-ar-cp)"/>
<text class="s tcp halo" x="578" y="388">GetActor · policy</text>
</svg>
</div>
<figcaption>Envoy tells ext_proc which filter chain accepted the request (<code>xds.filter_chain_name</code>), and that decides the direction, so a request cannot pick it. The outer chain decides once per TCP connection, and the inner chains decide every request.</figcaption>
</figure>

- **One way out of the sandbox.** nftables redirects every TCP connection except DNS on port 53 to atunnel. Other UDP is dropped and other protocols get ICMP unreachable. `169.254.0.0/16` and `fe80::/10` are blocked, except the actor's own `169.254.17.0/30`.
- **Actor identity.** Before the workload starts, atunnel makes a private key and gets a short-lived certificate through atelet. The certificate has an `ActorIdentity` extension with the atespace, name, UID and the purpose `atunnel`.
- **The actor's word is not proof.** The gateway can use Host or SNI as policy input. When it authorizes by hostname, it dials the authorized hostname, not whatever IP the CONNECT named.
- **Credential injection** happens per request on the MITM leg. Credentials come from a credential provider plugin, such as `k8s-credential-provider` reading Kubernetes Secrets, or from a freshly minted actor JWT. The sandbox holds none.
- **Policy updates.** DNS follows a new policy within about 5 seconds. New connections use it once cached decisions expire. An authorized passthrough connection is not cut.
- **Denied by default.** WebSocket, other HTTP upgrades and the actor's own CONNECT requests get 403. If no PEP is configured, or it fails, egress is dropped.

I went through the gateway code in much more detail in [Fifty-Two Notes on Agent Substrate's Gateway]({{< relref "substrate-gateway-notes.md" >}}).

## Inside a node: atelet and ateworker

<figure class="sd-figure wide">
<div class="sd-scroll" style="--minw:820px">
<svg class="sd" viewBox="0 0 980 330" role="img" aria-label="Inside a node: atelet calls the Worker service in ateworker over worker.sock, and ateworker calls the WorkerSupport service in atelet over worker-support.sock. ateworker drives the gVisor or microVM sandbox runtime. atelet's objectstore-plugin sidecar handles snapshots and assets.">
<rect class="zone" x="8" y="8" width="964" height="314" rx="12"/>
<text class="t sm tk8s" x="24" y="30">One Kubernetes node</text>
<rect class="cp" x="24" y="46" width="330" height="262" rx="8"/>
<text class="t c" x="189" y="72">atelet (DaemonSet)</text>
<text class="s" x="40" y="100">serves Atelet (called by ate-api-server)</text>
<text class="s" x="40" y="120">serves WorkerSupport (called by workers)</text>
<text class="s" x="40" y="140">snapshot cache · sandbox asset cache</text>
<text class="s" x="40" y="160">resetActorDirs: scrub dirs between actors</text>
<text class="s" x="40" y="180">relays worker calls to WorkerService</text>
<rect class="box" x="40" y="206" width="298" height="86" rx="6"/>
<text class="t sm" x="56" y="230">objectstore-plugin (sidecar)</text>
<text class="s" x="56" y="250">NodeProvider: upload / download snapshots</text>
<text class="s" x="56" y="268">AssetProvider: fetch sandbox assets</text>
<text class="s" x="56" y="284">withheld from atelet on sha256 mismatch</text>
<rect class="box" x="440" y="46" width="516" height="262" rx="8"/>
<text class="t sm" x="456" y="70">Worker Pod</text>
<rect class="dp" x="456" y="86" width="220" height="206" rx="6"/>
<text class="t c" x="566" y="110">ateworker</text>
<text class="s c" x="566" y="134">serves Worker</text>
<text class="s c m" x="566" y="158">RunWorkload</text>
<text class="s c m" x="566" y="176">RestoreWorkload</text>
<text class="s c m" x="566" y="194">CheckpointWorkload</text>
<text class="s c m" x="566" y="212">TerminateWorkload</text>
<text class="s c" x="566" y="240">hosts atunnel</text>
<text class="s c" x="566" y="258">a netns per actor</text>
<rect class="sbx" x="704" y="86" width="236" height="92" rx="6"/>
<text class="t sm c" x="822" y="110">gVisor</text>
<text class="s c m" x="822" y="130">runsc checkpoint / restore</text>
<text class="s c m" x="822" y="150">ateworker-gvisor</text>
<text class="s c" x="822" y="194">picked by the WorkerPool's sandbox class</text>
<rect class="sbx" x="704" y="200" width="236" height="92" rx="6"/>
<text class="t sm c" x="822" y="224">microVM</text>
<text class="s c" x="822" y="244">Kata + Cloud Hypervisor</text>
<text class="s c m" x="822" y="264">ateworker-microvm</text>
<line class="ln" x1="354" y1="120" x2="454" y2="120" marker-end="url(#sd-ar)"/>
<text class="s c m halo" x="404" y="110">worker.sock</text>
<text class="s c" x="404" y="138">atelet → Worker</text>
<line class="ln" x1="456" y1="230" x2="356" y2="230" marker-end="url(#sd-ar)"/>
<text class="s c m halo" x="404" y="220">worker-support.sock</text>
<text class="s c" x="404" y="248">worker → atelet</text>
<line class="ln" x1="676" y1="132" x2="702" y2="132" marker-end="url(#sd-ar)"/>
<line class="ln" x1="676" y1="246" x2="702" y2="246" marker-end="url(#sd-ar)"/>
</svg>
</div>
<figcaption>Both socket paths are defined in <code>internal/nodepath</code>. atelet and the worker runtimes share only <code>internal/proto/ateworkerpb</code> and these paths, not each other's implementation, so atelet can be upgraded ahead of workers later.</figcaption>
</figure>

- Sandbox binaries are not baked into the worker image. They come from the `SandboxConfig` the WorkerPool resolves, land in atelet's content-addressed cache, and are hard-linked into each actor.
- atelet holds no object storage credentials of its own. On GCS it moves an actor's snapshots with credentials scoped to that actor. Per-actor scoping on S3 is planned.

## Security boundaries and identity

The starting assumption is that actors run untrusted code and that actors from different tenants share machines. Every actor has an identity, but no credentials, no open network and no way out of its sandbox.

| Boundary | What it holds |
| --- | --- |
| 1. Sandbox | Each actor runs in its own gVisor or microVM sandbox with its own netns and storage. Plain Linux containers are not supported. |
| 2. Gateways | Traffic comes in only through atenet-router and goes out only through atenet-egress and the DNS relay. Policy and credential injection happen there. |
| 3. Control plane | Neither actors nor worker Pods can reach the Substrate API, its database or the Kubernetes API. API calls are authorized per atespace. |
| 4. Storage | Sandboxes never touch snapshot storage. Only atelet moves snapshots, and only the control plane copies or deletes them. |

| Who | Identity | Checked by |
| --- | --- | --- |
| Components | Pod certificates from podcertcontroller (`podid`, `servicedns`, `postgres`) | mTLS everywhere, except local `/healthz` and `/readyz` |
| API callers | SPIFFE mTLS or OIDC JWT | OpenFGA, with AccessPolicy per global scope and atespace; the bootstrap owner is the `ate-client` ServiceAccount |
| Actors | a 1-hour SPIFFE X.509 certificate, plus actor JWTs on demand | the egress gateway checks the certificate; outside services check JWTs against the JWKS from ate-idp-server |
| Network | Kubernetes NetworkPolicy | default-deny ingress in `ate-system`; atecontroller writes a NetworkPolicy per WorkerPool |

Substrate does not decide which end user can reach which actor. The application on top of it does.

## Through Envoy's eyes

Both gateways run the same `envoy-dataplane` image: `envoyproxy/envoy:v1.39.5` plus one Rust dynamic module. This is how Substrate's needs map onto Envoy features. `router/` below means `cmd/atenet/internal/router/`.

| Need | Envoy feature | Code |
| --- | --- | --- |
| pick the upstream per actor | ext_proc writes dynamic metadata; an `ORIGINAL_DST` cluster dials it | `router/ingress/`, `router/xds.go` |
| config | atenet runs an xDS server; agentgateway mode uses a static ConfigMap | `router/xds.go` |
| ingress on other ports | terminate CONNECT, re-enter through an internal listener | `connect_terminate_tls` and `main_internal` in `xds.go` |
| backpressure for parking | `max_requests` on the ext_proc cluster's circuit breaker | `router/xds.go`, `router/config.go` |
| tell the egress legs apart | ext_proc reads the `xds.filter_chain_name` attribute | `router/extproc/dispatch.go` |
| carry policy from the outer chain to the inner one | an HTTP filter writes the policy hash to shared filter state; a listener filter on the inner listener reads and enforces it | `cmd/dataplane/envoy/dynamic-modules/egress-policy` |
| MITM leaf certificates | SDS: `atenet sdsmint` mints them on demand over a Unix socket | `cmd/atenet/internal/sdsmint` |
| graceful shutdown | admin API drain, and `preStop` waits for a drain-complete marker | `router/drain.go`, `router/envoydrain.go` |

## Where to start reading

1. [docs/concepts/architecture.md](https://github.com/agent-substrate/substrate/blob/a46464663fa076221ae059e1fd4a273edd2d8b09/docs/concepts/architecture.md) and [docs/reference/resource-model.md](https://github.com/agent-substrate/substrate/blob/a46464663fa076221ae059e1fd4a273edd2d8b09/docs/reference/resource-model.md): the official architecture and resource model.
2. [api/ateapi/v1/ateapi.proto](https://github.com/agent-substrate/substrate/blob/a46464663fa076221ae059e1fd4a273edd2d8b09/api/ateapi/v1/ateapi.proto): the `Control` and `WorkerService` services.
3. [controlapi/workflow_resume.go](https://github.com/agent-substrate/substrate/blob/a46464663fa076221ae059e1fd4a273edd2d8b09/cmd/ateapi/internal/controlapi/workflow_resume.go), then `workflow_suspend.go`: the two paths that matter most.
4. [scheduling/scheduling.go](https://github.com/agent-substrate/substrate/blob/a46464663fa076221ae059e1fd4a273edd2d8b09/cmd/ateapi/internal/scheduling/scheduling.go): the whole scheduler, about 340 lines.
5. [cmd/atenet/internal/router/README.md](https://github.com/agent-substrate/substrate/blob/a46464663fa076221ae059e1fd4a273edd2d8b09/cmd/atenet/internal/router/README.md), then `ingress/resumer.go` and `ingress/parking.go`, alongside [request-parking.md](https://github.com/agent-substrate/substrate/blob/a46464663fa076221ae059e1fd4a273edd2d8b09/docs/concepts/request-parking.md).
6. [docs/guides/network-egress.md](https://github.com/agent-substrate/substrate/blob/a46464663fa076221ae059e1fd4a273edd2d8b09/docs/guides/network-egress.md), then `router/egress/egress.go` and the Rust module.
7. `internal/proto/ateletpb/atelet.proto` and `internal/proto/ateworkerpb/ateworker.proto`: the two interfaces on a node.
8. `cmd/atecontroller/internal/controllers/workerpool_controller.go` and `workersync/syncer.go`: how the Kubernetes side plugs in.
