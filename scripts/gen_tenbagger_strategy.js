/**
 * K-Quant 텐배거 매매전략 설명서 생성기
 * 2026.03.11
 */
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, PageNumber, PageBreak, LevelFormat,
} = require("docx");

// ── 색상 팔레트 ────────────────────────────────────────
const C = {
  dark: "1A1A2E",
  navy: "16213E",
  blue: "0F3460",
  accent: "E94560",
  gold: "F5A623",
  green: "27AE60",
  red: "E74C3C",
  gray: "95A5A6",
  lightGray: "ECF0F1",
  white: "FFFFFF",
  gradeA: "27AE60",
  gradeB: "F39C12",
  gradeC: "E67E22",
};

// ── 테이블 헬퍼 ────────────────────────────────────────
const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const borders = { top: border, bottom: border, left: border, right: border };
const cellMargins = { top: 60, bottom: 60, left: 100, right: 100 };

function headerCell(text, width) {
  return new TableCell({
    borders,
    width: { size: width, type: WidthType.DXA },
    shading: { fill: C.navy, type: ShadingType.CLEAR },
    margins: cellMargins,
    verticalAlign: "center",
    children: [new Paragraph({
      alignment: AlignmentType.CENTER,
      children: [new TextRun({ text, bold: true, color: C.white, font: "Arial", size: 18 })],
    })],
  });
}

function dataCell(text, width, opts = {}) {
  return new TableCell({
    borders,
    width: { size: width, type: WidthType.DXA },
    shading: opts.fill ? { fill: opts.fill, type: ShadingType.CLEAR } : undefined,
    margins: cellMargins,
    verticalAlign: "center",
    children: [new Paragraph({
      alignment: opts.align || AlignmentType.LEFT,
      children: [new TextRun({
        text,
        bold: !!opts.bold,
        color: opts.color || C.dark,
        font: "Arial",
        size: opts.size || 18,
      })],
    })],
  });
}

// ── 데이터 ─────────────────────────────────────────────
const koreaStocks = [
  { grade: "A", name: "우진", code: "105840", sector: "원전/SMR", character: "4대 핵심계측기 독점, 반복매출", buyPrice: "4,195", curPrice: "26,400", pnl: "+529%", status: "보유", action: "6배 근접 - 30% 차익실현 후 홀드" },
  { grade: "A", name: "비에이치아이", code: "083650", sector: "원전/SMR", character: "실적 증명된 원전+LNG", buyPrice: "48,157", curPrice: "106,200", pnl: "+121%", status: "보유", action: "2배 달성 - 50% 매도(원금회수)" },
  { grade: "A", name: "인텔리안테크", code: "189300", sector: "우주/방산", character: "LEO 위성안테나 흑자전환", buyPrice: "45,993", curPrice: "136,200", pnl: "+196%", status: "보유", action: "2배 초과 - 50% 매도(원금회수)" },
  { grade: "A", name: "일진파워", code: "094820", sector: "원전/SMR", character: "원전 정비 운영레이어", buyPrice: "5,086", curPrice: "17,560", pnl: "+245%", status: "보유", action: "3배+ - 30% 차익실현" },
  { grade: "A", name: "비츠로셀", code: "082920", sector: "우주/방산", character: "고마진 특수배터리", buyPrice: "8,880", curPrice: "21,750", pnl: "+145%", status: "보유", action: "2배 달성 - 50% 매도(원금회수)" },
  { grade: "B", name: "오르비텍", code: "046120", sector: "원전/SMR", character: "해체/폐기물 특수", buyPrice: "-", curPrice: "~5,000", pnl: "-", status: "미보유", action: "적자축소 확인 후 -10% 조정 시 1차 진입" },
  { grade: "B", name: "에스피지", code: "058610", sector: "로봇", character: "RV감속기 국산화", buyPrice: "-", curPrice: "~40,000", pnl: "-", status: "미보유", action: "-5% 조정 시 2분할 진입" },
  { grade: "B", name: "ICTK", code: "456010", sector: "양자보안", character: "PQC+PUF 하드웨어", buyPrice: "-", curPrice: "~15,000", pnl: "-", status: "미보유", action: "상용계약 확인 후 2분할 진입" },
  { grade: "C", name: "씨메스", code: "475400", sector: "로봇/AI", character: "피지컬AI 팔레타이징", buyPrice: "-", curPrice: "~25,000", pnl: "-", status: "미보유", action: "소량 1회 진입 (1~3%)" },
  { grade: "C", name: "씨에스윈드", code: "112610", sector: "클린에너지", character: "글로벌 풍력타워 1위", buyPrice: "-", curPrice: "~35,000", pnl: "-", status: "미보유", action: "IRA/금리 확인 후 소량 진입" },
];

const usStocks = [
  { grade: "A", name: "Centrus Energy", ticker: "LEU", sector: "원전/SMR", character: "HALEU 독점 병목", buyPrice: "$70", curPrice: "$203", pnl: "+190%", status: "보유", action: "2배 초과 - 50% 매도(원금회수)" },
  { grade: "A", name: "Denison Mines", ticker: "DNN", sector: "원전/SMR", character: "Phoenix ISR 건설 착공", buyPrice: "$1.50", curPrice: "$2.24", pnl: "+49%", status: "보유", action: "건설 진행 확인하며 홀드" },
  { grade: "A", name: "NexGen Energy", ticker: "NXE", sector: "원전/SMR", character: "CNSC 승인 완료", buyPrice: "-", curPrice: "$8.50", pnl: "-", status: "미보유", action: "3분할 피라미딩 진입" },
  { grade: "A", name: "BitGo", ticker: "BTGO", sector: "디지털자산", character: "커스터디 인프라", buyPrice: "$10", curPrice: "$11.71", pnl: "+17%", status: "보유", action: "IPO $18 하회 - 비대칭 구간, 홀드" },
  { grade: "B", name: "Credo Technology", ticker: "CRDO", sector: "AI인프라", character: "800G/1.6T 광연결", buyPrice: "-", curPrice: "$68", pnl: "-", status: "미보유", action: "-10% 조정 시 2분할 진입" },
  { grade: "B", name: "Circle", ticker: "CRCL", sector: "디지털자산", character: "USDC 스테이블코인", buyPrice: "-", curPrice: "$14", pnl: "-", status: "미보유", action: "법안 통과 확인 후 진입" },
  { grade: "B", name: "IonQ", ticker: "IONQ", sector: "양자컴퓨팅", character: "이온트랩 리더", buyPrice: "-", curPrice: "$30", pnl: "-", status: "미보유", action: "시총 부담 - 급락 시 소량" },
  { grade: "B", name: "MDA Space", ticker: "MDA", sector: "우주/방산", character: "우주인프라 실적형", buyPrice: "-", curPrice: "$25", pnl: "-", status: "미보유", action: "백로그 확인 후 2분할" },
  { grade: "C", name: "NuScale Power", ticker: "SMR", sector: "원전/SMR", character: "유일 NRC 승인 SMR", buyPrice: "-", curPrice: "$28", pnl: "-", status: "미보유", action: "고객계약 확인 후 소량 1회" },
  { grade: "C", name: "D-Wave Quantum", ticker: "QBTS", sector: "양자컴퓨팅", character: "어닐링 상용화", buyPrice: "-", curPrice: "$8", pnl: "-", status: "미보유", action: "매출 성장 확인 후 소량 1회" },
];

// ── 문서 생성 ──────────────────────────────────────────
function gradeColor(g) {
  if (g === "A") return C.gradeA;
  if (g === "B") return C.gradeB;
  return C.gradeC;
}

function makeStockTable(stocks, isKorea) {
  const codeLabel = isKorea ? "코드" : "티커";
  const colWidths = [600, 1200, 800, 900, 1900, 1000, 1000, 1960];
  const totalWidth = colWidths.reduce((a, b) => a + b, 0);

  const headerRow = new TableRow({
    children: [
      headerCell("등급", colWidths[0]),
      headerCell("종목명", colWidths[1]),
      headerCell(codeLabel, colWidths[2]),
      headerCell("섹터", colWidths[3]),
      headerCell("성격", colWidths[4]),
      headerCell("수익률", colWidths[5]),
      headerCell("상태", colWidths[6]),
      headerCell("액션플랜", colWidths[7]),
    ],
  });

  const dataRows = stocks.map((s) => {
    const gc = gradeColor(s.grade);
    return new TableRow({
      children: [
        dataCell(s.grade, colWidths[0], { bold: true, color: gc, align: AlignmentType.CENTER }),
        dataCell(s.name, colWidths[1], { bold: true }),
        dataCell(isKorea ? s.code : s.ticker, colWidths[2], { align: AlignmentType.CENTER, size: 16 }),
        dataCell(s.sector, colWidths[3], { size: 16 }),
        dataCell(s.character, colWidths[4], { size: 16 }),
        dataCell(s.pnl, colWidths[5], {
          align: AlignmentType.CENTER,
          bold: true,
          color: s.pnl.startsWith("+") ? C.green : C.gray,
        }),
        dataCell(s.status, colWidths[6], {
          align: AlignmentType.CENTER,
          bold: true,
          color: s.status === "보유" ? C.green : C.gray,
        }),
        dataCell(s.action, colWidths[7], { size: 16 }),
      ],
    });
  });

  return new Table({
    width: { size: totalWidth, type: WidthType.DXA },
    columnWidths: colWidths,
    rows: [headerRow, ...dataRows],
  });
}

// ── 본문 구성 ──────────────────────────────────────────
const children = [];

function addHeading(text, level = HeadingLevel.HEADING_1) {
  children.push(new Paragraph({ heading: level, children: [new TextRun({ text, bold: true, font: "Arial" })] }));
}

function addText(text, opts = {}) {
  children.push(new Paragraph({
    spacing: { after: opts.after || 120 },
    children: [new TextRun({
      text,
      font: "Arial",
      size: opts.size || 22,
      bold: !!opts.bold,
      color: opts.color || C.dark,
    })],
  }));
}

function addBullet(text, ref = "bullets") {
  children.push(new Paragraph({
    numbering: { reference: ref, level: 0 },
    children: [new TextRun({ text, font: "Arial", size: 20 })],
  }));
}

function addSpacer() {
  children.push(new Paragraph({ spacing: { after: 200 }, children: [] }));
}

// ── 표지 ───────────────────────────────────────────────
addSpacer();
addSpacer();
addSpacer();
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 200 },
  children: [new TextRun({ text: "K-Quant v12.0", font: "Arial", size: 56, bold: true, color: C.blue })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 300 },
  children: [new TextRun({ text: "텐배거 매매전략 설명서", font: "Arial", size: 44, bold: true, color: C.dark })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 100 },
  children: [new TextRun({ text: "20종목 | 8섹터 | 7팩터 스코어링 | A/B/C 등급 체계", font: "Arial", size: 24, color: C.gray })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 400 },
  children: [new TextRun({ text: "2026년 3월 11일 기준", font: "Arial", size: 22, color: C.gray })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  children: [new TextRun({ text: "4개 AI (Claude + ChatGPT + Gemini + DeepSeek) 합의 기반", font: "Arial", size: 20, color: C.blue })],
}));

children.push(new Paragraph({ children: [new PageBreak()] }));

// ── 1장: 투자 철학 ────────────────────────────────────
addHeading("1. 투자 철학 & 판단 기준");
addText("텐배거(10배 수익) 투자의 핵심은 좋은 스토리가 아니라, 실적과 비대칭입니다.", { bold: true, size: 24 });
addSpacer();
addText("4대 투자 판단 기준:", { bold: true });
addBullet("10배 경로가 실적으로 이어질 수 있는가?");
addBullet("앞으로 12~18개월 안에 확인할 숫자가 있는가?");
addBullet("가설이 틀렸을 때 빨리 인정할 수 있는가? (Kill Condition)");
addBullet("지금 가격대에서 아직 비대칭이 남아 있는가?");
addSpacer();
addText("한 줄 결론:", { bold: true, color: C.accent });
addBullet("한국 총알 하나: 우진 - 가장 작은 독점 + 가장 반복적인 매출");
addBullet("미국 총알 하나: LEU - HALEU 병목이 아직 안 풀림");

children.push(new Paragraph({ children: [new PageBreak()] }));

// ── 2장: 등급 체계 ────────────────────────────────────
addHeading("2. 등급 체계 (A/B/C)");
addSpacer();

const gradeTable = new Table({
  width: { size: 9360, type: WidthType.DXA },
  columnWidths: [1000, 2000, 1500, 4860],
  rows: [
    new TableRow({
      children: [
        headerCell("등급", 1000),
        headerCell("분류", 2000),
        headerCell("포지션", 1500),
        headerCell("설명", 4860),
      ],
    }),
    new TableRow({
      children: [
        dataCell("A", 1000, { bold: true, color: C.gradeA, align: AlignmentType.CENTER, size: 24 }),
        dataCell("코어 텐배거", 2000, { bold: true }),
        dataCell("8~10%", 1500, { align: AlignmentType.CENTER }),
        dataCell("10배 경로가 살아있고, 12~18개월 확인지표가 분명한 종목", 4860),
      ],
    }),
    new TableRow({
      children: [
        dataCell("B", 1000, { bold: true, color: C.gradeB, align: AlignmentType.CENTER, size: 24 }),
        dataCell("구조적 성장", 2000, { bold: true }),
        dataCell("4~6%", 1500, { align: AlignmentType.CENTER }),
        dataCell("좋은 사업이지만 10배보다 3~5배 확률이 더 높은 종목", 4860),
      ],
    }),
    new TableRow({
      children: [
        dataCell("C", 1000, { bold: true, color: C.gradeC, align: AlignmentType.CENTER, size: 24 }),
        dataCell("옵션 베팅", 2000, { bold: true }),
        dataCell("1~3%", 1500, { align: AlignmentType.CENTER }),
        dataCell("맞으면 크지만 틀리면 오래 고생. 이벤트/옵션 베팅", 4860),
      ],
    }),
  ],
});
children.push(gradeTable);

addSpacer();
addHeading("포트폴리오 구조", HeadingLevel.HEADING_2);
addBullet("코어 텐배거 (A등급): 45%");
addBullet("구조적 성장 (B등급): 35%");
addBullet("옵션 베팅 (C등급): 20%");
addBullet("지역 배분: 미국 60% / 한국 40%");

children.push(new Paragraph({ children: [new PageBreak()] }));

// ── 3장: 매수 전략 ────────────────────────────────────
addHeading("3. 매수 전략 (분할 매수 규칙)");
addSpacer();

addHeading("A등급: 3분할 피라미딩", HeadingLevel.HEADING_2);
addBullet("1차 매수 (40%): 즉시 시장가 진입");
addBullet("2차 매수 (35%): 현재가 대비 -5% 조정 시");
addBullet("3차 매수 (25%): 현재가 대비 -10% 조정 시");
addText("확신 있으면 +30% 이후 추가 피라미딩 가능", { size: 20, color: C.blue });

addSpacer();
addHeading("B등급: 2분할 균등", HeadingLevel.HEADING_2);
addBullet("1차 매수 (50%): 즉시 진입");
addBullet("2차 매수 (50%): -5% 조정 대기");

addSpacer();
addHeading("C등급: 1회 소량 진입", HeadingLevel.HEADING_2);
addBullet("전체 투자금의 1~3%만 1회 진입");
addBullet("맞으면 크지만 틀리면 빨리 인정");

addSpacer();
addText("매수하면 안 되는 날:", { bold: true, color: C.red });
addBullet("전일 +10% 이상 급등한 날 (추격매수 금지)");
addBullet("선물/옵션 만기일 (매월 둘째 목요일)");
addBullet("FOMC/CPI 발표 당일");
addBullet("Kill Condition 해당 시 (절대 매수 금지)");
addBullet("코스닥 -3% 이상 급락장 (패닉이 끝난 뒤 진입)");

children.push(new Paragraph({ children: [new PageBreak()] }));

// ── 4장: 매도 전략 ────────────────────────────────────
addHeading("4. 매도 전략 (손절/익절 규칙)");
addSpacer();

const sellTable = new Table({
  width: { size: 9360, type: WidthType.DXA },
  columnWidths: [2000, 1500, 2000, 3860],
  rows: [
    new TableRow({
      children: [
        headerCell("구분", 2000),
        headerCell("수익률", 1500),
        headerCell("비중", 2000),
        headerCell("행동", 3860),
      ],
    }),
    new TableRow({
      children: [
        dataCell("손절", 2000, { bold: true, color: C.red }),
        dataCell("-25%", 1500, { align: AlignmentType.CENTER, color: C.red, bold: true }),
        dataCell("전량 검토", 2000, { align: AlignmentType.CENTER }),
        dataCell("Kill Condition 확인 후 판단. 가설 붕괴 시 즉시 전량 매도", 3860),
      ],
    }),
    new TableRow({
      children: [
        dataCell("1차 익절 (2배)", 2000, { bold: true, color: C.gold }),
        dataCell("+100%", 1500, { align: AlignmentType.CENTER, color: C.gold, bold: true }),
        dataCell("50% 매도", 2000, { align: AlignmentType.CENTER }),
        dataCell("원금 회수. 나머지 50%는 무료 주식으로 장기 홀드", 3860),
      ],
    }),
    new TableRow({
      children: [
        dataCell("2차 익절 (6배)", 2000, { bold: true, color: C.green }),
        dataCell("+500%", 1500, { align: AlignmentType.CENTER, color: C.green, bold: true }),
        dataCell("30% 추가 매도", 2000, { align: AlignmentType.CENTER }),
        dataCell("잔여 20%만 텐배거 목표로 최종 홀드", 3860),
      ],
    }),
    new TableRow({
      children: [
        dataCell("텐배거 (10배)", 2000, { bold: true, color: C.blue }),
        dataCell("+900%", 1500, { align: AlignmentType.CENTER, color: C.blue, bold: true }),
        dataCell("최종 판단", 2000, { align: AlignmentType.CENTER }),
        dataCell("축하! 전량 매도 or 10% 잔류 후 러닝", 3860),
      ],
    }),
  ],
});
children.push(sellTable);

children.push(new Paragraph({ children: [new PageBreak()] }));

// ── 5장: 한국 포트폴리오 ──────────────────────────────
addHeading("5. 한국 텐배거 포트폴리오 (10종목)");
addSpacer();
children.push(makeStockTable(koreaStocks, true));

children.push(new Paragraph({ children: [new PageBreak()] }));

// ── 6장: 미국 포트폴리오 ──────────────────────────────
addHeading("6. 미국 텐배거 포트폴리오 (10종목)");
addSpacer();
children.push(makeStockTable(usStocks, false));

children.push(new Paragraph({ children: [new PageBreak()] }));

// ── 7장: 8대 섹터 분석 ───────────────────────────────
addHeading("7. 8대 핵심 섹터");
addSpacer();

const sectors = [
  { emoji: "원전/SMR", score: 95, stocks: "우진, BHI, 일진파워, 오르비텍 | LEU, DNN, NXE, SMR", thesis: "원전 르네상스 확실. HALEU 병목 미해소. 한국형 원전 수출 본격화." },
  { emoji: "양자보안/컴퓨팅", score: 80, stocks: "ICTK | IONQ, QBTS", thesis: "PQC 마이그레이션 시작. ICTK는 한국 양자에서 가장 현실적 상용화." },
  { emoji: "AI인프라/광학", score: 90, stocks: "CRDO", thesis: "800G/1.6T 전환기. 하이퍼스케일러 CAPEX 지속이 핵심." },
  { emoji: "디지털자산", score: 70, stocks: "BTGO, CRCL", thesis: "스테이블코인 법안 + 커스터디 제도화. 인프라장의 삽 파는 회사." },
  { emoji: "우주/방산/6G", score: 85, stocks: "인텔리안, 비츠로셀 | MDA", thesis: "LEO 위성 수요 폭발. 방산 예산 확대." },
  { emoji: "로봇/피지컬AI", score: 85, stocks: "에스피지, 씨메스", thesis: "감속기 국산화 + 피지컬AI 태동기. 실적 확인 필요." },
  { emoji: "클린에너지", score: 75, stocks: "씨에스윈드", thesis: "IRA 수혜 + 글로벌 풍력 1위. 정책/금리에 민감." },
];

sectors.forEach((s) => {
  addText(`${s.emoji} (순풍점수 ${s.score}/100)`, { bold: true, size: 22, color: C.blue });
  addText(`종목: ${s.stocks}`, { size: 20 });
  addText(`논리: ${s.thesis}`, { size: 20, color: C.gray });
  addSpacer();
});

children.push(new Paragraph({ children: [new PageBreak()] }));

// ── 8장: Kill Conditions ─────────────────────────────
addHeading("8. Kill Conditions (가설 붕괴 조건)");
addText("아래 조건 중 하나라도 해당되면 즉시 전량 매도를 검토합니다.", { color: C.red, bold: true });
addSpacer();

const killConditions = [
  { name: "우진", conditions: ["ICI/계측기 반복 수주 2~3분기 연속 약화", "원전 이용률 상승에도 교체수요 실적 미반영"] },
  { name: "BHI", conditions: ["연간 신규 수주 1조 미달", "LNG/HRSG 수주 급감"] },
  { name: "인텔리안", conditions: ["Gateway/ESA 매출 분기 감소 전환", "LEO 안테나 시장 경쟁 급격화"] },
  { name: "LEU", conditions: ["DOE 과업 체결 지연/취소", "Orano/Urenco HALEU 조기 양산으로 독점 상실"] },
  { name: "DNN", conditions: ["건설 일정 1년 이상 지연", "자본조달 실패"] },
  { name: "BTGO", conditions: ["순이익 적자전환", "기관 고객 이탈"] },
  { name: "ICTK", conditions: ["상용 계약 12개월 내 미확보", "경쟁 SW 솔루션에 밀림"] },
  { name: "씨메스", conditions: ["적자 확대 3분기 이상 지속", "추가 자금조달 필요"] },
];

killConditions.forEach((k) => {
  addText(k.name, { bold: true, size: 22 });
  k.conditions.forEach((c) => addBullet(c));
  addSpacer();
});

children.push(new Paragraph({ children: [new PageBreak()] }));

// ── 9장: 매일 코칭 시스템 ─────────────────────────────
addHeading("9. K-Quant 자동 코칭 시스템");
addSpacer();
addText("봇이 매일 자동으로 아래 항목을 관리합니다:", { bold: true });
addSpacer();

addHeading("매일 (월~금)", HeadingLevel.HEADING_2);
addBullet("16:30 가격 모니터링: 전일 대비 5% 이상 변동 시 알림");
addBullet("보유종목 코칭: 구체적 가격/수량으로 매수/매도 가이드");
addBullet("매수 금지 판단: 급등 직후, 만기일, FOMC 등 자동 경고");

addSpacer();
addHeading("매주 (일요일)", HeadingLevel.HEADING_2);
addBullet("7팩터 전체 리스코어링: 점수 10점 이상 변동 시 알림");
addBullet("등급 변동 체크: A에서 B로 하향 시 포지션 조정 안내");

addSpacer();
addHeading("매월 (1일)", HeadingLevel.HEADING_2);
addBullet("섹터 리뷰: 신규 후보 발굴, 졸업/제거 검토");
addBullet("포트폴리오 리밸런싱: 등급별 비중 점검");

addSpacer();
addText("코칭 예시:", { bold: true, color: C.blue });
addText("\"에스피지 39,200원 도달. B등급 2분할 전략: 1차 20주 x 39,200원 = 784,000원 즉시 매수. 2차 대기: 37,240원(-5%)에 20주. 손절선: 29,400원(-25%).\"", { size: 20, color: C.navy });

addSpacer();
addText("매수 말리기 예시:", { bold: true, color: C.red });
addText("\"오늘 우진 +15% 급등. 추격매수 금지! 내일 갭다운 확인 후 판단하세요. 현재 보유분 홀드.\"", { size: 20, color: C.red });

// ── Document 생성 ──────────────────────────────────────
const doc = new Document({
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 36, bold: true, font: "Arial", color: C.navy },
        paragraph: { spacing: { before: 360, after: 200 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 28, bold: true, font: "Arial", color: C.blue },
        paragraph: { spacing: { before: 240, after: 120 }, outlineLevel: 1 } },
    ],
  },
  numbering: {
    config: [
      {
        reference: "bullets",
        levels: [{
          level: 0, format: LevelFormat.BULLET, text: "\u2022",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } },
        }],
      },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 12240, height: 15840 },
        margin: { top: 1200, right: 1200, bottom: 1200, left: 1200 },
      },
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          alignment: AlignmentType.RIGHT,
          children: [new TextRun({ text: "K-Quant v12.0 | 텐배거 매매전략", font: "Arial", size: 16, color: C.gray })],
        })],
      }),
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [
            new TextRun({ text: "Page ", font: "Arial", size: 16, color: C.gray }),
            new TextRun({ children: [PageNumber.CURRENT], font: "Arial", size: 16, color: C.gray }),
          ],
        })],
      }),
    },
    children,
  }],
});

const outPath = "/Users/botddol/Downloads/텐배거_매매전략_2026.03.docx";
Packer.toBuffer(doc).then((buffer) => {
  fs.writeFileSync(outPath, buffer);
  console.log("Created:", outPath);
  console.log("Size:", (buffer.length / 1024).toFixed(1), "KB");
});
