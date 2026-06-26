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


EXPERIMENTAL_MODE = False
DEFAULT_COUNTER_ID = 18477952
DEFAULT_URL_CONTAINS = "chat"
VISIT_TABLE_COLUMNS = [
    "visitID",
    "clientID",
    "dateTime",
    "startURL",
    "endURL",
    "pageViews",
    "visitDuration",
    "bounce",
    "goalsID",
    "selected_goal_reached",
    "lastTrafficSource",
    "UTMSource",
    "UTMCampaign",
    "deviceCategory",
    "browser",
    "regionCountry",
    "regionCity",
]


@st.cache_data(show_spinner=False, ttl=3600)
def load_visits_and_hits(
    counter_id: int,
    date_from: str,
    date_to: str,
    url_contains: str,
    load_hits: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load visits and optionally hits from Yandex Metrica Logs API."""
    return MetrikaLogsClient().fetch_visits_and_hits(counter_id, date_from, date_to, url_contains, load_hits)


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


def _path(value: object) -> str:
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


def _path_chain(paths: pd.Series) -> str:
    normalized = [_path(value) for value in paths if _path(value) != "—"]
    deduped: list[str] = []
    for path in normalized:
        if not deduped or deduped[-1] != path:
            deduped.append(path)
    return " → ".join(deduped) if deduped else "—"


def _mark_selected_goals(visits: pd.DataFrame, goal_id: str) -> pd.DataFrame:
    visits = _normalize_columns(visits)
    selected_ids = set(_selected_goal_ids(goal_id))
    if not selected_ids or "goalsID" not in visits:
        visits["selected_goal_reached"] = False
        return visits
    visits["selected_goal_reached"] = visits["goalsID"].map(lambda value: bool(_goal_ids(value).intersection(selected_ids)))
    return visits


def _render_connection_status() -> bool:
    if get_metrika_token():
        st.success("Токен Яндекс Метрики найден. Можно загружать visits через Logs API.")
        return True

    st.warning(
        "YANDEX_METRIKA_TOKEN не задан. Приложение открылось стабильно, но загрузка visits недоступна до настройки секрета."
    )
    st.info("Добавьте YANDEX_METRIKA_TOKEN в переменные окружения или Streamlit secrets и перезапустите деплой.")
    return False


def _render_sidebar() -> tuple[int, dt.date, dt.date, str, str, bool, bool]:
    with st.sidebar:
        st.header("Параметры отчета")
        counter_id = st.number_input("counter_id", min_value=1, value=DEFAULT_COUNTER_ID, step=1)
        today = dt.date.today()
        yesterday = today - dt.timedelta(days=1)
        date_from = st.date_input("date_from", yesterday, max_value=yesterday)
        date_to = st.date_input("date_to", yesterday, max_value=yesterday)
        st.caption("Выбирайте завершенные даты: текущий день в Logs API может быть неполным или недоступным.")
        url_contains = st.text_input(
            "URL-фильтр",
            value=DEFAULT_URL_CONTAINS,
            help="Обязательный фильтр по startURL или endURL, чтобы не выгружать весь счетчик.",
        )
        goal_id = st.text_input("ID целей", value="2898778", help="Один или несколько ID целей через запятую.")
        load_hits = st.checkbox("Загрузить hits для полного отчета по путям", value=False)
        load = st.button("Загрузить отчет", type="primary")
    return int(counter_id), date_from, date_to, url_contains.strip(), goal_id.strip(), load_hits, load


def _validate_inputs(date_from: dt.date, date_to: dt.date, url_contains: str, token_available: bool) -> bool:
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
    if not url_contains:
        st.error("Заполните URL-фильтр, чтобы не выгружать весь счетчик.")
        return False
    return True


def _goal_breakdown(visits: pd.DataFrame, selected_ids: list[str]) -> pd.DataFrame:
    rows = []
    for goal in selected_ids:
        count = int(visits["goalsID"].map(lambda value: goal in _goal_ids(value)).sum()) if "goalsID" in visits else 0
        rows.append({"goalID": goal, "visits": count})
    return pd.DataFrame(rows)


def _render_summary(visits: pd.DataFrame, selected_ids: list[str], url_contains: str, date_from: dt.date, date_to: dt.date) -> None:
    st.header("1. Сводка")
    total = len(visits)
    target = int(visits["selected_goal_reached"].sum()) if "selected_goal_reached" in visits else 0
    cr = target / total if total else 0
    found = target > 0
    st.success("Цели найдены: да") if found else st.warning("Цели не найдены — проверьте ID цели, дату, счетчик и URL-фильтр.")
    c1, c2, c3 = st.columns(3)
    c1.metric("URL-фильтр", url_contains)
    c2.metric("Период", f"{date_from} — {date_to}")
    c3.metric("Всего визитов", total)
    c4, c5 = st.columns(2)
    c4.metric("Визитов с выбранными целями", target)
    c5.metric("CR в выбранные цели", f"{cr:.2%}")
    st.write("Разбивка только по выбранным ID целей")
    st.dataframe(_goal_breakdown(visits, selected_ids), use_container_width=True, hide_index=True)


def _paths_from_hits(visits: pd.DataFrame, hits: pd.DataFrame) -> pd.DataFrame:
    hits = _normalize_columns(hits)
    if hits.empty or "visitID" not in hits or "URL" not in hits:
        return pd.DataFrame()
    if "dateTime" in hits:
        hits = hits.sort_values(["visitID", "dateTime"])
    chains = hits.groupby("visitID")["URL"].apply(_path_chain).reset_index(name="path")
    merged = chains.merge(visits[["visitID", "selected_goal_reached"]], on="visitID", how="left")
    exits = visits[["visitID", "endURL"]].copy()
    exits["exit_path"] = exits["endURL"].map(_path)
    merged = merged.merge(exits[["visitID", "exit_path"]], on="visitID", how="left")
    merged["is_exit_path"] = merged.apply(lambda row: str(row.get("path", "")).endswith(str(row.get("exit_path", ""))), axis=1)
    return merged


def _render_where_users_go(visits: pd.DataFrame, hits: pd.DataFrame) -> None:
    st.header("2. Куда уходят пользователи")
    if hits.empty:
        st.info("Без hits видим только вход и выход, не полный путь.")
        flows = visits.copy()
        flows["path"] = flows["startURL"].map(_path) + " → " + flows["endURL"].map(_path)
        table = flows.groupby("path", dropna=False).agg(visits=("visitID", "nunique"), target_goal_visits=("selected_goal_reached", "sum")).reset_index()
        table["CR"] = table["target_goal_visits"] / table["visits"]
        st.dataframe(table.sort_values("visits", ascending=False).head(10), use_container_width=True, hide_index=True)
        st.warning("Для полноценного отчета по путям включите hits.")
        return

    paths = _paths_from_hits(visits, hits)
    if paths.empty:
        st.info("Hits загружены, но не удалось собрать цепочки URL по visitID.")
        return
    table = paths.groupby("path", dropna=False).agg(
        visits=("visitID", "nunique"),
        target_goal_visits=("selected_goal_reached", "sum"),
        exits=("is_exit_path", "sum"),
    ).reset_index()
    table["CR"] = table["target_goal_visits"] / table["visits"]
    table["exit_rate"] = table["exits"] / table["visits"]
    st.dataframe(table.drop(columns="exits").sort_values("visits", ascending=False).head(10), use_container_width=True, hide_index=True)


def _converter_table(visits: pd.DataFrame, converted: bool, hits: pd.DataFrame) -> pd.DataFrame:
    subset = visits[visits["selected_goal_reached"] == converted].copy()
    if subset.empty:
        return pd.DataFrame(columns=["path", "visits", "share", "avg_pageViews", "avg_visitDuration"])
    paths = _paths_from_hits(visits, hits) if not hits.empty else pd.DataFrame()
    if not paths.empty:
        subset = subset.merge(paths[["visitID", "path"]], on="visitID", how="left")
    else:
        subset["path"] = subset["startURL"].map(_path) + " → " + subset["endURL"].map(_path)
    subset["pageViews"] = pd.to_numeric(subset.get("pageViews"), errors="coerce")
    subset["visitDuration"] = pd.to_numeric(subset.get("visitDuration"), errors="coerce")
    table = subset.groupby("path", dropna=False).agg(
        visits=("visitID", "nunique"),
        avg_pageViews=("pageViews", "mean"),
        avg_visitDuration=("visitDuration", "mean"),
    ).reset_index()
    table["share"] = table["visits"] / len(subset)
    return table[["path", "visits", "share", "avg_pageViews", "avg_visitDuration"]].sort_values("visits", ascending=False).head(10)


def _render_converters(visits: pd.DataFrame, hits: pd.DataFrame) -> None:
    st.header("3. Конвертеры vs неконвертеры")
    left, right = st.columns(2)
    left.subheader("Топ путей с выбранной целью")
    left.dataframe(_converter_table(visits, True, hits), use_container_width=True, hide_index=True)
    right.subheader("Топ путей без выбранной цели")
    right.dataframe(_converter_table(visits, False, hits), use_container_width=True, hide_index=True)


def _watch_reason(row: pd.Series) -> tuple[str, str]:
    path = str(row.get("path", ""))
    if "prices" in path or "pricing" in path:
        return "landing → prices → выход", "Пользователь ушел на цены и не зарегистрировался — проверить, не возникает ли барьер цены до CTA."
    if "auth/create" in path or "create/auth" in path or "signup" in path or "register" in path:
        return "landing → auth/create → выход без цели", "Пользователь дошел до create/auth, но цель не сработала — проверить, не было ли проблемы на шаге регистрации."
    if row.get("pageViews", 0) >= 4 or row.get("visitDuration", 0) >= 120:
        return "длинный визит без цели", "Пользователь долго изучал сайт, но не достиг цели — проверить, где теряется следующий шаг к CTA."
    return "landing → выход", "Пользователь ушел после входа без цели — проверить релевантность первого экрана и понятность CTA."


def _render_webvisor_watchlist(visits: pd.DataFrame, hits: pd.DataFrame) -> None:
    st.header("4. Что смотреть в Вебвизоре")
    candidates = visits[~visits["selected_goal_reached"]].copy()
    candidates["pageViews"] = pd.to_numeric(candidates.get("pageViews"), errors="coerce").fillna(0)
    candidates["visitDuration"] = pd.to_numeric(candidates.get("visitDuration"), errors="coerce").fillna(0)
    if candidates.empty:
        st.info("Нет визитов без выбранной цели для просмотра.")
        return
    if not hits.empty:
        paths = _paths_from_hits(visits, hits)
        candidates = candidates.merge(paths[["visitID", "path"]], on="visitID", how="left")
    if "path" not in candidates:
        candidates["path"] = candidates["startURL"].map(_path) + " → " + candidates["endURL"].map(_path)
    candidates[["reason_group", "reason_to_watch"]] = candidates.apply(lambda row: pd.Series(_watch_reason(row)), axis=1)
    candidates["priority"] = candidates["reason_group"].map({
        "landing → выход": 1,
        "landing → prices → выход": 2,
        "landing → auth/create → выход без цели": 3,
        "длинный визит без цели": 4,
    }).fillna(9)
    candidates = candidates.sort_values(["priority", "visitDuration", "pageViews"], ascending=[True, False, False])
    cols = ["reason_group", "visitID", "dateTime", "deviceCategory", "UTMSource", "UTMCampaign", "path", "reason_to_watch"]
    st.dataframe(candidates[[column for column in cols if column in candidates]].head(20), use_container_width=True, hide_index=True)


def _render_debug(visits: pd.DataFrame, hits: pd.DataFrame, selected_ids: list[str]) -> None:
    with st.expander("Отладка", expanded=False):
        st.subheader("Техническая диагностика целей")
        if "goalsID" in visits:
            parsed_goals = visits["goalsID"].map(_goal_ids)
            goal_counts = parsed_goals.explode().dropna().value_counts().rename_axis("goalID").reset_index(name="visits")
            st.write(f"Все уникальные goalsID в выборке: **{', '.join(goal_counts['goalID'].astype(str).tolist()) or '—'}**")
            debug_goals = visits[[column for column in ["visitID", "goalsID"] if column in visits]].copy()
            debug_goals["parsed_goals"] = parsed_goals.map(lambda ids: ", ".join(sorted(ids, key=int)))
            st.dataframe(debug_goals.head(100), use_container_width=True, hide_index=True)
            st.dataframe(goal_counts, use_container_width=True, hide_index=True)
        st.subheader("Примеры сырых визитов")
        st.dataframe(visits.head(50), use_container_width=True, hide_index=True)
        st.download_button("CSV-экспорт visits", csv_bytes(visits), "metrika_visits.csv", "text/csv")
        if not hits.empty:
            st.subheader("Примеры сырых hits")
            st.dataframe(_normalize_columns(hits).head(50), use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(page_title="Маркетинговый отчет Метрики", layout="wide")
    st.title("Маркетинговый отчет по URL и целям")
    st.caption("Deterministic-отчет по выбранному URL-фильтру и выбранным ID целей. Технические данные скрыты в отладке.")

    token_available = _render_connection_status()
    counter_id, date_from, date_to, url_contains, goal_id, load_hits, load = _render_sidebar()
    selected_ids = _selected_goal_ids(goal_id)

    if load and _validate_inputs(date_from, date_to, url_contains, token_available):
        try:
            with st.spinner("Загружаем данные из Logs API..."):
                visits, hits = load_visits_and_hits(counter_id, str(date_from), str(date_to), url_contains, load_hits)
                st.session_state["visits"] = _mark_selected_goals(visits, goal_id)
                st.session_state["hits"] = _normalize_columns(hits)
                st.session_state["report_params"] = (url_contains, date_from, date_to, goal_id, load_hits)
        except MetrikaAPIError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error("Не удалось загрузить данные. Приложение продолжает работать, проверьте параметры и попробуйте снова.")
            st.exception(exc)

    visits = st.session_state.get("visits")
    if visits is None:
        st.info("Введите counter_id, период, URL-фильтр и ID целей, затем нажмите «Загрузить отчет».")
        return

    hits = st.session_state.get("hits", pd.DataFrame())
    _render_summary(visits, selected_ids, url_contains, date_from, date_to)
    _render_where_users_go(visits, hits)
    if hits.empty:
        _render_webvisor_watchlist(visits, hits)
    else:
        _render_converters(visits, hits)
        _render_webvisor_watchlist(visits, hits)
    _render_debug(visits, hits, selected_ids)

    if EXPERIMENTAL_MODE:
        st.divider()
        st.info("Экспериментальные функции включены, но не участвуют в основном стабильном потоке.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        st.error("Критическая ошибка при запуске приложения.")
        st.exception(exc)
