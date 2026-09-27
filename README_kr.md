# agra_lacal: OMS Language-Agnostic CAL 위에서 A-GRA 메시지 송/수신

> English: [README.md](README.md)

이 프로젝트는 OMS Language-Agnostic CAL(OMSC-SPC-013 RevB, OWP 1.0)을 구현한 Python CAL Server와 CAL Client입니다.
A-GRA 6.0a 메시지 세트(`A-GRA_MessageDefinitions_v6_0_a.xsd`, 스키마 버전 `006.0.ASK`)를 전달하며,
데모 노드 두 개가 이를 통해 MS 인터페이스의 DMS Minimum Message Set(MMS)을 주고받습니다.

```
node_a (MA, "ma_core") ──WS "owp" + OMS JSON──► CAL Server A ══ Cyclone DDS (RTPS/CDR) ══ CAL Server B ◄──WS── node_b (DMS 모사, "ms_dms")
```

## LA-CAL을 선택한 이유
* A-GRA(ICD §3.3, §11)는 온보드 구간(MS, VI, MA L2)에 OMS CAL/ASB를 요구합니다. 다만 CAL 구현, 직렬화 방식, ASB는 정하지 않습니다.
* OMS(OMSC-STD-001 §6.5.2)는 CAL을 두 계열로 정의합니다. 그중 Language-Agnostic CAL이 Python 같은 언어를 쓰는 공식 경로입니다. 플랫폼 필수 요소는 아니며, C++/Java CAL을 대체하지도 않습니다.
* 버전 참고: A-GRA ICD는 OMS v2.3을 참조하지만, 여기서 사용한 LA-CAL 규격은 OMS v2.5 문서입니다.

## 빠른 시작
```
pip install -r requirements.txt
python tools/prepare_schema.py            # XSD 복사 및 SecurityMarkings include 파일명 보정
python -m pytest                          # 테스트 76개 (서버 2개 DDS 테스트 포함)
python tools/run_demo.py                  # 서버 1개, 로컬 라우팅
python tools/run_demo.py --dds            # DDS로 연결된 서버 2개, 서버 간 병합 그래프 출력
```
수동 실행(터미널 3개):
```
python -m server --config config/server_a.yaml
python nodes/node_b.py
python nodes/node_a.py --count 5 --linger 30
python tools/lacal_cli.py graph            # node_a가 연결을 유지하는 동안 실행, Graphviz 출력은 --dot
```

## 구간별 전송 형태
| 구간 | 형식 |
|---|---|
| 클라이언트 ⇄ CAL Server | WebSocket 텍스트 프레임입니다. OWP 명령은 `INIT/PUB/SUB/UNSUB`와 `INFO/MSG/+OK/-ERR`이고, 메시지는 **OMS JSON**(규격 §6.1)입니다 |
| 서버 내부 | OMS JSON을 XML로 변환(content model 순서 정렬, `$type`→`xsi:type`, UUID→hexBinary)한 뒤 **XSD 검증**(lxml)을 합니다. 유효하지 않으면 `-ERR Invalid-Message`를 반환합니다 |
| CAL Server ⇄ CAL Server | DDS/RTPS 위에 **CDR 바이너리** 구조체 `lacal::CalEnvelope {message_name, cal_topic, src_system, src_service, server_id, encoding, sequence<octet> payload}`를 싣습니다. payload에는 정규화된 OMS JSON(encoding 1)이 들어갑니다. DDS 토픽은 `cal/r/<topic>`(RELIABLE)과 `cal/b/<topic>`(BEST_EFFORT)이며, MS L1 Volume §1.2.5.5에 따라 UCI primitive로 고릅니다. UserData QoS에는 SystemID UUID를 넣습니다 |
| `MA_TxDataPayloadCommand`/`MA_RxDataPayload`의 `EncodedPayload` | 내부 UCI 메시지의 UTF-8 XML을 hexBinary로 표현합니다(MS L1 Volume, L1 경계 규정) |

메시지별 IDL 대신 범용 봉투(Envelope) 하나를 쓴 이유는 다음과 같습니다.
- CAL 규격이 ASB 내부 포맷을 자유롭게 두고 있습니다.
- 메시지를 추가하거나 확장해도 코드를 다시 생성할 필요가 없습니다(MA-L2-002).
- A-GRA L1의 `MA_DataPayloadWrapper`와 같은 구조입니다.

## 구성
| 경로 | 역할 |
|---|---|
| `server/omsjson.py` | OMS JSON ⇄ XML 코덱과 XSD 검증. 스키마의 전역 요소 860개 전체로 검증을 마쳤습니다 |
| `server/owp_codec.py`, `session.py` | OWP 파싱과 연결별 상태 머신(`AWAIT_INIT → READY → CLOSED`) |
| `server/router.py` | 메시지 분배(fan-out), Subscription Group(그룹+토픽당 한 멤버에게만 전달), 탭 |
| `server/graph.py`, `app.py` | pub/sub 그래프와 관리 API. JSON 형식은 `docs/graph_api_kr.md`에 정의했습니다 |
| `server/backend_dds.py` | Cyclone DDS 백엔드. `lacal/graph` 토픽(TRANSIENT_LOCAL, 서버별 key)으로 그래프를 동기화합니다 |
| `server/config.py`, `config/*.yaml` | Network Configuration: 서비스, UUID, 허용 토픽, 백엔드 |
| `client/lacal_client.py` | asyncio 기반 CAL Client |
| `tools/json_skeleton.py` | 임의 메시지의 최소 유효 OMS JSON 생성기(`--check`로 검증) |
| `tools/lacal_cli.py` | `graph`, `service list`, `topic list/info/hz/echo`, `events`, `pub` |
| `tests/test_session_certs.py` | CERT LACAL-000001~014의 동작마다 테스트 하나씩 |

## 규격 해석과 알려진 제약
* **필드 분리:** OWP는 공백과 탭으로 필드를 나누지만, JSON 안에도 공백이 들어갈 수 있습니다. 그래서 `INIT`, `PUB`, `MSG`의 마지막 필드는 "프레임의 나머지 전체"로 읽습니다.
* **INIT 실패:** 서버는 `-ERR`를 보낸 뒤 종료 코드 1008로 연결을 닫습니다("fail the connection").
* **Subscription Group:** 서버 단위로 분배합니다. 멤버가 여러 서버에 흩어져 있으면 서버마다 한 번씩 전달됩니다(서버 간 조정은 향후 과제).
* **payload 인코딩:** OMS JSON만 사용합니다. EXI(`encoding` 3)와 DDS-Security(mTLS)는 아직 구현하지 않았습니다.
* **Decimal:** `xs:decimal` 값은 Python float로 변환합니다. A-GRA 스키마는 실제로 `double`/`int` 계열만 씁니다.
* **Windows:** 서버는 Selector 이벤트 루프를 씁니다. 기본 Proactor 루프는 accept가 한 번만 중단돼도(WinError 64) 수신 대기를 멈춥니다.
* **DDS 스레딩:** DDS reader는 listener 대신 WaitSet 스레드로 처리합니다. Python listener는 GIL과 Cyclone 내부 락 때문에 `DataWriter.write()`와 교착을 일으킵니다.
