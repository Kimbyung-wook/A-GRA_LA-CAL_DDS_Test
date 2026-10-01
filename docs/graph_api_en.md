# Graph API (contract for rqt_graph-style viewers)

> 한국어(메인): [graph_api.md](graph_api.md)

Served by every CAL Server on its **admin port** (default `8766`), separate from the OWP port.
`api_version` is `1`; additive changes keep the version, removals/renames bump it.

## Endpoints

| Method | Path | Returns |
|---|---|---|
| GET | `/graph` | Merged snapshot: this server plus graphs received from other servers over DDS |
| GET | `/graph/local` | This server only (the document each server publishes on DDS topic `lacal/graph`) |
| GET | `/graph.dot` | Graphviz DOT of `/graph` (`dot -Tpng`) |
| GET | `/services`, `/topics` | The service or topic nodes of `/graph` |
| GET | `/stats` | Router counters: `published`, `delivered`, `dropped`, `remote_in`, `remote_out`, `sessions` |
| GET | `/healthz` | `ok` |
| WS | `/graph/events` | `snapshot` first, then incremental events (below) |
| WS | `/tap?topic=T&msg=M` | Every message routed on the server, filters optional (`topic echo`) |

HTTP responses carry `Access-Control-Allow-Origin: *` so a browser page can be the viewer.

## Snapshot (`GET /graph`)

```json
{
  "api_version": 1,
  "generated_at": 1790000000.0,
  "servers": [{"server_id": "calsrv-a", "system_label": "AGRA-Demo-ACP-1",
               "system_uuid": "8159a223-...", "local": true}],
  "nodes": [
    {"kind": "service", "id": "svc:calsrv-a:1", "label": "ma_core", "service_id": "ma_core",
     "server_id": "calsrv-a", "system_label": "AGRA-Demo-ACP-1",
     "uuids": {"system": "...", "service": "...", "subsystem": "...", "components": {"name": "uuid"}},
     "connected_at": 1790000000.0, "remote_addr": "127.0.0.1:61569", "invalid_count": 0,
     "declared": {"pub": ["MA_TxDataPayloadCommand@ms.dms"], "sub": ["..."]}},
    {"kind": "topic", "id": "topic:ms.dms/MA_TxDataPayloadCommand", "label": "MA_TxDataPayloadCommand@ms.dms",
     "topic": "ms.dms", "message_name": "MA_TxDataPayloadCommand", "primitive": "Command-2"}
  ],
  "edges": [
    {"kind": "pub", "id": "pub:svc:calsrv-a:1:topic:ms.dms/MA_TxDataPayloadCommand",
     "source": "svc:calsrv-a:1", "target": "topic:ms.dms/MA_TxDataPayloadCommand", "server_id": "calsrv-a",
     "first_seen": 0.0, "last_seen": 0.0, "msg_count": 5, "byte_count": 9120, "rate_hz": 1.0,
     "invalid_count": 0, "drop_count": 0, "active": true, "sub_id": null, "group": null},
    {"kind": "sub", "id": "sub:svc:calsrv-b:1:s2", "source": "topic:ms.dms/MA_TxDataPayloadCommand",
     "target": "svc:calsrv-b:1", "sub_id": "s2", "group": null, "active": true, "...": "same counters"}
  ]
}
```

Semantics:

* **Service node**: one live OWP connection after a successful `INIT`. Removed on disconnect.
* **Topic node**: a *(CAL topic, message name)* pair, because an OWP subscription is keyed by both.
  Topic nodes with the same id are shared between servers in the merged view. A topic node disappears
  when no edge references it.
* **pub edge**: created on the service's **first accepted PUB** (OWP has no advertise operation).
  `active` becomes `false` when nothing was published for `graph.inactive_after_s` (default 10 s).
* **sub edge**: exists from `SUB` until `UNSUB`/disconnect. `group` is the subscription group.
  Counters count deliveries (`drop_count` = outbound queue full).
* `rate_hz`: messages in the last 5 s divided by 5.
* `declared`: the Service Contract view from the server config, to compare with what is observed.

## Events (`WS /graph/events`)

The first frame is `{"type": "snapshot", "graph": <snapshot>}`. After that:

| `type` | Payload |
|---|---|
| `node_added` | `node` (as in the snapshot) |
| `node_removed` | `id` |
| `edge_added` | `edge` |
| `edge_removed` | `id` |
| `remote_updated` / `remote_removed` | `remote_server_id`; re-read `GET /graph` for the merged view |
| `stats` | `edges: [{id, msg_count, byte_count, rate_hz, drop_count, active}]` about once per second, local edges only |

Every event also carries `server_id` and `t` (epoch seconds). A slow observer can lose events
(the queue is bounded) and should resynchronise with `GET /graph`.

## Suggested rendering (future viewer)

Cytoscape.js or similar: services are ellipses and topics are boxes. Edge width follows `rate_hz`,
inactive edges are grey, and a compound box groups the services of each server. Filters apply to
message name, service and `primitive`.
