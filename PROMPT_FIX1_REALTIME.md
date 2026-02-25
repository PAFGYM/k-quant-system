# 수정 1: 실시간 시황 보장

## 문제
1. `_handle_quick_question`(commands.py:781)에서 `build_full_context_with_macro(self.db, self.macro_client)`를 호출할 때 `yf_client`를 전달하지 않아서, 빠른 질문(시장분석/포트폴리오점검/매수추천/리스크점검)에 실시간 기술지표가 빠짐
2. `context_builder.py`에 레거시 메서드 `build_full_context()`(L471-497)와 `build_full_context_async()`(L500-537)가 남아있음. 어디서도 호출되지 않는 죽은 코드이고, 누군가 실수로 쓰면 시장 데이터 없이 AI가 답변하게 됨

## 작업 지시

### 작업 1: commands.py 수정 (1줄)
파일: `src/kstock/bot/mixins/commands.py` L781

변경 전:
```python
ctx = await build_full_context_with_macro(self.db, self.macro_client)
```

변경 후:
```python
ctx = await build_full_context_with_macro(self.db, self.macro_client, self.yf_client)
```

참고: 같은 파일 L733에 이미 올바른 호출이 있음:
```python
ctx = await build_full_context_with_macro(
    self.db, self.macro_client, self.yf_client,
)
```
이 패턴과 동일하게 맞추면 됨.

### 작업 2: context_builder.py 레거시 삭제
파일: `src/kstock/bot/context_builder.py`

다음 두 메서드를 완전히 삭제:
- `build_full_context()` (def build_full_context ~ return 블록까지)
- `build_full_context_async()` (async def build_full_context_async ~ return 블록까지)

삭제 전 확인: `grep -rn "build_full_context_async\|build_full_context(" src/` 로 이 두 메서드를 호출하는 곳이 없는지 확인. `build_full_context_with_macro`만 호출되어야 함.

`build_full_context_with_macro()`는 절대 삭제하지 마라. 이것이 정상 메서드임.

## 검증
1. `PYTHONPATH=src python3 -m pytest tests/ -x -q` 전체 통과
2. `grep -rn "build_full_context(" src/ | grep -v "with_macro"` → 결과 없어야 함 (import 제외)
3. `grep -rn "build_full_context_async" src/` → 결과 없어야 함
