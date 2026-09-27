# 그래프 API (rqt_graph 스타일 뷰어를 위한 계약)

> English: [graph_api.md](graph_api.md)

모든 CAL Server가 OWP 포트와 분리된 **관리 포트**(기본 `8766`)에서 제공합니다.
`api_version`은 `1`입니다. 항목을 추가할 때는 버전을 유지하고, 삭제하거나 이름을 바꿀 때는 올립니다.

## 엔드포인트

| 메서드 | 경로 | 반환 |
|---|---|---|
| GET | `/graph` | 병합 스냅샷: 이 서버와, DDS로 받은 다른 서버들의 그래프 |
| GET | `/graph/local` | 이 서버만의 그래프. 각 서버가 DDS 토픽 `lacal/graph`로 발행하는 문서와 같습니다 |
| GET | `/graph.dot` | `/graph`의 Graphviz DOT(`dot -Tpng`으로 렌더링) |
| GET | `/services`, `/topics` | `/graph` 중 서비스 노드 또는 토픽 노드 |
| GET | `/stats` | 라우터 카운터: `published`, `delivered`, `dropped`, `remote_in`, `remote_out`, `sessions` |
| GET | `/healthz` | `ok` |
| WS | `/graph/events` | 먼저 `snapshot`을 보내고, 이후 증분 이벤트를 보냅니다(아래 참고) |
| WS | `/tap?topic=T&msg=M` | 서버가 라우팅하는 모든 메시지. 필터는 선택 사항입니다(`topic echo`에 해당) |

브라우저 페이지를 뷰어로 쓸 수 있도록 HTTP 응답에 `Access-Control-Allow-Origin: *`를 붙입니다.

## 스냅샷 (`GET /graph`)

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
     "target": "svc:calsrv-b:1", "sub_id": "s2", "group": null, "active": true, "...": "같은 카운터"}
  ]
}
```

의미:

* **서비스 노드:** `INIT`에 성공한 OWP 연결 하나입니다. 연결이 끊기면 제거됩니다.
* **토픽 노드:** *(CAL 토픽, 메시지 이름)* 쌍입니다. OWP 구독이 이 두 값을 함께 key로 쓰기 때문입니다.
  병합 뷰에서는 id가 같은 토픽 노드를 여러 서버가 공유합니다. 어떤 엣지도 참조하지 않으면 토픽 노드는 사라집니다.
* **pub 엣지:** 서비스의 **첫 번째 정상 PUB** 때 생성됩니다. OWP에는 발행 예정을 알리는(advertise) 명령이 없기 때문입니다.
  `graph.inactive_after_s`(기본 10초) 동안 발행이 없으면 `active`가 `false`가 됩니다.
* **sub 엣지:** `SUB`부터 `UNSUB` 또는 연결 종료까지 존재합니다. `group`은 Subscription Group입니다.
  카운터는 전달 횟수를 셉니다(`drop_count`는 송신 큐가 가득 차 버린 건수).
* `rate_hz`: 최근 5초 동안의 메시지 수를 5로 나눈 값입니다.
* `declared`: 서버 설정에 적은 Service Contract 관점의 pub/sub 목록입니다. 실제 관측과 비교하는 용도입니다.

## 이벤트 (`WS /graph/events`)

첫 프레임은 `{"type": "snapshot", "graph": <스냅샷>}`입니다. 그 뒤로는 다음 이벤트가 옵니다.

| `type` | 내용 |
|---|---|
| `node_added` | `node` (스냅샷과 같은 형식) |
| `node_removed` | `id` |
| `edge_added` | `edge` |
| `edge_removed` | `id` |
| `remote_updated` / `remote_removed` | `remote_server_id`. 병합 뷰는 `GET /graph`로 다시 읽습니다 |
| `stats` | `edges: [{id, msg_count, byte_count, rate_hz, drop_count, active}]`. 약 1초마다, 로컬 엣지만 |

모든 이벤트에는 `server_id`와 `t`(epoch 초)가 붙습니다. 큐 크기가 제한되어 있어서 느린 관찰자는 이벤트를 놓칠 수 있습니다.
그럴 때는 `GET /graph`로 다시 동기화합니다.

## 권장 렌더링 (향후 뷰어)

Cytoscape.js 등을 권장합니다. 서비스는 타원, 토픽은 사각형으로 그립니다.
- 엣지 굵기는 `rate_hz`를 따르고, 비활성 엣지는 회색으로 표시합니다.
- 서버마다 서비스를 묶는 복합 박스를 둡니다.
- 필터 기준은 메시지 이름, 서비스, `primitive`입니다.
