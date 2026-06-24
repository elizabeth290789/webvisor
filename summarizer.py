"""AI and rule-based summaries for problematic sessions."""
from __future__ import annotations

import os
import pandas as pd


def build_summary(scored: pd.DataFrame) -> str:
    top = scored.sort_values("score", ascending=False).head(50).copy()
    if top.empty:
        return "Нет визитов для анализа."
    if os.getenv("OPENAI_API_KEY"):
        try:
            return _openai_summary(top)
        except Exception as exc:  # show fallback instead of breaking the app
            return f"OpenAI API недоступен ({exc}).\n\n" + _rule_summary(top)
    return _rule_summary(top)


def _openai_summary(top: pd.DataFrame) -> str:
    from openai import OpenAI

    cols = [c for c in ["score", "visitID", "deviceCategory", "startURL", "endURL", "visitDuration", "pageViews", "UTMCampaign", "UTMSource", "reasons"] if c in top]
    sample = top[cols].to_csv(index=False)
    prompt = (
        "Проанализируй top-50 проблемных визитов Яндекс Метрики. "
        "Кратко перечисли повторяющиеся проблемы, страницы потерь, устройства, гипотезы и visitID для первоочередного просмотра.\n\n"
        + sample
    )
    client = OpenAI()
    response = client.responses.create(model="gpt-4.1-mini", input=prompt)
    return response.output_text


def _rule_summary(top: pd.DataFrame) -> str:
    device = top.get("deviceCategory", pd.Series(dtype=str)).value_counts().head(5)
    urls = top.get("startURL", pd.Series(dtype=str)).astype(str).value_counts().head(5)
    reasons = top.get("reasons", pd.Series(dtype=str)).str.get_dummies(sep="; ").sum().sort_values(ascending=False).head(8)
    visit_ids = top.get("visitID", pd.Series(dtype=str)).head(15).astype(str).tolist()
    return "\n".join([
        "## Rule-based выводы",
        "### Повторяющиеся проблемы",
        *(f"- {k}: {v} визитов" for k, v in reasons.items()),
        "### Страницы с потерями",
        *(f"- {k}: {v} визитов" for k, v in urls.items()),
        "### Устройства",
        *(f"- {k}: {v} визитов" for k, v in device.items()),
        "### Гипотезы для проверки",
        "- Проверить видимость и текст CTA на страницах с максимальным score.",
        "- Проверить мобильные формы, ошибки валидации и закрытие модальных окон.",
        "- Сравнить UTM-кампании с высоким score и без регистрации.",
        "### VisitID для первоочередного просмотра",
        "- " + ", ".join(visit_ids),
    ])
