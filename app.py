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
    from scoring import aggregate_stats, analyze_hits, score_sessions
    from summarizer import build_summary
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
        counter_id = st.number_input("counter_id", min_value=1, value=18477952, step=1)
        today = dt.date.today()
        yesterday = today - dt.timedelta(days=1)
        date_from = st.date_input("date_from", yesterday, max_value=yesterday)
        date_to = st.date_input("date_to", yesterday, max_value=yesterday)
        st.caption("Для больших счетчиков лучше выбирать один завершенный день. Текущий день может быть недоступен или неполным в Logs API.")
        if date_to > date_from and (date_to - date_from).days + 1 > 1:
            st.warning("Вы выбрали период больше 1 дня. Для большого счетчика выгрузка может занять много времени. Рекомендуем начать с одного дня и URL-фильтра.")
        load_hits = st.checkbox(
            "Загружать hits",
            value=False,
            help="Выключено по умолчанию для быстрой выгрузки больших счетчиков.",
        )
        url_search_label = st.selectbox(
            "Где искать URL",
            list(URL_SEARCH_OPTIONS),
            index=list(URL_SEARCH_OPTIONS).index(DEFAULT_URL_SEARCH_LABEL),
        )
        url_search_scope = URL_SEARCH_OPTIONS[url_search_label]
        url_field_label = "URL содержит *" if load_hits else "URL входа или выхода содержит *"
        url_contains_load = st.text_input(
            url_field_label,
            value="chat",
            help="Обязательный фильтр: применяется в Logs API до скачивания данных.",
        )
        st.caption(URL_WITHOUT_HITS_HELP)
        if url_search_scope == "any_hit" and not load_hits:
            st.warning("Поиск по любому URL внутри визита доступен только при включенной загрузке hits.")
        reg_goals = st.text_input(
            "ID целей регистрации через запятую",
            value="2898778",
            help="Если оставить пустым, любая сессия считается без регистрации для правил регистрации.",
        )
        if demo_mode:
            st.info("Сейчас приложение работает на демо-данных. Поля ниже нужны для реальной Метрики после добавления токена.")
            load_label = "Обновить демо-данные"
        else:
            load_label = "Загрузить и посчитать"
        load = st.button(load_label, type="primary")

    if load and demo_mode:
        st.session_state["scored"] = load_demo_score([x.strip() for x in reg_goals.split(",") if x.strip()] or ["1001"])
        st.session_state["hits"] = build_demo_visits_and_hits()[1]
        st.session_state["url_search_scope"] = url_search_scope
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
                    visits_df, hits_df = load_data(
                        int(counter_id),
                        str(date_from),
                        str(date_to),
                        url_contains_load.strip(),
                        load_hits,
                        url_search_scope,
                    )
                    st.session_state["scored"] = score_sessions(
                        visits_df,
                        hits_df,
                        [x.strip() for x in reg_goals.split(",") if x.strip()],
                    )
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

    st.subheader("Фильтры")
    col1, col2, col3 = st.columns(3)
    with col1:
        device_filter = st.selectbox("Устройство", ["Все", "mobile", "desktop", "tablet"])
        no_reg_only = st.checkbox("Только без регистрации", value=True)
    with col2:
        filter_hits_loaded = not st.session_state.get("hits", pd.DataFrame()).empty
        current_scope = st.session_state.get("url_search_scope", URL_SEARCH_OPTIONS[DEFAULT_URL_SEARCH_LABEL])
        current_label = next(
            (label for label, scope in URL_SEARCH_OPTIONS.items() if scope == current_scope),
            DEFAULT_URL_SEARCH_LABEL,
        )
        filter_url_search_label = st.selectbox(
            "Где искать URL",
            list(URL_SEARCH_OPTIONS),
            index=list(URL_SEARCH_OPTIONS).index(current_label),
            key="filter_url_search_label",
        )
        filter_url_search_scope = URL_SEARCH_OPTIONS[filter_url_search_label]
        filter_url_label = "URL содержит" if filter_hits_loaded else "URL входа или выхода содержит"
        url_contains = st.text_input(filter_url_label, value=st.session_state.get("url_contains_load", ""))
        st.caption(URL_WITHOUT_HITS_HELP)
        if filter_url_search_scope == "any_hit" and not filter_hits_loaded:
            st.warning("Поиск по любому URL внутри визита доступен только при включенной загрузке hits.")
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
        filtered = filter_by_url(
            filtered,
            st.session_state.get("hits", pd.DataFrame()),
            url_contains,
            filter_url_search_scope,
        )
    if utm_contains and "UTMCampaign" in filtered:
        filtered = filtered[filtered["UTMCampaign"].astype(str).str.contains(utm_contains, case=False, na=False)]
    filtered = filtered[(filtered["score"] >= min_score) & (pd.to_numeric(filtered.get("visitDuration", 0), errors="coerce").fillna(0) >= min_duration)]

    top_sessions = filtered.sort_values("score", ascending=False).head(20)

    st.subheader("Короткий вывод")
    st.markdown(build_summary(top_sessions, filtered))

    st.subheader("Группировки")
    stats = aggregate_stats(filtered)
    for title, table in stats.items():
        with st.expander(title, expanded=title in {"По устройствам", "deviceCategory × UTMCampaign"}):
            st.dataframe(table, use_container_width=True, hide_index=True)

    st.subheader("Сессии для просмотра в Вебвизоре")
    show_cols = [c for c in [
        "priority_rank", "score", "visitID", "dateTime", "deviceCategory", "startURL", "endURL",
        "visitDuration", "pageViews", "goalsID", "UTMSource", "UTMCampaign", "reason_to_watch"
    ] if c in top_sessions]
    st.dataframe(top_sessions[show_cols], use_container_width=True, hide_index=True)
    st.download_button("CSV: top-20 visitID для просмотра", csv_bytes(top_sessions[show_cols]), "webvisor_top_20_sessions.csv", "text/csv")

    st.subheader("Анализ hits")
    hits_analysis = analyze_hits(st.session_state.get("hits", pd.DataFrame()))
    if "warning" in hits_analysis:
        st.warning(str(hits_analysis["warning"]))
    else:
        c_hits1, c_hits2 = st.columns(2)
        with c_hits1:
            st.caption("Были ли просмотры нескольких URL")
            st.dataframe(hits_analysis["per_visit"], use_container_width=True, hide_index=True)
            st.caption("Какие URL чаще встречаются в цепочке")
            st.dataframe(hits_analysis["url_freq"], use_container_width=True, hide_index=True)
        with c_hits2:
            st.caption("Где пользователи уходят")
            st.dataframe(hits_analysis["exits"], use_container_width=True, hide_index=True)
            st.caption("Цели/события в hits")
            event_freq = hits_analysis.get("event_freq", pd.DataFrame())
            if isinstance(event_freq, pd.DataFrame) and not event_freq.empty:
                st.dataframe(event_freq, use_container_width=True, hide_index=True)
            else:
                st.info("В hits не найдены цели или события в доступных полях.")

    st.subheader("Экспорт")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.download_button("CSV: top проблемных", csv_bytes(filtered.sort_values("score", ascending=False).head(200)), "top_problem_visits.csv", "text/csv")
    with c2:
        st.download_button("CSV: все визиты со score", csv_bytes(scored), "all_scored_visits.csv", "text/csv")
    with c3:
        combined = pd.concat({k: v for k, v in stats.items() if not v.empty}, names=["group"]).reset_index(level=0) if stats else pd.DataFrame()
        st.download_button("CSV: агрегаты", csv_bytes(combined), "aggregated_stats.csv", "text/csv")


try:
    main()
except Exception as exc:
    st.error("Критическая ошибка при запуске приложения.")
    st.exception(exc)
