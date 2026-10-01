# 트레이스 API (실시간 메시지 타이밍)

> English: [trace_api.md](trace_api.md)

모든 CAL Server는 라우팅 단계마다 **트레이스 이벤트**를 하나씩 기록하며, 각 이벤트에는 밀리초 단위 타임스탬프가 붙습니다.
이 이벤트는 실시간 모니터 페이지와 `lacal trace` 명령이 사용하고, 직접 만든 도구에서도 받아 쓸 수 있습니다.
관리 포트(기본 `8766`)에서 제공합니다.

## 보는 방법
| 방법 | 내용 |
|---|---|
| `http://127.0.0.1:8766/monitor` | 웹 실시간 모니터: 시퀀스 형태 타임라인, 메시지별 타이밍, 명령→응답 RTT, 이벤트 상세 |
| `.../monitor?admin=http://127.0.0.1:8766,http://127.0.0.1:8776` | 여러 CAL Server를 하나의 타임라인으로 합쳐 봅니다(서버 간 DDS 구간이 보임) |
| `python tools/lacal_cli.py --admin http://127.0.0.1:8766 trace` | 터미널에 이벤트를 한 줄씩 출력(`--kinds`, `--filter`, `--backlog`) |
| `python tools/run_demo.py --dds --live` | 서버 2개와 노드 2개를 계속 실행하고 모니터를 엽니다 |

## 엔드포인트
| 메서드 | 경로 | 반환 |
|---|---|---|
| WS | `/trace/events?since=<seq>` | 먼저 `{"kind":"hello","server_id":…}`, 이어서 `seq > since`인 이력, 그 뒤 실시간 이벤트 |
| GET | `/trace` | 이력: 이 서버의 최근 이벤트 2000건(JSON 배열) |
| GET | `/monitor` | 모니터 페이지 |

## 이벤트
모든 이벤트에는 `seq`(서버별 증가 번호), `t`(epoch 초, 밀리초 이하 해상도), `server_id`, `kind`가 들어갑니다.
나머지 필드는 종류에 따라 다릅니다.

| `kind` | 발생 시점 | 추가 필드 |
|---|---|---|
| `connect` | 클라이언트가 `INIT`을 마침 | `node_id`, `service`, `remote_addr` |
| `disconnect` | 연결 종료 | `node_id`, `service` |
| `sub` / `unsub` | `SUB` / `UNSUB` 수락 | `service`, `topic`, `message`, `sub_id`, `group` |
| `pub` | `PUB` 수락(OMS JSON→XML→XSD 검증 후) | `service`, `topic`, `message`, `bytes`, `summary`, `validate_ms` |
| `deliver` | 구독자에게 `MSG`를 큐에 넣음 | `service`, `topic`, `message`, `sub_id`, `group`, `bytes`, `origin`(발행 서비스 또는 원격 서버 id) |
| `drop` | 클라이언트 송신 큐가 가득 차 `MSG`를 버림 | `deliver`와 같음 |
| `dds_out` | 봉투(envelope)를 DDS로 기록 | `service`, `topic`, `message`, `bytes`, `qos`(`RELIABLE`/`BEST_EFFORT`) |
| `dds_in` | 다른 서버에서 봉투 도착 | `topic`, `message`, `bytes`, `qos`, `from_server`, `from_service_uuid`, `summary` |
| `err` | `-ERR` 반환 | `service`, `op`, `code`, `detail` |

`summary`는 메시지 요약입니다. 다음 필드 중 메시지에 있는 것만 담깁니다.
- `id_field`/`id`: CommandID, RxDataPayloadID, SystemID, RequestID 중 처음 발견된 것
- `CommandState`, `CommandProcessingState`, `MessageType`, `SystemState`, `DestinationRouting`
- `header_ts`: MessageHeader.Timestamp

## 모니터의 타이밍 지표
* **Δ ms:** 바로 위에 보이는 행과의 시간 간격입니다.
* **Hz:** 해당 발행자가 최근 5초 동안 그 메시지를 발행한 빈도입니다.
* **평균 Δ / 지터:** 최근 50개 발행 간격의 평균과 표준편차입니다.
* **명령 → 응답 RTT:** `…Command`의 `pub`부터, CommandID가 같은 `…CommandStatus`의 `pub`까지 걸린 시간입니다.
  둘 다 발행된 서버에서 측정하므로, 서버가 2개면 왕복 DDS 구간이 포함됩니다.
* **DDS 구간:** `dds_in` 행의 ms 값은 도착 시각에서 다른 서버의 대응 `pub` 시각을 뺀 값입니다.
  두 서버를 모두 모니터링할 때만 표시됩니다. 같은 호스트이거나 시계가 동기화된 경우에만 비교할 수 있습니다.

뒤처진 모니터는 이벤트를 놓칠 수 있습니다(큐 크기 제한). 그럴 때는 `GET /trace`나 `?since=`로 서버 이력을 다시 읽습니다.
