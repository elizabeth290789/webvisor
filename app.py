from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Webvisor Session Triage", layout="wide")
st.title("Отбор записей Вебвизора для ручного просмотра")
st.caption("Приложение выгружает visits/hits из Logs API, считает score и помогает выбрать visitID для просмотра в интерфейсе Яндекс Метрики.")

try:
    from demo_data import build_demo_visits_and_hits
    from metrika_client import MetrikaAPIError, MetrikaLogsClient, get_metrika_token
    from scoring import aggregate_stats, score_sessions
    from summarizer import build_summary
except Exception as exc:
    st.error("Ошибка при импорте модулей приложения. Проверьте зависимости и конфигурацию деплоя.")
    st.exception(exc)
    st.stop()


@st.cache_data(show_spinner=False, ttl=3600)
def load_data(counter_id: int, date_from: str, date_to: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    return MetrikaLogsClient().fetch_visits_and_hits(counter_id, date_from, date_to)


def load_demo_score(registration_goal_ids: list[str] | None = None) -> pd.DataFrame:
    visits_df, hits_df = build_demo_visits_and_hits()
    return score_sessions(visits_df, hits_df, registration_goal_ids or ["1001"])


def csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def main() -> None:
    demo_mode = not bool(get_metrika_token())
    if demo_mode:
        st.warning("Демо-режим: YANDEX_METRIKA_TOKEN не задан, поэтому показаны тестовые данные. Добавьте секрет в Streamlit Community Cloud, чтобы подключить реальные данные.")
        if "scored" not in st.session_state or not st.session_state.get("demo_mode"):
            st.session_state["scored"] = load_demo_score()
        st.session_state["demo_mode"] = True
    else:
        st.success("Токен Яндекс Метрики найден. Можно загрузить реальные данные из Logs API.")
        st.session_state["demo_mode"] = False

    with st.sidebar:
        st.header("Параметры")
        counter_id = st.number_input("counter_id", min_value=1, step=1)
        today = dt.date.today()
        date_from = st.date_input("date_from", today - dt.timedelta(days=7))
        date_to = st.date_input("date_to", today - dt.timedelta(days=1))
        reg_goals = st.text_input("ID целей регистрации через запятую", help="Если оставить пустым, любая сессия считается без регистрации для правил регистрации.")
        if demo_mode:
            st.info("Сейчас приложение работает на демо-данных. Поля ниже нужны для реальной Метрики после добавления токена.")
            load_label = "Обновить демо-данные"
        else:
            load_label = "Загрузить и посчитать"
        load = st.button(load_label, type="primary")

    if load and demo_mode:
        st.session_state["scored"] = load_demo_score([x.strip() for x in reg_goals.split(",") if x.strip()] or ["1001"])
    elif load:
        try:
            with st.spinner("Создаем requests в Logs API и скачиваем parts..."):
                visits_df, hits_df = load_data(int(counter_id), str(date_from), str(date_to))
                st.session_state["scored"] = score_sessions(visits_df, hits_df, [x.strip() for x in reg_goals.split(",") if x.strip()])
        except MetrikaAPIError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.exception(exc)

    scored = st.session_state.get("scored")
    if scored is None:
        st.info("Введите параметры и нажмите «Загрузить и посчитать». Если YANDEX_METRIKA_TOKEN не задан, приложение покажет демо-данные.")
        st.stop()

    st.subheader("Фильтры")
    col1, col2, col3 = st.columns(3)
    with col1:
        device_filter = st.selectbox("Устройство", ["Все", "mobile", "desktop", "tablet"])
        no_reg_only = st.checkbox("Только без регистрации", value=True)
    with col2:
        url_contains = st.text_input("URL содержит")
        utm_contains = st.text_input("UTM campaign содержит")
    with col3:
        min_score = st.slider("score больше N", 0, 100, 40)
        min_duration = st.number_input("длительность больше N секунд", min_value=0, value=0)

    filtered = scored.copy()
    if device_filter != "Все" and "deviceCategory" in filtered:
        filtered = filtered[filtered["deviceCategory"].astype(str).str.contains(device_filter, case=False, na=False)]
    if no_reg_only and "registered" in filtered:
        filtered = filtered[~filtered["registered"]]
    if url_contains:
        filtered = filtered[filtered.get("startURL", pd.Series(dtype=str)).astype(str).str.contains(url_contains, case=False, na=False) | filtered.get("endURL", pd.Series(dtype=str)).astype(str).str.contains(url_contains, case=False, na=False)]
    if utm_contains and "UTMCampaign" in filtered:
        filtered = filtered[filtered["UTMCampaign"].astype(str).str.contains(utm_contains, case=False, na=False)]
    filtered = filtered[(filtered["score"] >= min_score) & (pd.to_numeric(filtered.get("visitDuration", 0), errors="coerce").fillna(0) >= min_duration)]

    show_cols = [c for c in ["score", "visitID", "clientID", "dateTime", "deviceCategory", "startURL", "endURL", "visitDuration", "pageViews", "goalsID", "UTMCampaign", "UTMSource", "reasons"] if c in filtered]
    st.subheader(f"Проблемные визиты: {len(filtered)}")
    st.dataframe(filtered[show_cols], use_container_width=True, hide_index=True)

    st.subheader("Экспорт")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.download_button("CSV: top проблемных", csv_bytes(filtered.sort_values("score", ascending=False).head(200)), "top_problem_visits.csv", "text/csv")
    with c2:
        st.download_button("CSV: все визиты со score", csv_bytes(scored), "all_scored_visits.csv", "text/csv")
    with c3:
        stats = aggregate_stats(scored)
        combined = pd.concat({k: v for k, v in stats.items() if not v.empty}, names=["group"]).reset_index(level=0)
        st.download_button("CSV: агрегаты", csv_bytes(combined), "aggregated_stats.csv", "text/csv")

    if st.button("Сформировать выводы"):
        st.markdown(build_summary(filtered.sort_values("score", ascending=False).head(50)))


try:
    main()
except Exception as exc:
    st.error("Критическая ошибка при запуске приложения.")
    st.exception(exc)
