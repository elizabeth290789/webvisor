from __future__ import annotations

import datetime as dt

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
    "goal_reached",
    "lastTrafficSource",
    "UTMSource",
    "UTMCampaign",
    "deviceCategory",
    "browser",
    "regionCountry",
    "regionCity",
]


@st.cache_data(show_spinner=False, ttl=3600)
def load_visits(counter_id: int, date_from: str, date_to: str, url_contains: str) -> pd.DataFrame:
    """Load only visits from Yandex Metrica Logs API for the stable app flow."""
    return MetrikaLogsClient().fetch_visits(counter_id, date_from, date_to, url_contains)


def csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def _normalize_visit_columns(visits: pd.DataFrame) -> pd.DataFrame:
    visits = visits.copy()
    visits.columns = [column.replace("ym:s:", "") for column in visits.columns]
    return visits


def _goal_ids(value: object) -> set[str]:
    text = "" if pd.isna(value) else str(value)
    for separator in [",", ";", "|"]:
        text = text.replace(separator, " ")
    return {part.strip() for part in text.split() if part.strip()}


def _mark_goal(visits: pd.DataFrame, goal_id: str) -> pd.DataFrame:
    visits = _normalize_visit_columns(visits)
    clean_goal_id = goal_id.strip()
    if not clean_goal_id or "goalsID" not in visits:
        return visits
    visits["goal_reached"] = visits["goalsID"].map(lambda value: clean_goal_id in _goal_ids(value))
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


def _render_sidebar() -> tuple[int, dt.date, dt.date, str, str, bool]:
    with st.sidebar:
        st.header("Параметры Метрики")
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
        goal_id = st.text_input("ID цели", value="2898778", help="Один ID цели для пометки goal_reached в таблице.")
        load = st.button("Загрузить visits", type="primary")
    return int(counter_id), date_from, date_to, url_contains.strip(), goal_id.strip(), load


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


def _render_visits_table(visits: pd.DataFrame) -> None:
    st.subheader("Таблица визитов")
    if visits.empty:
        st.info("Visits не найдены для выбранных параметров.")
        return

    cols = [column for column in VISIT_TABLE_COLUMNS if column in visits]
    st.dataframe(visits[cols] if cols else visits, use_container_width=True, hide_index=True)
    st.download_button("CSV-экспорт visits", csv_bytes(visits), "metrika_visits.csv", "text/csv")


def main() -> None:
    st.set_page_config(page_title="Metrika Visits", layout="wide")
    st.title("Metrika Visits")
    st.caption("Минимальная стабильная версия: подключение к Метрике, параметры загрузки, visits, таблица и CSV-экспорт.")

    token_available = _render_connection_status()
    counter_id, date_from, date_to, url_contains, goal_id, load = _render_sidebar()

    if load and _validate_inputs(date_from, date_to, url_contains, token_available):
        try:
            with st.spinner("Загружаем visits из Logs API..."):
                visits = load_visits(counter_id, str(date_from), str(date_to), url_contains)
                st.session_state["visits"] = _mark_goal(visits, goal_id)
        except MetrikaAPIError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error("Не удалось загрузить visits. Приложение продолжает работать, проверьте параметры и попробуйте снова.")
            st.exception(exc)

    visits = st.session_state.get("visits")
    if visits is None:
        st.info("Введите counter_id, date_from, date_to, URL-фильтр и ID цели, затем нажмите «Загрузить visits».")
        return

    _render_visits_table(visits)

    if EXPERIMENTAL_MODE:
        st.divider()
        st.info("Экспериментальные функции включены, но не участвуют в основном стабильном потоке.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        st.error("Критическая ошибка при запуске приложения.")
        st.exception(exc)
