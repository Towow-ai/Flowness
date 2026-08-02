# Candidate compatibility and operational boundary

Status: public Open Alpha compatibility boundary. This document records the
currently verified local constraints, not a broad platform support promise.

## Runtime

- Python **3.11 or newer** is required by package metadata. A local attempt
  with the macOS system Python 3.9 was rejected before installation; a fresh
  Python 3.12 environment built, installed and ran the candidate demo offline.
- The implementation uses `fcntl` for its local writer lock. The candidate is
  therefore **POSIX-local only** today. It has no Windows lock implementation
  and no cross-platform compatibility evidence.

## Concurrency

The only stated scope is one local ledger directory on a POSIX filesystem.
The lock serializes candidate writes from cooperating local processes. It does
not establish distributed consensus, replication, global order, exactly-once
delivery, NFS correctness, leader election, or crash ownership of another
process.

## Storage and recovery

The candidate calls `fsync` after an append and can truncate an incomplete
final JSONL tail. It fails closed for malformed complete records, broken hash
links, duplicate IDs and conflicting decisions. It does not prove durability
under power loss, filesystem bugs, arbitrary concurrent mutation or production
incident conditions.

## Upgrade boundary

Open Alpha APIs and record formats may change before Beta. The published Alpha
coordinate does not imply backward-compatible migration, Windows or NFS
support, or support for an unlisted Python/platform combination.
