# ADR-0001: Use Valkey Streams with a consumer group per stage

- Status: Accepted
- Date: 2026-11-13
- Deciders: Jian, Claude

## Context

The trigger is a Redis message naming a TIFF in S3. Stages must survive crashes, scale by replica, and neither lose nor double-process work. A hosted service later will want the same queue behind an API.

## Decision

One stream per hop: `jobs:in`, `tiles`, `detections`, `jobs:done`, plus `jobs:dead`. Each stage reads through a consumer group, acks only after publishing its output, and reclaims stale entries with `XAUTOCLAIM`. The server is Valkey (BSD-3-Clause, from memory, not re-verified), which speaks the Redis protocol, so the client is `redis-py` (MIT). Redis 8 is avoided because it offers an AGPL option (ADR-0009).

## Alternatives considered

| Option | Why not |
|--------|---------|
| Pub/Sub | Messages lost if a consumer is down |
| Lists (`BLPOP`) | No ack or redelivery without hand-built machinery |
| Kafka/RabbitMQ | Heavier than needed now; revisit when hosted at scale |

## Consequences

Cheap redelivery and horizontal scaling. Delivery is at-least-once, so every stage must be idempotent (ADR-0005). Streams need `MAXLEN` trimming.
