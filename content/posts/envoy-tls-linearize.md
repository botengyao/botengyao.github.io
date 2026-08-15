---
title: "A Copy That Never Stops Copying"
date: 2026-08-14T10:00:00+08:00
draft: false
tags: ["envoy", "performance", "tls", "cpp", "buffers", "learning-notes"]
summary: "Envoy's TLS write path re-copies 16 KB on every write, forever. The obvious fix does nothing, and my fix was wrong too — the interesting part is why each one failed."
---

*Learning notes. I went looking for CPU waste in Envoy's hot paths, found something structural, and then got the fix wrong twice. Both failures were more instructive than the fix.*

**Envoy's TLS write path copies 16 KB for every 16 KB it sends — permanently, by construction.**

| | |
| --- | --- |
| Where | `SslSocket::doWrite` |
| Cost | one 16 KB malloc + 16 KB memcpy + free, per 16 KB of TLS egress |
| Trigger | any HTTP response: small headers in front of a full-size body |
| Fix | emit one short TLS record, once |
| Result | **−10.3% CPU**, copies per iteration **10 → 1** |

## The line

```cpp
int rc = SSL_write(rawSsl(), write_buffer.linearize(bytes_to_write), bytes_to_write);
```

`SSL_write()` needs contiguous memory. Envoy's write buffer is a chain of slices. `linearize()` bridges them: if the front slice already holds enough, it returns a pointer for free; otherwise it allocates, copies, and returns that.

It reads like a fallback. It's the steady state.

## Why it never recovers

Two numbers matter, and they're the same number: the most Envoy hands to one `SSL_write()` is **16384** bytes, and `Slice::default_slice_size_` — the size of the slices the read path produces — is also **16384**.

The short slice at the front is the response headers — `ConnectionImpl::write()` moves the codec's output in wholesale, so a few hundred bytes of headers land as their own slice ahead of the body's full-size ones. Call that size **H**; nothing below depends on its value.

Now watch what `linearize(16384)` does to `[H][16384][16384]…`:

```text
copy    16384 bytes = all H of slice 1, plus (16384 − H) of slice 2
drain   16384 bytes = pops slice 1, takes (16384 − H) off slice 2
                    → slice 2 keeps 16384 − (16384 − H) = H bytes
push    the fresh 16 KB slice at the front

        [16 KB new][H][16384]…   →  write + drain removes the new slice
                   [H][16384]…   ←  exactly where we started
```

**The leftover is always exactly H.** The copy takes H bytes off the front and leaves H bytes behind on the next slice, so the offset is conserved rather than worn down. The short slice in the "after" picture is a *different* H bytes — the tail of slice 2, not the original headers.

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
<text x="500" y="66" font-family="system-ui,sans-serif" font-size="11" fill="#8C1D1D">headers — call this H</text>
<line x1="24" y1="104" x2="696" y2="104" stroke="#eaeef2"/>
<text x="24" y="122" font-family="system-ui,sans-serif" font-size="11.5" fill="#57606a">linearize(16 KB) drains all H of slice 1, then (16 KB − H) off slice 2 …</text>
<text x="24" y="137" font-family="system-ui,sans-serif" font-size="11.5" font-weight="600" fill="#8C1D1D">… so slice 2 is left holding 16 KB − (16 KB − H) = H bytes. The offset is conserved.</text>
<line x1="24" y1="146" x2="696" y2="146" stroke="#eaeef2"/>
<text x="24" y="176" font-family="system-ui,sans-serif" font-size="13" font-weight="600" fill="#8C1D1D">After the write</text>
<rect x="24" y="190" width="58" height="34" rx="5" fill="#FDF0EF" stroke="#B22B2B"/>
<text x="53" y="212" font-family="system-ui,sans-serif" font-size="11" fill="#8C1D1D" text-anchor="middle">200 B</text>
<rect x="86" y="190" width="185" height="34" rx="5" fill="#f6f8fa" stroke="#57606a"/>
<text x="178" y="212" font-family="system-ui,sans-serif" font-size="11.5" fill="#1f2328" text-anchor="middle">16 KB</text>
<rect x="275" y="190" width="185" height="34" rx="5" fill="#f6f8fa" stroke="#57606a"/>
<text x="367" y="212" font-family="system-ui,sans-serif" font-size="11.5" fill="#1f2328" text-anchor="middle">16 KB</text>
<text x="474" y="212" font-family="system-ui,sans-serif" font-size="12" fill="#57606a">…</text>
<text x="500" y="204" font-family="system-ui,sans-serif" font-size="11" fill="#8C1D1D">also H — but this is the</text>
<text x="500" y="217" font-family="system-ui,sans-serif" font-size="11" fill="#8C1D1D">tail of slice 2, not the headers</text>
<path d="M660 224 C 690 224 690 48 660 48" fill="none" stroke="#B22B2B" stroke-width="1.6"/>
<path d="M666 54 L 658 47 L 666 41" fill="none" stroke="#B22B2B" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
<text x="612" y="140" font-family="system-ui,sans-serif" font-size="11" font-weight="600" fill="#B22B2B" text-anchor="middle">identical shape</text>
<text x="360" y="286" font-family="system-ui,sans-serif" font-size="11.5" fill="#57606a" text-anchor="middle">Same shape, same H, for any H. The state after the write is the state before it.</text>
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

So the decision can't come from the buffer's current state at all. "Front slice is short" is true in *both* the recoverable case and the fixed point. What separates them is whether the copy is about to **repeat** — one bit of history.

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

That was my fix. It was also wrong, in a way that took a second pass to find.

## The second wrong turn

The escape assumes the short prefix is **one slice deep**. Consume it, and the full-size slice behind it lines up.

Plenty of real buffers aren't shaped like that. HTTP/1 chunked framing interleaves size headers and CRLFs with the body. HTTP/2 builds a fresh buffer per frame. `addBufferFragment` chains never coalesce at all. Those produce chains of *uniformly* short slices — and there, writing the front slice re-aligns nothing. It emits a tiny record and leaves the next write to copy exactly as before.

I only caught it because I wrote the test before believing the answer:

| slice size | TLS records, off → on |
| --- | --- |
| 4096 B | 16 → 25 (1.56×) |
| 4097 B | 17 → **32 (1.88×)** |
| 5462 B | 22 → **42 (1.90×)** |

Nearly double the records and `writev` syscalls, for **identical bytes copied**. A 2-byte TLS record is 24 bytes on the wire and one whole syscall. On those shapes my "optimization" was pure loss.

The discriminator is what I should have written first. The escape is worth it exactly when it *achieves* alignment — and that is testable directly, in O(1), rather than inferred from history:

```cpp
// Whether the slice behind the front one is on its own big enough for the write that would
// follow a short record, i.e. whether writing just the front slice actually re-aligns.
bool nextSliceCoversWrite(Buffer::Instance& write_buffer, uint64_t bytes_to_write) {
  const Buffer::RawSliceVector slices = write_buffer.getRawSlices(/*max_slices=*/2);
  return slices.size() == 2 && slices[1].len_ >= bytes_to_write;
}
```

which makes the whole decision:

```cpp
if (front_slice.len_ >= bytes_to_write) {
  // Already contiguous — linearize() would return this pointer without copying.
  return {front_slice.mem_, bytes_to_write, false};
}

if (avoid_repeated_linearize && linearized_last_write &&
    nextSliceCoversWrite(write_buffer, bytes_to_write)) {
  // Copied last time, about to copy again, and one short record fixes it. Take it.
  return {front_slice.mem_, front_slice.len_, false};
}

return {write_buffer.linearize(bytes_to_write), bytes_to_write, true};
```

Every shape above falls back to the old path untouched, and the motivating case keeps its full win. The check also quietly subsumes a condition I had been carrying separately: it can only pass when data remains behind the front slice, so a write that drains the whole buffer is never split in two.

## The state has to survive a retry

`SSL_write()` can return `SSL_ERROR_WANT_WRITE`, and Envoy retries the same write later. My first version re-decided on the retry — which looks at the buffer *after* the linearize, concludes nothing was copied, and clears the history. The retry lands, the short remainder is back at the front, and it linearizes again.

Worse than it sounds. When the socket accepts less than one full record per readiness event, that happens on *every* write and the optimization never engages at all: simulated over 400 events on a congested connection, the copy count was identical with the feature on and off.

The fix is to stop re-deciding. A pending write is recorded with its origin and repeated verbatim — same pointer, same length, same "this came from a copy" — until it lands. BoringSSL wants the same pointer anyway, since Envoy doesn't set `SSL_MODE_ACCEPT_MOVING_WRITE_BUFFER`.

Then one ordering detail I got wrong and had to fix again: I discharged that pending state *after* `write_buffer.drain()`. But `drain()` isn't a leaf call — it runs low-watermark callbacks and slice drain trackers, which can close the connection and re-enter `doWrite` synchronously. A nested call would find a write still pending that had already landed. The code I replaced cleared its retry state at the top of the function and never depended on the ordering; mine did. Discharge first, drain second.

The escape sets the flag false, so it fires at most once per copy — never a run of small records. On an aligned buffer the second branch is unreachable, so that case is untouched rather than merely "not much worse".

## Numbers

Eight alternating paired runs (baseline, fixed, baseline, fixed — so interference hits both arms):

<figure>
<svg width="100%" viewBox="0 0 720 290" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>Per-pair CPU change, misaligned buffer versus aligned control</title>
<desc>On the misaligned buffer all eight paired runs are faster, ranging from 8.2 to 12.6 percent, with a median of 10.3 percent. On the aligned control the eight pairs scatter above and below zero with a median of 0.2 percent, showing no consistent effect.</desc>
<rect x="1" y="1" width="718" height="288" rx="10" fill="#ffffff" stroke="#d0d7de"/>
<text x="50" y="28" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#085041">Misaligned buffer</text>
<text x="386" y="28" font-family="system-ui,sans-serif" font-size="12" font-weight="600" fill="#57606a">Aligned buffer (control)</text>
<line x1="44" y1="130" x2="690" y2="130" stroke="#eaeef2"/>
<line x1="44" y1="170" x2="690" y2="170" stroke="#eaeef2"/>
<text x="36" y="94" font-family="system-ui,sans-serif" font-size="10" fill="#57606a" text-anchor="end">0%</text>
<text x="36" y="134" font-family="system-ui,sans-serif" font-size="10" fill="#57606a" text-anchor="end">−5</text>
<text x="36" y="174" font-family="system-ui,sans-serif" font-size="10" fill="#57606a" text-anchor="end">−10</text>
<text x="14" y="212" font-family="system-ui,sans-serif" font-size="10" fill="#57606a">faster ↓</text>
<rect x="50" y="90" width="22" height="71.2" rx="3" fill="#0F6E56"/>
<rect x="86" y="90" width="22" height="80" rx="3" fill="#0F6E56"/>
<rect x="122" y="90" width="22" height="98.4" rx="3" fill="#0F6E56"/>
<rect x="158" y="90" width="22" height="92.8" rx="3" fill="#0F6E56"/>
<rect x="194" y="90" width="22" height="100.8" rx="3" fill="#0F6E56"/>
<rect x="230" y="90" width="22" height="84" rx="3" fill="#0F6E56"/>
<rect x="266" y="90" width="22" height="65.6" rx="3" fill="#0F6E56"/>
<rect x="302" y="90" width="22" height="72" rx="3" fill="#0F6E56"/>
<text x="205" y="205" font-family="system-ui,sans-serif" font-size="10" font-weight="600" fill="#085041" text-anchor="middle">−12.6</text>
<line x1="44" y1="172.4" x2="344" y2="172.4" stroke="#085041" stroke-width="1.2" stroke-dasharray="4 3"/>
<line x1="367" y1="20" x2="367" y2="222" stroke="#eaeef2"/>
<rect x="386" y="90" width="22" height="28" rx="3" fill="#0F6E56"/>
<rect x="422" y="89" width="22" height="2" fill="#8c959f"/>
<rect x="458" y="86.8" width="22" height="3.2" rx="3" fill="#B22B2B"/>
<rect x="494" y="77.2" width="22" height="12.8" rx="3" fill="#B22B2B"/>
<rect x="530" y="67.6" width="22" height="22.4" rx="3" fill="#B22B2B"/>
<rect x="566" y="90" width="22" height="21.6" rx="3" fill="#0F6E56"/>
<rect x="602" y="83.6" width="22" height="6.4" rx="3" fill="#B22B2B"/>
<rect x="638" y="90" width="22" height="6.4" rx="3" fill="#0F6E56"/>
<line x1="380" y1="88.4" x2="690" y2="88.4" stroke="#57606a" stroke-width="1.2" stroke-dasharray="4 3"/>
<line x1="44" y1="90" x2="690" y2="90" stroke="#57606a" stroke-width="1.2"/>
<text x="50" y="248" font-family="system-ui,sans-serif" font-size="11.5" font-weight="600" fill="#085041">median −10.3%  ·  8 of 8 pairs faster</text>
<text x="50" y="266" font-family="system-ui,sans-serif" font-size="11" fill="#57606a">one direction, every time</text>
<text x="386" y="248" font-family="system-ui,sans-serif" font-size="11.5" font-weight="600" fill="#57606a">median +0.2%  ·  3 faster, 4 slower, 1 flat</text>
<text x="386" y="266" font-family="system-ui,sans-serif" font-size="11" fill="#57606a">no consistent direction — as intended</text>
<text x="50" y="284" font-family="system-ui,sans-serif" font-size="10" fill="#8c959f">dashed line = median</text>
</svg>
<figcaption>Each bar is one baseline-versus-fixed pair. The control panel is the point: where the bug isn't present, the change does nothing.</figcaption>
</figure>

| buffer | median | spread |
| --- | --- | --- |
| misaligned | **−10.3%** | 8 of 8 pairs favor the change, −8.2% to −12.6% |
| aligned | +0.2% | signs mixed — no regression |

I nearly got this wrong too. My first number was −12%, measured sequentially — and partway through, a macOS sync daemon started burning 150% CPU and inflated absolute times sixfold. Sequential A/B on a machine you don't control is measuring the machine.

The number I actually trust isn't a timing. The benchmark counts writes that had to copy, and that counter is deterministic:

**10 copies per iteration → 1.**

*Caveat: measured on macOS. This trades a memcpy for at most one extra syscall, and that exchange rate differs in production.*

## The takeaway

Both wrong turns came from describing the bug loosely and then solving the description.

The threshold failed because I'd framed the bug as *"the front slice is sometimes too small"* when it was *"the buffer is a fixed point under the operation meant to fix it."* The first framing suggests a threshold; the second tells you a threshold cannot work.

The escape failed for the mirror-image reason. I'd fixed the framing but kept solving for *"has this copied before?"* when the question the code actually needs to answer is *"will writing the front slice make the next write contiguous?"* — which is not a fact about history at all. It's a fact about the buffer, one slice ahead, cheap to just look at.

History was the right answer to the wrong question. It works on the shape I had in mind and quietly doubles the syscall count on the shapes I didn't.

What saved both of them was the same thing: a benchmark and a test suite that count operations, not just time. A stopwatch says "about the same." A copy counter and a record counter say 17 → 32, and there's no arguing with that.
