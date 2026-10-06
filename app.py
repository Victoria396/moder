from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd
import streamlit as st


st.set_page_config(page_title="Модератор комментариев", page_icon="📝", layout="wide")

REQUIRED_COLUMNS = ["ID", "Комментарий", "Объект", "Кем создан", "Создан"]
NEGATIVE_PHRASES = [
    "слишком сложно", "слишком сложный", "слишком трудный", "не смог ответить",
    "непросто", "тяжело", "трудный", "трудно", "мало вопросов", "маловато",
    "мало заданий", "жаль что короткий", "жаль, что короткий", "хотелось бы побольше вопросов",
    "не зачли правильный ответ", "не засчитали правильный ответ", "начислено меньше баллов",
    "не начислили баллы", "не работает", "не грузится", "не загружается", "кнопка не нажимается",
    "изображения не грузятся", "подсветка не работала", "приложение тормозит", "висит",
    "не открывается", "узкие часы работы", "короткий режим работы", "короткий рабочий день",
    "сложно попасть", "попасть довольно сложно", "не удалось войти", "не удалось попасть",
    "нет пандуса", "нет пандусов", "припарковаться негде", "нет парковки", "дороговато",
    "дорого", "цена кусается", "дорогущие", "скучно", "ничего особенного", "не впечатлило",
    "ожидал большего", "ожидала большего", "спорный памятник", "ничем не примечательная",
    "не понравился", "не понравилось", "не очень понравилось", "двоякое впечатление",
    "на любителя", "намного круче", "требует реставрации", "в плачевном состоянии",
    "уставшие кресла", "не все ответы верные", "не совпадает с поиском", "не соглашусь с вопросом",
    "не упомянуты другие", "почему только",
]
FILLERS = {"", "не указано", "не указано.", "—", "–", "-", "...", "…", "нет", "n/a", "- - -"}
POSITIVE_MARKERS = ["интересно", "понравилось", "познавательно", "узнал", "узнала", "рекомендую", "советую", "увлекательно", "здорово", "отлично"]
URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
WORD_RE = re.compile(r"[а-яёa-z]{2,}", re.IGNORECASE)


@dataclass
class Finding:
    category: str
    reason: str


def parse_json_cell(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return {}
    try:
        result = json.loads(str(value))
        return result if isinstance(result, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def extract_id(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return "" if text.casefold() in {"", "nan", "none", "null"} else text


def positive_context(text: str, phrase: str) -> bool:
    """Avoid false positives for the prompt's explicit 'difficult but enjoyable' cases."""
    low = text.casefold()
    match_at = low.find(phrase.casefold())
    if match_at < 0:
        return False
    tail = low[match_at:]
    # A positive clause after a concession usually changes the meaning of a difficulty complaint.
    for joiner in ("но", "зато", "однако"):
        pos = tail.find(joiner)
        if pos >= 0 and any(marker in tail[pos:] for marker in POSITIVE_MARKERS):
            return True
    if phrase in {"тяжело", "трудно", "трудный", "непросто", "слишком сложно", "слишком трудный"}:
        return any(marker in low for marker in POSITIVE_MARKERS)
    return False


def check_comment(comment: Any, forbidden_terms: list[str]) -> list[Finding]:
    text = "" if comment is None or pd.isna(comment) else str(comment).strip()
    low = text.casefold()
    findings: list[Finding] = []
    if low in FILLERS:
        return [Finding("Пустой комментарий", "Пустой текст или бессодержательный заполнитель")]

    if URL_RE.search(text):
        findings.append(Finding("Ссылка / спам", "В тексте обнаружена ссылка"))

    for term in forbidden_terms:
        term = term.strip()
        if term and re.search(rf"(?<!\w){re.escape(term)}(?!\w)", low, re.IGNORECASE):
            findings.append(Finding("Запрещённые слова", f"Найдено слово/выражение: «{term}»"))

    matched = next((phrase for phrase in NEGATIVE_PHRASES if phrase in low), None)
    if matched and not positive_context(text, matched):
        findings.append(Finding("Негатив / жалоба", f"Негативная формулировка: «{matched}»"))

    words = WORD_RE.findall(low)
    if len(words) >= 5:
        unique_ratio = len(set(words)) / len(words)
        long_tokens = [w for w in words if len(w) >= 8]
        if unique_ratio > 0.9 and len(long_tokens) >= 3 and not any(ch in text for ch in ".,!?:;\n"):
            findings.append(Finding("Бессвязный текст", "Похоже на случайный набор слов; проверьте вручную"))

    return findings


def load_upload(uploaded_file) -> pd.DataFrame:
    data = pd.read_excel(uploaded_file, engine="openpyxl")
    data["__source_file"] = uploaded_file.name
    data["__source_order"] = range(len(data))
    return data


def analyze(files: list[pd.DataFrame], forbidden_terms: list[str]) -> pd.DataFrame:
    data = pd.concat(files, ignore_index=True)
    missing = [column for column in REQUIRED_COLUMNS if column not in data.columns]
    if missing:
        raise ValueError("Не найдены обязательные колонки: " + ", ".join(missing))

    users: list[str] = []
    objects: list[str] = []
    for value in data["Кем создан"]:
        author = parse_json_cell(value)
        # Email is a practical fallback for older exports without author.id.
        users.append(extract_id(author.get("id")) or str(author.get("email") or "").strip().casefold())
    for value in data["Объект"]:
        objects.append(extract_id(parse_json_cell(value).get("id")))
    data["ID пользователя"] = users
    data["ID объекта"] = objects

    data["Причина"] = ""
    data["Категория"] = ""
    data["Рекомендация"] = "Опубликовать"

    # Duplicate policy: same user ID + same object ID always blocks every later review,
    # regardless of comment text. When timestamps tie, original row order decides.
    valid_pair = data["ID пользователя"].ne("") & data["ID объекта"].ne("")
    data["__created_sort"] = pd.to_datetime(data["Создан"], errors="coerce", dayfirst=False)
    sorted_idx = data.loc[valid_pair].sort_values(
        ["__created_sort", "__source_file", "__source_order"], na_position="last", kind="stable"
    ).index
    seen: set[tuple[str, str]] = set()
    duplicate_indices: set[int] = set()
    for idx in sorted_idx:
        pair = (data.at[idx, "ID пользователя"], data.at[idx, "ID объекта"])
        if pair in seen:
            duplicate_indices.add(idx)
        else:
            seen.add(pair)

    for idx, row in data.iterrows():
        findings = check_comment(row["Комментарий"], forbidden_terms)
        if idx in duplicate_indices:
            findings.append(Finding("Повторный отзыв", "У пользователя уже есть отзыв к этому объекту; текст не имеет значения"))
        if not extract_id(row["ID пользователя"]):
            data.at[idx, "Причина"] = "Не найден ID/email автора; повторы по автору не проверены"
            data.at[idx, "Категория"] = "Нужна проверка данных"
        if not extract_id(row["ID объекта"]):
            data.at[idx, "Причина"] = (data.at[idx, "Причина"] + "; " if data.at[idx, "Причина"] else "") + "Не удалось определить ID объекта"
            data.at[idx, "Категория"] = (data.at[idx, "Категория"] + "; " if data.at[idx, "Категория"] else "") + "Нужна проверка данных"
        if findings:
            categories = [f.category for f in findings]
            reasons = [f.reason for f in findings]
            data.at[idx, "Категория"] = "; ".join(filter(None, [data.at[idx, "Категория"], *categories]))
            data.at[idx, "Причина"] = "; ".join(filter(None, [data.at[idx, "Причина"], *reasons]))
            data.at[idx, "Рекомендация"] = "Блокировать"

    return data.drop(columns=["__created_sort"])


def to_excel(data: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    visible = data.drop(columns=[c for c in data.columns if c.startswith("__")], errors="ignore")
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        visible.to_excel(writer, index=False, sheet_name="Результаты")
        blocked = visible[visible["Рекомендация"] == "Блокировать"]
        blocked[["ID", "ID пользователя", "ID объекта", "Категория", "Причина"]].to_excel(
            writer, index=False, sheet_name="К блокировке"
        )
    return output.getvalue()


st.title("Модератор комментариев")
st.caption("Загрузите Excel-файлы. Проверка выполняется скриптом; ИИ и общее хранилище не используются.")

with st.expander("Правила проверки", expanded=False):
    st.markdown(
        "- Один и тот же **ID пользователя + ID объекта**: самый ранний отзыв остаётся, каждый следующий рекомендуется заблокировать. Текст не сравнивается.\n"
        "- Проверяются ссылки, пустые заполнители и набор явных негативных формулировок.\n"
        "- Отсутствующие ID автора или объекта отмечаются для ручной проверки.\n"
        "- Проверка бессвязного текста эвристическая и может ошибаться."
    )

with st.sidebar:
    st.header("Настройки")
    forbidden_raw = st.text_area(
        "Запрещённые слова/выражения (по одному на строку)",
        placeholder="Добавьте ваш утверждённый список. Сейчас список пуст.",
        height=160,
    )
    st.caption("Политически окрашенные высказывания автоматически не классифицируются: для этого нужны точные правила или словарь.")

uploads = st.file_uploader("Excel-файлы с комментариями", type=["xlsx"], accept_multiple_files=True)
if uploads:
    st.info(f"Загружено файлов: {len(uploads)}. Файлы обрабатываются только в текущем сеансе.")
    try:
        frames = [load_upload(file) for file in uploads]
        result = analyze(frames, [term for term in forbidden_raw.splitlines() if term.strip()])
        blocked_count = int((result["Рекомендация"] == "Блокировать").sum())
        st.subheader("Результаты")
        m1, m2, m3 = st.columns(3)
        m1.metric("Комментариев", len(result))
        m2.metric("К блокировке", blocked_count)
        m3.metric("Нужна проверка данных", int((result["Категория"].str.contains("Нужна проверка данных", na=False)).sum()))

        show_cols = ["ID", "Комментарий", "ID пользователя", "ID объекта", "Категория", "Причина", "Рекомендация", "__source_file"]
        editable = result[show_cols].rename(columns={"__source_file": "Файл"})
        edited = st.data_editor(
            editable,
            use_container_width=True,
            hide_index=True,
            disabled=[c for c in editable.columns if c != "Рекомендация"],
            column_config={
                "Комментарий": st.column_config.TextColumn(width="large"),
                "Причина": st.column_config.TextColumn(width="large"),
                "Рекомендация": st.column_config.SelectboxColumn(options=["Блокировать", "Опубликовать", "Проверить вручную"], required=True),
            },
        )
        result["Рекомендация"] = edited["Рекомендация"].values

        blocked_ids = result.loc[result["Рекомендация"] == "Блокировать", "ID"].astype(str).tolist()
        st.markdown("**Итоговый список ID к блокировке:** " + (", ".join(blocked_ids) if blocked_ids else "нет"))
        st.download_button(
            "Скачать Excel с результатами",
            data=to_excel(result),
            file_name="moderation_results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )
    except Exception as exc:
        st.error(f"Не удалось обработать файл: {exc}")
else:
    st.write("После загрузки появится таблица рекомендаций. Решение можно вручную изменить перед скачиванием.")
