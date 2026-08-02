# Mechanism card — committed-visible ledger candidate

Status: **experimental local static candidate**. This card is bound to the
fresh-room `flowness-ledger-core` files named below, not to a sealed Flowness
server snapshot or a public release. It must not be promoted to
`current_verified` without sealed source and runtime evidence.

## Why it exists

A proposed change must not become reader-visible merely because a writer began
emitting records. The candidate therefore separates proposal, proposed record,
and immutable accepted/rejected decision. It addresses the failure mode where a
consumer observes partial or later-rejected work as committed truth.

## State and authority

`proposal → proposed_record* → decision(accepted|rejected)`.

The JSONL audit stream is authoritative. `Ledger.read("committed")` derives a
view containing only `proposed_record` rows whose proposal has an accepted
decision. A caller may append a proposal or decision; it cannot mutate a prior
decision, sequence or hash link. An incomplete tail blocks reads and writes
until bounded recovery truncates only that tail.

## Static evidence chain

| Layer | Exact local locator | Observed role |
| --- | --- | --- |
| Writer / producer | `ledger.py:151-175`, SHA-256 `ee42ef…170f7` | Appends sequence-linked, fsynced immutable rows. |
| Gate / state machine | `ledger.py:179-220` | Binds proposal input and rejects conflicting terminal decisions. |
| Consumer | `ledger.py:222-232` | Builds committed visibility only after accepted decision. |
| Failure / recovery | `ledger.py:240-276`, `278-330` | Persists self-hashed recovery receipt and verifies the bound prefix. |
| Projection consumer | `projection.py:22-82`, SHA-256 `31a2a2…82ff` | Reads only committed rows; watermark covers all audit rows and stale reads refuse. |
| Tests | `test_ledger.py:12-69`, `test_projection.py:8-34` | Covers pending/rejected invisibility, decision conflict, interrupted tail, tampering, stale watermark and deterministic rebuild. |

## Failure and recovery boundaries

- A malformed complete record, duplicate ID, broken hash/sequence, unsafe
  path, incomplete tail, stale projection or modified receipt is rejected.
- Recovery is limited to an unterminated final JSONL tail. It cannot repair a
  malformed complete record, invent a decision or assert runtime delivery.
- Rebuild recomputes a committed-type projection from the same ledger state;
  it is not a distributed replay or exactly-once guarantee.

## Evolution and open questions

This is a fresh-room implementation inspired by unsealed local mechanism
study, not a copied private EventLog. The implemented progression is:
proposal visibility → terminal decision immutability → recovery receipts →
watermarked committed projection. Still unknown: source/right export status,
external clean-room behavior, cross-platform concurrency, operational use,
review-verdict integration and any server producer/consumer lineage.

Public claim ceiling: “a private local candidate demonstrates these bounded
ledger semantics in tests and an offline wheel demo.” It cannot claim Flowness
runtime compatibility, production reliability or public availability.
