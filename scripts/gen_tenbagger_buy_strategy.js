/**
 * K-Quant 텐배거 매수 코칭 전략서 생성기
 * 2026.03.11
 *
 * "내일부터 살 주식" 매매전략 DOCX — 종목별 진입 가격/수량/분할/손절/목표
 */
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, PageNumber, PageBreak, LevelFormat,
} = require("docx");

// ── 색상 ────────────────────────────────────────
const C = {
  navy: "16213E", blue: "0F3460", accent: "E94560", gold: "F5A623",
  green: "27AE60", red: "E74C3C", gray: "95A5A6", lightGray: "ECF0F1",
  white: "FFFFFF", gradeA: "27AE60", gradeB: "F39C12", gradeC: "E67E22",
};

// ── 테이블 헬퍼 ──────────────────────────────────
const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const borders = { top: border, bottom: border, left: border, right: border };
const margins = { top: 60, bottom: 60, left: 100, right: 100 };

function hCell(text, w) {
  return new TableCell({
    borders, width: { size: w, type: WidthType.DXA },
    shading: { fill: C.navy, type: ShadingType.CLEAR }, margins,
    children: [new Paragraph({ alignment: AlignmentType.CENTER,
      children: [new TextRun({ text, bold: true, color: C.white, font: "Arial", size: 18 })] })],
  });
}
function dCell(text, w, opts = {}) {
  return new TableCell({
    borders, width: { size: w, type: WidthType.DXA }, margins,
    shading: opts.fill ? { fill: opts.fill, type: ShadingType.CLEAR } : undefined,
    children: [new Paragraph({ alignment: opts.center ? AlignmentType.CENTER : AlignmentType.LEFT,
      children: [new TextRun({ text: String(text), font: "Arial", size: 18,
        bold: opts.bold, color: opts.color })] })],
  });
}

function heading(text, level = HeadingLevel.HEADING_1) {
  return new Paragraph({ heading: level, spacing: { before: 300, after: 200 },
    children: [new TextRun({ text, font: "Arial", bold: true })] });
}
function para(text, opts = {}) {
  return new Paragraph({ spacing: { after: 120 },
    children: [new TextRun({ text, font: "Arial", size: 22, ...opts })] });
}
function bullet(text, ref = "bullets") {
  return new Paragraph({ numbering: { reference: ref, level: 0 },
    children: [new TextRun({ text, font: "Arial", size: 22 })] });
}

// ── 한국 10종목 유니버스 ─────────────────────────
const KR = [
  { code: "105840", name: "우진", sector: "원전/SMR", grade: "A", rank: 1,
    character: "한국형 원전 4대 핵심계측기 독점, 반복매출형",
    catalysts: ["한수원 88억 수주(2026.02)", "ICI 102.9억 수주(2025.11)", "체코/폴란드 수출 I&C"],
    kills: ["ICI 반복수주 2~3분기 연속 약화", "원전 이용률 상승에도 실적 미반영", "i-SMR 기대만 있고 본업 정체"],
    split: "3분할(40/35/25%)", stop: "-25%", tp: "2배/6배/10배" },
  { code: "083650", name: "비에이치아이", sector: "원전/SMR", grade: "A", rank: 2,
    character: "실적이 숫자로 증명된 원전+LNG 하이브리드",
    catalysts: ["25년 매출 7,716억/영업이익 733억(사상최대)", "25년 신규수주 1.8조", "체코 원전 보조기기"],
    kills: ["연간 신규수주 1조 미달", "LNG/HRSG 수주 급감", "원전 프리미엄 미발생"],
    split: "3분할(40/35/25%)", stop: "-25%", tp: "2배/6배/10배" },
  { code: "189300", name: "인텔리안테크", sector: "우주/방산", grade: "A", rank: 3,
    character: "LEO 매출이 실제 숫자로 보이기 시작",
    catalysts: ["25년 매출 3,196억/영업이익 120억(흑자전환)", "4Q 매출 1,243억(역대최고)", "게이트웨이+LEO 수요"],
    kills: ["게이트웨이/ESA 매출 분기 감소", "특정 고객/프로그램 지연", "LEO 경쟁 급격화"],
    split: "3분할(40/35/25%)", stop: "-25%", tp: "2배/6배/10배" },
  { code: "094820", name: "일진파워", sector: "원전/SMR", grade: "A", rank: 4,
    character: "SMR이 늦어져도 돈 버는 운영/정비 레이어",
    catalysts: ["한빛 3-4호기 정비 199.8억", "25년 매출 2,520억(+30.5%)/영업이익 253억(+146.5%)"],
    kills: ["정비 수주 2분기 연속 감소", "발전소 이용률 하락"],
    split: "3분할(40/35/25%)", stop: "-25%", tp: "2배/6배/10배" },
  { code: "082920", name: "비츠로셀", sector: "우주/방산", grade: "A", rank: 5,
    character: "고마진 특수배터리 현금창출주",
    catalysts: ["25년 매출 2,431억/영업이익 697억(역대최대)", "앰플/열전지/고온전지 매출 증가"],
    kills: ["방산 예산 축소/수출 감소", "영업이익률 25% 하회", "경쟁사 진입"],
    split: "3분할(40/35/25%)", stop: "-25%", tp: "2배/6배/10배" },
  { code: "046120", name: "오르비텍", sector: "원전/SMR", grade: "B", rank: 6,
    character: "해체/폐기물 특수상황 — 좋지만 적자/희석 리스크",
    catalysts: ["월성 1-2호기 방사선관리용역 253억", "KRID 설치 완료", "고리1호 해체(1조+)"],
    kills: ["적자 2분기 이상 확대", "추가 유상증자", "해체 일정 2년+ 지연"],
    split: "2분할(50/50%)", stop: "-25%", tp: "2배/6배/10배" },
  { code: "058610", name: "에스피지", sector: "로봇", grade: "B", rank: 7,
    character: "좋은 부품주지만 아직 실적 폭발 미확인",
    catalysts: ["25년 영업이익 179억(+45.4%)", "26년 전망 매출 4,186억/영업이익 271억", "감속기 국산화"],
    kills: ["감속기 매출비중 정체", "로봇 시장 성장 둔화", "일본 경쟁사 가격 압박"],
    split: "2분할(50/50%)", stop: "-25%", tp: "2배/6배/10배" },
  { code: "456010", name: "ICTK", sector: "양자보안", grade: "B", rank: 8,
    character: "한국 양자에서 가장 현실적인 상용화 축",
    catalysts: ["MWC 2026 PQC+PUF 양자보안 칩 공개", "통신사 PQC KMS 솔루션 개발 완료"],
    kills: ["상용 계약 12개월 내 미확보", "PQC 시장 지연", "SW 솔루션에 밀림"],
    split: "2분할(50/50%)", stop: "-25%", tp: "2배/6배/10배" },
  { code: "475400", name: "씨메스", sector: "로봇/피지컬AI", grade: "C", rank: 9,
    character: "100배 꿈 있으나 아직 적자 확대",
    catalysts: ["25년 매출 130.6억(+89.5%)", "AW 2026 랜덤 팔레타이징", "표준화 물류자동화 제품"],
    kills: ["적자 확대 3분기+ 지속", "표준 제품 반복수주 미확보", "자금 소진"],
    split: "1회 소량", stop: "-25%", tp: "2배/6배/10배" },
  { code: "112610", name: "씨에스윈드", sector: "클린에너지", grade: "C", rank: 10,
    character: "정책/금리 옵션 — 싸 보이지만 너무 민감",
    catalysts: ["글로벌 풍력타워 1위", "IRA 수혜", "Bladt 인수"],
    kills: ["IRA 축소/폐지", "금리 하락 지연", "풍력 발주 2년 연속 감소"],
    split: "1회 소량", stop: "-25%", tp: "2배/6배/10배" },
];

// ── 미국 10종목 유니버스 ─────────────────────────
const US = [
  { ticker: "LEU", name: "Centrus Energy", sector: "원전/SMR", grade: "A", rank: 1,
    character: "미국 원전 가장 직접적인 병목 수혜 (HALEU)",
    catalysts: ["25년 매출 $4.487억/순이익 $7,780만", "미국 최초 HALEU 1톤+ 생산", "DOE 10년 $27억 농축 지원"],
    kills: ["DOE 과업 지연/취소", "Orano/Urenco HALEU 조기 양산", "HALEU 독점 상실"],
    split: "3분할(40/35/25%)", stop: "-25%" },
  { ticker: "DNN", name: "Denison Mines", sector: "원전/SMR", grade: "A", rank: 2,
    character: "이야기에서 건설 단계로 넘어감 (ISR 저비용)",
    catalysts: ["2026.02 Phoenix ISR FID 확정", "2026.03 건설 착공", "우라늄 가격 상승"],
    kills: ["건설 일정 1년+ 지연", "자본조달 실패", "ISR 기술적 문제"],
    split: "3분할(40/35/25%)", stop: "-25%" },
  { ticker: "NXE", name: "NexGen Energy", sector: "원전/SMR", grade: "A", rank: 3,
    character: "승인/현금/대형 자원 삼박자",
    catalysts: ["2026.03 CNSC 최종 건설 승인", "역대급 고품위 Rook I", "2028 생산 목표"],
    kills: ["건설 원가 2배+ 초과", "환경 반대 지연", "우라늄 $60 이하 장기 체류"],
    split: "3분할(40/35/25%)", stop: "-25%" },
  { ticker: "BTGO", name: "BitGo", sector: "블록체인", grade: "A", rank: 4,
    character: "디지털자산의 새 증권예탁결제원",
    catalysts: ["2026.01 IPO $2.128억 조달", "25년 9개월 순이익 $3,530만", "OCC 신탁은행 확장"],
    kills: ["순이익 적자전환", "크립토 규제 역행", "기관 고객 이탈"],
    split: "3분할(40/35/25%)", stop: "-25%" },
  { ticker: "CRDO", name: "Credo Technology", sector: "AI인프라", grade: "B", rank: 5,
    character: "AI 연결 인프라 — 품질주 전환 중",
    catalysts: ["FY26 3Q 매출 $4.07억(+201.5%)", "비GAAP 총마진 68.6%", "800G/1.6T 전환"],
    kills: ["하이퍼스케일러 CAPEX 감소", "고객 집중도 리스크", "AI 인프라 피크아웃"],
    split: "2분할(50/50%)", stop: "-25%" },
  { ticker: "CRCL", name: "Circle", sector: "스테이블코인", grade: "B", rank: 6,
    character: "구조적 성장주지만 금리 민감",
    catalysts: ["USDC 유통량 $753억", "4Q 온체인 거래량 $11.9조", "스테이블코인 법안"],
    kills: ["금리 급락 리저브 수익 감소", "USDC 점유율 하락", "규제 역풍"],
    split: "2분할(50/50%)", stop: "-25%" },
  { ticker: "IONQ", name: "IonQ", sector: "양자컴퓨팅", grade: "B", rank: 7,
    character: "여전히 선두지만 이미 너무 커졌다",
    catalysts: ["25년 매출 $1.30억(+202%)", "현금+투자자산 $33억", "미 국방부 계약"],
    kills: ["상용 매출 비중 정체", "IBM/Google 기술 격차 축소", "시총 대비 매출 성장 둔화"],
    split: "2분할(50/50%)", stop: "-25%" },
  { ticker: "MDA", name: "MDA Space", sector: "우주/방산", grade: "B", rank: 8,
    character: "실적형 우주주 — 3~5배형",
    catalysts: ["25년 매출 $16.33억", "백로그 $40억", "SDA Tranche 2"],
    kills: ["백로그 감소 전환", "우주 예산 축소", "마진 압박"],
    split: "2분할(50/50%)", stop: "-25%" },
  { ticker: "SMR", name: "NuScale Power", sector: "원전/SMR", grade: "C", rank: 9,
    character: "살아남을 현금은 있지만 아직 옵션주",
    catalysts: ["현금 ~$13억", "유일한 NRC 승인 SMR 설계"],
    kills: ["고객 계약 추가 취소", "현금 소진 가속화", "경쟁 SMR NRC 승인"],
    split: "1회 소량", stop: "-25%" },
  { ticker: "QBTS", name: "D-Wave Quantum", sector: "양자컴퓨팅", grade: "C", rank: 10,
    character: "QUBT보다 나은 고위험 양자 옵션",
    catalysts: ["25년 매출 $2,460만(+179%)", "양자 어닐링 상용화"],
    kills: ["매출 성장 둔화", "현금 소진", "양자 겨울 장기화"],
    split: "1회 소량", stop: "-25%" },
];

// ── 종목별 매수 계획 테이블 생성 ──────────────────
function makeStockTable(stocks, market) {
  const currency = market === "KR" ? "원" : "USD";
  const colWidths = [700, 1500, 700, 2900, 1800, 1760];
  const totalW = colWidths.reduce((a,b) => a+b, 0);

  const headerRow = new TableRow({
    children: [
      hCell("#", colWidths[0]),
      hCell("종목", colWidths[1]),
      hCell("등급", colWidths[2]),
      hCell("성격/전략", colWidths[3]),
      hCell("분할전략", colWidths[4]),
      hCell("손절/목표", colWidths[5]),
    ],
  });

  const rows = [headerRow];
  for (const s of stocks) {
    const gradeColor = s.grade === "A" ? C.gradeA : s.grade === "B" ? C.gradeB : C.gradeC;
    const code = market === "KR" ? s.code : s.ticker;
    rows.push(new TableRow({
      children: [
        dCell(String(s.rank), colWidths[0], { center: true }),
        dCell(`${s.name}\n(${code})`, colWidths[1], { bold: true }),
        dCell(s.grade, colWidths[2], { center: true, bold: true, color: gradeColor }),
        dCell(s.character, colWidths[3]),
        dCell(s.split, colWidths[4], { center: true }),
        dCell(`손절${s.stop}\n${s.tp || "2x/6x/10x"}`, colWidths[5], { center: true }),
      ],
    }));
  }

  return new Table({
    width: { size: totalW, type: WidthType.DXA },
    columnWidths: colWidths,
    rows,
  });
}

// ── 종목별 상세 카드 생성 ──────────────────────────
function makeStockCards(stocks, market) {
  const elements = [];
  for (const s of stocks) {
    const code = market === "KR" ? s.code : s.ticker;
    const gradeLabel = s.grade === "A" ? "코어 텐배거" : s.grade === "B" ? "구조적 성장" : "옵션 베팅";

    elements.push(
      heading(`${s.rank}. ${s.name} (${code}) — ${s.grade}등급 ${gradeLabel}`, HeadingLevel.HEADING_3),
      para(s.character, { italics: true, color: C.blue }),
    );

    elements.push(para("카탈리스트:", { bold: true }));
    for (const c of s.catalysts) elements.push(bullet(c));

    elements.push(para("매수 전략:", { bold: true, color: C.green }));
    elements.push(para(`분할: ${s.split} | 손절: ${s.stop}`));

    if (s.grade === "A") {
      elements.push(bullet("1차: 현재가 시장가/지정가로 40% 진입"));
      elements.push(bullet("2차: -5% 조정 시 35% 추가 매수"));
      elements.push(bullet("3차: -10% 조정 시 25% 최종 진입"));
    } else if (s.grade === "B") {
      elements.push(bullet("1차: 현재가 근처 50% 진입"));
      elements.push(bullet("2차: -5~7% 조정 시 50% 추가 매수"));
    } else {
      elements.push(bullet("1회 소량 진입 (포트폴리오 1~3%)"));
    }

    elements.push(para("가설 붕괴(Kill) 조건:", { bold: true, color: C.red }));
    for (const k of s.kills) elements.push(bullet(k));

    elements.push(new Paragraph({ spacing: { after: 200 }, children: [] }));
  }
  return elements;
}

// ── 매수 금지일 섹션 ──────────────────────────────
function makeNoBuySection() {
  return [
    heading("5. 매수하면 안 되는 날", HeadingLevel.HEADING_1),
    para("텐배거 종목이라도 아래 조건에서는 절대 매수하지 않는다.", { bold: true }),
    new Paragraph({ spacing: { after: 100 }, children: [] }),

    para("시장 환경 금지:", { bold: true, color: C.red }),
    bullet("선물옵션 만기일 (매월 두번째 목요일) — 변동성 극대"),
    bullet("FOMC 결과 발표일/당일 — 방향성 불확실"),
    bullet("CPI 발표일 (미국 물가) — 급변동 가능"),
    bullet("VIX 30 이상 — 공포 과열, 침착하게 대기"),
    bullet("Fear & Greed Index 20 미만 — 극단 공포 (역발상 기회지만 급하면 안됨)"),
    new Paragraph({ spacing: { after: 100 }, children: [] }),

    para("종목 개별 금지:", { bold: true, color: C.red }),
    bullet("전일 대비 +10% 이상 급등 — 추격매수 절대 금지"),
    bullet("전일 대비 +5% 이상 — 시초가 눌림 확인 후에만 진입"),
    bullet("전일 대비 -8% 이상 급락 — 패닉셀 여부 확인, 하루 관망"),
    bullet("Kill 조건 발생 — 매수 즉시 중단, 기보유분도 검토"),
    bullet("거래량 급증 + 음봉 — 세력 탈출 가능성, 매수 금지"),
    new Paragraph({ spacing: { after: 100 }, children: [] }),

    para("시간대 금지:", { bold: true, color: C.red }),
    bullet("장 시작 직후 5분 (09:00~09:05) — 허매물 주의"),
    bullet("장 마감 직전 10분 (15:20~15:30) — 다음날 갭 리스크"),
    bullet("금요일 오후 — 주말 이벤트 리스크"),
  ];
}

// ── 메인 문서 생성 ────────────────────────────────
async function main() {
  const doc = new Document({
    styles: {
      default: { document: { run: { font: "Arial", size: 22 } } },
      paragraphStyles: [
        { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { size: 32, bold: true, font: "Arial", color: C.navy },
          paragraph: { spacing: { before: 360, after: 240 }, outlineLevel: 0 } },
        { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { size: 28, bold: true, font: "Arial", color: C.blue },
          paragraph: { spacing: { before: 240, after: 180 }, outlineLevel: 1 } },
        { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { size: 24, bold: true, font: "Arial", color: C.navy },
          paragraph: { spacing: { before: 200, after: 120 }, outlineLevel: 2 } },
      ],
    },
    numbering: {
      config: [{
        reference: "bullets",
        levels: [{ level: 0, format: LevelFormat.BULLET, text: "\u2022",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } }],
      }],
    },
    sections: [{
      properties: {
        page: {
          size: { width: 12240, height: 15840 },
          margin: { top: 1200, bottom: 1200, left: 1200, right: 1200 },
        },
      },
      headers: {
        default: new Header({
          children: [new Paragraph({
            alignment: AlignmentType.RIGHT,
            children: [new TextRun({ text: "K-Quant v12.0 | Tenbagger Buy Strategy", font: "Arial", size: 16, color: C.gray })],
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
      children: [
        // ── 표지 ──
        new Paragraph({ spacing: { before: 2000 }, children: [] }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 },
          children: [new TextRun({ text: "K-Quant v12.0", font: "Arial", size: 56, bold: true, color: C.navy })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 400 },
          children: [new TextRun({ text: "텐배거 매수 코칭 전략서", font: "Arial", size: 44, bold: true, color: C.accent })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 },
          children: [new TextRun({ text: "내일부터 살 주식 — 8섹터 20종목 매수 전략", font: "Arial", size: 24, color: C.gray })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 100 },
          children: [new TextRun({ text: "2026년 3월 11일", font: "Arial", size: 22, color: C.gray })] }),
        new Paragraph({ alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "4개 AI(Claude + ChatGPT + Gemini + DeepSeek) 합의 기반", font: "Arial", size: 20, color: C.blue })] }),

        new Paragraph({ children: [new PageBreak()] }),

        // ── 1. 투자 철학 ──
        heading("1. 텐배거 투자 핵심 원칙", HeadingLevel.HEADING_1),
        para("Peter Lynch의 텐배거 투자법을 8개 성장 섹터에 적용한다."),
        para("4가지 투자 판단 기준:", { bold: true }),
        bullet("10배 경로가 실적으로 이어질 수 있는가"),
        bullet("앞으로 12~18개월 안에 확인할 숫자가 있는가"),
        bullet("가설이 틀렸을 때 빨리 인정할 수 있는가"),
        bullet("지금 가격대에서 아직 비대칭이 남아 있는가"),
        new Paragraph({ spacing: { after: 200 }, children: [] }),

        // ── 2. 등급 체계 & 포트폴리오 구조 ──
        heading("2. 등급 체계 & 포트폴리오 구조", HeadingLevel.HEADING_1),
        para("A등급 (코어 텐배거) — 포트폴리오 45%, 종목당 8~10%", { bold: true, color: C.gradeA }),
        para("10배 경로 + 12~18개월 확인지표 분명. 3분할 피라미딩 매수."),
        para("B등급 (구조적 성장) — 포트폴리오 35%, 종목당 4~6%", { bold: true, color: C.gradeB }),
        para("좋은 사업이지만 3~5배 확률이 더 높음. 2분할 균등 매수."),
        para("C등급 (옵션 베팅) — 포트폴리오 20%, 종목당 1~3%", { bold: true, color: C.gradeC }),
        para("맞으면 크지만 틀리면 오래 고생. 1회 소량 진입."),
        new Paragraph({ spacing: { after: 100 }, children: [] }),
        para("지역 배분: 미국 60% / 한국 40%", { bold: true }),
        para("매도 기준: 손절 -25% | 1차익절 2배(50%매도) | 2차익절 6배(30%매도) | 10배 텐배거!", { bold: true, color: C.accent }),

        new Paragraph({ children: [new PageBreak()] }),

        // ── 3. 한국 10종목 매수 전략 ──
        heading("3. 한국 10종목 매수 전략", HeadingLevel.HEADING_1),
        makeStockTable(KR, "KR"),
        new Paragraph({ spacing: { after: 200 }, children: [] }),
        ...makeStockCards(KR, "KR"),

        new Paragraph({ children: [new PageBreak()] }),

        // ── 4. 미국 10종목 매수 전략 ──
        heading("4. 미국 10종목 매수 전략", HeadingLevel.HEADING_1),
        makeStockTable(US, "US"),
        new Paragraph({ spacing: { after: 200 }, children: [] }),
        ...makeStockCards(US, "US"),

        new Paragraph({ children: [new PageBreak()] }),

        // ── 5. 매수 금지일 ──
        ...makeNoBuySection(),

        new Paragraph({ children: [new PageBreak()] }),

        // ── 6. 자동 코칭 시스템 ──
        heading("6. K-Quant 자동 코칭 시스템", HeadingLevel.HEADING_1),
        para("K-Quant 봇이 매일 자동으로 매수/대기 코칭 메시지를 보낸다.", { bold: true }),
        new Paragraph({ spacing: { after: 100 }, children: [] }),

        para("일일 스케줄:", { bold: true }),
        bullet("08:00 데일리 매수 코칭 — 오늘 매수 가능 종목/대기 종목 안내"),
        bullet("16:30 가격 모니터링 — 5% 이상 변동 알림"),
        bullet("일요일 10:00 리스코어링 — 7팩터 점수 재평가"),
        bullet("매월 1일 08:00 섹터 리뷰 — 졸업/제거 검토"),
        new Paragraph({ spacing: { after: 100 }, children: [] }),

        para("코칭 메시지 예시:", { bold: true }),
        bullet("매수 가능: \"우진(105840) 26,400원 — A등급 3분할 진입 가능\""),
        bullet("매수 대기: \"비에이치아이 +7% 급등 — 눌림 확인 후 진입\""),
        bullet("매수 금지: \"선물옵션 만기일 — 전종목 매수 자제\""),
        new Paragraph({ spacing: { after: 200 }, children: [] }),

        para("핵심: 매수할 날이 아니면 사지 말라고 말려준다!", { bold: true, color: C.accent }),

        new Paragraph({ spacing: { after: 300 }, children: [] }),
        new Paragraph({ alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "--- END ---", font: "Arial", size: 20, color: C.gray })] }),
      ],
    }],
  });

  const buffer = await Packer.toBuffer(doc);
  const outPath = "/Users/botddol/Downloads/텐배거_매수코칭전략서_2026.03.docx";
  fs.writeFileSync(outPath, buffer);
  console.log("Created:", outPath, `(${(buffer.length / 1024).toFixed(1)} KB)`);
}

main().catch(console.error);
