from kstock.ingest.naver_discussion import analyze_discussion_titles, parse_discussion_titles


def test_parse_discussion_titles_extracts_naver_board_titles():
    html = """
    <table>
      <tr><td class="title"><a href="/item/board_read.naver?code=123456">상한가 갑니다 내일도</a></td></tr>
      <tr><td class="title"><a href="/item/board_read.naver?code=123456">세력 매집 흔적 보인다</a></td></tr>
    </table>
    """

    titles = parse_discussion_titles(html)

    assert titles == ["상한가 갑니다 내일도", "세력 매집 흔적 보인다"]


def test_analyze_discussion_titles_detects_overheat_and_accumulation():
    overheat = analyze_discussion_titles(
        "123456",
        "테마주",
        [
            "상한가 간다",
            "급등 시작",
            "내일도 추천",
            "풀매수 가즈아",
            "주도주 맞네",
            "리딩방 떴다",
            "폭등 간다",
            "상따 대기",
        ],
    )
    accumulation = analyze_discussion_titles(
        "654321",
        "실적주",
        [
            "매집 흔적 보인다",
            "실적 좋아진다",
            "눌림에서 분할",
            "수주 계약 확인",
            "저평가 구간 같다",
            "바닥 확인",
        ],
    )

    assert overheat["label"] == "토론방 과열"
    assert overheat["score_adj"] < 0
    assert accumulation["label"] == "토론방 매집 감지"
    assert accumulation["score_adj"] > 0
