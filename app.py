from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Webvisor Session Triage", layout="wide")
st.title("Отбор записей Вебвизора для ручного просмотра")
st.caption("Честный помощник: сначала проверяет, хватает ли данных для выводов, затем подбирает записи для просмотра в Вебвизоре.")

try:
    from demo_data import build_demo_visits_and_hits
    from metrika_client import MetrikaAPIError, MetrikaLogsClient, get_metrika_token
    from scoring import (
        aggregate_stats,
        analyze_hits,
        baseline_metrics,
        find_problem_segments,
        score_sessions,
        select_records_to_watch,
        webvisor_filter_table,
    )
    from summarizer import build_recommendations
except Exception as exc:
    st.error("Ошибка при импорте модулей приложения. Проверьте зависимости и конфигурацию деплоя.")
    st.exception(exc)
    st.stop()


URL_SEARCH_OPTIONS = {
    "startURL — страница входа": "start",
    "endURL — страница выхода": "end",
    "startURL или endURL": "start_or_end",
    "любой URL внутри визита, только если включены hits": "any_hit",
}
DEFAULT_URL_SEARCH_LABEL = "startURL или endURL"
URL_WITHOUT_HITS_HELP = (
    "Без загрузки hits приложение видит только startURL и endURL визита. "
    "Чтобы искать посещение страницы внутри визита, включите загрузку hits."
)
INSUFFICIENT_DATA_WARNING = (
    "Данных недостаточно для сравнения сегментов. Можно только отобрать записи для ручного просмотра, "
    "но нельзя делать выводы о просадках CR."
)
WATCH_COLUMNS = [
    "visitID",
    "dateTime",
    "deviceCategory",
    "UTMSource",
    "UTMCampaign",
    "startURL",
    "endURL",
    "visitDuration",
    "pageViews",
    "reason_to_watch",
]


@st.cache_data(show_spinner=False, ttl=3600)
def load_data(
    counter_id: int,
    date_from: str,
    date_to: str,
    url_contains: str,
    load_hits: bool,
    url_search_scope: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    return MetrikaLogsClient().fetch_visits_and_hits(
        counter_id,
        date_from,
        date_to,
        url_contains,
        load_hits,
        url_search_scope,
    )


def load_demo_score(registration_goal_ids: list[str] | None = None) -> pd.DataFrame:
    visits_df, hits_df = build_demo_visits_and_hits()
    return score_sessions(visits_df, hits_df, registration_goal_ids or ["1001"])


def csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def filter_by_url(scored: pd.DataFrame, hits: pd.DataFrame, url_contains: str, url_search_scope: str) -> pd.DataFrame:
    start_match = scored.get("startURL", pd.Series(dtype=str)).astype(str).str.contains(url_contains, case=False, na=False)
    end_match = scored.get("endURL", pd.Series(dtype=str)).astype(str).str.contains(url_contains, case=False, na=False)

    if url_search_scope == "start":
        return scored[start_match]
    if url_search_scope == "end":
        return scored[end_match]
    if url_search_scope == "any_hit" and not hits.empty and "ym:pv:visitID" in hits and "ym:pv:URL" in hits:
        hit_visit_ids = hits.loc[
            hits["ym:pv:URL"].astype(str).str.contains(url_contains, case=False, na=False),
            "ym:pv:visitID",
        ].astype(str)
        return scored[scored.get("visitID", pd.Series(dtype=str)).astype(str).isin(hit_visit_ids)]

    return scored[start_match | end_match]


def _sample_period(df: pd.DataFrame) -> str:
    if df.empty or "dateTime" not in df:
        return "не определен"
    dates = pd.to_datetime(df["dateTime"], errors="coerce").dropna()
    if dates.empty:
        return "не определен"
    return f"{dates.min():%Y-%m-%d %H:%M} — {dates.max():%Y-%m-%d %H:%M}"


def _nunique(df: pd.DataFrame, col: str) -> int:
    if col not in df:
        return 0
    values = df[col].fillna("").astype(str)
    return int(values[values.ne("")].nunique())


def _render_sample_status(filtered: pd.DataFrame, url_contains: str) -> tuple[dict[str, float], bool, bool]:
    baseline = baseline_metrics(filtered)
    visits = int(baseline["total_visits"])
    registrations = int(baseline["registrations"])
    cr = float(baseline["registration_cr"])
    enough_for_manual = visits >= 100 and registrations >= 10 and cr > 0
    enough_for_segments = visits >= 300 and registrations >= 20 and cr > 0

    st.subheader("1. Можно ли делать выводы?")
    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    c1.metric("визиты", visits)
    c2.metric("регистрации", registrations)
    c3.metric("CR", f"{cr * 100:.2f}%")
    c4.metric("устройств", _nunique(filtered, "deviceCategory"))
    c5.metric("кампаний", _nunique(filtered, "UTMCampaign"))
    c6.metric("период", _sample_period(filtered))
    c7.metric("URL-фильтр", url_contains or "не задан")

    if not enough_for_manual:
        st.warning(INSUFFICIENT_DATA_WARNING, icon="⚠️")
    else:
        st.success("Данных достаточно для аккуратного отбора записей и базовой проверки сегментов. Для сегментного анализа используется более строгий порог: 300 визитов и 20 регистраций.")
    return baseline, enough_for_manual, enough_for_segments


def main() -> None:
    demo_mode = not bool(get_metrika_token())
    if demo_mode:
        st.warning("Демо-режим: YANDEX_METRIKA_TOKEN не задан, поэтому показаны тестовые данные. Добавьте секрет в Streamlit Community Cloud, чтобы подключить реальные данные.")
        if "scored" not in st.session_state or not st.session_state.get("demo_mode"):
            st.session_state["scored"] = load_demo_score()
            st.session_state["hits"] = build_demo_visits_and_hits()[1]
        st.session_state["demo_mode"] = True
    else:
        st.success("Токен Яндекс Метрики найден. Можно загрузить реальные данные из Logs API.")
        st.session_state["demo_mode"] = False

    with st.sidebar:
        st.header("Параметры")
        simple_mode = st.toggle("Простой режим", value=True, help="Показывает только статус выборки, записи для просмотра и краткие рекомендации.")
        counter_id = st.number_input("counter_id", min_value=1, value=18477952, step=1)
        today = dt.date.today()
        yesterday = today - dt.timedelta(days=1)
        date_from = st.date_input("date_from", yesterday, max_value=yesterday)
        date_to = st.date_input("date_to", yesterday, max_value=yesterday)
        st.caption("Для больших счетчиков лучше выбирать один завершенный день. Текущий день может быть недоступен или неполным в Logs API.")
        load_hits = st.checkbox("Загружать hits", value=False, help="Выключено по умолчанию для быстрой выгрузки больших счетчиков.")
        url_search_label = st.selectbox("Где искать URL", list(URL_SEARCH_OPTIONS), index=list(URL_SEARCH_OPTIONS).index(DEFAULT_URL_SEARCH_LABEL))
        url_search_scope = URL_SEARCH_OPTIONS[url_search_label]
        url_contains_load = st.text_input("URL содержит *" if load_hits else "URL входа или выхода содержит *", value="chat", help="Обязательный фильтр: применяется в Logs API до скачивания данных.")
        st.caption(URL_WITHOUT_HITS_HELP)
        if url_search_scope == "any_hit" and not load_hits:
            st.warning("Поиск по любому URL внутри визита доступен только при включенной загрузке hits.")
        reg_goals = st.text_input("ID целей регистрации через запятую", value="2898778")
        load = st.button("Обновить демо-данные" if demo_mode else "Загрузить и посчитать", type="primary")

    if load and demo_mode:
        st.session_state["scored"] = load_demo_score([x.strip() for x in reg_goals.split(",") if x.strip()] or ["1001"])
        st.session_state["hits"] = build_demo_visits_and_hits()[1]
        st.session_state["url_search_scope"] = url_search_scope
        st.session_state["url_contains_load"] = url_contains_load.strip()
    elif load:
        if date_to < date_from:
            st.error("date_to должен быть не раньше date_from.")
        elif date_to > yesterday:
            st.error("date_to не должен быть позже вчерашнего дня, потому что Logs API может не отдавать текущий день или отдавать неполные данные.")
        elif not url_contains_load.strip():
            st.error("Заполните обязательное поле URL, чтобы не выгружать весь счетчик.")
        else:
            try:
                with st.spinner("Создаем requests в Logs API и скачиваем parts..."):
                    st.session_state["url_contains_load"] = url_contains_load.strip()
                    visits_df, hits_df = load_data(int(counter_id), str(date_from), str(date_to), url_contains_load.strip(), load_hits, url_search_scope)
                    st.session_state["scored"] = score_sessions(visits_df, hits_df, [x.strip() for x in reg_goals.split(",") if x.strip()])
                    st.session_state["hits"] = hits_df
                    st.session_state["url_search_scope"] = url_search_scope
            except MetrikaAPIError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.exception(exc)

    scored = st.session_state.get("scored")
    if scored is None:
        st.info("Введите параметры и нажмите «Загрузить и посчитать». Если YANDEX_METRIKA_TOKEN не задан, приложение покажет демо-данные.")
        st.stop()

    with st.expander("Фильтры выборки", expanded=not simple_mode):
        col1, col2, col3 = st.columns(3)
        with col1:
            device_filter = st.selectbox("Устройство", ["Все", "mobile", "desktop", "tablet"])
            no_reg_only = st.checkbox("Только без регистрации", value=False, help="Для анализа CR оставьте всю выборку.")
        with col2:
            filter_hits_loaded = not st.session_state.get("hits", pd.DataFrame()).empty
            current_scope = st.session_state.get("url_search_scope", URL_SEARCH_OPTIONS[DEFAULT_URL_SEARCH_LABEL])
            current_label = next((label for label, scope in URL_SEARCH_OPTIONS.items() if scope == current_scope), DEFAULT_URL_SEARCH_LABEL)
            filter_url_search_label = st.selectbox("Где искать URL", list(URL_SEARCH_OPTIONS), index=list(URL_SEARCH_OPTIONS).index(current_label), key="filter_url_search_label")
            filter_url_search_scope = URL_SEARCH_OPTIONS[filter_url_search_label]
            url_contains = st.text_input("URL содержит" if filter_hits_loaded else "URL входа или выхода содержит", value=st.session_state.get("url_contains_load", ""))
            utm_contains = st.text_input("UTM campaign содержит")
        with col3:
            min_segment_visits = st.number_input("минимум визитов в сегменте", min_value=1, value=10)
            min_duration = st.number_input("длительность больше N секунд", min_value=0, value=0)

    filtered = scored.copy()
    if device_filter != "Все" and "deviceCategory" in filtered:
        filtered = filtered[filtered["deviceCategory"].astype(str).str.contains(device_filter, case=False, na=False)]
    if no_reg_only and "registered" in filtered:
        filtered = filtered[~filtered["registered"]]
    if url_contains:
        filtered = filter_by_url(filtered, st.session_state.get("hits", pd.DataFrame()), url_contains, filter_url_search_scope)
    if utm_contains and "UTMCampaign" in filtered:
        filtered = filtered[filtered["UTMCampaign"].astype(str).str.contains(utm_contains, case=False, na=False)]
    filtered = filtered[pd.to_numeric(filtered.get("visitDuration", 0), errors="coerce").fillna(0) >= min_duration]

    baseline, _, enough_for_segments = _render_sample_status(filtered, url_contains)

    st.subheader("2. Что смотреть в Вебвизоре")
    records_to_watch = select_records_to_watch(filtered, limit=20)
    watch_cols = [c for c in WATCH_COLUMNS if c in records_to_watch]
    if records_to_watch.empty:
        st.info("Нет неконверсионных визитов для отбора. Проверьте фильтры или ID целей регистрации.")
    else:
        st.dataframe(records_to_watch[watch_cols], use_container_width=True, hide_index=True)
        st.download_button("CSV: записи для просмотра", csv_bytes(records_to_watch[watch_cols]), "webvisor_records_to_watch.csv", "text/csv")

    st.markdown(build_recommendations(baseline, records_to_watch, enough_for_segments))

    st.subheader("3. Сегменты с возможной проблемой")
    problem_segments = pd.DataFrame()
    if enough_for_segments:
        min_visits = max(int(min_segment_visits), int(baseline["total_visits"] * 0.03), 20)
        problem_segments = find_problem_segments(filtered, min_visits=min_visits)
        if problem_segments.empty:
            st.info("Сегменты с заметной просадкой CR относительно baseline не найдены.")
        else:
            display_cols = [c for c in problem_segments.columns if c != "priority_score"]
            st.dataframe(problem_segments[display_cols], use_container_width=True, hide_index=True)
            st.download_button("CSV: проблемные сегменты", csv_bytes(problem_segments[display_cols]), "problem_segments.csv", "text/csv")
    else:
        st.info("Сегментный анализ скрыт, потому что выборка слишком маленькая.")

    with st.expander("Технические детали", expanded=not simple_mode):
        st.caption("Служебные таблицы не нужны для обычного отбора записей и скрыты по умолчанию.")
        stats = aggregate_stats(filtered)
        for title, table in stats.items():
            if not table.empty:
                st.markdown(f"**{title}**")
                st.dataframe(table, use_container_width=True, hide_index=True)
        webvisor_filters = webvisor_filter_table(records_to_watch)
        if not webvisor_filters.empty:
            st.markdown("**Что фильтровать в Вебвизоре**")
            st.dataframe(webvisor_filters, use_container_width=True, hide_index=True)
        hits_analysis = analyze_hits(st.session_state.get("hits", pd.DataFrame()), filtered)
        if "warning" in hits_analysis:
            st.warning(str(hits_analysis["warning"]))
        else:
            for title, table in hits_analysis.items():
                if isinstance(table, pd.DataFrame) and not table.empty:
                    st.markdown(f"**{title}**")
                    st.dataframe(table, use_container_width=True, hide_index=True)
        c1, c2, c3 = st.columns(3)
        with c1:
            st.download_button("CSV: записи", csv_bytes(records_to_watch), "records_export.csv", "text/csv")
        with c2:
            st.download_button("CSV: все визиты", csv_bytes(scored), "all_scored_visits.csv", "text/csv")
        with c3:
            combined = pd.concat({k: v for k, v in stats.items() if not v.empty}, names=["group"]).reset_index(level=0) if stats else pd.DataFrame()
            st.download_button("CSV: агрегаты", csv_bytes(combined), "aggregated_stats.csv", "text/csv")


try:
    main()
except Exception as exc:
    st.error("Критическая ошибка при запуске приложения.")
    st.exception(exc)
