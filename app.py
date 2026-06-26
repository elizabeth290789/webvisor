from __future__ import annotations

import datetime as dt
import re
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

try:
    from metrika_client import MetrikaAPIError, MetrikaLogsClient, get_metrika_token
except Exception as exc:
    st.error("Ошибка при импорте модулей приложения. Проверьте зависимости и конфигурацию деплоя.")
    st.exception(exc)
    st.stop()


DEFAULT_COUNTER_ID = 18477952
DEFAULT_URL_CONTAINS = "/promo/b24messenger/team/"
DEFAULT_GOAL_ID = "2898778"
VISIT_TABLE_COLUMNS = [
    "visitID",
    "clientID",
    "dateTime",
    "startURL",
    "endURL",
    "pageViews",
    "visitDuration",
    "bounce",
    "selected_goal_reached",
    "lastTrafficSource",
    "UTMSource",
    "UTMCampaign",
    "deviceCategory",
    "browser",
    "regionCountry",
    "regionCity",
]
EXIT_LABEL = "Выход / другой URL не найден"


@st.cache_data(show_spinner=False, ttl=3600)
def load_visits(counter_id: int, date_from: str, date_to: str, url_contains: str) -> pd.DataFrame:
    """Load visits for the simple Webvisor tab."""
    return MetrikaLogsClient().fetch_visits(counter_id, date_from, date_to, url_contains)


@st.cache_data(show_spinner=False, ttl=3600)
def load_user_path_report(
    counter_id: int,
    date_from: str,
    date_to: str,
    url_contains: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load visits and full hit chains for visits where selected URL is present in hits."""
    return MetrikaLogsClient().fetch_user_path_report(
        counter_id,
        date_from,
        date_to,
        url_contains,
        mode="Пути после выбранной страницы",
    )


def csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def _normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame.columns = [column.replace("ym:s:", "").replace("ym:pv:", "") for column in frame.columns]
    return frame


def _goal_ids(value: object) -> set[str]:
    if value is None:
        text = ""
    else:
        try:
            text = "" if pd.isna(value) else str(value)
        except (TypeError, ValueError):
            text = str(value)
    text = re.sub(r"\b(\d+)\.0+\b", r"\1", text)
    return set(re.findall(r"\d+", text))


def _selected_goal_ids(goal_id: str) -> list[str]:
    return sorted(_goal_ids(goal_id), key=int)


def _normalize_url(value: object) -> str:
    if value is None:
        return "—"
    try:
        if pd.isna(value):
            return "—"
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text:
        return "—"
    parsed = urlparse(text if "://" in text else f"https://placeholder{text if text.startswith('/') else '/' + text}")
    path = parsed.path or "/"
    return path.rstrip("/") or "/"


def _mark_selected_goals(visits: pd.DataFrame, goal_id: str) -> pd.DataFrame:
    visits = _normalize_columns(visits)
    selected_ids = set(_selected_goal_ids(goal_id))
    if not selected_ids or "goalsID" not in visits:
        visits["selected_goal_reached"] = False
        return visits
    visits["selected_goal_reached"] = visits["goalsID"].map(lambda value: bool(_goal_ids(value).intersection(selected_ids)))
    return visits


def _validate_common_inputs(date_from: dt.date, date_to: dt.date, url_contains: str, token_available: bool) -> bool:
    yesterday = dt.date.today() - dt.timedelta(days=1)
    if not token_available:
        st.error("Сначала настройте YANDEX_METRIKA_TOKEN для подключения к Метрике.")
        return False
    if date_to < date_from:
        st.error("date_to должен быть не раньше date_from.")
        return False
    if date_to > yesterday:
        st.error("date_to не должен быть позже вчерашнего дня.")
        return False
    if not url_contains.strip():
        st.error("Укажите конкретный URL, иначе отчет по большому счетчику будет слишком тяжелым.")
        return False
    return True


def _render_connection_status() -> bool:
    if get_metrika_token():
        st.success("Токен Яндекс Метрики найден. Можно загружать данные через Logs API.")
        return True
    st.warning("YANDEX_METRIKA_TOKEN не задан. Загрузка данных недоступна до настройки секрета.")
    st.info("Добавьте YANDEX_METRIKA_TOKEN в переменные окружения или Streamlit secrets и перезапустите деплой.")
    return False


def _render_webvisor_sidebar() -> tuple[int, dt.date, dt.date, str, str, bool]:
    with st.sidebar:
        st.header("Параметры Вебвизора")
        counter_id = st.number_input("counter_id", min_value=1, value=DEFAULT_COUNTER_ID, step=1, key="webvisor_counter")
        yesterday = dt.date.today() - dt.timedelta(days=1)
        date_from = st.date_input("date_from", yesterday, max_value=yesterday, key="webvisor_from")
        date_to = st.date_input("date_to", yesterday, max_value=yesterday, key="webvisor_to")
        url_contains = st.text_input("URL-фильтр", value=DEFAULT_URL_CONTAINS, key="webvisor_url")
        goal_id = st.text_input("ID целей", value=DEFAULT_GOAL_ID, key="webvisor_goals")
        load = st.button("Загрузить записи", type="primary", key="webvisor_load")
    return int(counter_id), date_from, date_to, url_contains.strip(), goal_id.strip(), load


def _render_webvisor_tab(token_available: bool) -> None:
    st.header("Вебвизор: записи")
    counter_id, date_from, date_to, url_contains, goal_id, load = _render_webvisor_sidebar()

    if load and _validate_common_inputs(date_from, date_to, url_contains, token_available):
        try:
            with st.spinner("Загружаем visits из Logs API..."):
                visits = load_visits(counter_id, str(date_from), str(date_to), url_contains)
                st.session_state["webvisor_visits"] = _mark_selected_goals(visits, goal_id)
        except MetrikaAPIError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error("Не удалось загрузить visits. Проверьте параметры и попробуйте снова.")
            st.exception(exc)

    visits = st.session_state.get("webvisor_visits")
    if visits is None:
        st.info("Введите параметры и нажмите «Загрузить записи».")
        return

    total = len(visits)
    target = int(visits["selected_goal_reached"].sum()) if "selected_goal_reached" in visits else 0
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Визиты", total)
    c2.metric("Визиты с целью", target)
    c3.metric("CR", f"{target / total:.2%}" if total else "0.00%")
    c4.metric("Период", f"{date_from} — {date_to}")

    table = visits[[column for column in VISIT_TABLE_COLUMNS if column in visits]].copy()
    st.subheader("Таблица визитов")
    st.dataframe(table, use_container_width=True, hide_index=True)
    st.download_button("CSV", csv_bytes(table), "webvisor_visits.csv", "text/csv")


def _build_selected_page_paths(visits: pd.DataFrame, hits: pd.DataFrame, goal_id: str, selected_url: str, max_steps: int) -> pd.DataFrame:
    visits = _mark_selected_goals(visits, goal_id)
    hits = _normalize_columns(hits)
    if visits.empty or hits.empty or "visitID" not in visits or "visitID" not in hits or "URL" not in hits:
        return pd.DataFrame()

    selected_path = _normalize_url(selected_url)
    visits = visits.copy()
    hits = hits.copy()
    visits["visitID"] = visits["visitID"].astype(str)
    hits["visitID"] = hits["visitID"].astype(str)
    if "dateTime" in hits:
        hits = hits.sort_values(["visitID", "dateTime"])

    rows: list[dict[str, object]] = []
    visit_lookup = visits.drop_duplicates("visitID").set_index("visitID")
    for visit_id, group in hits.groupby("visitID", sort=False):
        normalized_steps = [_normalize_url(value) for value in group["URL"].tolist()]
        steps: list[str] = []
        for step in normalized_steps:
            if step != "—" and (not steps or steps[-1] != step):
                steps.append(step)
        try:
            first_index = steps.index(selected_path)
        except ValueError:
            continue
        path_steps = steps[first_index : first_index + int(max_steps)]
        meta = visit_lookup.loc[visit_id].to_dict() if visit_id in visit_lookup.index else {}
        rows.append(
            {
                "visitID": visit_id,
                "path_steps": path_steps,
                "path": " → ".join(path_steps),
                "next_url": path_steps[1] if len(path_steps) > 1 else EXIT_LABEL,
                "target_reached": bool(meta.get("selected_goal_reached", False)),
                "dateTime": meta.get("dateTime"),
                "deviceCategory": meta.get("deviceCategory"),
                "UTMSource": meta.get("UTMSource"),
                "UTMCampaign": meta.get("UTMCampaign"),
                "pageViews": meta.get("pageViews"),
                "visitDuration": meta.get("visitDuration"),
                "path_length": len(path_steps),
            }
        )
    return pd.DataFrame(rows)


def _filter_paths(paths: pd.DataFrame, device: str, utm_campaign: str) -> pd.DataFrame:
    result = paths.copy()
    if device != "все" and "deviceCategory" in result:
        result = result[result["deviceCategory"].astype(str).str.lower().eq(device)]
    if utm_campaign and "UTMCampaign" in result:
        result = result[result["UTMCampaign"].astype(str).str.contains(utm_campaign, case=False, na=False)]
    return result


def _render_path_summary(paths: pd.DataFrame) -> None:
    total = len(paths)
    target = int(paths["target_reached"].sum()) if "target_reached" in paths else 0
    duration = pd.to_numeric(paths.get("visitDuration"), errors="coerce")
    pageviews = pd.to_numeric(paths.get("pageViews"), errors="coerce")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Визиты с выбранным URL", total)
    c2.metric("Визиты с выбранной целью", target)
    c3.metric("CR", f"{target / total:.2%}" if total else "0.00%")
    c4.metric("Средняя глубина", f"{pageviews.mean():.1f}" if total else "—")
    c5.metric("Средняя длительность", f"{duration.mean():.0f} сек" if total else "—")


def _next_step_table(paths: pd.DataFrame) -> pd.DataFrame:
    if paths.empty:
        return pd.DataFrame(columns=["next_url", "visits", "share", "target_visits", "CR"])
    table = paths.groupby("next_url", dropna=False).agg(
        visits=("visitID", "nunique"),
        target_visits=("target_reached", "sum"),
    ).reset_index()
    table["share"] = table["visits"] / max(table["visits"].sum(), 1)
    table["CR"] = table["target_visits"] / table["visits"]
    return table[["next_url", "visits", "share", "target_visits", "CR"]].sort_values("visits", ascending=False)


def _top_paths_table(paths: pd.DataFrame) -> pd.DataFrame:
    if paths.empty:
        return pd.DataFrame(columns=["path", "visits", "share", "target_visits", "CR", "avg_duration", "avg_pageviews"])
    source = paths.copy()
    source["pageViews"] = pd.to_numeric(source.get("pageViews"), errors="coerce")
    source["visitDuration"] = pd.to_numeric(source.get("visitDuration"), errors="coerce")
    table = source.groupby("path", dropna=False).agg(
        visits=("visitID", "nunique"),
        target_visits=("target_reached", "sum"),
        avg_duration=("visitDuration", "mean"),
        avg_pageviews=("pageViews", "mean"),
    ).reset_index()
    table["share"] = table["visits"] / max(table["visits"].sum(), 1)
    table["CR"] = table["target_visits"] / table["visits"]
    return table[["path", "visits", "share", "target_visits", "CR", "avg_duration", "avg_pageviews"]].sort_values("visits", ascending=False)


def _watchlist_from_paths(paths: pd.DataFrame) -> pd.DataFrame:
    columns = ["visitID", "dateTime", "deviceCategory", "UTMSource", "UTMCampaign", "path", "target_reached", "reason_to_watch"]
    if paths.empty:
        return pd.DataFrame(columns=columns)
    source = paths.copy()
    groups = [
        (source[source["next_url"].eq(EXIT_LABEL)], "выбранная страница → выход"),
        (source[source["path"].str.contains("prices|pricing", case=False, na=False)], "выбранная страница → prices"),
        (source[source["path"].str.contains("register|create|auth", case=False, na=False)], "выбранная страница → register/create/auth"),
        (source[source["target_reached"]], "визит с выбранной целью"),
    ]
    rows = []
    seen: set[str] = set()
    for frame, reason in groups:
        for _, row in frame.head(5).iterrows():
            visit_id = str(row.get("visitID"))
            if visit_id in seen:
                continue
            item = row.to_dict()
            item["reason_to_watch"] = reason
            rows.append(item)
            seen.add(visit_id)
            if len(rows) >= 20:
                break
        if len(rows) >= 20:
            break
    return pd.DataFrame(rows)[columns] if rows else pd.DataFrame(columns=columns)


def _render_user_paths_tab(token_available: bool) -> None:
    st.header("Пути пользователей")
    st.info("Для отчета по путям нужны hits. Выберите один завершенный день, точный URL и нажмите загрузить.")
    yesterday = dt.date.today() - dt.timedelta(days=1)
    c1, c2, c3 = st.columns(3)
    counter_id = c1.number_input("counter_id", min_value=1, value=DEFAULT_COUNTER_ID, step=1, key="paths_counter")
    date_from = c2.date_input("date_from", yesterday, max_value=yesterday, key="paths_from")
    date_to = c3.date_input("date_to", yesterday, max_value=yesterday, key="paths_to")
    selected_url = st.text_input("Выбранный URL", value=DEFAULT_URL_CONTAINS, key="paths_url")
    c4, c5, c6 = st.columns(3)
    goal_id = c4.text_input("ID целей", value=DEFAULT_GOAL_ID, key="paths_goal")
    max_steps = c5.selectbox("Максимум шагов", [3], key="paths_steps")
    device = c6.selectbox("Устройство", ["все", "desktop", "mobile"], key="paths_device")
    utm_campaign = st.text_input("UTM campaign содержит", value="", key="paths_utm_campaign")

    if st.button("Загрузить", type="primary", key="paths_load"):
        if _validate_common_inputs(date_from, date_to, selected_url, token_available):
            with st.spinner("Загружаем visits и hits..."):
                visits, hits = load_user_path_report(int(counter_id), str(date_from), str(date_to), selected_url.strip())
                st.session_state["paths_visits"] = visits
                st.session_state["paths_hits"] = hits
                st.session_state["paths_params"] = (goal_id.strip(), selected_url.strip(), int(max_steps))

    visits = st.session_state.get("paths_visits")
    hits = st.session_state.get("paths_hits", pd.DataFrame())
    if visits is None:
        return
    if hits.empty:
        st.error("Для отчета по путям нужны hits. Выберите один завершенный день, точный URL и нажмите загрузить.")
        return

    saved_goal_id, saved_url, saved_max_steps = st.session_state.get("paths_params", (goal_id, selected_url, max_steps))
    paths = _build_selected_page_paths(visits, hits, saved_goal_id, saved_url, saved_max_steps)
    paths = _filter_paths(paths, device, utm_campaign.strip())

    if paths.empty:
        st.warning("Выбранный URL не найден в hits после применения фильтров.")
        return
    if len(paths) < 10:
        st.warning("Мало данных по выбранной странице. Расширьте дату или проверьте URL-фильтр.")

    st.subheader("Сводка")
    _render_path_summary(paths)

    st.subheader("Следующий шаг после выбранной страницы")
    st.dataframe(_next_step_table(paths), use_container_width=True, hide_index=True)

    st.subheader("Топ путей")
    st.dataframe(_top_paths_table(paths), use_container_width=True, hide_index=True)

    st.subheader("Записи для просмотра в Вебвизоре")
    st.dataframe(_watchlist_from_paths(paths), use_container_width=True, hide_index=True)

    with st.expander("Отладка", expanded=False):
        st.caption("Сырые данные показаны только для диагностики.")
        st.subheader("visits")
        st.dataframe(_normalize_columns(visits).head(100), use_container_width=True, hide_index=True)
        st.subheader("hits")
        st.dataframe(_normalize_columns(hits).head(100), use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(page_title="Маркетинговый отчет Метрики", layout="wide")
    st.title("Маркетинговый отчет Метрики")
    st.caption("Стабильный MVP: записи Вебвизора и простой отчет по путям пользователей.")

    token_available = _render_connection_status()
    webvisor_tab, paths_tab = st.tabs(["Вебвизор: записи", "Пути пользователей"])

    with webvisor_tab:
        _render_webvisor_tab(token_available)

    with paths_tab:
        try:
            _render_user_paths_tab(token_available)
        except Exception as exc:
            st.error("Ошибка в отчете по путям пользователей")
            st.exception(exc)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        st.error("Критическая ошибка при запуске приложения.")
        st.exception(exc)
