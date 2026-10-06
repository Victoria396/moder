from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd
import streamlit as st


st.set_page_config(page_title="Модератор комментариев", page_icon="📝", layout="wide")

REQUIRED_COLUMNS = ["ID", "Оценка", "Комментарий", "Объект", "Кем создан", "Создан"]
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

BUILTIN_MODERATION_TERMS = [
    "бля", "блядь", "блядский", "блядская", "блядское", "блядские",
    "ебать", "ебан", "ебуч", "ебал", "ебала", "ебись", "заебал", "заебала",
    "заебись", "уебок", "уебан", "уебищ", "долбоеб", "долбоёб",
    "пизд", "пиздец", "пиздат", "пиздюк", "пиздюлина",
    "хуй", "хуйн", "хуесос", "хуеплет", "хуета", "хуйня",
    "манда", "мандовошк", "сучара", "сучка", "сука",
    "мудак", "мудила", "мудозвон", "дебил", "дебилка", "дебильн",
    "идиот", "идиотка", "идиотский", "кретин", "кретинка",
    "долбоёб", "тупица", "тупой", "тупая", "тупоголов",
    "придурок", "придурочная", "даун", "даунизм",
    "ублюдок", "ублюдочная", "урод", "уродина", "уродец",
    "тварь", "скотина", "сволочь", "падла", "гнида", "мерзавец",
    "мерзавка", "позорник", "позорница", "ничтожество",
    "козёл", "козел", "коза", "хам", "хамло",
    "шлюха", "шалава", "проститутка",
    "пидор", "пидорас", "пидорасина", "пидр", "гомик", "гомосек", "лесбуха",
    "чурка", "хач", "хачик", "черножоп", "узкоглаз", "жид", "жидовк", "жидоед",
    "ниггер", "нигер", "спик", "спикс",
    "сдохни", "сдохнете", "сдохнет", "сдохла", "сдох", "убью", "убейся", "убиваться",
    "чтоб ты сдох", "чтобы ты сдох", "пошел нахуй", "пошла нахуй", "пошли нахуй",
    "иди нахуй", "идите нахуй", "идти нахуй", "иди в жопу", "пошел в жопу", "пошла в жопу",
]


def normalize_moderation_text(text: str) -> str:
    normalized = text.casefold().replace("ё", "е")
    normalized = re.sub(r"[^а-яa-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def find_builtin_moderation_terms(text: str) -> list[str]:
    normalized = normalize_moderation_text(text)
    found: list[str] = []
    for term in BUILTIN_MODERATION_TERMS:
        normalized_term = normalize_moderation_text(term)
        if normalized_term and re.search(rf"(?<![а-яa-z0-9]){re.escape(normalized_term)}(?![а-яa-z0-9])", normalized):
            found.append(term)
    return found


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
    low = text.casefold()
    match_at = low.find(phrase.casefold())
    if match_at < 0:
        return False
    tail = low[match_at:]
    for joiner in ("но", "зато", "однако"):
        pos = tail.find(joiner)
        if pos >= 0 and any(marker in tail[pos:] for marker in POSITIVE_MARKERS):
            return True
    if phrase in {"тяжело", "трудно", "трудный", "непросто", "слишком сложно", "слишком трудный"}:
        return any(marker in low for marker in POSITIVE_MARKERS)
    return False


def check_comment(comment: Any) -> list[Finding]:
    text = "" if comment is None or pd.isna(comment) else str(comment).strip()
    low = text.casefold()
    findings: list[Finding] = []
    if low in FILLERS:
        return [Finding("Пустой комментарий", "Пустой текст или бессодержательный заполнитель")]
    if URL_RE.search(text):
        findings.append(Finding("Ссылка / спам", "В тексте обнаружена ссылка"))
    moderation_terms = find_builtin_moderation_terms(text)
    if moderation_terms:
        shown_terms = ", ".join(f"«{term}»" for term in moderation_terms[:5])
        if len(moderation_terms) > 5:
            shown_terms += f" и ещё {len(moderation_terms) - 5}"
        findings.append(Finding("Негатив / жалоба", f"Оскорбление, ненормативная или уничижительная лексика: {shown_terms}"))
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
    uploaded_file.seek(0)
    data = pd.read_excel(uploaded_file, engine="openpyxl")
    data["__source_file"] = uploaded_file.name
    data["__source_order"] = range(len(data))
    return data


def analyze(files: list[pd.DataFrame]) -> pd.DataFrame:
    data = pd.concat(files, ignore_index=True)
    missing = [column for column in REQUIRED_COLUMNS if column not in data.columns]
    if missing:
        raise ValueError("Не найдены обязательные колонки: " + ", ".join(missing))
    users, objects = [], []
    for value in data["Кем создан"]:
        author = parse_json_cell(value)
        users.append(extract_id(author.get("id")) or str(author.get("email") or "").strip().casefold())
    for value in data["Объект"]:
        objects.append(extract_id(parse_json_cell(value).get("id")))
    data["ID пользователя"] = users
    data["ID объекта"] = objects
    data["Причина"] = ""
    data["Категория"] = ""
    data["Рекомендация"] = "Опубликовать"
    valid_pair = data["ID пользователя"].ne("") & data["ID объекта"].ne("")
    data["__created_sort"] = pd.to_datetime(data["Создан"], errors="coerce", dayfirst=False)
    sorted_idx = data.loc[valid_pair].sort_values(["__created_sort", "__source_file", "__source_order"], na_position="last", kind="stable").index
    seen: set[tuple[str, str]] = set()
    duplicate_indices: set[int] = set()
    for idx in sorted_idx:
        pair = (data.at[idx, "ID пользователя"], data.at[idx, "ID объекта"])
        if pair in seen:
            duplicate_indices.add(idx)
        else:
            seen.add(pair)
    for idx, row in data.iterrows():
        findings = check_comment(row["Комментарий"])
        if idx in duplicate_indices:
            findings.append(Finding("Повторный отзыв", "У пользователя уже есть отзыв к этому объекту; текст не имеет значения"))
        if not extract_id(row["ID пользователя"]):
            data.at[idx, "Причина"] = "Не найден ID/email автора; повторы по автору не проверены"
            data.at[idx, "Категория"] = "Нужна проверка данных"
        if not extract_id(row["ID объекта"]):
            data.at[idx, "Причина"] = (data.at[idx, "Причина"] + "; " if data.at[idx, "Причина"] else "") + "Не удалось определить ID объекта"
            data.at[idx, "Категория"] = (data.at[idx, "Категория"] + "; " if data.at[idx, "Категория"] else "") + "Нужна проверка данных"
        if findings:
            data.at[idx, "Категория"] = "; ".join(filter(None, [data.at[idx, "Категория"], *[f.category for f in findings]]))
            data.at[idx, "Причина"] = "; ".join(filter(None, [data.at[idx, "Причина"], *[f.reason for f in findings]]))
            data.at[idx, "Рекомендация"] = "Блокировать"
    return data.drop(columns=["__created_sort"])


def to_excel(data: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    export = data.copy()
    export["Файл"] = export["__source_file"]
    visible = export.drop(columns=[c for c in export.columns if c.startswith("__")], errors="ignore")
    table_columns = ["ID", "Файл", "Комментарий", "Оценка", "ID пользователя", "ID объекта", "Категория", "Причина"]
    category_sheets = [("Повторный отзыв", "Повторный отзыв"), ("Бессвязный текст", "Бессвязный текст"), ("Негатив жалоба", "Негатив / жалоба")]
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        blocked = visible[visible["Рекомендация"] == "Блокировать"]
        category_values = visible["Категория"].fillna("").str.split("; ")
        for sheet_name, category in category_sheets:
            mask = category_values.apply(lambda values: category in values)
            blocked.loc[mask[blocked.index], table_columns].to_excel(writer, index=False, sheet_name=sheet_name)
        blocked[table_columns].to_excel(writer, index=False, sheet_name="Все блокировки")
        visible.to_excel(writer, index=False, sheet_name="Полные данные")
    return output.getvalue()


st.title("Модератор комментариев")
st.caption("Загрузите Excel-файлы. Проверка выполняется скриптом; ИИ и общее хранилище не используются.")
with st.expander("Правила проверки", expanded=False):
    st.markdown("- Один и тот же **ID пользователя + ID объекта**: самый ранний отзыв остаётся, каждый следующий рекомендуется заблокировать. Текст не сравнивается.\n- Проверяются ссылки, пустые заполнители и набор явных негативных формулировок.\n- Отсутствующие ID автора или объекта отмечаются для ручной проверки.\n- Проверка бессвязного текста эвристическая и может ошибаться.")
with st.sidebar:
    st.header("Настройки")
    st.info("Используется встроенный словарь модерации: ненормативная лексика, оскорбления, уничижительные обозначения групп и явные угрозы.")
    st.caption("Срабатывания этого словаря попадают в категорию «Негатив / жалоба». Список зашит в код и не редактируется пользователем.")

uploads = st.file_uploader("Excel-файлы с комментариями", type=["xlsx"], accept_multiple_files=True)
if uploads:
    st.info(f"Загружено файлов: {len(uploads)}. Файлы обрабатываются только в текущем сеансе.")
    try:
        frames, file_rows = [], []
        for file in uploads:
            frame = load_upload(file)
            frames.append(frame)
            file_rows.append({"Файл": file.name, "Строк": len(frame)})
        result = analyze(frames)
        st.caption("Загружено в проверку: " + "; ".join(f"{item['Файл']} — {item['Строк']} строк" for item in file_rows))
        blocked_count = int((result["Рекомендация"] == "Блокировать").sum())
        st.subheader("Результаты")
        m1, m2, m3 = st.columns(3)
        m1.metric("Комментариев", len(result))
        m2.metric("К блокировке", blocked_count)
        m3.metric("Нужна проверка данных", int(result["Категория"].str.contains("Нужна проверка данных", na=False).sum()))
        category_tables = [("Повторный отзыв", "Повторный отзыв"), ("Бессвязный текст", "Бессвязный текст"), ("Негатив / жалоба", "Негатив / жалоба")]
        table_columns = ["ID", "__source_file", "Комментарий", "Оценка", "ID пользователя", "ID объекта", "Категория", "Причина"]
        category_values = result["Категория"].fillna("").str.split("; ")
        for title, category in category_tables:
            st.subheader(title)
            category_mask = category_values.apply(lambda values: category in values)
            category_rows = result.loc[(result["Рекомендация"] == "Блокировать") & category_mask, table_columns].rename(columns={"__source_file": "Файл"})
            if category_rows.empty:
                st.info(f"В категории «{title}» блокировок нет.")
            else:
                st.dataframe(category_rows, width="stretch", hide_index=True, column_config={"Оценка": st.column_config.NumberColumn("Оценка", min_value=1, max_value=5, step=1, format="%d"), "Комментарий": st.column_config.TextColumn(width="large"), "Причина": st.column_config.TextColumn(width="large")})
        st.subheader("Полные данные по всем комментариям")
        show_cols = ["ID", "__source_file", "Комментарий", "Оценка", "ID пользователя", "ID объекта", "Категория", "Причина", "Рекомендация"]
        editable = result[show_cols].rename(columns={"__source_file": "Файл"})
        edited = st.data_editor(editable, width="stretch", hide_index=True, disabled=[c for c in editable.columns if c != "Рекомендация"], column_config={"Оценка": st.column_config.NumberColumn("Оценка", min_value=1, max_value=5, step=1, format="%d"), "Комментарий": st.column_config.TextColumn(width="large"), "Причина": st.column_config.TextColumn(width="large"), "Рекомендация": st.column_config.SelectboxColumn(options=["Блокировать", "Опубликовать", "Проверить вручную"], required=True)})
        result["Рекомендация"] = edited["Рекомендация"].values
        blocked_ids = result.loc[result["Рекомендация"] == "Блокировать", "ID"].astype(str).tolist()
        st.markdown("**Итоговый список ID к блокировке:** " + (", ".join(blocked_ids) if blocked_ids else "нет"))
        st.download_button("Скачать Excel с результатами", data=to_excel(result), file_name="moderation_results.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")
    except Exception as exc:
        st.error(f"Не удалось обработать файл: {exc}")
else:
    st.write("После загрузки появится таблица рекомендаций. Решение можно вручную изменить перед скачиванием.")
