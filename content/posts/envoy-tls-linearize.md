---
title: "A Copy That Never Stops Copying"
date: 2026-08-14T10:00:00+08:00
draft: false
tags: ["envoy", "performance", "tls", "cpp", "buffers", "learning-notes"]
summary: "Envoy's TLS write path re-copies 16 KB on every write, forever. The obvious fix does nothing — and why it does nothing is the whole point."
---

*Learning notes. I went looking for CPU waste in Envoy's hot paths, found something structural, and the fix I reached for first was useless.*

**Envoy's TLS write path copies 16 KB for every 16 KB it sends — permanently, by construction.**

| | |
| --- | --- |
| Where | `SslSocket::doWrite` |
| Cost | one 16 KB malloc + 16 KB memcpy + free, per 16 KB of TLS egress |
| Trigger | any HTTP response: small headers in front of a full-size body |
| Fix | emit one short TLS record, once |
| Result | **−9.9% CPU**, copies per iteration **10 → 1** |

## The line

```cpp
int rc = SSL_write(rawSsl(), write_buffer.linearize(bytes_to_write), bytes_to_write);
```

`SSL_write()` needs contiguous memory. Envoy's write buffer is a chain of slices. `linearize()` bridges them: if the front slice already holds enough, it returns a pointer for free; otherwise it allocates, copies, and returns that.

It reads like a fallback. It's the steady state.

## Why it never recovers

Two numbers matter, and they're the same number: the most Envoy hands to one `SSL_write()` is **16384** bytes, and `Slice::default_slice_size_` — the size of the slices the read path produces — is also **16384**.

So `linearize(16384)` on a short front slice copies 16 KB into a fresh slice, drains 16 KB, and pushes the new slice to the front. That drain eats the short slice *and* bites into the next one, leaving it short by exactly the same offset.

<figure>
<svg width="100%" viewBox="0 0 720 330" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>Why the write buffer never re-aligns</title>
<desc>Before the write, the buffer is a short header slice followed by full 16 KB slices. After linearize copies, writes and drains, the buffer is again a short slice followed by full 16 KB slices — the same shape, so the next write copies again.</desc>
<rect x="1" y="1" width="718" height="328" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<text x="24" y="34" font-family="system-ui,sans-serif" font-size="13" font-weight="600" fill="#8C1D1D">Before the write</text>
<rect x="24" y="48" width="58" height="34" rx="5" fill="#FDF0EF" stroke="#B22B2B"/>
<text x="53" y="70" font-family="system-ui,sans-serif" font-size="11" fill="#8C1D1D" text-anchor="middle">200 B</text>
<rect x="86" y="48" width="185" height="34" rx="5" fill="#f6f8fa" stroke="#57606a"/>
<text x="178" y="70" font-family="system-ui,sans-serif" font-size="11.5" fill="#1f2328" text-anchor="middle">16 KB</text>
<rect x="275" y="48" width="185" height="34" rx="5" fill="#f6f8fa" stroke="#57606a"/>
<text x="367" y="70" font-family="system-ui,sans-serif" font-size="11.5" fill="#1f2328" text-anchor="middle">16 KB</text>
<text x="474" y="70" font-family="system-ui,sans-serif" font-size="12" fill="#57606a">…</text>
<text x="500" y="66" font-family="system-ui,sans-serif" font-size="11" fill="#8C1D1D">front slice is short</text>
<line x1="24" y1="104" x2="696" y2="104" stroke="#eaeef2"/>
<text x="24" y="128" font-family="system-ui,sans-serif" font-size="11.5" fill="#57606a">linearize(16 KB): allocate 16 KB · memcpy 16 KB across the boundary · drain 16 KB · write</text>
<line x1="24" y1="142" x2="696" y2="142" stroke="#eaeef2"/>
<text x="24" y="176" font-family="system-ui,sans-serif" font-size="13" font-weight="600" fill="#8C1D1D">After the write</text>
<rect x="24" y="190" width="58" height="34" rx="5" fill="#FDF0EF" stroke="#B22B2B"/>
<text x="53" y="212" font-family="system-ui,sans-serif" font-size="11" fill="#8C1D1D" text-anchor="middle">200 B</text>
<rect x="86" y="190" width="185" height="34" rx="5" fill="#f6f8fa" stroke="#57606a"/>
<text x="178" y="212" font-family="system-ui,sans-serif" font-size="11.5" fill="#1f2328" text-anchor="middle">16 KB</text>
<rect x="275" y="190" width="185" height="34" rx="5" fill="#f6f8fa" stroke="#57606a"/>
<text x="367" y="212" font-family="system-ui,sans-serif" font-size="11.5" fill="#1f2328" text-anchor="middle">16 KB</text>
<text x="474" y="212" font-family="system-ui,sans-serif" font-size="12" fill="#57606a">…</text>
<text x="500" y="208" font-family="system-ui,sans-serif" font-size="11" fill="#8C1D1D">short again</text>
<path d="M660 224 C 690 224 690 48 660 48" fill="none" stroke="#B22B2B" stroke-width="1.6"/>
<path d="M666 54 L 658 47 L 666 41" fill="none" stroke="#B22B2B" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
<text x="612" y="140" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#B22B2B" text-anchor="middle">identical shape</text>
<text x="360" y="286" font-family="system-ui,sans-serif" font-size="11.5" fill="#57606a" text-anchor="middle">The state after the write is the state before it. No sequence of writes recovers alignment.</text>
<text x="360" y="308" font-family="system-ui,sans-serif" font-size="11.5" font-weight="600" fill="#8C1D1D" text-anchor="middle">One 16 KB malloc + one 16 KB memcpy + one free, per 16 KB, for the life of the connection.</text>
</svg>
<figcaption>The buffer is self-similar across the operation meant to fix it. That's the whole bug.</figcaption>
</figure>

Getting into this shape is trivial: `ConnectionImpl::write()` moves codec slices in wholesale, so a few hundred bytes of headers ahead of a full-size body does it. That's an ordinary HTTP response.

## The fix that does nothing

Don't copy — just write whatever the front slice holds. `SSL_write()` accepts a smaller length; you get a smaller TLS record.

The worry is that a smaller record means an extra record, and Envoy issues one `writev` syscall per record. So: a size floor. Write the front slice directly *only if it's big enough to be worth a syscall*.

I implemented it. It changed nothing — same time, same copy count.

<figure>
<svg width="100%" viewBox="0 0 720 200" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>Why a size threshold can never fire</title>
<desc>A scale of front-slice size from 0 to 16 KB. The fast path opens only above a 4 KB threshold, but the bug pins the front slice at 200 bytes, far below it, so the guard is never true.</desc>
<rect x="1" y="1" width="718" height="198" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<text x="24" y="32" font-family="system-ui,sans-serif" font-size="13" font-weight="600" fill="#1f2328">Front slice size — where each path applies</text>
<rect x="60" y="60" width="150" height="30" fill="#FDF0EF" stroke="#B22B2B" stroke-width="1"/>
<rect x="210" y="60" width="450" height="30" fill="#E1F5EE" stroke="#0F6E56" stroke-width="1"/>
<text x="135" y="80" font-family="system-ui,sans-serif" font-size="11" fill="#8C1D1D" text-anchor="middle">copy path</text>
<text x="435" y="80" font-family="system-ui,sans-serif" font-size="11" fill="#085041" text-anchor="middle">fast path — write the front slice directly</text>
<line x1="210" y1="52" x2="210" y2="98" stroke="#1f2328" stroke-width="1.5"/>
<text x="210" y="114" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#1f2328" text-anchor="middle">4 KB threshold</text>
<text x="60" y="114" font-family="system-ui,sans-serif" font-size="11" fill="#57606a" text-anchor="middle">0</text>
<text x="660" y="114" font-family="system-ui,sans-serif" font-size="11" fill="#57606a" text-anchor="middle">16 KB</text>
<line x1="67" y1="60" x2="67" y2="150" stroke="#B22B2B" stroke-width="2"/>
<circle cx="67" cy="60" r="4" fill="#B22B2B"/>
<text x="82" y="146" font-family="system-ui,sans-serif" font-size="11.5" font-weight="600" fill="#8C1D1D">actual front slice: 200 B — and it stays there</text>
<text x="24" y="180" font-family="system-ui,sans-serif" font-size="11.5" fill="#57606a">The fast path opens at 4 KB. The bug pins the slice at 200 B. The guard is never true.</text>
</svg>
<figcaption>The condition guarding the fast path is precisely the condition the bug prevents.</figcaption>
</figure>

> The floor asks "is the front slice big enough?" The entire bug is that the front slice is **small**. The guard condition is exactly the condition that never holds when you need it.

A threshold can't escape a fixed point whose defining property is being under the threshold. Escaping *requires* one short record — that short write is what consumes the offset. The cost isn't avoidable, it's payable **once instead of forever**.

## But not unconditionally either

Always writing the front slice regresses ~6% on already-aligned buffers, where one copy genuinely does fix things and small records buy nothing.

So the decision can't come from the buffer's current state at all. "Front slice is short" is true in *both* the recoverable case and the fixed point. What separates them is whether the copy is about to **repeat** — one bit of history:

```cpp
if (front_slice.len_ >= bytes_to_write) {
  // Already contiguous — linearize() would return this pointer without copying.
  return {front_slice.mem_, bytes_to_write, false};
}

if (avoid_repeated_linearize && linearized_last_write && front_slice.len_ > 0) {
  // Copied last time, about to copy again: this chain is a fixed point.
  // One short record consumes the offset and aligns everything behind it.
  return {front_slice.mem_, front_slice.len_, false};
}

return {write_buffer.linearize(bytes_to_write), bytes_to_write, true};
```

<figure>
<svg width="100%" viewBox="0 0 720 300" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>How one short record escapes the cycle</title>
<desc>Step one: copied last write and about to copy again. Step two: write only the 200-byte front slice as a short record and drain it. Step three: the buffer now starts on a slice boundary, so every following write is contiguous and free.</desc>
<rect x="1" y="1" width="718" height="298" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<text x="24" y="32" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#8C1D1D">1 — copied last write, about to copy again</text>
<rect x="24" y="42" width="58" height="30" rx="5" fill="#FDF0EF" stroke="#B22B2B"/>
<text x="53" y="62" font-family="system-ui,sans-serif" font-size="11" fill="#8C1D1D" text-anchor="middle">200 B</text>
<rect x="86" y="42" width="185" height="30" rx="5" fill="#f6f8fa" stroke="#57606a"/>
<text x="178" y="62" font-family="system-ui,sans-serif" font-size="11.5" fill="#1f2328" text-anchor="middle">16 KB</text>
<rect x="275" y="42" width="185" height="30" rx="5" fill="#f6f8fa" stroke="#57606a"/>
<text x="367" y="62" font-family="system-ui,sans-serif" font-size="11.5" fill="#1f2328" text-anchor="middle">16 KB</text>
<text x="474" y="62" font-family="system-ui,sans-serif" font-size="12" fill="#57606a">…</text>
<text x="24" y="106" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#085041">2 — write just the front slice: one short record, no copy</text>
<rect x="24" y="116" width="58" height="30" rx="5" fill="#E1F5EE" stroke="#0F6E56" stroke-width="1.6"/>
<text x="53" y="136" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#085041" text-anchor="middle">200 B</text>
<path d="M92 131 L 128 131" fill="none" stroke="#0F6E56" stroke-width="1.6"/>
<path d="M122 126 L 129 131 L 122 136" fill="none" stroke="#0F6E56" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
<text x="138" y="135" font-family="system-ui,sans-serif" font-size="11.5" fill="#085041">written and drained — this consumes the offset</text>
<text x="24" y="182" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#085041">3 — aligned</text>
<rect x="24" y="192" width="185" height="30" rx="5" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="116" y="212" font-family="system-ui,sans-serif" font-size="11.5" fill="#085041" text-anchor="middle">16 KB</text>
<rect x="213" y="192" width="185" height="30" rx="5" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="305" y="212" font-family="system-ui,sans-serif" font-size="11.5" fill="#085041" text-anchor="middle">16 KB</text>
<rect x="402" y="192" width="185" height="30" rx="5" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="494" y="212" font-family="system-ui,sans-serif" font-size="11.5" fill="#085041" text-anchor="middle">16 KB</text>
<text x="600" y="212" font-family="system-ui,sans-serif" font-size="12" fill="#57606a">…</text>
<text x="24" y="256" font-family="system-ui,sans-serif" font-size="11.5" font-weight="600" fill="#085041">Every write from here on is contiguous: no allocation, no memcpy.</text>
<text x="24" y="278" font-family="system-ui,sans-serif" font-size="11.5" fill="#57606a">The cost isn't avoided — it's paid once instead of on every write.</text>
</svg>
<figcaption>One short record buys permanent alignment. That's the whole trade.</figcaption>
</figure>

The escape sets the flag false, so it fires at most once per copy — never a run of small records. On an aligned buffer the second branch is unreachable, so that case is untouched rather than merely "not much worse".

## Numbers

Ten alternating paired runs (baseline, fixed, baseline, fixed — so interference hits both arms):

<figure>
<svg width="100%" viewBox="0 0 720 290" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>Per-pair CPU change, misaligned buffer versus aligned control</title>
<desc>On the misaligned buffer all ten paired runs are faster, ranging from 6.8 to 13.9 percent, with a median of 9.9 percent. On the aligned control the ten pairs scatter above and below zero with a median of 0.6 percent, showing no consistent effect.</desc>
<rect x="1" y="1" width="718" height="288" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<text x="50" y="28" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#085041">Misaligned buffer</text>
<text x="386" y="28" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#57606a">Aligned buffer (control)</text>
<line x1="44" y1="130" x2="690" y2="130" stroke="#eaeef2"/>
<line x1="44" y1="170" x2="690" y2="170" stroke="#eaeef2"/>
<text x="36" y="94" font-family="system-ui,sans-serif" font-size="10" fill="#57606a" text-anchor="end">0%</text>
<text x="36" y="134" font-family="system-ui,sans-serif" font-size="10" fill="#57606a" text-anchor="end">−5</text>
<text x="36" y="174" font-family="system-ui,sans-serif" font-size="10" fill="#57606a" text-anchor="end">−10</text>
<text x="14" y="212" font-family="system-ui,sans-serif" font-size="10" fill="#57606a">faster ↓</text>
<rect x="50" y="90" width="18" height="54.4" rx="3" fill="#0F6E56"/>
<rect x="80" y="90" width="18" height="88" rx="3" fill="#0F6E56"/>
<rect x="110" y="90" width="18" height="55.2" rx="3" fill="#0F6E56"/>
<rect x="140" y="90" width="18" height="83.2" rx="3" fill="#0F6E56"/>
<rect x="170" y="90" width="18" height="80" rx="3" fill="#0F6E56"/>
<rect x="200" y="90" width="18" height="99.2" rx="3" fill="#0F6E56"/>
<rect x="230" y="90" width="18" height="64.8" rx="3" fill="#0F6E56"/>
<rect x="260" y="90" width="18" height="55.2" rx="3" fill="#0F6E56"/>
<rect x="290" y="90" width="18" height="111.2" rx="3" fill="#0F6E56"/>
<rect x="320" y="90" width="18" height="78.4" rx="3" fill="#0F6E56"/>
<text x="299" y="216" font-family="system-ui,sans-serif" font-size="10" font-weight="600" fill="#085041" text-anchor="middle">−13.9</text>
<line x1="44" y1="169.2" x2="344" y2="169.2" stroke="#085041" stroke-width="1.2" stroke-dasharray="4 3"/>
<line x1="367" y1="20" x2="367" y2="222" stroke="#eaeef2"/>
<rect x="386" y="90" width="18" height="12.8" rx="3" fill="#0F6E56"/>
<rect x="416" y="80.4" width="18" height="9.6" rx="3" fill="#B22B2B"/>
<rect x="446" y="74" width="18" height="16" rx="3" fill="#B22B2B"/>
<rect x="476" y="90" width="18" height="12.8" rx="3" fill="#0F6E56"/>
<rect x="506" y="90" width="18" height="9.6" rx="3" fill="#0F6E56"/>
<rect x="536" y="86.8" width="18" height="3.2" rx="1.5" fill="#B22B2B"/>
<rect x="566" y="90" width="18" height="3.2" rx="1.5" fill="#0F6E56"/>
<rect x="596" y="90" width="18" height="6.4" rx="3" fill="#0F6E56"/>
<rect x="626" y="90" width="18" height="9.6" rx="3" fill="#0F6E56"/>
<rect x="656" y="89" width="18" height="2" fill="#8c959f"/>
<line x1="380" y1="94.8" x2="690" y2="94.8" stroke="#57606a" stroke-width="1.2" stroke-dasharray="4 3"/>
<line x1="44" y1="90" x2="690" y2="90" stroke="#57606a" stroke-width="1.2"/>
<text x="50" y="248" font-family="system-ui,sans-serif" font-size="11.5" font-weight="600" fill="#085041">median −9.9%  ·  10 of 10 pairs faster</text>
<text x="50" y="266" font-family="system-ui,sans-serif" font-size="11" fill="#57606a">one direction, every time</text>
<text x="386" y="248" font-family="system-ui,sans-serif" font-size="11.5" font-weight="600" fill="#57606a">median −0.6%  ·  6 faster, 3 slower, 1 flat</text>
<text x="386" y="266" font-family="system-ui,sans-serif" font-size="11" fill="#57606a">no consistent direction — as intended</text>
<text x="50" y="284" font-family="system-ui,sans-serif" font-size="10" fill="#8c959f">dashed line = median</text>
</svg>
<figcaption>Each bar is one baseline-versus-fixed pair. The control panel is the point: where the bug isn't present, the change does nothing.</figcaption>
</figure>

| buffer | median | spread |
| --- | --- | --- |
| misaligned | **−9.9%** | 10 of 10 pairs favor the change, −6.8% to −13.9% |
| aligned | −0.6% | signs mixed — no regression |

I nearly got this wrong. My first number was −12%, measured sequentially — and partway through, a macOS sync daemon started burning 150% CPU and inflated absolute times sixfold. Sequential A/B on a machine you don't control is measuring the machine.

The number I actually trust isn't a timing. The benchmark counts writes that had to copy, and that counter is deterministic:

**10 copies per iteration → 1.**

*Caveat: measured on macOS. This trades a memcpy for at most one extra syscall, and that exchange rate differs in production.*

## Found, not fixed

```cpp
auto result = bio_io_handle(b)->writev(&slice, 1);
```

One iovec, one syscall, per TLS record. The plaintext path hands the *entire* buffer to a single multi-iovec `writev`.

<figure>
<svg width="100%" viewBox="0 0 720 210" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>Syscalls per 1 MB response, plaintext versus TLS</title>
<desc>Sending a 1 MB response over the plaintext path costs a single writev syscall. The same response over TLS costs sixty-four, one per 16 KB record.</desc>
<rect x="1" y="1" width="718" height="208" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<text x="24" y="30" font-family="system-ui,sans-serif" font-size="12.5" font-weight="600" fill="#1f2328">writev syscalls to send one 1 MB response</text>
<text x="24" y="66" font-family="system-ui,sans-serif" font-size="11.5" font-weight="600" fill="#085041">plaintext</text>
<rect x="120" y="52" width="170" height="20" rx="3" fill="#E1F5EE" stroke="#0F6E56"/>
<text x="205" y="66" font-family="system-ui,sans-serif" font-size="10.5" fill="#085041" text-anchor="middle">one multi-iovec writev</text>
<text x="304" y="66" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#085041">1</text>
<text x="24" y="112" font-family="system-ui,sans-serif" font-size="11.5" font-weight="600" fill="#8C1D1D">TLS</text>
<g fill="#B22B2B">
<rect x="120" y="92" width="8" height="8" rx="1"/><rect x="131" y="92" width="8" height="8" rx="1"/><rect x="142" y="92" width="8" height="8" rx="1"/><rect x="153" y="92" width="8" height="8" rx="1"/><rect x="164" y="92" width="8" height="8" rx="1"/><rect x="175" y="92" width="8" height="8" rx="1"/><rect x="186" y="92" width="8" height="8" rx="1"/><rect x="197" y="92" width="8" height="8" rx="1"/><rect x="208" y="92" width="8" height="8" rx="1"/><rect x="219" y="92" width="8" height="8" rx="1"/><rect x="230" y="92" width="8" height="8" rx="1"/><rect x="241" y="92" width="8" height="8" rx="1"/><rect x="252" y="92" width="8" height="8" rx="1"/><rect x="263" y="92" width="8" height="8" rx="1"/><rect x="274" y="92" width="8" height="8" rx="1"/><rect x="285" y="92" width="8" height="8" rx="1"/>
<rect x="120" y="103" width="8" height="8" rx="1"/><rect x="131" y="103" width="8" height="8" rx="1"/><rect x="142" y="103" width="8" height="8" rx="1"/><rect x="153" y="103" width="8" height="8" rx="1"/><rect x="164" y="103" width="8" height="8" rx="1"/><rect x="175" y="103" width="8" height="8" rx="1"/><rect x="186" y="103" width="8" height="8" rx="1"/><rect x="197" y="103" width="8" height="8" rx="1"/><rect x="208" y="103" width="8" height="8" rx="1"/><rect x="219" y="103" width="8" height="8" rx="1"/><rect x="230" y="103" width="8" height="8" rx="1"/><rect x="241" y="103" width="8" height="8" rx="1"/><rect x="252" y="103" width="8" height="8" rx="1"/><rect x="263" y="103" width="8" height="8" rx="1"/><rect x="274" y="103" width="8" height="8" rx="1"/><rect x="285" y="103" width="8" height="8" rx="1"/>
<rect x="120" y="114" width="8" height="8" rx="1"/><rect x="131" y="114" width="8" height="8" rx="1"/><rect x="142" y="114" width="8" height="8" rx="1"/><rect x="153" y="114" width="8" height="8" rx="1"/><rect x="164" y="114" width="8" height="8" rx="1"/><rect x="175" y="114" width="8" height="8" rx="1"/><rect x="186" y="114" width="8" height="8" rx="1"/><rect x="197" y="114" width="8" height="8" rx="1"/><rect x="208" y="114" width="8" height="8" rx="1"/><rect x="219" y="114" width="8" height="8" rx="1"/><rect x="230" y="114" width="8" height="8" rx="1"/><rect x="241" y="114" width="8" height="8" rx="1"/><rect x="252" y="114" width="8" height="8" rx="1"/><rect x="263" y="114" width="8" height="8" rx="1"/><rect x="274" y="114" width="8" height="8" rx="1"/><rect x="285" y="114" width="8" height="8" rx="1"/>
<rect x="120" y="125" width="8" height="8" rx="1"/><rect x="131" y="125" width="8" height="8" rx="1"/><rect x="142" y="125" width="8" height="8" rx="1"/><rect x="153" y="125" width="8" height="8" rx="1"/><rect x="164" y="125" width="8" height="8" rx="1"/><rect x="175" y="125" width="8" height="8" rx="1"/><rect x="186" y="125" width="8" height="8" rx="1"/><rect x="197" y="125" width="8" height="8" rx="1"/><rect x="208" y="125" width="8" height="8" rx="1"/><rect x="219" y="125" width="8" height="8" rx="1"/><rect x="230" y="125" width="8" height="8" rx="1"/><rect x="241" y="125" width="8" height="8" rx="1"/><rect x="252" y="125" width="8" height="8" rx="1"/><rect x="263" y="125" width="8" height="8" rx="1"/><rect x="274" y="125" width="8" height="8" rx="1"/><rect x="285" y="125" width="8" height="8" rx="1"/>
</g>
<text x="304" y="116" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#8C1D1D">64</text>
<text x="330" y="116" font-family="system-ui,sans-serif" font-size="10.5" fill="#8C1D1D">one per 16 KB record</text>
<text x="24" y="172" font-family="system-ui,sans-serif" font-size="11.5" fill="#57606a">Same bytes, same buffer. The TLS path just never batches them.</text>
<text x="24" y="192" font-family="system-ui,sans-serif" font-size="11" fill="#57606a">Untouched — a buffering BIO trades 63 syscalls for 64 ciphertext copies, which needs a design discussion.</text>
</svg>
<figcaption>Found while chasing the record-size question, and larger than what I set out to fix.</figcaption>
</figure>

A buffering BIO would trade N−1 syscalls for N ciphertext copies — likely worth it on Linux — but it tangles with partial writes and the `WANT_WRITE` contract enough to deserve a design discussion, not a patch.

## The takeaway

My first fix failed for a reason visible before I wrote it. I'd framed the bug as *"the front slice is sometimes too small"* when it was *"the buffer is a fixed point under the operation meant to fix it."*

The first framing suggests a threshold. The second tells you a threshold cannot work, and that you need history rather than state.

I wrote the threshold version anyway — and the benchmark told me in ten seconds, because it had a counter in it and not just a stopwatch.
