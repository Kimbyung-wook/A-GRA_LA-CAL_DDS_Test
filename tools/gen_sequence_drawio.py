"""Generate docs/sequence_dms.drawio: sequence diagram of the DMS MMS demo (tools/run_demo.py --dds).

    uv run python tools/gen_sequence_drawio.py
    "C:/Program Files/draw.io/draw.io.exe" -x -f png -s 1.5 -o docs/sequence_dms.png docs/sequence_dms.drawio
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parents[1] / "docs" / "sequence_dms.drawio"

LANES = {  # id: (x-center, header label)
    "na": (150, "<b>node_a</b><br>MA 컴포넌트 · ma_core"),
    "sa": (520, "<b>CAL Server A</b><br>calsrv-a :8765"),
    "sb": (890, "<b>CAL Server B</b><br>calsrv-b :8775"),
    "nb": (1260, "<b>node_b</b><br>MS DMS 모사 · ms_dms"),
}
LANE_W, HEAD_H, TOP = 200, 56, 40
ROW = 34

OWP = "#1f2937"        # WebSocket / OWP
REL = "#1d4ed8"        # DDS RELIABLE
BEST = "#c2410c"       # DDS BEST_EFFORT
GRAPH = "#6b7280"      # graph sync


class Diagram:
    def __init__(self):
        self.cells: list[str] = []
        self.y = TOP + HEAD_H + 30
        self.n = 2

    def _id(self) -> str:
        self.n += 1
        return f"c{self.n}"

    def vertex(self, label, style, x, y, w, h):
        self.cells.append(
            f'<mxCell id="{self._id()}" value="{escape(label, {chr(34): "&quot;"})}" style="{style}" vertex="1" parent="1">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')

    def msg(self, src, dst, label, color=OWP, dashed=False, open_arrow=False, width=1.5):
        if "<br>" in label:
            self.y += 14  # room for the second label line above the arrow
        y = self.y
        x1, x2 = LANES[src][0], LANES[dst][0]
        style = (f"html=1;verticalAlign=bottom;labelBackgroundColor=#ffffff;fontSize=11;strokeColor={color};"
                 f"fontColor={color};strokeWidth={width};endArrow={'open' if open_arrow else 'block'};endFill=1;"
                 f"{'dashed=1;' if dashed else ''}")
        self.cells.append(
            f'<mxCell id="{self._id()}" value="{escape(label, {chr(34): "&quot;"})}" style="{style}" edge="1" parent="1">'
            f'<mxGeometry relative="1" as="geometry"><mxPoint x="{x1}" y="{y}" as="sourcePoint"/>'
            f'<mxPoint x="{x2}" y="{y}" as="targetPoint"/></mxGeometry></mxCell>')
        self.y += ROW

    def note(self, lane, text, w=190, h=30, left=False):
        x = LANES[lane][0] - w - 8 if left else LANES[lane][0] + 8
        self.vertex(text, "shape=note;whiteSpace=wrap;html=1;size=10;fillColor=#fef9c3;strokeColor=#ca8a04;"
                          "fontSize=10;align=left;spacingLeft=6;", x, self.y - 14, w, h)
        self.y += h + 8

    def section(self, title):
        self.y += 8
        x0 = LANES["na"][0] - LANE_W / 2
        x1 = LANES["nb"][0] + LANE_W / 2
        self.vertex(title, "rounded=0;html=1;fillColor=#e5e7eb;strokeColor=none;fontStyle=1;fontSize=12;"
                           "align=left;spacingLeft=10;opacity=85;", x0, self.y - 12, x1 - x0, 22)
        self.y += 34

    def frame_start(self):
        self._frame_top = self.y - 10

    def frame_end(self, label):
        x0 = LANES["na"][0] - 70
        x1 = LANES["nb"][0] + 90
        self.vertex(label, "shape=umlFrame;whiteSpace=wrap;html=1;width=90;height=24;fontStyle=1;fontSize=11;"
                           "fillColor=none;strokeColor=#374151;", x0, self._frame_top, x1 - x0, self.y - self._frame_top)
        self.y += 14

    def xml(self) -> str:
        bottom = self.y + 20
        heads = []
        for lane, (cx, label) in LANES.items():
            heads.append(
                f'<mxCell id="lane_{lane}" value="{escape(label, {chr(34): "&quot;"})}" '
                f'style="shape=umlLifeline;perimeter=lifelinePerimeter;whiteSpace=wrap;html=1;container=0;'
                f'collapsible=0;recursiveResize=0;outlineConnect=0;size={HEAD_H};fillColor=#dbeafe;'
                f'strokeColor=#1e3a8a;fontSize=12;" vertex="1" parent="1">'
                f'<mxGeometry x="{cx - LANE_W / 2}" y="{TOP}" width="{LANE_W}" height="{bottom - TOP}" as="geometry"/></mxCell>')
        body = "".join(heads + self.cells)  # lifelines first so messages draw on top
        return ('<mxfile host="agra_lacal"><diagram id="seq" name="DMS MMS sequence">'
                '<mxGraphModel dx="1400" dy="900" grid="0" gridSize="10" guides="1" tooltips="1" connect="1" '
                'arrows="1" fold="1" page="0" pageScale="1" math="0" shadow="0"><root>'
                '<mxCell id="0"/><mxCell id="1" parent="0"/>' + body + '</root></mxGraphModel></diagram></mxfile>\n')


def build() -> str:
    d = Diagram()
    d.vertex("<b>A-GRA DMS MMS 송/수신 시퀀스</b> — OMS LA-CAL (OWP 1.0) + Cyclone DDS · <i>tools/run_demo.py --dds</i>",
             "text;html=1;fontSize=15;align=left;verticalAlign=middle;", LANES["na"][0] - LANE_W / 2, 4, 1100, 30)

    d.section("① 연결 · 초기화 · 구독")
    d.msg("nb", "sb", "WebSocket 핸드셰이크 (Sec-WebSocket-Protocol: owp)")
    d.msg("nb", "sb", "INIT {versions:[\"1.0\"], schema:\"006.0.ASK\", service_id:\"ms_dms\"}")
    d.msg("sb", "nb", "+OK · INFO {server_id:calsrv-b, uuids{system, service, subsystem}}", dashed=True, open_arrow=True)
    d.msg("nb", "sb", "SUB s1 MA_MessagingSubscriptionSettingCommand ms.dms")
    d.msg("nb", "sb", "SUB s2 MA_TxDataPayloadCommand ms.dms")
    d.note("sb", "DDS reader 생성<br>cal/r/ms/dms · cal/b/ms/dms", w=180, h=34)
    d.msg("na", "sa", "WebSocket(owp) + INIT {service_id:\"ma_core\"}")
    d.msg("sa", "na", "+OK · INFO {server_id:calsrv-a, uuids{...}}", dashed=True, open_arrow=True)
    d.msg("na", "sa", "SUB s1..s4: …SettingCommandStatus, …TxDataPayloadCommandStatus,<br>MA_RxDataPayload, MA_MessagingSubscriptionStatus")
    d.y += 8
    d.msg("sa", "sb", "DDS lacal/graph (TRANSIENT_LOCAL) — 그래프 스냅샷", color=GRAPH, dashed=True, open_arrow=True, width=1)
    d.msg("sb", "sa", "DDS lacal/graph — 그래프 스냅샷", color=GRAPH, dashed=True, open_arrow=True, width=1)

    d.section("② 구독 설정 명령 (Command-2 → RELIABLE)")
    d.msg("na", "sa", "PUB ms.dms {MA_MessagingSubscriptionSettingCommand}<br>AddTopicSubscription: Topic=SystemStatus")
    d.note("sa", "OMS JSON → XML → XSD 검증 통과", w=180)
    d.msg("sa", "na", "+OK", dashed=True, open_arrow=True)
    d.msg("sa", "sb", "DDS cal/r/ms/dms · CalEnvelope (CDR) [RELIABLE]", color=REL)
    d.msg("sb", "nb", "MSG s1 {MA_MessagingSubscriptionSettingCommand}")
    d.msg("nb", "sb", "PUB ms.dms {MA_MessagingSubscriptionSettingCommandStatus: ACCEPTED}")
    d.msg("sb", "sa", "DDS cal/r/ms/dms [RELIABLE]", color=REL)
    d.msg("sa", "na", "MSG s1 {…SettingCommandStatus: ACCEPTED}", dashed=True, open_arrow=True)

    d.section("③ 페이로드 전송 명령")
    d.frame_start()
    d.y += 30
    d.msg("na", "sa", "PUB ms.dms {MA_TxDataPayloadCommand}<br>EncodedPayload = hex(SystemStatus UTF-8 XML, 781 B)")
    d.msg("sa", "na", "+OK", dashed=True, open_arrow=True)
    d.msg("sa", "sb", "DDS cal/r/ms/dms [RELIABLE]", color=REL)
    d.msg("sb", "nb", "MSG s2 {MA_TxDataPayloadCommand}")
    d.note("nb", "hex → XML → XSD 검증<br>(내부 메시지: SystemStatus)", w=170, h=34, left=True)
    d.msg("nb", "sb", "PUB {MA_TxDataPayloadCommandStatus: ACCEPTED}")
    d.msg("sb", "sa", "DDS cal/r/ms/dms [RELIABLE]", color=REL)
    d.msg("sa", "na", "MSG s2 {…TxDataPayloadCommandStatus: ACCEPTED} → RTT 측정", dashed=True, open_arrow=True)
    d.msg("nb", "sb", "PUB {MA_RxDataPayload} 동일 payload (오프보드 수신 모사)")
    d.msg("sb", "sa", "DDS cal/b/ms/dms [BEST_EFFORT · Data-1]", color=BEST)
    d.msg("sa", "na", "MSG s3 {MA_RxDataPayload} → SystemStatus 복원·검증", dashed=True, open_arrow=True)
    d.frame_end("loop ×5")

    d.section("④ 주기 상태 (Status-1 → BEST_EFFORT)")
    d.frame_start()
    d.y += 30
    d.msg("nb", "sb", "PUB {MA_MessagingSubscriptionStatus} TopicSubscription: SystemStatus")
    d.msg("sb", "sa", "DDS cal/b/ms/dms [BEST_EFFORT · Status-1]", color=BEST)
    d.msg("sa", "na", "MSG s4 {MA_MessagingSubscriptionStatus}", dashed=True, open_arrow=True)
    d.frame_end("loop 1 Hz")

    d.section("⑤ 종료")
    d.msg("na", "sa", "WebSocket close")
    d.note("sa", "구독 해제 · 그래프 노드/엣지 제거", w=190)
    d.msg("sa", "sb", "DDS lacal/graph 갱신 (ma_core 제거)", color=GRAPH, dashed=True, open_arrow=True, width=1)

    d.y += 16
    x0 = LANES["na"][0] - LANE_W / 2
    d.vertex("<b>실행 결과</b> (2026-09-27): 명령 6/6 ACCEPTED · MA_RxDataPayload 5건 수신 · RTT min 1.83 / median 7.83 / max 16.15 ms"
             "<br><b>범례</b>: <font color='#1f2937'>━ WebSocket/OWP (OMS JSON 텍스트)</font> · "
             "<font color='#1d4ed8'>━ DDS RELIABLE</font> · <font color='#c2410c'>━ DDS BEST_EFFORT</font> · "
             "<font color='#6b7280'>┅ 그래프 동기화</font> · 점선 화살표 = 응답/전달",
             "rounded=1;whiteSpace=wrap;html=1;fillColor=#f9fafb;strokeColor=#9ca3af;align=left;spacingLeft=10;fontSize=11;",
             x0, d.y, LANES["nb"][0] + LANE_W / 2 - x0, 48)
    d.y += 60
    return d.xml()


if __name__ == "__main__":
    OUT.write_text(build(), encoding="utf-8")
    print(OUT)
