# Trace API (live message timing)

> 한국어(메인): [trace_api.md](trace_api.md)

Every CAL Server records one **trace event** per routing step. Each event carries a millisecond
timestamp. The events feed the live monitor page and the `lacal trace` command, and can be consumed
by your own tools. They are served on the admin port (default `8766`).

## Viewing
| How | What |
|---|---|
| `http://127.0.0.1:8766/monitor` | Live web monitor: a sequence-style timeline, per-message timing, command→status RTT and event details |
| `.../monitor?admin=http://127.0.0.1:8766,http://127.0.0.1:8776` | Merges several CAL Servers into one timeline (DDS hops between servers become visible) |
| `uv run python tools/lacal_cli.py --admin http://127.0.0.1:8766 trace` | One line per event in the terminal (`--kinds`, `--filter`, `--backlog`) |
| `uv run python tools/run_demo.py --dds --live` | Starts two servers and both nodes continuously and opens the monitor |

## Endpoints
| Method | Path | Returns |
|---|---|---|
| WS | `/trace/events?since=<seq>` | `{"kind":"hello","server_id":…}` first, then the backlog events with `seq > since`, then live events |
| GET | `/trace` | The backlog: the last 2000 events of this server, as a JSON array |
| GET | `/monitor` | The monitor page |

## Event
Every event contains `seq` (per server, increasing), `t` (epoch seconds, sub-millisecond resolution),
`server_id` and `kind`. The other fields depend on the kind:

| `kind` | When | Extra fields |
|---|---|---|
| `connect` | A client finished `INIT` | `node_id`, `service`, `remote_addr` |
| `disconnect` | The connection closed | `node_id`, `service` |
| `sub` / `unsub` | `SUB` / `UNSUB` accepted | `service`, `topic`, `message`, `sub_id`, `group` |
| `pub` | `PUB` accepted, after OMS JSON→XML→XSD validation | `service`, `topic`, `message`, `bytes`, `summary`, `validate_ms` |
| `deliver` | A `MSG` was queued for a subscriber | `service`, `topic`, `message`, `sub_id`, `group`, `bytes`, `origin` (publishing service, or the remote server id) |
| `drop` | A `MSG` was dropped because the client's outbound queue was full | same as `deliver` |
| `dds_out` | The envelope was written to DDS | `service`, `topic`, `message`, `bytes`, `qos` (`RELIABLE`/`BEST_EFFORT`) |
| `dds_in` | An envelope arrived from another server | `topic`, `message`, `bytes`, `qos`, `from_server`, `from_service_uuid`, `summary` |
| `err` | A `-ERR` was returned | `service`, `op`, `code`, `detail` |

`summary` is a digest of the message: `id_field`/`id` (first of CommandID, RxDataPayloadID, SystemID,
RequestID), `CommandState`, `CommandProcessingState`, `MessageType`, `SystemState`, `DestinationRouting`
and `header_ts` (MessageHeader.Timestamp) when present.

## Timing figures in the monitor
* **Δ ms:** time since the previous visible row.
* **Hz:** publications of that message by that publisher during the last 5 s.
* **Mean Δ / jitter:** mean and standard deviation of the last 50 publication intervals.
* **Command → status RTT:** time between the `pub` of a `…Command` and the `pub` of the `…CommandStatus`
  with the same CommandID. Both are measured where they are published, so on two servers the figure
  includes the DDS hop in both directions.
* **DDS hop:** the ms figure on a `dds_in` row is the arrival time minus the matching `pub` on the other
  server. It is only shown when both servers are monitored. Clocks are only comparable on the same host
  or with synchronised clocks.

Monitors that fall behind lose events (queues are bounded). The server's backlog can be re-read with
`GET /trace` or `?since=`.
