# agra_lacal: A-GRA messages over an OMS Language-Agnostic CAL

> 한국어: [README_kr.md](README_kr.md)

This project is a Python CAL Server and CAL Client for the OMS Language-Agnostic CAL
(OMSC-SPC-013 RevB, OWP 1.0). It carries the A-GRA 6.0a message set
(`A-GRA_MessageDefinitions_v6_0_a.xsd`, schema version `006.0.ASK`). Two demo nodes exchange the
MS-interface DMS Minimum Message Set through it.

```
node_a (MA, "ma_core") ──WS "owp" + OMS JSON──► CAL Server A ══ Cyclone DDS (RTPS/CDR) ══ CAL Server B ◄──WS── node_b (DMS mock, "ms_dms")
```

## Why LA-CAL
* A-GRA (ICD 3.3, 11) requires the OMS CAL/ASB onboard (MS, VI, MA L2) but leaves the CAL
  implementation, serialization and ASB open.
* OMS (OMSC-STD-001 6.5.2) defines two CAL families. The Language-Agnostic CAL is the official way to
  use languages such as Python. It is optional for a Platform and does not replace the C++/Java CALs.
* Version note: the A-GRA ICD references OMS v2.3, while the LA-CAL spec used here comes from the
  OMS v2.5 document set.

## Quick start
```
pip install -r requirements.txt
python -m pytest                          # 80 tests, includes a 2-server DDS test
python tools/run_demo.py                  # 1 server, local routing
python tools/run_demo.py --dds            # 2 servers bridged by DDS; prints the cross-server graph
python tools/run_demo.py --dds --live     # keep running and open the live monitor (Ctrl+C to stop)
```

### Live monitoring
Every routing step (PUB, MSG delivery, DDS out/in, errors, connect/subscribe) is recorded as a
timestamped trace event:
* **Web:** `http://127.0.0.1:8766/monitor` shows a sequence-style timeline, per-message rate, interval
  and jitter, and command→status RTT. Add `?admin=http://127.0.0.1:8766,http://127.0.0.1:8776` to merge
  both servers.
* **Terminal:** `python tools/lacal_cli.py --admin http://127.0.0.1:8766 trace`
* The event format is described in `docs/trace_api.md`.
### Schema
`schema/` already contains the A-GRA 6.0a XSDs, so no preparation step is needed after cloning.
* `A-GRA_MessageDefinitions_v6_0_a.xsd` is a **patched copy**. Upstream it includes
  `A-GRA_SecurityMarkings_v6_0.xsd`, but the ASK ships that file as `A-GRA_SecurityMarkings_v6_0_a.xsd`.
  Only this `schemaLocation` was changed; the content is otherwise identical.
* The schema carries "Distribution Statement A. Approved for public release: distribution is unlimited."
* `schema/primitives.json` is a cache of the `UCI_PRIMITIVE` annotations. It is rebuilt automatically when missing.
* To re-import the XSDs (for example a newer ASK release), download them from the
  [Open Arsenal A-GRA repository](https://gitlab.com/open-arsenal/a-gra/standard) (GitLab)
  or copy them from a local checkout. Both paths apply the same include fix:
  ```
  python tools/prepare_schema.py --download [--ref main]          # branch, tag or commit
  python tools/prepare_schema.py --src <checkout>/Schema
  ```
  Without options the script uses `../OpenArsenalGit/A-GRA_standard/Schema` if it exists. Otherwise it
  keeps the bundled schema and prints the commands above.

Manual run (three terminals):
```
python -m server --config config/server_a.yaml
python nodes/node_b.py
python nodes/node_a.py --count 5 --linger 30
python tools/lacal_cli.py graph            # while node_a lingers; --dot for Graphviz
```

## What is on the wire
| Hop | Format |
|---|---|
| Client ⇄ CAL Server | WebSocket text frames. OWP operations `INIT/PUB/SUB/UNSUB` and `INFO/MSG/+OK/-ERR`. Messages are **OMS JSON** (spec 6.1) |
| Inside the server | OMS JSON → XML (content-model order, `$type`→`xsi:type`, UUID → hexBinary) → **XSD validation** (lxml). Invalid messages get `-ERR Invalid-Message` |
| CAL Server ⇄ CAL Server | DDS/RTPS carrying a **CDR binary** `lacal::CalEnvelope {message_name, cal_topic, src_system, src_service, server_id, encoding, sequence<octet> payload}`. The payload is canonical OMS JSON (encoding 1). DDS topics are `cal/r/<topic>` (RELIABLE) and `cal/b/<topic>` (BEST_EFFORT), chosen by UCI primitive as in MS L1 Volume 1.2.5.5. UserData QoS carries the SystemID UUID |
| `EncodedPayload` inside `MA_TxDataPayloadCommand`/`MA_RxDataPayload` | UTF-8 XML of the inner UCI message as hexBinary (MS L1 Volume, L1 boundary) |

The design uses one generic envelope instead of per-message IDL. The reasons: the CAL spec leaves the
ASB format open, new or extended messages need no regeneration (MA-L2-002), and it mirrors the
A-GRA L1 `MA_DataPayloadWrapper`.

## Layout
| Path | Role |
|---|---|
| `server/omsjson.py` | OMS JSON ⇄ XML codec plus XSD validation. It is checked on all 860 global elements of the schema |
| `server/owp_codec.py`, `session.py` | OWP parsing and the per-connection state machine (`AWAIT_INIT → READY → CLOSED`) |
| `server/router.py` | Fan-out, subscription groups (one member per group+topic) and taps |
| `server/graph.py`, `app.py` | Pub/sub graph and the admin API. The contract is in `docs/graph_api.md` (Korean: `docs/graph_api_kr.md`) |
| `server/backend_dds.py` | Cyclone DDS backend and graph sync on `lacal/graph` (TRANSIENT_LOCAL, keyed by server) |
| `server/config.py`, `config/*.yaml` | Network Configuration: services, UUIDs, allowed topics, backend |
| `client/lacal_client.py` | asyncio CAL Client |
| `tools/json_skeleton.py` | Minimal valid OMS JSON for any message (`--check` validates it) |
| `server/trace.py`, `server/monitor.html` | Trace events (`/trace`, `/trace/events`) and the live monitor page (`/monitor`) |
| `tools/lacal_cli.py` | `graph`, `service list`, `topic list/info/hz/echo`, `events`, `trace`, `pub` |
| `tests/test_session_certs.py` | One test per LACAL-000001..014 CERT behaviour |

## Interpretations and known limits
* **Field splitting:** OWP splits fields on spaces and tabs, but JSON can contain spaces. The last
  field of `INIT`, `PUB` and `MSG` is therefore read as "the rest of the frame".
* **INIT failures:** the server sends `-ERR` and then closes with code 1008 ("fail the connection").
* **Subscription groups:** they are balanced per server. Groups whose members sit on different
  servers each receive the message once per server (cross-server coordination is future work).
* **Payload encoding:** only OMS JSON is used. EXI (`encoding` 3) and DDS-Security (mTLS) are not
  implemented yet.
* **Decimals:** `xs:decimal` values are mapped through Python floats. The A-GRA schema uses
  `double`/`int` types in practice.
* **Windows:** the server uses the Selector event loop. The Proactor loop stops listening after a single
  aborted accept (WinError 64).
* **DDS threading:** DDS readers are served by a WaitSet thread, not listeners. Python listeners
  deadlock against `DataWriter.write()` because of the GIL and Cyclone's internal locks.
