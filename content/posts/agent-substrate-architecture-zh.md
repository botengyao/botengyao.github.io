---
title: "Agent Substrate 架构图解"
date: 2026-11-15T10:00:00+08:00
draft: false
tags: ["kubernetes", "ai-agents", "infrastructure", "envoy", "architecture", "ai-assisted"]
summary: "用图读懂 Agent Substrate（2026 年 11 月）：状态放在哪里、控制面、一次请求如何唤醒挂起的 actor、经 Envoy 网关的 ingress 和 egress 流量、节点上跑什么，以及安全边界。"
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

*English version: [Agent Substrate Architecture in Diagrams]({{< relref "agent-substrate-architecture.md" >}})*

Agent Substrate 把大量 actor（agent 一类的负载）多路复用到少量预热的 worker Pod 上。Kubernetes 负责 Pod、节点这类变化慢的基础设施；Substrate 自己的控制面负责 actor 的创建、挂起、恢复和路由，这些操作要在百毫秒级完成。

这个项目为什么存在，我在[之前一篇英文文章]({{< relref "agent-substrate.md" >}})里写过。这篇是一张地图，基于 upstream 提交 [`a46464663`](https://github.com/agent-substrate/substrate/commit/a46464663fa076221ae059e1fd4a273edd2d8b09)（2026 年 11 月），每一部分都注明了该读哪些文件。

图中蓝色是 Kubernetes，琥珀色是 Substrate 控制面，青色是流量路径，红色虚线框是沙箱边界。较宽的图在手机上可以左右滑动。

*本文由 AI agent 阅读该提交的代码和文档后起草，标识符和流程都对照代码核对过。*

## 核心思想：actor 远多于 worker

agent 类负载大部分时间在等输入。如果每个 agent 常驻一个 Pod，空闲时也要占 CPU、内存和每节点的 Pod 配额。冷启动一个 Pod 又要经过调度、拉镜像、启动容器，通常是秒级。

Substrate 预先启动一批 worker Pod。请求到来时，它把 actor 的快照恢复进某个 worker 的空闲槽位。actor 空闲后被 checkpoint 成快照并释放槽位，下一次可能恢复到另一个 worker 上。整个过程不经过 kube-scheduler，也不创建 Pod。

项目的目标指标：激活延迟 p95 100ms，单集群 10 亿个 actor，每秒 1000 次唤醒。

<figure class="sd-figure wide">
<div class="sd-scroll" style="--minw:760px">
<svg class="sd" viewBox="0 0 960 300" role="img" aria-label="多路复用：左边是大量 actor 记录，少数处于 RUNNING；右边是三个 worker Pod，每个有四个槽位；ResumeActor 把 actor 放进槽位，SuspendActor 把它取出来存成快照。">
<defs>
<pattern id="sd-p-actor" x="30" y="66" width="22" height="22" patternUnits="userSpaceOnUse"><rect x="2" y="2" width="14" height="14" rx="3" class="pat"/></pattern>
</defs>
<text class="t" x="30" y="34">Actor 记录 · 存在 Postgres</text>
<text class="s" x="30" y="52">大多处于 SUSPENDED：一条记录 + 一份快照</text>
<rect x="30" y="66" width="308" height="154" fill="url(#sd-p-actor)"/>
<rect class="run" x="76" y="90" width="14" height="14" rx="3"/>
<rect class="run" x="186" y="134" width="14" height="14" rx="3"/>
<rect class="run" x="274" y="68" width="14" height="14" rx="3"/>
<rect class="run" x="120" y="178" width="14" height="14" rx="3"/>
<rect class="run" x="296" y="156" width="14" height="14" rx="3"/>
<rect class="run" x="230" y="200" width="14" height="14" rx="3"/>
<rect class="run" x="30" y="238" width="14" height="14" rx="3"/>
<text class="s" x="52" y="250">RUNNING · 占一个 worker 槽位</text>
<rect class="pat" x="30" y="262" width="14" height="14" rx="3"/>
<text class="s" x="52" y="274">SUSPENDED · 不占任何计算资源</text>
<line class="ln-dp" x1="372" y1="120" x2="586" y2="120" marker-end="url(#sd-ar-dp)"/>
<text class="s c tdp" x="480" y="110">ResumeActor：调度 + 恢复快照</text>
<line class="ln-cp" x1="588" y1="176" x2="374" y2="176" marker-end="url(#sd-ar-cp)"/>
<text class="s c tcp" x="480" y="196">SuspendActor：checkpoint + 释放槽位</text>
<rect class="box" x="400" y="226" width="160" height="48" rx="8"/>
<text class="t sm c" x="480" y="246">快照存储</text>
<text class="s c" x="480" y="264">对象存储 + 节点本地副本</text>
<text class="t" x="610" y="34">Worker Pod · 少量、预热</text>
<text class="s" x="610" y="52">WorkerPool 通过 Deployment 预先拉起</text>
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
<text class="s" x="610" y="270">worker 容量 = actor 槽位 + CPU / 内存（取自 Pod resources）</text>
</svg>
</div>
<figcaption>6 个 RUNNING actor 正好占满右边 6 个槽位，其余 actor 只是记录和快照。Kubernetes 只看得见 3 个 worker Pod，看不见 actor 在槽位间进出。</figcaption>
</figure>

## 两层资源模型：状态放在哪里

kube-apiserver 和 etcd 擅长异步协调，但不适合存上百万个对象，也扛不住每秒数千次写入。所以 Substrate 把 actor 级状态放进 Postgres，经由 `ate-api-server` 的 gRPC API 访问。基础设施仍然是 CRD，平台团队可以继续用 K8s RBAC、审计和 HPA。

<figure class="sd-figure wide">
<div class="sd-scroll" style="--minw:780px">
<svg class="sd" viewBox="0 0 960 400" role="img" aria-label="左边是 Kubernetes API 中的 SandboxConfig、WorkerPool、NetworkPolicy、Deployment、Worker Pod；右边是 Substrate API 中的 Atespace、ActorTemplate、Actor、Tag、Worker、ActorAssignment。ActorTemplate 用标签选择 WorkerPool，Worker 与 Worker Pod 一一对应。">
<text class="t tk8s" x="30" y="30">Kubernetes API · etcd</text>
<text class="s" x="30" y="48">低频、声明式：基础设施</text>
<text class="t tcp" x="530" y="30">Substrate API · Postgres</text>
<text class="s" x="530" y="48">高频、海量：actor 生命周期</text>
<rect class="k8s" x="30" y="72" width="170" height="50" rx="8"/>
<text class="t c" x="115" y="93">SandboxConfig</text>
<text class="s c" x="115" y="111">CRD · 集群级 · 运行时资产</text>
<rect class="k8s" x="250" y="72" width="180" height="50" rx="8"/>
<text class="t c" x="340" y="93">WorkerPool</text>
<text class="s c" x="340" y="111">CRD · 预热的 worker 池</text>
<line class="ln-k8s" x1="250" y1="97" x2="202" y2="97" marker-end="url(#sd-ar-k8s)"/>
<path class="ln-k8s" d="M290,122 V142 H115 V168" marker-end="url(#sd-ar-k8s)"/>
<line class="ln-k8s" x1="340" y1="122" x2="340" y2="168" marker-end="url(#sd-ar-k8s)"/>
<text class="s" x="348" y="152">atecontroller 生成</text>
<rect class="k8s" x="30" y="170" width="170" height="44" rx="8"/>
<text class="t c" x="115" y="190">NetworkPolicy</text>
<text class="s c" x="115" y="206">每个 WorkerPool 一份</text>
<rect class="k8s" x="250" y="170" width="180" height="44" rx="8"/>
<text class="t c" x="340" y="197">Deployment</text>
<line class="ln-k8s" x1="340" y1="214" x2="340" y2="290" marker-end="url(#sd-ar-k8s)"/>
<rect class="box" x="258" y="302" width="180" height="56" rx="8"/>
<rect class="k8s" x="250" y="294" width="180" height="56" rx="8"/>
<text class="t c" x="340" y="318">Worker Pod × N</text>
<text class="s c" x="340" y="336">ateworker 容器</text>
<text class="s" x="30" y="384">用 kubectl 管理 · WorkerPool 带 scale 子资源，可接 HPA</text>
<rect class="cp" x="530" y="72" width="170" height="40" rx="8"/>
<text class="t c" x="615" y="97">Atespace</text>
<path class="ln" d="M545,112 V246 M545,143 H565 M545,195 H565 M545,246 H565"/>
<rect class="cp" x="565" y="124" width="160" height="38" rx="8"/>
<text class="t c" x="645" y="148">ActorTemplate</text>
<rect class="cp" x="565" y="174" width="160" height="42" rx="8"/>
<text class="t c" x="645" y="191">Actor</text>
<text class="s c" x="645" y="207">引用一个 ActorTemplate</text>
<rect class="cp" x="565" y="228" width="160" height="36" rx="8"/>
<text class="t c" x="645" y="251">Tag · 快照别名</text>
<rect class="cp" x="560" y="300" width="160" height="44" rx="8"/>
<text class="t c" x="640" y="318">Worker</text>
<text class="s c" x="640" y="334">全局资源 · 由 Substrate 写</text>
<line class="ln" x1="720" y1="322" x2="760" y2="322"/>
<rect class="cp" x="760" y="300" width="170" height="44" rx="8"/>
<text class="t c" x="845" y="318">ActorAssignment</text>
<text class="s c" x="845" y="334">每个托管的 actor 一条</text>
<path class="ln" d="M800,300 V195 H727" marker-end="url(#sd-ar)"/>
<text class="s" x="806" y="252">actor</text>
<path class="ln ret" d="M565,143 L432,100" marker-end="url(#sd-ar)"/>
<text class="s c halo" x="492" y="111">worker_selector</text>
<line class="ln ret" x1="558" y1="322" x2="434" y2="322" marker-start="url(#sd-ar)" marker-end="url(#sd-ar)"/>
<text class="s c halo" x="496" y="314">1:1 · workersync</text>
</svg>
</div>
<figcaption>虚线箭头跨越两个 API：ActorTemplate 用标签选 WorkerPool；atecontroller 的 workersync 把每个 worker Pod 同步成一条 Worker 记录。图中省略了 Actor 的子资源 EgressPolicy，以及全局和 atespace 级的 AccessPolicy。</figcaption>
</figure>

| 资源 | 存放位置 | 谁来写 | 变化频率 |
| --- | --- | --- | --- |
| `WorkerPool` | CRD，命名空间级 | Substrate 管理员 | 低 |
| `SandboxConfig` | CRD，集群级，列出 gvisor / microvm 的运行时版本和资产 | Substrate 管理员 | 低 |
| `ActorTemplate` | Postgres，不可变 | agent 开发者（`kubectl ate create actor-template`） | 低 |
| `Actor` | Postgres | 应用 / 上层系统 | 高，每次 resume 和 suspend 都写 |
| `EgressPolicy` | Postgres，Actor 的单例子资源 `default` | 应用 | 中 |
| `Worker` / `ActorAssignment` | Postgres | Substrate 自己 | 高 |
| `Tag` | Postgres，持有自己的快照副本 | 应用 | 低 |

## 组件全景

先看一张总图，后面各节再逐块展开。青色线是用户流量，琥珀色线是控制面调用。

<figure class="sd-figure wide">
<div class="sd-scroll" style="--minw:980px">
<svg class="sd" viewBox="0 0 1180 640" role="img" aria-label="组件拓扑：调用方的请求经 atenet-router 进入 worker 节点上的 atunnel，再到 actor 沙箱；actor 的出站流量经 atunnel 到 atenet-egress 再到外部服务；router 和 egress 都调用 ate-api-server；ate-api-server 使用 Postgres 并调用 atelet；atelet 通过 objectstore-plugin 读写对象存储。">
<rect class="zone" x="150" y="12" width="880" height="552" rx="14"/>
<text class="t tk8s" x="166" y="32">Kubernetes 集群</text>
<rect class="k8s" x="172" y="72" width="128" height="104" rx="8"/>
<text class="t c" x="236" y="96">kube-apiserver</text>
<text class="s c" x="236" y="118">WorkerPool</text>
<text class="s c" x="236" y="134">SandboxConfig</text>
<text class="s c" x="236" y="150">Deployment / Pod</text>
<rect class="zone" x="316" y="42" width="699" height="150" rx="10"/>
<text class="s" x="330" y="60">ate-system 命名空间 · 控制面（--cordon-system-infra-components 可隔离到专用节点）</text>
<rect class="cp" x="328" y="72" width="150" height="104" rx="8"/>
<text class="t c" x="403" y="96">atecontroller</text>
<text class="s c" x="403" y="118">WorkerPool → Deployment</text>
<text class="s c" x="403" y="134">每池 NetworkPolicy</text>
<text class="s c" x="403" y="150">workersync → Worker</text>
<rect class="cp" x="506" y="72" width="214" height="104" rx="8"/>
<text class="t c" x="613" y="96">ate-api-server</text>
<text class="s c" x="613" y="116">Control API · WorkerService</text>
<text class="s c" x="613" y="132">认证 · OpenFGA 授权</text>
<text class="s c" x="613" y="148">workflow · scheduler</text>
<text class="s c m" x="613" y="166">sidecar: objectstore-plugin</text>
<rect class="cp" x="748" y="72" width="104" height="104" rx="8"/>
<text class="t c" x="800" y="108">Postgres</text>
<text class="s c" x="800" y="128">Actor / Worker</text>
<text class="s c" x="800" y="144">lease</text>
<rect class="box" x="872" y="72" width="130" height="48" rx="8"/>
<text class="t sm c" x="937" y="92">ate-idp-server</text>
<text class="s c" x="937" y="110">actor JWT 的 JWKS</text>
<rect class="box" x="872" y="128" width="130" height="48" rx="8"/>
<text class="t sm c" x="937" y="148">podcertcontroller</text>
<text class="s c" x="937" y="166">组件 mTLS 证书</text>
<line class="ln-k8s" x1="302" y1="124" x2="326" y2="124" marker-start="url(#sd-ar-k8s)" marker-end="url(#sd-ar-k8s)"/>
<line class="ln-cp" x1="478" y1="124" x2="504" y2="124" marker-end="url(#sd-ar-cp)"/>
<line class="ln-cp" x1="720" y1="124" x2="746" y2="124" marker-end="url(#sd-ar-cp)"/>
<rect class="box" x="12" y="322" width="120" height="60" rx="8"/>
<text class="t c" x="72" y="346">调用方</text>
<text class="s c" x="72" y="366">应用 / 上层系统</text>
<text class="s c m tdp" x="72" y="402">ate-target-actor</text>
<text class="s c" x="72" y="418">header 指定 actor</text>
<rect class="dp" x="172" y="290" width="172" height="124" rx="8"/>
<text class="t c" x="258" y="314">atenet-router</text>
<text class="s c" x="258" y="336">Envoy · Svc :443 / :8444</text>
<text class="s c" x="258" y="354">ext_proc：resume + 路由</text>
<text class="s c" x="258" y="372">request parking</text>
<text class="s c m" x="258" y="400">ate-system</text>
<rect class="zone" x="384" y="212" width="442" height="334" rx="10"/>
<text class="s" x="398" y="230">Worker 节点 × N</text>
<rect class="cp" x="400" y="242" width="410" height="52" rx="8"/>
<text class="t" x="416" y="264">atelet（DaemonSet）</text>
<text class="s" x="416" y="284">快照缓存 · 资产缓存 · sidecar: objectstore-plugin</text>
<rect class="box" x="410" y="334" width="402" height="200" rx="8"/>
<rect class="box" x="400" y="324" width="402" height="200" rx="8"/>
<text class="t" x="416" y="344">Worker Pod（ateworker-gvisor / -microvm）</text>
<rect class="dp" x="416" y="356" width="128" height="150" rx="6"/>
<text class="t c" x="480" y="378">atunnel</text>
<text class="s c" x="480" y="400">:8443 ingress</text>
<text class="s c" x="480" y="418">egress 客户端</text>
<text class="s c" x="480" y="436">DNS relay :53</text>
<text class="s c" x="480" y="460">由 ateworker 托管</text>
<rect class="sbx" x="566" y="356" width="222" height="66" rx="6"/>
<text class="t c" x="677" y="382">Actor A · RUNNING</text>
<text class="s c" x="677" y="402">沙箱 · 私有 netns · /30 veth</text>
<rect class="sbx" x="566" y="436" width="222" height="66" rx="6"/>
<text class="t c" x="677" y="462">Actor B · RUNNING</text>
<text class="s c" x="677" y="482">沙箱 · 无凭证 · 默认拒绝</text>
<line class="ln-dp" x1="544" y1="389" x2="564" y2="389" marker-end="url(#sd-ar-dp)"/>
<line class="ln-dp" x1="566" y1="469" x2="546" y2="469" marker-end="url(#sd-ar-dp)"/>
<rect class="dp" x="852" y="290" width="163" height="124" rx="8"/>
<text class="t c" x="933" y="314">atenet-egress</text>
<text class="s c" x="933" y="336">Envoy + ext-proc</text>
<text class="s c" x="933" y="354">sdsmint · MITM 证书</text>
<text class="s c" x="933" y="372">Rust 策略模块</text>
<text class="s c m" x="933" y="400">ate-system</text>
<rect class="box" x="1048" y="322" width="120" height="60" rx="8"/>
<text class="t c" x="1108" y="346">外部服务</text>
<text class="s c" x="1108" y="366">SaaS API / Internet</text>
<rect class="box" x="470" y="578" width="260" height="46" rx="8"/>
<text class="t c" x="600" y="598">对象存储（GCS / S3）</text>
<text class="s c" x="600" y="615">快照 · 沙箱资产</text>
<path class="ln-cp" d="M400,268 H372 V601 H468" marker-end="url(#sd-ar-cp)"/>
<text class="s e tcp halo" x="366" y="480">上传 / 下载快照</text>
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
<figcaption>worker Pod 连不到 API，worker 发往控制面的调用（注册、签证书、上报）都经由本节点的 atelet 转发到 WorkerService。ate-api-server 的 objectstore-plugin sidecar 负责整份快照的复制和删除，图中没有画这条线。</figcaption>
</figure>

| 组件 | 部署形态 | 职责 | 代码 |
| --- | --- | --- | --- |
| ate-api-server | Deployment | Control API、WorkerService、调度、workflow、认证授权 | `cmd/ateapi` |
| Postgres | StatefulSet，或 Cloud SQL | actor / worker 状态、lease | `cmd/ateapi/internal/store/atepg` |
| atecontroller | Deployment | WorkerPool → Deployment、每池 NetworkPolicy、worker Pod → Worker 记录 | `cmd/atecontroller` |
| atelet | DaemonSet | 节点 supervisor：驱动 ateworker、搬运和缓存快照 | `cmd/atelet` |
| ateworker | worker Pod 的容器 | 调用沙箱运行时做 run / checkpoint / restore，托管 atunnel | `cmd/ateworker-gvisor`、`cmd/ateworker-microvm` |
| atunnel | ateworker 进程内 | ingress 终点、egress 隧道客户端、DNS relay | `internal/atunnel` |
| atenet-router | Deployment：Envoy + atenet | ingress：resume、parking、路由，并下发 xDS | `cmd/atenet/internal/router` |
| atenet-egress | Deployment：Envoy + ext-proc + sdsmint | egress 策略执行点（PEP）、凭证注入 | `cmd/atenet/internal/router/egress` |
| objectstore-plugin | atelet 和 ate-api-server 的 sidecar | 经 Unix socket 提供 `objectstore.v1`，后端是 GCS 或 S3 | `cmd/objectstore-plugin` |
| podcertcontroller | Deployment | 签发组件证书（podid、servicedns、postgres 三个 signer） | `cmd/podcertcontroller` |
| ate-idp-server | Deployment | 发布 actor JWT 的 OIDC discovery 和 JWKS | `cmd/ate-idp-server` |
| kubectl-ate | CLI | 调用 Control API | `cmd/kubectl-ate` |

## 控制面

控制面只做高频、低延迟的 actor 操作。Pod 的创建、扩缩、节点供给仍然交给 Kubernetes。

<figure class="sd-figure wide">
<div class="sd-scroll" style="--minw:820px">
<svg class="sd" viewBox="0 0 1000 430" role="img" aria-label="ate-api-server 内部：调用方先经认证、再经 OpenFGA 授权，进入 Control 服务和 ActorWorkflow；workflow 用调度器选 worker，调度器读 workercache；workflow 调用 atelet 和 objectstore-plugin，并读写 store；atelet 调用 WorkerService。">
<rect class="box" x="10" y="56" width="150" height="40" rx="8"/>
<text class="t sm c" x="85" y="81">kubectl ate / SDK</text>
<rect class="box" x="10" y="108" width="150" height="40" rx="8"/>
<text class="t sm c" x="85" y="133">router ext_proc</text>
<rect class="box" x="10" y="160" width="150" height="40" rx="8"/>
<text class="t sm c" x="85" y="185">egress ext_proc</text>
<rect class="box" x="10" y="330" width="150" height="48" rx="8"/>
<text class="t sm c" x="85" y="350">atelet</text>
<text class="s c" x="85" y="368">代表本节点 worker</text>
<rect class="zone" x="185" y="16" width="625" height="400" rx="12"/>
<text class="t tcp" x="200" y="38">ate-api-server</text>
<rect class="cp" x="200" y="56" width="130" height="134" rx="8"/>
<text class="t c" x="265" y="82">认证</text>
<text class="s c m" x="265" y="100">apiauthn</text>
<text class="s c" x="265" y="128">SPIFFE mTLS</text>
<text class="s c" x="265" y="146">OIDC JWTProvider</text>
<rect class="cp" x="350" y="56" width="130" height="134" rx="8"/>
<text class="t c" x="415" y="82">授权</text>
<text class="s c m" x="415" y="100">authz</text>
<text class="s c" x="415" y="128">OpenFGA model.fga</text>
<text class="s c" x="415" y="146">按全局 / atespace</text>
<text class="s c" x="415" y="164">无条目的 RPC 拒绝</text>
<rect class="cp" x="500" y="56" width="130" height="134" rx="8"/>
<text class="t c" x="565" y="82">Control 服务</text>
<text class="s c m" x="565" y="100">controlapi</text>
<text class="s c" x="565" y="128">资源 CRUD</text>
<text class="s c" x="565" y="146">Resume / Suspend</text>
<text class="s c" x="565" y="164">Revert / Delete</text>
<rect class="cp" x="650" y="56" width="140" height="134" rx="8"/>
<text class="t c" x="720" y="82">ActorWorkflow</text>
<text class="s c m" x="720" y="100">workflow_*.go</text>
<text class="s c" x="720" y="128">actor lease 串行化</text>
<text class="s c" x="720" y="146">分步骤执行</text>
<text class="s c" x="720" y="164">每步一个 trace span</text>
<rect class="cp" x="500" y="236" width="130" height="64" rx="8"/>
<text class="t c" x="565" y="260">调度器</text>
<text class="s c" x="565" y="282">P2C 选 worker</text>
<rect class="cp" x="650" y="236" width="140" height="64" rx="8"/>
<text class="t c" x="720" y="260">workercache</text>
<text class="s c" x="720" y="282">内存中的 worker 视图</text>
<rect class="cp" x="200" y="330" width="280" height="70" rx="8"/>
<text class="t c" x="340" y="354">WorkerService</text>
<text class="s c" x="340" y="374">RegisterWorker · 签发 actor 证书 / JWT</text>
<text class="s c" x="340" y="390">上报快照状态 · 上报崩溃</text>
<rect class="cp" x="500" y="330" width="290" height="70" rx="8"/>
<text class="t c" x="645" y="354">store（atepg）</text>
<text class="s c" x="645" y="374">Actor · Worker · ActorAssignment</text>
<text class="s c" x="645" y="390">lease · watch</text>
<rect class="box" x="840" y="46" width="150" height="70" rx="8"/>
<text class="t c" x="915" y="68">atelet</text>
<text class="s c" x="915" y="88">Run · Restore · Checkpoint</text>
<text class="s c" x="915" y="104">DetachActor · Terminate</text>
<rect class="box" x="840" y="146" width="150" height="54" rx="8"/>
<text class="t sm c" x="915" y="168">objectstore-plugin</text>
<text class="s c" x="915" y="186">复制 / 删除快照</text>
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
<figcaption>三个外部调用方走同一条认证、授权链。atelet 用节点身份调用 WorkerService，这是另一组 RPC，worker 用它向控制面报告自己。</figcaption>
</figure>

- <strong>认证再授权。</strong>每个 RPC 先由 `apiauthn` 认证（SPIFFE mTLS 或配置的 OIDC `JWTProvider`），再按内嵌的 OpenFGA 模型 `authz/model.fga` 授权。没有写进 `defaultRPCPermissions` 的 RPC 一律拒绝，漏写会让测试失败。
- <strong>actor 级串行化。</strong>`ActorWorkflow` 先拿到 actor 的 lease 再执行步骤，所以同一个 actor 的 resume、suspend 不会并发执行。冲突时返回 `Aborted`，router 会重试。
- <strong>调度。</strong>`scheduling.Schedule` 先按约束过滤，再随机取两个候选，留下负载较低的一个（power of two choices）。负载取槽位利用率和 CPU / 内存利用率中较高者。约束包括：快照的 sandbox class、快照的 runtime 版本、ActorTemplate 和 Actor 的 label selector，以及快照还在节点本地时的 `RequiredNodes`。
- <strong>调度不查库。</strong>`workercache` 通过 store 的 `WatchWorkers` 维护内存中的 worker 视图，调度器只读内存。
- <strong>Worker 的来源。</strong>atecontroller 的 `workersync` 监听 worker Pod，调 `CreateWorker` / `DeleteWorker` 维护 Worker 记录；worker 进程启动后经 atelet 调 `RegisterWorker` 登记自己，再调 `SetWorkerSandboxRuntimes` 报告可用的 sandbox runtime。调度器只选已登记的 worker。

## 一次请求如何唤醒 actor

下图是 actor 处于 SUSPENDED 时，一个 HTTP 请求的完整路径。actor 已经在运行时，`ResumeActor` 直接返回当前 worker 地址，中间的恢复步骤都会跳过。

<figure class="sd-figure wide">
<div class="sd-scroll" style="--minw:820px">
<svg class="sd" viewBox="0 0 980 650" role="img" aria-label="ResumeActor 时序图：调用方到 Envoy，Envoy 到 ext_proc，ext_proc 调 ateapi ResumeActor；ateapi 依次加载、建卷、分配 worker、挂卷，然后调 atelet Restore；atelet 取快照并调 ateworker RestoreWorkload；就绪后 worker IP 回到 ext_proc，Envoy 通过 mTLS CONNECT 把请求送到 atunnel，再转给 actor。">
<rect class="box" x="6" y="14" width="116" height="40" rx="8"/><text class="t sm c" x="64" y="39">调用方</text>
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
<text class="s c halo" x="252" y="110">ext_proc 请求头</text>
<line class="ln-cp" x1="320" y1="150" x2="473" y2="150" marker-end="url(#sd-ar-cp)"/>
<text class="s c halo" x="397" y="142">ResumeActor</text>
<rect class="dp" x="236" y="166" width="150" height="48" rx="6"/>
<text class="s" x="246" y="186">无空闲 worker 时</text>
<text class="s" x="246" y="204">停车重试，预算 5s</text>
<rect class="cp" x="402" y="166" width="150" height="96" rx="6"/>
<text class="s" x="412" y="186">① LoadActorForResume</text>
<text class="s" x="412" y="204">② CreateVolumes</text>
<text class="s" x="412" y="222">③ AssignWorker（调度）</text>
<text class="s" x="412" y="240">④ AttachVolumes</text>
<line class="ln-cp" x1="475" y1="290" x2="623" y2="290" marker-end="url(#sd-ar-cp)"/>
<text class="s c halo" x="550" y="282">⑤ Restore</text>
<rect class="cp" x="560" y="304" width="130" height="40" rx="6"/>
<text class="s c" x="625" y="320">取快照：节点本地</text>
<text class="s c" x="625" y="336">或对象存储</text>
<line class="ln" x1="625" y1="368" x2="773" y2="368" marker-end="url(#sd-ar)"/>
<text class="s c m halo" x="700" y="360">RestoreWorkload</text>
<rect class="dp" x="712" y="382" width="126" height="40" rx="6"/>
<text class="s c" x="775" y="398">恢复沙箱</text>
<text class="s c" x="775" y="414">激活 actor 网络</text>
<line class="ln ret" x1="775" y1="446" x2="627" y2="446" marker-end="url(#sd-ar)"/>
<text class="s c halo" x="700" y="438">就绪</text>
<line class="ln-cp ret" x1="625" y1="476" x2="477" y2="476" marker-end="url(#sd-ar-cp)"/>
<text class="s c halo" x="550" y="468">worker IP</text>
<rect class="cp" x="390" y="490" width="170" height="26" rx="6"/>
<text class="s c" x="475" y="507">⑥ FinalizeRunning → RUNNING</text>
<line class="ln-cp ret" x1="475" y1="540" x2="322" y2="540" marker-end="url(#sd-ar-cp)"/>
<text class="s c halo" x="397" y="532">Actor + worker 地址</text>
<line class="ln-dp ret" x1="320" y1="568" x2="187" y2="568" marker-end="url(#sd-ar-dp)"/>
<text class="s c halo" x="252" y="560">dynamic metadata</text>
<line class="ln-dp" x1="185" y1="598" x2="773" y2="598" marker-end="url(#sd-ar-dp)"/>
<text class="s c halo tdp" x="480" y="590">mTLS CONNECT :8443（ORIGINAL_DST 集群）</text>
<line class="ln-dp" x1="775" y1="626" x2="913" y2="626" marker-end="url(#sd-ar-dp)"/>
<text class="s c halo" x="845" y="618">转发到 actor 端口</text>
</svg>
</div>
<figcaption>①到⑥对应 <code>workflow_resume.go</code> 里的 <code>stepSpan</code> 名称。响应沿原路返回，图中省略。</figcaption>
</figure>

## Actor 生命周期与快照

<figure class="sd-figure wide">
<div class="sd-scroll" style="--minw:760px">
<svg class="sd" viewBox="0 0 960 290" role="img" aria-label="Actor 状态机：CreateActor 进入 SUSPENDED；ResumeActor 到 RESUMING；restore 完成到 RUNNING；SuspendActor 到 SUSPENDING；checkpoint 写完回到 SUSPENDED；RUNNING 崩溃到 CRASHED；RevertActor 回到 SUSPENDED。">
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
<text class="s c" x="490" y="106">restore 完成</text>
<line class="ln" x1="680" y1="115" x2="758" y2="115" marker-end="url(#sd-ar)"/>
<text class="s c" x="720" y="106">SuspendActor</text>
<path class="ln" d="M845,92 V52 H145 V90" marker-end="url(#sd-ar)"/>
<text class="s c halo" x="495" y="47">checkpoint 写完（同步模式还要等上传完成）</text>
<line class="ln-sec" x1="605" y1="138" x2="605" y2="220" marker-end="url(#sd-ar-sec)"/>
<text class="s tsec" x="612" y="184">ReportActorCrashed</text>
<path class="ln" d="M530,242 H145 V140" marker-end="url(#sd-ar)"/>
<text class="s c halo" x="338" y="236">RevertActor：回到最后一份持久快照</text>
<text class="s" x="740" y="248">DeleteActor：任何状态都可调用</text>
</svg>
</div>
<figcaption>RevertActor 也接受 RUNNING，以及快照还在节点本地的 SUSPENDED。它会终止沙箱、丢弃本地或未完成的快照，保留最后一份已完成的外部快照。</figcaption>
</figure>

快照最多包含三层状态，由 ActorTemplate 的 `snapshotConfig.preferredFidelity` 决定。

<figure class="sd-figure">
<div class="sd-scroll" style="--minw:420px">
<svg class="sd" viewBox="0 0 520 210" role="img" aria-label="快照的三层保真度：MEMORY 包含 ROOTFS，ROOTFS 包含 VOLUMES。">
<rect class="dp" x="10" y="10" width="500" height="190" rx="10"/>
<text class="t" x="26" y="34">MEMORY（默认）</text>
<text class="s" x="26" y="52">再加上进程树和内存</text>
<rect class="box" x="30" y="66" width="460" height="124" rx="8"/>
<text class="t" x="46" y="90">ROOTFS</text>
<text class="s" x="46" y="108">再加上容器 rootfs 写入层</text>
<rect class="cp" x="50" y="122" width="420" height="56" rx="6"/>
<text class="t" x="66" y="146">VOLUMES</text>
<text class="s" x="66" y="164">DurableDir 卷的内容</text>
</svg>
</div>
<figcaption>由 ActorTemplate 的 <code>snapshotConfig.preferredFidelity</code> 决定，三层逐层包含。</figcaption>
</figure>

- <strong>异步挂起（默认）：</strong>本地 checkpoint 写完就返回，`durableSnapshot` 为 `PENDING`；atelet 在后台上传，完成后变为 `COMPLETED`。节点在上传前挂掉会丢状态。
- <strong>同步挂起：</strong>`DURABILITY_MODE_SYNCHRONOUS`，上传完成才返回。
- <strong>节点本地副本：</strong>挂起后快照留在节点上（`locality`），下次 resume 优先调度回该节点，省去对象存储往返。`--snapshot-detach-delay` 之后删除本地副本。
- <strong>runtime 版本：</strong>MEMORY 快照记录 sandbox runtime 版本，只能恢复到仍提供该版本的 worker；没有这样的 worker 有空位时，退回 ROOTFS 冷启动。
- <strong>Tag：</strong>atespace 内的不可变别名，持有自己的快照副本，发布后可被其他 atespace 复用。

gVisor 用 `runsc` 的 checkpoint / restore；microVM 跑在 Kata + Cloud Hypervisor 上，内存快照用 `userfaultfd` 按需换页恢复，rootfs 写入层和 DurableDir 都经 virtio-fs 放在宿主机侧，以 tar 形式随快照传输。

## Ingress：从调用方到 actor

<figure class="sd-figure wide">
<div class="sd-scroll" style="--minw:860px">
<svg class="sd" viewBox="0 0 1040 340" role="img" aria-label="atenet-router 内部：:8443 监听器经 ext_proc 过滤器到 ORIGINAL_DST 集群，再经 mTLS 到 worker 上的 atunnel 和 actor；ext_proc 过滤器与 atenet 进程之间是 gRPC 流，atenet 调用 ate-api-server；:8444 监听器终止 CONNECT 后把字节送进 internal listener，再回到同一个 ext_proc 过滤器。">
<rect class="zone" x="160" y="16" width="545" height="318" rx="12"/>
<text class="s" x="176" y="36">atenet-router Pod（Envoy 容器 + atenet 容器）</text>
<rect class="box" x="8" y="55" width="130" height="60" rx="8"/>
<text class="t sm c" x="73" y="80">调用方</text>
<text class="s c" x="73" y="98">HTTPS · Svc :443</text>
<rect class="box" x="8" y="262" width="130" height="60" rx="8"/>
<text class="t sm c" x="73" y="287">调用方</text>
<text class="s c m" x="73" y="305">CONNECT host:9090</text>
<rect class="dp" x="178" y="55" width="124" height="60" rx="8"/>
<text class="t sm c" x="240" y="80">listener :8443</text>
<text class="s c" x="240" y="98">HTTP 连接管理器</text>
<rect class="dp" x="330" y="55" width="124" height="60" rx="8"/>
<text class="t sm c" x="392" y="80">ext_proc 过滤器</text>
<text class="s c" x="392" y="98">读 ate-target-actor</text>
<rect class="dp" x="530" y="55" width="160" height="60" rx="8"/>
<text class="t sm c" x="610" y="80">ORIGINAL_DST 集群</text>
<text class="s c" x="610" y="98">目标来自 dynamic metadata</text>
<rect class="cp" x="480" y="150" width="210" height="96" rx="8"/>
<text class="t sm c" x="585" y="172">atenet（ext_proc 服务）</text>
<text class="s c m" x="585" y="192">ingress.Handler</text>
<text class="s c" x="585" y="210">flight 去重 · parking lot</text>
<text class="s c" x="585" y="228">同时作为 Envoy 的 xDS 服务</text>
<rect class="dp" x="178" y="262" width="124" height="60" rx="8"/>
<text class="t sm c" x="240" y="287">listener :8444</text>
<text class="s c" x="240" y="305">终止 CONNECT</text>
<rect class="dp" x="330" y="262" width="124" height="60" rx="8"/>
<text class="t sm c" x="392" y="287">internal listener</text>
<text class="s c m" x="392" y="305">main_internal</text>
<rect class="dp" x="740" y="55" width="140" height="60" rx="8"/>
<text class="t sm c" x="810" y="80">atunnel :8443</text>
<text class="s c" x="810" y="98">读 TargetPortHeader</text>
<rect class="sbx" x="910" y="55" width="122" height="60" rx="8"/>
<text class="t sm c" x="971" y="80">Actor</text>
<text class="s c" x="971" y="98">默认端口 80</text>
<rect class="cp" x="740" y="168" width="140" height="60" rx="8"/>
<text class="t sm c" x="810" y="193">ate-api-server</text>
<text class="s c" x="810" y="211">ResumeActor</text>
<text class="s" x="740" y="266">Host 头只是应用的 authority，</text>
<text class="s" x="740" y="282">不参与选择 actor。</text>
<line class="ln-dp" x1="138" y1="85" x2="176" y2="85" marker-end="url(#sd-ar-dp)"/>
<line class="ln-dp" x1="302" y1="85" x2="328" y2="85" marker-end="url(#sd-ar-dp)"/>
<line class="ln-dp" x1="454" y1="85" x2="528" y2="85" marker-end="url(#sd-ar-dp)"/>
<text class="s c halo" x="491" y="77">放行</text>
<path class="ln-cp" d="M420,117 V198 H478" marker-start="url(#sd-ar-cp)" marker-end="url(#sd-ar-cp)"/>
<text class="s tcp halo" x="426" y="150">gRPC 流</text>
<line class="ln-dp" x1="138" y1="292" x2="176" y2="292" marker-end="url(#sd-ar-dp)"/>
<line class="ln-dp" x1="302" y1="292" x2="328" y2="292" marker-end="url(#sd-ar-dp)"/>
<line class="ln-dp" x1="380" y1="262" x2="380" y2="117" marker-end="url(#sd-ar-dp)"/>
<text class="s e tdp halo" x="372" y="196">隧道内每个请求</text>
<line class="ln-cp" x1="690" y1="198" x2="738" y2="198" marker-end="url(#sd-ar-cp)"/>
<line class="ln-dp" x1="690" y1="85" x2="738" y2="85" marker-end="url(#sd-ar-dp)"/>
<text class="s c tdp" x="714" y="77">mTLS</text>
<line class="ln-dp" x1="880" y1="85" x2="908" y2="85" marker-end="url(#sd-ar-dp)"/>
</svg>
</div>
<figcaption>非默认端口的 CONNECT 流量被终止后重新进入同一条 ext_proc 路径，所以长连接隧道里的每个请求都会单独 resume、单独路由；actor 换了 worker 也能跟上。目前只支持隧道内的 HTTP(S)。</figcaption>
</figure>

- <strong>选 actor：</strong>header `ate-target-actor: <atespace>/<actor>`。ingress 处理器把所有请求头都当作未认证的客户端输入。
- <strong>选上游：</strong>ext_proc 把 worker 地址写进 dynamic metadata，而不是 header；Envoy 的 `ORIGINAL_DST` 集群据此拨号。actor 端口要传给 atunnel 进程，所以仍走 header `TargetPortHeader`。
- <strong>到 worker 的一跳</strong>默认是 mTLS CONNECT 到 :8443；`--upstream-connect=false` 时改为 HTTPS 到 :443。
- <strong>请求合并：</strong>同一 actor 的并发请求在 resumer 的 flight 表里合并成一次 `ResumeActor` 调用。
- <strong>优雅下线：</strong>收到 SIGTERM 后依次翻转 `/readyz`、等 endpoint 传播、通过 admin API 让 Envoy drain、停掉 ext_proc，最后写一个 marker 放行 Envoy 容器的 `preStop`。停车中的请求能拿到完整预算。

### Request parking：池子满了先等一等

多路复用意味着突发流量会短暂占满 worker 池，`ResumeActor` 返回 `ResourceExhausted`。通常几毫秒后就有别的 actor 挂起、腾出槽位，所以 router 不立刻回 503，而是把请求“停”下来，指数退避重试，直到成功或用完预算。

| ResumeActor 结果 | router 行为 | 客户端看到 |
| --- | --- | --- |
| `OK` | 路由 | actor 的响应 |
| `Aborted`（并发 resume 冲突） | 总是重试 | 成功后正常响应 |
| `ResourceExhausted`（没有空槽位） | 停车重试 | 超出预算后 503 |
| `Unavailable`（控制面抖动、滚动升级） | 停车重试 | 超出预算后 503 |
| `FailedPrecondition`（如 CRASHED） | 立即失败 | 503 |
| `NotFound` | 立即失败 | 404 |
| `DeadlineExceeded` | 立即失败 | 504 |
| `PermissionDenied` / `Unauthenticated` | 立即失败 | 403 / 401 |

- `--parked-request-budget`（默认 5s）按 flight 计算：后加入同一 flight 的请求共享剩余预算。预算只限制发起新的重试，已经在途的 resume 不会被取消，晚到的结果仍会被服务。
- `--parked-request-max`（默认 1024）是停车场容量，满了直接回 503 “router at capacity”。设为 0 关闭停车。
- 每个停车请求占一条 ext_proc 流，所以 Envoy 对 ext_proc 集群的 circuit breaker 是硬上限。`--extproc-max-requests` 默认取停车场容量的两倍（至少 1024），留一半给已在运行的 actor 的快速路径。

## Egress：actor 的出站流量

actor 的出站 TCP 都要经过一个策略执行点（PEP），默认是 `atenet-egress`。唯一的例外是发往沙箱网关 53 端口的 DNS，由 atunnel 里的 DNS relay 处理。

<figure class="sd-figure wide">
<div class="sd-scroll" style="--minw:900px">
<svg class="sd" viewBox="0 0 1080 450" role="img" aria-label="Egress 路径：actor 进程的 TCP 连接被 nftables 重定向到 atunnel，atunnel 用 actor 证书向 atenet-egress 发起 mTLS CONNECT；外层链验证证书并向 ate-api-server 查询 actor 和策略，然后按协议进入三条内层链之一，再到上游。DNS 查询由 DNS relay 只为策略中的主机名转发。">
<rect class="sbx" x="8" y="40" width="170" height="330" rx="10"/>
<text class="t sm tsec" x="22" y="62">Actor 沙箱</text>
<text class="s" x="22" y="78">不可信</text>
<rect class="box" x="22" y="96" width="142" height="56" rx="6"/>
<text class="t sm c" x="93" y="120">actor 进程</text>
<text class="s c m" x="93" y="138">connect(IP:port)</text>
<rect class="box" x="22" y="270" width="142" height="56" rx="6"/>
<text class="t sm c" x="93" y="294">DNS 查询</text>
<text class="s c" x="93" y="312">发往网关 :53</text>
<rect class="zone" x="200" y="40" width="230" height="330" rx="10"/>
<text class="t sm" x="214" y="62">Worker Pod</text>
<text class="s" x="214" y="78">ateworker · 可信</text>
<rect class="box" x="214" y="96" width="202" height="56" rx="6"/>
<text class="t sm c" x="315" y="118">nftables</text>
<text class="s c" x="315" y="136">TCP REDIRECT · 其它丢弃</text>
<rect class="dp" x="214" y="170" width="202" height="80" rx="6"/>
<text class="t sm c" x="315" y="192">atunnel</text>
<text class="s c m" x="315" y="210">SO_ORIGINAL_DST</text>
<text class="s c" x="315" y="228">actor 私钥 + 短期证书</text>
<text class="s c" x="315" y="244">证书经 atelet 签发</text>
<rect class="dp" x="214" y="270" width="202" height="56" rx="6"/>
<text class="t sm c" x="315" y="292">DNS relay</text>
<text class="s c" x="315" y="310">只解析策略里的主机名</text>
<text class="s c" x="315" y="348">其余返回 NXDOMAIN</text>
<rect class="zone" x="470" y="20" width="400" height="360" rx="12"/>
<text class="t sm" x="486" y="42">atenet-egress Pod（Envoy）</text>
<rect class="dp" x="486" y="58" width="170" height="306" rx="8"/>
<text class="t sm c" x="571" y="82">外层 CONNECT 链</text>
<text class="s c m" x="571" y="100">egress</text>
<text class="s" x="498" y="130">① 验证 actor 客户端证书</text>
<text class="s" x="498" y="148">（Actor Identity CA）</text>
<text class="s" x="498" y="178">② 问 ate-api-server：</text>
<text class="s" x="498" y="196">actor 存在 · UID 一致 ·</text>
<text class="s" x="498" y="214">RUNNING 或 RESUMING</text>
<text class="s" x="498" y="244">③ 取 EgressPolicy</text>
<text class="s" x="498" y="262">Rust 模块存 policy hash</text>
<text class="s" x="498" y="292">没有策略就不建隧道</text>
<rect class="box" x="690" y="58" width="165" height="90" rx="8"/>
<text class="t sm c m" x="772" y="80">egress_cleartext</text>
<text class="s c" x="772" y="100">明文 HTTP</text>
<text class="s c" x="772" y="118">每个请求按 Host 判定</text>
<text class="s c" x="772" y="136">拒绝返回 403</text>
<rect class="box" x="690" y="162" width="165" height="100" rx="8"/>
<text class="t sm c m" x="772" y="184">egress_tls_mitm</text>
<text class="s c" x="772" y="204">终止 TLS（sdsmint 签证书）</text>
<text class="s c" x="772" y="222">每个请求按 https 规则</text>
<text class="s c" x="772" y="240">在这里注入凭证</text>
<rect class="box" x="690" y="276" width="165" height="88" rx="8"/>
<text class="t sm c m" x="772" y="298">tls_passthrough</text>
<text class="s c" x="772" y="318">按 SNI 判定，不解密</text>
<text class="s c" x="772" y="336">不匹配则握手时断开</text>
<rect class="box" x="910" y="58" width="160" height="306" rx="8"/>
<text class="t sm c" x="990" y="204">上游</text>
<text class="s c" x="990" y="222">策略选定的目标</text>
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
<text class="s tcp halo" x="578" y="388">GetActor · 读策略</text>
</svg>
</div>
<figcaption>出站方向由 Envoy 告诉 ext_proc 的 filter chain 名称（<code>xds.filter_chain_name</code>）决定，请求内容无法伪造它。外层链按 TCP 连接判定一次，内层链对每个请求判定。</figcaption>
</figure>

- <strong>沙箱内只有一条路：</strong>除 53 端口 DNS 外的 TCP 都被重定向到 atunnel；其它 UDP 直接丢弃；其它协议用 ICMP 拒绝。`169.254.0.0/16` 和 `fe80::/10` 被阻断，actor 自己的 `169.254.17.0/30` 除外。
- <strong>actor 身份：</strong>workload 启动前，atunnel 生成私钥，经 atelet 申请短期证书。证书带 `ActorIdentity` 扩展（atespace、名字、UID、用途 `atunnel`）。
- <strong>不信任 actor 自报的目的地：</strong>网关可以把 Host 或 SNI 当作策略输入，但按主机名授权时会拨号到被授权的主机名，而不是 CONNECT 里给出的任意 IP。
- <strong>凭证注入：</strong>在 MITM 腿上按请求替换凭证，凭证来自 credential provider 插件（例如 `k8s-credential-provider` 读 Kubernetes Secret）或现场签发的 actor JWT。沙箱里不放任何凭证。
- <strong>策略更新：</strong>DNS 约 5 秒内跟上新策略；新连接在缓存的决策过期后用新策略；已授权的 passthrough 连接不会被中断。
- <strong>默认拒绝：</strong>WebSocket、其它 HTTP upgrade、actor 自己发起的 CONNECT 都返回 403。没有配置 PEP 或 PEP 故障时，出站流量被丢弃。

网关代码更细的分析见我之前的英文文章 [Fifty-Two Notes on Agent Substrate's Gateway]({{< relref "substrate-gateway-notes.md" >}})。

## 节点内部：atelet 与 ateworker

<figure class="sd-figure wide">
<div class="sd-scroll" style="--minw:820px">
<svg class="sd" viewBox="0 0 980 330" role="img" aria-label="节点内部：atelet 经 worker.sock 调用 ateworker 的 Worker 服务；ateworker 经 worker-support.sock 调用 atelet 的 WorkerSupport 服务；ateworker 驱动 gVisor 或 microVM 沙箱运行时；atelet 的 objectstore-plugin sidecar 负责快照和资产。">
<rect class="zone" x="8" y="8" width="964" height="314" rx="12"/>
<text class="t sm tk8s" x="24" y="30">一个 Kubernetes 节点</text>
<rect class="cp" x="24" y="46" width="330" height="262" rx="8"/>
<text class="t c" x="189" y="72">atelet（DaemonSet）</text>
<text class="s" x="40" y="100">实现 Atelet 服务（ate-api-server 调用）</text>
<text class="s" x="40" y="120">实现 WorkerSupport（worker 调用）</text>
<text class="s" x="40" y="140">snapshot cache · sandbox asset cache</text>
<text class="s" x="40" y="160">resetActorDirs：actor 之间清理目录</text>
<text class="s" x="40" y="180">把 worker 的调用转发到 WorkerService</text>
<rect class="box" x="40" y="206" width="298" height="86" rx="6"/>
<text class="t sm" x="56" y="230">objectstore-plugin（sidecar）</text>
<text class="s" x="56" y="250">NodeProvider：上传 / 下载快照文件</text>
<text class="s" x="56" y="268">AssetProvider：拉取沙箱资产</text>
<text class="s" x="56" y="284">sha256 不符就不交给 atelet</text>
<rect class="box" x="440" y="46" width="516" height="262" rx="8"/>
<text class="t sm" x="456" y="70">Worker Pod</text>
<rect class="dp" x="456" y="86" width="220" height="206" rx="6"/>
<text class="t c" x="566" y="110">ateworker</text>
<text class="s c" x="566" y="134">实现 Worker 服务</text>
<text class="s c m" x="566" y="158">RunWorkload</text>
<text class="s c m" x="566" y="176">RestoreWorkload</text>
<text class="s c m" x="566" y="194">CheckpointWorkload</text>
<text class="s c m" x="566" y="212">TerminateWorkload</text>
<text class="s c" x="566" y="240">托管 atunnel</text>
<text class="s c" x="566" y="258">为每个 actor 建 netns</text>
<rect class="sbx" x="704" y="86" width="236" height="92" rx="6"/>
<text class="t sm c" x="822" y="110">gVisor</text>
<text class="s c m" x="822" y="130">runsc checkpoint / restore</text>
<text class="s c m" x="822" y="150">ateworker-gvisor</text>
<text class="s c" x="822" y="194">按 WorkerPool 的 sandbox class 二选一</text>
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
<figcaption>两条 Unix socket 的路径定义在 <code>internal/nodepath</code>。atelet 和 worker 运行时只共享 <code>internal/proto/ateworkerpb</code> 和这些路径，彼此不依赖对方的实现，这样 atelet 将来可以先于 worker 升级。</figcaption>
</figure>

- sandbox 二进制不打进 worker 镜像，而是运行时从 WorkerPool 解析到的 `SandboxConfig` 获取，存进 atelet 的内容寻址缓存，再以硬链接交给每个 actor。
- atelet 自己没有对象存储凭证。GCS 上，它用只对单个 actor 有效的凭证搬运该 actor 的快照（S3 的按 actor 限权还在计划中）。

## 安全边界与身份

设计前提是：actor 里跑的都是不可信代码，不同租户的 actor 会挤在同一台机器上。每个 actor 有身份，但没有凭证、没有开放网络，也出不了沙箱。

| 边界 | 内容 |
| --- | --- |
| 1. 沙箱 | 每个 actor 在自己的 gVisor 或 microVM 里，有独立的 netns 和存储。不支持普通 Linux 容器。 |
| 2. 网关 | 入站只能经 atenet-router，出站只能经 atenet-egress 和 DNS relay。策略和凭证注入都在网关上。 |
| 3. 控制面 | actor 和 worker Pod 都连不到 Substrate API、它的数据库和 Kubernetes API。API 调用按 atespace 授权。 |
| 4. 存储 | 沙箱碰不到快照存储。只有 atelet 搬运快照，只有控制面复制或删除快照。 |

| 谁 | 身份 | 怎么校验 |
| --- | --- | --- |
| 组件之间 | podcertcontroller 签发的 Pod 证书（`podid`、`servicedns`、`postgres`） | 全部 mTLS，只有本地的 `/healthz`、`/readyz` 例外 |
| API 调用方 | SPIFFE mTLS 或 OIDC JWT | OpenFGA；AccessPolicy 按全局和 atespace 授权，初始 owner 是 `ate-client` ServiceAccount |
| actor | 1 小时有效的 SPIFFE X.509，加上按需签发的 actor JWT | egress 网关校验证书；外部服务用 ate-idp-server 发布的 JWKS 校验 JWT |
| 网络 | Kubernetes NetworkPolicy | `ate-system` 默认拒绝入站；atecontroller 为每个 WorkerPool 生成 NetworkPolicy |

Substrate 不负责最终用户到 actor 的认证授权。哪个用户能访问哪个 actor，由调用 Substrate 的应用层决定。

## 用 Envoy 的视角看

两个网关都跑同一个 `envoy-dataplane` 镜像（基于 `envoyproxy/envoy:v1.39.5`，加了一个 Rust dynamic module）。Substrate 的需求和对应的 Envoy 机制如下，表中 `router/` 指 `cmd/atenet/internal/router/`。

| 需求 | Envoy 机制 | 代码位置 |
| --- | --- | --- |
| 按 actor 动态选上游 | ext_proc 写 dynamic metadata，`ORIGINAL_DST` 集群据此拨号 | `router/ingress/`、`router/xds.go` |
| 配置下发 | atenet 内置 xDS server；agentgateway 模式改用静态 ConfigMap | `router/xds.go` |
| 非默认端口 ingress | CONNECT 终止 + internal listener 重入同一条 HCM | `xds.go` 的 `connect_terminate_tls`、`main_internal` |
| parking 背压 | ext_proc 集群的 circuit breaker `max_requests` | `router/xds.go`、`router/config.go` |
| 区分出站的几条腿 | ext_proc 读 `xds.filter_chain_name` 属性 | `router/extproc/dispatch.go` |
| 外层与内层之间传策略 | HTTP filter 把 policy hash 写进共享 filter state，内层 listener filter 读出并执行 | `cmd/dataplane/envoy/dynamic-modules/egress-policy` |
| MITM 叶证书 | SDS：`atenet sdsmint` 在 UDS 上按需签发 | `cmd/atenet/internal/sdsmint` |
| 优雅下线 | admin API drain + `preStop` 等待 drain-complete marker | `router/drain.go`、`router/envoydrain.go` |

## 建议的读代码顺序

1. [docs/concepts/architecture.md](https://github.com/agent-substrate/substrate/blob/a46464663fa076221ae059e1fd4a273edd2d8b09/docs/concepts/architecture.md) 和 [docs/reference/resource-model.md](https://github.com/agent-substrate/substrate/blob/a46464663fa076221ae059e1fd4a273edd2d8b09/docs/reference/resource-model.md)：官方架构和资源模型。
2. [api/ateapi/v1/ateapi.proto](https://github.com/agent-substrate/substrate/blob/a46464663fa076221ae059e1fd4a273edd2d8b09/api/ateapi/v1/ateapi.proto)：`Control` 和 `WorkerService` 两个服务。
3. [controlapi/workflow_resume.go](https://github.com/agent-substrate/substrate/blob/a46464663fa076221ae059e1fd4a273edd2d8b09/cmd/ateapi/internal/controlapi/workflow_resume.go)，然后是 `workflow_suspend.go`：最核心的两条路径。
4. [scheduling/scheduling.go](https://github.com/agent-substrate/substrate/blob/a46464663fa076221ae059e1fd4a273edd2d8b09/cmd/ateapi/internal/scheduling/scheduling.go)：约 340 行的调度器。
5. [cmd/atenet/internal/router/README.md](https://github.com/agent-substrate/substrate/blob/a46464663fa076221ae059e1fd4a273edd2d8b09/cmd/atenet/internal/router/README.md)，然后是 `ingress/resumer.go`、`ingress/parking.go`，配合 [request-parking.md](https://github.com/agent-substrate/substrate/blob/a46464663fa076221ae059e1fd4a273edd2d8b09/docs/concepts/request-parking.md)。
6. [docs/guides/network-egress.md](https://github.com/agent-substrate/substrate/blob/a46464663fa076221ae059e1fd4a273edd2d8b09/docs/guides/network-egress.md)，然后是 `router/egress/egress.go` 和 Rust 模块。
7. `internal/proto/ateletpb/atelet.proto` 和 `internal/proto/ateworkerpb/ateworker.proto`：节点上的两个接口。
8. `cmd/atecontroller/internal/controllers/workerpool_controller.go` 和 `workersync/syncer.go`：Kubernetes 侧如何接入。
