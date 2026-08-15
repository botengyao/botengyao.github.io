---
title: "A Copy That Never Stops Copying"
date: 2026-08-14T10:00:00+08:00
draft: false
tags: ["envoy", "performance", "tls", "cpp", "buffers"]
summary: "Envoy's TLS write path copies 16 KB for every 16 KB it sends, for the life of a connection. What causes it, and the change that fixes it."
---

Envoy's TLS write path copies 16 KB for every 16 KB it sends — not occasionally, but for the life of a connection. This is what causes it and how the fix works.

| | |
| --- | --- |
| Where | `SslSocket::doWrite` |
| Cost | one 16 KB malloc + 16 KB memcpy + free, per 16 KB of TLS egress |
| Trigger | any HTTP response: small headers in front of a full-size body |
| Fix | write the contiguous front slice as one short TLS record |
| Result | **−10.3% CPU** on the affected path, copies per iteration **10 → 1** |

## The line

```cpp
int rc = SSL_write(rawSsl(), write_buffer.linearize(bytes_to_write), bytes_to_write);
```

`SSL_write()` needs contiguous memory. Envoy's write buffer is a chain of slices. `linearize()` bridges them: if the front slice already holds enough it returns a pointer for free, otherwise it allocates a slice, copies into it, and returns that.

It reads like a fallback for an awkward case. On a proxied response it is the steady state.

## Why it never recovers

Two numbers matter, and they are the same number: the most Envoy hands to one `SSL_write()` is **16384** bytes, and `Slice::default_slice_size_` — the size of the slices the read path produces — is also **16384**.

The short slice at the front is the response headers. `ConnectionImpl::write()` moves the codec's output into the connection buffer wholesale, so a few hundred bytes of headers land as their own slice ahead of the body's full-size ones. Call that size **H**.

`linearize(16384)` on `[H][16384][16384]…` does this:

```text
copy    16384 bytes = all H of slice 1, plus (16384 − H) of slice 2
drain   16384 bytes = pops slice 1, takes (16384 − H) off slice 2
                    → slice 2 keeps 16384 − (16384 − H) = H bytes
push    the fresh 16 KB slice at the front

        [16 KB new][H][16384]…   →  write + drain removes the new slice
                   [H][16384]…   ←  exactly where it started
```

**The leftover is always exactly H**, for any H. The copy takes H bytes off the front and leaves H bytes behind on the next slice, so the offset is conserved rather than worn down. The short slice in the "after" state is a *different* H bytes — the tail of slice 2, not the original headers.

Call a buffer in this shape — a short slice of H bytes sitting ahead of full-size ones — a **misaligned chain**. The point is that `linearize()` does not clear it. It reproduces it, so every write from then on allocates and copies 16 KB.

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

## The fix

When the previous write had to copy and this one would too, write just the contiguous front slice instead. That costs one short TLS record, after which the buffer is aligned and the writes that follow are copy-free.

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
<figcaption>One short record buys permanent alignment. That is the trade the change makes.</figcaption>
</figure>

The full decision is three branches:

```cpp
if (front_slice.len_ >= bytes_to_write) {
  // Already contiguous — linearize() would return this pointer without copying.
  return {front_slice.mem_, bytes_to_write, false};
}

if (avoid_repeated_linearize && linearized_last_write &&
    nextSliceCoversWrite(write_buffer, bytes_to_write)) {
  // Copied last time, about to copy again, and one short record fixes it.
  return {front_slice.mem_, front_slice.len_, false};
}

return {write_buffer.linearize(bytes_to_write), bytes_to_write, true};
```

Each condition in the middle branch is load-bearing.

**`linearized_last_write` — why not a size threshold.** A natural guard would be "only write the front slice directly if it is large enough to be worth a syscall". It never fires. A threshold asks whether the front slice is *big*; the state it needs to act on is the one where the front slice is permanently *small* — held at H by the conservation shown above. The answer is no on exactly the writes where the fast path would help.

<figure>
<svg width="100%" viewBox="0 0 720 200" role="img" xmlns="http://www.w3.org/2000/svg" style="max-width:720px">
<title>Why a size threshold can never fire</title>
<desc>A scale of front-slice size from 0 to 16 KB. The fast path opens only above a 4 KB threshold, but on a misaligned chain the front slice stays at 200 bytes, far below it, so the guard is never true.</desc>
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
<text x="82" y="146" font-family="system-ui,sans-serif" font-size="11.5" font-weight="600" fill="#8C1D1D">front slice on a misaligned chain: 200 B, permanently</text>
<text x="24" y="180" font-family="system-ui,sans-serif" font-size="11.5" fill="#57606a">The fast path opens at 4 KB. A misaligned chain holds the front slice at H. The guard is never true.</text>
</svg>
<figcaption>A threshold tests for a large front slice. A misaligned chain never has one, so no threshold value helps.</figcaption>
</figure>

**`nextSliceCoversWrite` — why the slice behind matters.** Writing the front slice only re-aligns the buffer if the slice behind it can satisfy the next write on its own:

```cpp
bool nextSliceCoversWrite(Buffer::Instance& write_buffer, uint64_t bytes_to_write) {
  const Buffer::RawSliceVector slices = write_buffer.getRawSlices(/*max_slices=*/2);
  return slices.size() == 2 && slices[1].len_ >= bytes_to_write;
}
```

Without it, chains that are fragmented more than one slice deep — HTTP/1 chunked framing, small HTTP/2 frames, `addBufferFragment` chains — get a short record that re-aligns nothing:

| slice size | TLS records, without the check |
| --- | --- |
| 4096 B | 16 → 25 (1.56×) |
| 4097 B | 17 → 32 (1.88×) |
| 5462 B | 22 → 42 (1.90×) |

Nearly double the records and `writev` syscalls, for identical bytes copied. The check also means a write that drains the whole buffer is never split in two, since it can only pass when data remains behind the front slice.

## Retries

`SSL_write()` can return `SSL_ERROR_WANT_WRITE`, and the write is retried later. The retry repeats the pending write verbatim — same pointer, same length, and the same record of whether it came from a copy — rather than deciding again. Deciding again would look at the buffer *after* the linearize, conclude nothing was copied, and lose the history the next decision depends on. On a connection that accepts less than one full record per readiness event, that happens on every write and the optimization never engages.

BoringSSL requires the same pointer on a retry regardless, since Envoy does not set `SSL_MODE_ACCEPT_MOVING_WRITE_BUFFER`. The pending write is discharged before `write_buffer.drain()`, because `drain()` runs low-watermark callbacks and slice drain trackers that can re-enter `doWrite()`.

## Results

Eight alternating paired runs — baseline, fixed, baseline, fixed — so machine interference lands on both arms:

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
<figcaption>Each bar is one baseline-versus-fixed pair. The control panel is the point: where the buffer is not misaligned, the change does nothing.</figcaption>
</figure>

| buffer | median | spread |
| --- | --- | --- |
| misaligned | **−10.3%** | 8 of 8 pairs faster, −8.2% to −12.6% |
| aligned (control) | +0.2% | signs mixed — no regression |

The timing is macOS, where a memcpy is cheap relative to a syscall; the balance differs in production. The deterministic number is the copy counter: **10 copies per iteration → 1**.

## Rollout

Guarded by `envoy.reloadable_features.tls_avoid_repeated_linearize`, default on. The flag is read when a TLS connection is created, so disabling it applies to new connections rather than to connections already open.
