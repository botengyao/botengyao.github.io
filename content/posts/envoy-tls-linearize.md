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

The escape sets the flag false, so it fires at most once per copy — never a run of small records. On an aligned buffer the second branch is unreachable, so that case is untouched rather than merely "not much worse".

## Numbers

Ten alternating paired runs (baseline, fixed, baseline, fixed — so interference hits both arms):

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

**A 1 MB response: 1 syscall in plaintext, 64 over TLS.**

A buffering BIO would trade N−1 syscalls for N ciphertext copies — likely worth it on Linux — but it tangles with partial writes and the `WANT_WRITE` contract enough to deserve a design discussion, not a patch.

## The takeaway

My first fix failed for a reason visible before I wrote it. I'd framed the bug as *"the front slice is sometimes too small"* when it was *"the buffer is a fixed point under the operation meant to fix it."*

The first framing suggests a threshold. The second tells you a threshold cannot work, and that you need history rather than state.

I wrote the threshold version anyway — and the benchmark told me in ten seconds, because it had a counter in it and not just a stopwatch.
