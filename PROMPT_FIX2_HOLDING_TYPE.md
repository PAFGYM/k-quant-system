# 수정 2: holding_type 저장 + 등록 UI

## 문제
- `holdings` 테이블에 `holding_type` 컬럼이 없어서, 사용자가 "삼성전자는 5년 장투"라고 의도해도 시스템이 모름
- `investor_profile.py`의 `classify_hold_type()`이 매수일 경과일수로만 자동 분류 (3일 이내 scalp, 14일 이내 swing 등)
- 결과적으로 장기투자 종목에 AI가 매도 제안을 하게 됨
- `holding_analysis` 테이블에 `hold_type`이 있지만, 이건 자동 분류 결과일 뿐 사용자 의도가 아님

## 작업 지시

### 작업 1: sqlite.py — holdings 테이블 migration
파일: `src/kstock/store/sqlite.py`

기존 migration 패턴(quantity/eval_amount 추가하는 부분, L796-807)을 찾아서 그 바로 아래에 동일한 패턴으로 추가:

```python
# Migrate: add holding_type to holdings table (v3.7)
for col, sql in [
    ("holding_type", "ALTER TABLE holdings ADD COLUMN holding_type TEXT DEFAULT 'auto'"),
]:
    try:
        conn.execute(f"SELECT {col} FROM holdings LIMIT 1")
    except sqlite3.OperationalError:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
```

### 작업 2: sqlite.py — add_holding() 메서드 수정
파일: `src/kstock/store/sqlite.py` L916-933의 `add_holding()` 메서드

변경 전 시그니처:
```python
def add_holding(self, ticker: str, name: str, buy_price: float) -> int:
```

변경 후 시그니처:
```python
def add_holding(self, ticker: str, name: str, buy_price: float, holding_type: str = "auto") -> int:
```

INSERT 쿼리에 holding_type 컬럼과 값 추가. 기존 컬럼 목록에 `holding_type`을 넣고, VALUES에 파라미터 추가.

### 작업 3: sqlite.py — upsert_holding() 메서드 수정
파일: `src/kstock/store/sqlite.py` L970-1019의 `upsert_holding()` 메서드

시그니처에 `holding_type: str = "auto"` 파라미터 추가.
INSERT 경로에 holding_type 컬럼과 값 추가.
UPDATE 경로에서는 holding_type이 'auto'가 아닐 때만 업데이트 (기존 사용자 설정을 덮어쓰지 않도록).

### 작업 4: sqlite.py — update_holding_type() 새 메서드 추가
`upsert_holding` 근처에 새 메서드 추가:

```python
def update_holding_type(self, holding_id: int, holding_type: str) -> None:
    """보유종목의 투자 유형을 업데이트."""
    now = datetime.utcnow().isoformat()
    with self._connect() as conn:
        conn.execute(
            "UPDATE holdings SET holding_type = ?, updated_at = ? WHERE id = ?",
            (holding_type, now, holding_id),
        )
```

### 작업 5: trading.py — 종목 추가 후 holding_type 선택 UI
파일: `src/kstock/bot/mixins/trading.py`

`_action_confirm_text_holding` 메서드(L252-291)에서 종목이 DB에 추가된 후, 투자 기간 선택 InlineKeyboard를 표시.

종목 추가 성공 메시지 뒤에 다음 키보드 추가:
```python
# holding_id는 db.add_holding()의 반환값
keyboard = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("⚡ 초단기 (1~3일)", callback_data=f"ht:scalp:{holding_id}"),
        InlineKeyboardButton("🔥 단기 (1~4주)", callback_data=f"ht:swing:{holding_id}"),
    ],
    [
        InlineKeyboardButton("📊 중기 (1~6개월)", callback_data=f"ht:position:{holding_id}"),
        InlineKeyboardButton("💎 장기 (6개월+)", callback_data=f"ht:long_term:{holding_id}"),
    ],
])
await query.message.reply_text(
    f"⏰ {name}의 투자 기간을 선택하세요:",
    reply_markup=keyboard,
)
```

참고: 이미 trading.py L410-425에 비슷한 hz 선택 키보드 패턴이 있으니 그 스타일을 따르면 됨.

### 작업 6: trading.py — 스크린샷 종목 추가 후에도 동일 UI
파일: `src/kstock/bot/mixins/trading.py`

`_action_add_from_screenshot` 메서드(L182-250)에서 종목이 추가된 후에도 동일한 holding_type 선택 키보드 표시.

단, 스크린샷에서 여러 종목을 한번에 추가할 수 있으므로:
- `add_ss:all`인 경우: 전체 종목에 대해 하나의 키보드로 일괄 설정
  ```python
  keyboard = InlineKeyboardMarkup([
      [
          InlineKeyboardButton("⚡ 전체 초단기", callback_data=f"ht:scalp:all"),
          InlineKeyboardButton("🔥 전체 단기", callback_data=f"ht:swing:all"),
      ],
      [
          InlineKeyboardButton("📊 전체 중기", callback_data=f"ht:position:all"),
          InlineKeyboardButton("💎 전체 장기", callback_data=f"ht:long_term:all"),
      ],
      [
          InlineKeyboardButton("🔀 개별 설정은 나중에", callback_data="ht:skip:0"),
      ],
  ])
  ```
- `add_ss:one:{ticker}`인 경우: 해당 종목만 선택 키보드 표시

### 작업 7: trading.py — _action_set_holding_type 새 메서드
파일: `src/kstock/bot/mixins/trading.py`

```python
async def _action_set_holding_type(
    self, query, context, payload: str,
) -> None:
    """보유종목 투자 유형 설정 콜백 핸들러.

    콜백 데이터: ht:{type}:{holding_id_or_all}
    type: scalp, swing, position, long_term, skip
    """
    parts = payload.split(":", 1)
    if len(parts) < 2:
        return
    hold_type, target = parts[0], parts[1]

    if hold_type == "skip":
        await query.edit_message_text("⏭️ 투자 유형은 나중에 설정할 수 있습니다.")
        return

    type_labels = {
        "scalp": "⚡ 초단기 (1~3일)",
        "swing": "🔥 단기 (1~4주)",
        "position": "📊 중기 (1~6개월)",
        "long_term": "💎 장기 (6개월+)",
    }
    label = type_labels.get(hold_type, hold_type)

    if target == "all":
        # 최근 추가된 스크린샷 종목들 전체 업데이트
        recent_ids = context.user_data.get("recent_holding_ids", [])
        for hid in recent_ids:
            self.db.update_holding_type(hid, hold_type)
        await query.edit_message_text(
            f"✅ {len(recent_ids)}개 종목 → {label} 설정 완료"
        )
    else:
        holding_id = int(target)
        self.db.update_holding_type(holding_id, hold_type)
        await query.edit_message_text(f"✅ 투자 유형: {label} 설정 완료")
```

### 작업 8: core_handlers.py — 콜백 dispatch에 "ht" 추가
파일: `src/kstock/bot/mixins/core_handlers.py`

`handle_callback` 메서드의 dispatch 딕셔너리(L1061-1126 부근)에 추가:
```python
"ht": self._action_set_holding_type,
```

### 작업 9: investor_profile.py — classify_hold_type() 수정
파일: `src/kstock/core/investor_profile.py` L95-133의 `classify_hold_type()` 함수

현재 코드:
```python
def classify_hold_type(holding: dict) -> str:
    user_type = holding.get("holding_type", "auto")
    if user_type and user_type != "auto" and user_type in HOLD_TYPE_CONFIG:
        return user_type
    # ... 날짜 기반 분류
```

이 로직은 이미 올바름! `holding_type`이 DB에 저장되면 자동으로 작동함.
단, `get_active_holdings()` 반환값에 `holding_type` 컬럼이 포함되는지 확인 필요.

### 작업 10: sqlite.py — get_active_holdings() 확인
`get_active_holdings()` 메서드의 SELECT 쿼리에 `holding_type` 컬럼이 포함되는지 확인.
`SELECT *`이면 자동 포함됨. 만약 컬럼을 명시적으로 나열하고 있다면 `holding_type`을 추가.

## 검증
1. `PYTHONPATH=src python3 -m pytest tests/ -x -q` 전체 통과
2. `sqlite3 data/kquant.db ".schema holdings"` → `holding_type TEXT DEFAULT 'auto'` 확인
3. `grep -rn "ht:" src/kstock/bot/mixins/trading.py` → 콜백 데이터 확인
4. `grep -rn '"ht"' src/kstock/bot/mixins/core_handlers.py` → dispatch 등록 확인
5. 테스트: 텔레그램에서 "삼성전자 10주 75000원" 입력 → 종목 추가 확인 → 투자 기간 선택 키보드 표시 확인
