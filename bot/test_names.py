"""Очистка имён участников (clean_name): невидимые символы и повторные пробелы не оставляют «дыру» в имени."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
from core.members import clean_name, DEFAULT_NAME, NAME_MAX


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got!r}, ожидали {expected!r}"


check("обычное имя", clean_name("Аня"), "Аня")
check("пробел внутри остаётся один", clean_name("Яр чик"), "Яр чик")
check("нулевая ширина убирается", clean_name("Яр​чик"), "Ярчик")
check("«пустой» хангыль между словами даёт один пробел или ничего", clean_name("Ярㅤчик"), "Ярчик")
check("повторные пробелы и перевод строки схлопываются", clean_name("Яр    чик\n"), "Яр чик")
check("пустой брайль", clean_name("⠀Аня⠀"), "Аня")
check("только невидимое: имя по умолчанию", clean_name("​ㅤ"), DEFAULT_NAME)
check("не строка", clean_name(None), DEFAULT_NAME)
check("направления текста убираются", clean_name("A‮B"), "AB")
check("ZWJ в составном эмодзи сохраняется", clean_name("\U0001F468‍\U0001F469"), "\U0001F468‍\U0001F469")
check("длина ограничена", len(clean_name("я" * 100)), NAME_MAX)
print("test_names OK")
