"""
스팩 청산가치 트래커 - Streamlit 대시보드 (웹 배포 버전)

웹 모드는 .streamlit/secrets.toml 또는 Streamlit Cloud의 Secrets 에
  IS_WEB = true
가 있을 때 활성화됩니다.

웹 모드에서는:
- DART API 키를 각 사용자의 세션에만 저장 (브라우저 닫으면 사라짐)
- KSFC 금리와 SPAC 마스터 데이터는 공유 (재배포 시 휘발)
"""
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import db
import calc
import pipeline
import excel_export


st.set_page_config(
    page_title="스팩 청산가치 트래커",
    page_icon="📈",
    layout="wide",
)


def _is_web_mode() -> bool:
    try:
        return bool(st.secrets.get("IS_WEB", False))
    except Exception:
        return False


WEB_MODE = _is_web_mode()
db.init_db()


with st.sidebar:
    st.markdown("### ⚙️ 설정")

    if WEB_MODE:
        saved_key = st.session_state.get("dart_api_key", "")
    else:
        saved_key = st.session_state.get("dart_api_key") or db.get_setting("OPEN_DART_API_KEY", "")
        if saved_key:
            st.session_state["dart_api_key"] = saved_key

    key_masked = ("●" * 8 + saved_key[-4:]) if saved_key else ""

    with st.expander("OpenDART API 키", expanded=not saved_key):
        if WEB_MODE:
            st.caption("🔒 웹 버전입니다. API 키는 이 브라우저 세션에만 임시 저장되며 다른 사용자에게 공유되지 않습니다.")
        else:
            st.caption("https://opendart.fss.or.kr 에서 발급한 키를 입력하세요.")
        if saved_key:
            st.text(f"현재 키: {key_masked}")
        new_key = st.text_input("API 키 입력", type="password", placeholder="40자리 키")
        col_a, col_b = st.columns(2)
        if col_a.button("저장", use_container_width=True):
            if new_key and len(new_key) >= 20:
                stripped = new_key.strip()
                st.session_state["dart_api_key"] = stripped
                if not WEB_MODE:
                    db.set_setting("OPEN_DART_API_KEY", stripped)
                st.success("저장됨")
                st.rerun()
            else:
                st.error("키가 너무 짧습니다.")
        if col_b.button("삭제", use_container_width=True):
            st.session_state.pop("dart_api_key", None)
            if not WEB_MODE:
                db.set_setting("OPEN_DART_API_KEY", "")
            st.warning("삭제됨")
            st.rerun()

    st.markdown("---")
    st.markdown("### 🔄 데이터 갱신")

    if st.button("🚀 전체 새로고침", type="primary", use_container_width=True):
        with st.status("데이터를 가져오는 중...", expanded=True) as status:
            all_results = pipeline.refresh_all(saved_key)
            for label, res in all_results:
                if res.get("ok"):
                    st.write(f"✅ **{label}** — {res.get('msg','완료')}")
                else:
                    st.write(f"⚠️ **{label}** — {res.get('msg','오류')}")
            for label, res in all_results:
                if label == "DART 공시 보강" and res.get("details"):
                    st.session_state["dart_details"] = res["details"]
            status.update(label="새로고침 완료", state="complete")
        st.rerun()

    col_c, col_d = st.columns(2)
    if col_c.button("KRX만", use_container_width=True):
        with st.spinner("KRX 동기화..."):
            r = pipeline.sync_krx()
            (st.success if r["ok"] else st.error)(r["msg"])
        st.rerun()
    if col_d.button("KSFC만", use_container_width=True):
        with st.spinner("KSFC 금리 조회..."):
            r = pipeline.fetch_ksfc_rate()
            (st.success if r["ok"] else st.error)(r["msg"])
        st.rerun()

    st.markdown("---")
    st.markdown("### 💰 KSFC 12개월 금리")
    last_ksfc_date, last_ksfc_rate = db.get_latest_ksfc_rate()
    if last_ksfc_rate is not None:
        st.success(f"현재 적용 중: **{last_ksfc_rate}%** ({last_ksfc_date})")
    else:
        st.warning("⚠️ KSFC 금리 미설정")

    with st.expander("📝 KSFC 금리 입력", expanded=last_ksfc_rate is None):
        if WEB_MODE:
            st.caption("⚠️ 웹 버전에서는 이 값이 모든 사용자에게 공유됩니다.")
        st.markdown("📌 [한국증권금융 사이트](https://www.ksfc.co.kr:4443/product/rate/deposit.do)")
        st.caption("경로: 예수금상품 → 만기지급금리 → 기관고객 → 기업인수목적회사예수금 → 거치식 12개월")
        manual_rate = st.number_input(
            "12개월 거치금 금리(%)",
            min_value=0.50, max_value=10.0,
            value=float(last_ksfc_rate) if last_ksfc_rate else 2.85,
            step=0.01, format="%.2f",
            key="ksfc_manual_input",
        )
        manual_date = st.date_input("적용일", value=date.today(), key="ksfc_manual_date")
        if st.button("💾 저장", use_container_width=True, key="save_ksfc"):
            db.upsert_ksfc_rate(manual_date.isoformat(), manual_rate)
            st.success(f"저장됨: {manual_rate}%")
            st.rerun()

    if WEB_MODE:
        st.markdown("---")
        st.caption("🌐 웹 공개 버전")


st.title("📈 스팩 청산가치 트래커")
st.caption("상장된 스팩의 예치이율·청산예상금·연환산 수익률을 한눈에 확인하세요.")

spacs = db.get_all_spacs(include_delisted=False)

if not spacs:
    st.info("🎯 데이터가 없습니다. 왼쪽 사이드바에서 [전체 새로고침] 버튼을 눌러주세요.")
    st.stop()

computed = [calc.compute_row(s) for s in spacs]
df = pd.DataFrame(computed)


col1, col2, col3, col4 = st.columns(4)
col1.metric("총 종목 수", f"{len(df)}개")
imminent = df[df["days_until_liquidation"].apply(lambda x: isinstance(x, (int, float)) and x is not None and x <= 90)]
col2.metric("⚠️ 청산임박 (90일↓)", f"{len(imminent)}개")
top_yield = df["annualized_return_pct"].dropna()
if len(top_yield) > 0:
    col3.metric("최고 연환산 수익률", f"{top_yield.max():.2f}%")
    col4.metric("평균 연환산 수익률", f"{top_yield.mean():.2f}%")


st.markdown("### 🔎 필터")
f1, f2, f3, f4 = st.columns([1, 1, 1, 2])
hide_no_rate = f1.checkbox("이율 미상 종목 숨김", value=False)
only_positive_yield = f2.checkbox("플러스 수익률만", value=False)
imminent_only = f3.checkbox("청산임박(180일↓)만", value=False)
search = f4.text_input("종목명 검색", "")

view = df.copy()
if hide_no_rate:
    view = view[view["rate_y1"].notna()]
if only_positive_yield:
    view = view[view["annualized_return_pct"].fillna(-999) > 0]
if imminent_only:
    view = view[view["days_until_liquidation"].apply(lambda x: isinstance(x, (int, float)) and x is not None and x <= 180)]
if search:
    view = view[view["name"].str.contains(search, na=False)]

view = view.sort_values("days_until_liquidation", na_position="last")


display_cols_map = {
    "ticker": "종목코드", "name": "종목명",
    "payment_date": "납입기일", "estimated_liquidation_date": "청산예상일",
    "days_until_liquidation": "남은일수",
    "offering_price": "공모가", "current_price": "현재가",
    "rate_y1": "1년차%", "rate_y2": "2년차%", "rate_y3": "3년차%",
    "liquidation_value": "예상청산금",
    "total_return_pct": "총수익률%", "annualized_return_pct": "연환산%",
    "rate_source": "이율출처", "rate_note": "출처비고",
}
view_d = view[list(display_cols_map.keys())].rename(columns=display_cols_map)


def color_days(val):
    if val is None or pd.isna(val):
        return ""
    try:
        v = int(val)
    except (TypeError, ValueError):
        return ""
    if v <= 90:
        return "background-color: #FFCDD2; font-weight: bold;"
    if v <= 180:
        return "background-color: #FFF59D;"
    return ""


def color_source(val):
    if val == "DART":
        return "background-color: #C8E6C9;"
    if val == "KSFC":
        return "background-color: #E0E0E0; color: #666;"
    if val == "MIXED":
        return "background-color: #FFF9C4;"
    return ""


def color_yield(val):
    if val is None or pd.isna(val):
        return ""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ""
    if v >= 5:
        return "color: #1B5E20; font-weight: bold;"
    if v >= 3:
        return "color: #2E7D32;"
    if v < 0:
        return "color: #C62828;"
    return ""


styled = (
    view_d.style
    .map(color_days, subset=["남은일수"])
    .map(color_source, subset=["이율출처"])
    .map(color_yield, subset=["연환산%", "총수익률%"])
    .format({
        "공모가": "{:,.0f}",
        "현재가": lambda x: "—" if pd.isna(x) else f"{x:,.0f}",
        "1년차%": lambda x: "—" if pd.isna(x) else f"{x:.2f}",
        "2년차%": lambda x: "—" if pd.isna(x) else f"{x:.2f}",
        "3년차%": lambda x: "—" if pd.isna(x) else f"{x:.2f}",
        "예상청산금": lambda x: "—" if pd.isna(x) else f"{x:,.1f}",
        "총수익률%": lambda x: "—" if pd.isna(x) else f"{x:+.2f}",
        "연환산%": lambda x: "—" if pd.isna(x) else f"{x:+.2f}",
        "남은일수": lambda x: "—" if pd.isna(x) else f"{int(x):,}",
    })
)

st.markdown(f"### 📋 종목 리스트 ({len(view_d)}개)")
st.dataframe(styled, use_container_width=True, height=600)

st.caption("🟥 90일 이하 (관리종목 임박)  🟨 180일 이하  |  🟩 DART공시(확정)  ⬜ KSFC(추정)  🟦 MIXED(혼합)")


st.markdown("### 📥 엑셀 출력")
if st.button("💾 엑셀로 저장", type="primary"):
    tmp_path = Path("/tmp/스팩정리_export.xlsx") if WEB_MODE else Path(__file__).parent / f"스팩정리_{date.today().isoformat()}.xlsx"
    excel_export.export_to_xlsx(tmp_path)
    with open(tmp_path, "rb") as f:
        data = f.read()
    st.download_button(
        "📥 파일 다운로드",
        data,
        file_name=f"스팩정리_{date.today().isoformat()}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


with st.expander("✏️ 종목별 수동 보정"):
    if WEB_MODE:
        st.caption("⚠️ 웹 버전에서는 수동 보정도 모든 사용자에게 공유됩니다.")
    options = [f"{s['ticker']} - {s['name']}" for s in spacs]
    pick = st.selectbox("종목 선택", options)
    if pick:
        ticker = pick.split(" - ")[0]
        target = next(s for s in spacs if s["ticker"] == ticker)
        e1, e2, e3, e4 = st.columns(4)
        new_pay = e1.text_input("납입기일", target.get("payment_date") or "", placeholder="2024-03-15")
        new_op  = e2.number_input("공모가", value=float(target.get("offering_price") or 2000), step=100.0)
        new_r1  = e3.number_input("1년차 이율(%)", value=float(target.get("rate_y1") or 0), step=0.01, format="%.2f")
        new_r2  = e3.number_input("2년차 이율(%)", value=float(target.get("rate_y2") or 0), step=0.01, format="%.2f")
        new_r3  = e4.number_input("3년차 이율(%)", value=float(target.get("rate_y3") or 0), step=0.01, format="%.2f")
        new_note = st.text_input("메모", target.get("note") or "")
        manual = st.checkbox("🔒 수동 보정 (자동 갱신 보호)", value=bool(target.get("manual_override")))
        if st.button("💾 저장", key="save_manual"):
            db.upsert_spac({
                "ticker": ticker,
                "payment_date": new_pay or None,
                "offering_price": new_op,
                "rate_y1": new_r1 or None,
                "rate_y2": new_r2 or None,
                "rate_y3": new_r3 or None,
                "rate_source": "MANUAL" if manual else target.get("rate_source"),
                "manual_override": 1 if manual else 0,
                "note": new_note or None,
            })
            st.success("저장됨")
            st.rerun()


with st.expander("🔬 DART 처리 진단 (최근 새로고침)"):
    details = st.session_state.get("dart_details", [])
    if details:
        st.caption(f"앞 {len(details)}개 종목의 단계별 처리 결과:")
        for d in details:
            st.markdown(f"**{d['ticker']} - {d['name']}**")
            for step in d.get("steps", []):
                st.text(f"  · {step}")
    else:
        st.caption("아직 새로고침 안 됨")


with st.expander("🗑️ 상장폐지 종목 보기"):
    delisted = [s for s in db.get_all_spacs(include_delisted=True) if s.get("delisted_at")]
    if delisted:
        ddf = pd.DataFrame(delisted)[["ticker", "name", "delisted_at"]]
        ddf.columns = ["종목코드", "종목명", "폐지마킹일"]
        st.dataframe(ddf, use_container_width=True)
    else:
        st.caption("폐지 종목 없음")


st.caption(f"마지막 표시 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
