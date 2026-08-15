---
title: "A Copy That Never Stops Copying"
date: 2026-08-14T10:00:00+08:00
draft: false
tags: ["envoy", "performance", "tls", "cpp", "buffers", "learning-notes"]
summary: "Learning notes: Envoy's TLS write path re-copies 16 KB on every write, forever, because of an alignment that can never recover. The obvious fix does nothing, and finding out why was the interesting part."
---

*These are learning notes rather than settled advice. I went looking for CPU waste in Envoy's hot paths, found something that turned out to be structural rather than incidental, and the fix I reached for first was useless. Writing down why is how I understood the problem.*

Envoy's TLS write path has one line that does more work than it looks like:

```cpp
int rc = SSL_write(rawSsl(), write_buffer.linearize(bytes_to_write), bytes_to_write);
```

`SSL_write()` needs contiguous memory. Envoy's write buffer is a chain of slices. `linearize()` bridges the two: if the front slice already holds enough bytes it hands back a pointer and costs nothing; otherwise it allocates a new slice, copies across the slice boundary, and returns that.

That reads like a fallback for an awkward case. It is actually the steady state, and once you land in it you never leave.

## The shape that never recovers

Two numbers matter, and they are the same number.

The most Envoy ever hands to a single `SSL_write()` is 16384 bytes — one TLS record's worth of plaintext. And `Slice::default_slice_size_`, the size of the slices the read path produces, is also 16384.

Now trace what `linearize(16384)` does when the front slice is short. It copies 16384 bytes into a fresh slice, drains 16384 bytes from the chain, and pushes the new slice at the front. The drain consumes the short slice entirely and eats into the next one — which leaves *that* slice short by exactly the offset the first one introduced.

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

<text x="360" y="286" font-family="system-ui,sans-serif" font-size="11.5" fill="#57606a" text-anchor="middle">The state after the write is the state before it. There is no sequence of writes that recovers alignment.</text>
<text x="360" y="308" font-family="system-ui,sans-serif" font-size="11.5" font-weight="600" fill="#8C1D1D" text-anchor="middle">One 16 KB malloc + one 16 KB memcpy + one free, per 16 KB of TLS egress, for the life of the connection.</text>
</svg>
<figcaption>The buffer is self-similar across the operation meant to fix it. That is the whole bug.</figcaption>
</figure>

The state after the write is structurally identical to the state before it. This is not a case that degrades under load or shows up at a particular request size — it is a fixed point.

And getting into it is trivial. `ConnectionImpl::write()` moves codec slices into the connection buffer wholesale, so a response with a few hundred bytes of headers in front of a body made of full-size slices produces exactly this shape. That is a completely ordinary HTTP response.

There is a second cost tucked underneath. `Slice`'s constructor calls `new uint8_t[]` directly, so this allocation bypasses the thread-local slice free list that the read path uses. Not only is there a copy per 16 KB, the allocation behind it is the slow kind.

## The fix that does nothing

The obvious response is: don't copy, just write whatever the front slice happens to hold contiguously. `SSL_write()` is perfectly happy with a smaller length — you get a smaller TLS record.

The reason to hesitate is that a smaller record means an extra record, and Envoy issues one `writev` syscall per TLS record, so an extra record is an extra syscall. So the natural guard is a size floor: write the front slice directly only if it is big enough to be worth a syscall, otherwise fall back to copying.

I implemented that. It changed nothing. Not "helped a little" — the measured time and the copy count were identical to the original.

Once you see it, it is obvious. The floor is asking "is the front slice big enough?" and the entire problem is that the front slice is *small*. In the pathological state it is 200 bytes forever. The condition guarding the fast path is precisely the condition that never holds when you need it. A size threshold cannot escape a fixed point whose defining property is being under the threshold.

That was the useful moment in this whole exercise. Escaping requires emitting one short record — accepting the syscall, exactly once — because that short write is what consumes the misaligning offset and leaves the chain aligned behind it. There is no version of this that avoids both the copy and the short record. The cost is not avoidable; it is payable once instead of forever.

## But not unconditionally

So write the front slice directly, always? That regresses. On a buffer that is *already* aligned, or misaligned in a way a single copy genuinely fixes, the old behavior is correct: one copy, then everything is contiguous. Always-write-the-front-slice turns that into a run of small records for no benefit, and I measured it about 6% slower on that shape.

Which means the decision cannot be made from the buffer's current state at all. "Front slice is short" is true in both the recoverable case and the fixed point. The two are indistinguishable from a single snapshot — what separates them is whether the copy is *about to repeat*.

That is one bit of history:

```cpp
if (front_slice.len_ >= bytes_to_write) {
  // Already contiguous — linearize() would return this pointer without copying.
  return {front_slice.mem_, bytes_to_write, false};
}

if (avoid_repeated_linearize && linearized_last_write && front_slice.len_ > 0) {
  // Copied last time and about to copy again: the chain is a fixed point.
  // One short record consumes the offset and aligns everything behind it.
  return {front_slice.mem_, front_slice.len_, false};
}

return {write_buffer.linearize(bytes_to_write), bytes_to_write, true};
```

Copy once, as before. If the very next write would copy again, take the short record instead. The escape sets the flag false, so it can fire at most once per copy and never produces a run of small records. On an already-aligned buffer the second branch is unreachable, which is why that case is untouched rather than merely "not much worse".

The one contract to be careful with: after `SSL_ERROR_WANT_WRITE`, BoringSSL requires the retry to present the same pointer, and Envoy does not set `SSL_MODE_ACCEPT_MOVING_WRITE_BUFFER`. Nothing is drained on a failed write and new data only ever appends at the back, so the front bytes don't move — and slice storage is a `unique_ptr`, so even when the slice deque grows and relocates its handles, the bytes stay put.

## Measuring it, and nearly getting that wrong too

My first number was −12%. My final number is −9.9%. The gap is not rounding, and how I got there is the part I'd want to remember.

The benchmark I was using runs the two variants sequentially. Partway through the session a macOS sync daemon woke up and started burning 150% CPU, which inflated absolute times sixfold — and because the arms run one after the other, a burst like that lands on whichever arm happens to be running. Sequential A/B on a machine you don't control is measuring the machine.

The fix is to alternate: run baseline, fixed, baseline, fixed, in separate processes, and pair each adjacent result. Interference then hits both arms roughly equally and the paired difference survives it. Ten pairs:

| | median | spread |
| --- | --- | --- |
| misaligned buffer | **−9.9%** | 10 of 10 pairs favor the change, −6.8% to −13.9% |
| aligned buffer | −0.6% | signs mixed — no regression |

Ten out of ten in the same direction is worth more than any single median; under a sign test that alone is about p = 0.001.

But the number I actually trust is not a timing at all. The benchmark counts how many writes had to copy, and that counter is deterministic — it does not care what else the machine is doing:

**10 copies per iteration → 1.**

When a timing result and a counter disagree, the counter is the one telling you whether the mechanism you think you fixed is the mechanism you fixed. I should have led with it instead of the percentage.

## The thing I found and didn't fix

Chasing the syscall question earlier turned up something larger than what I set out to fix. Envoy's BIO write does this:

```cpp
auto result = bio_io_handle(b)->writev(&slice, 1);
```

One iovec, one syscall, per `BIO_write` — and BoringSSL emits one `BIO_write` per record. Meanwhile the plaintext path, `RawBufferSocket`, hands the *entire* buffer to a single multi-iovec `writev`.

So a 1 MB response costs one syscall in plaintext and sixty-four over TLS.

I haven't touched it. A buffering BIO would trade N−1 syscalls for N ciphertext copies — probably worth it on Linux, where syscalls dominate memcpy at these sizes — but it tangles with partial writes and the `WANT_WRITE` contract in ways that deserve a design discussion rather than a patch. It is also the reason I'd want the headline number re-confirmed on Linux: this change trades a memcpy for at most one extra syscall, and the exchange rate between those two is not the same on macOS as it is in production.

## What I took from it

The part worth keeping isn't the patch. It's that the first fix failed for a reason that was visible before I wrote it: I had characterized the bug as "the front slice is sometimes too small" when it was really "the buffer is a fixed point under the operation meant to fix it". The first framing suggests a threshold. The second tells you a threshold cannot work, and that you need history rather than state.

I wrote the threshold version anyway, and the benchmark told me in about ten seconds. That is a good trade — but only because the benchmark had a counter in it, not just a stopwatch.
