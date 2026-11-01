from datetime import date, timedelta

import pytest

from app.agent.rules import parse

MON = date(2026, 11, 2)


def test_main_scenario():
    c = parse("Найди SSD Samsung 2 ТБ с гарантией и доставкой до пятницы", MON)
    assert c.category == "ssd" and c.brand == "Samsung" and c.capacity_gb == 2000
    assert c.deliver_by == date(2026, 11, 6)
    assert c.min_warranty_months == 1
    assert c.max_total_rub is None


def test_budget_variants():
    assert parse("ssd до 20 тысяч", MON).max_total_rub == 20000
    assert parse("ssd не дороже 17к", MON).max_total_rub == 17000
    assert parse("ssd бюджет 15 500 ₽", MON).max_total_rub == 15500
    assert parse("ssd до 5 ноября", MON).max_total_rub is None
    assert parse("ssd за 3 дня до 20 тысяч", MON).max_total_rub == 20000


def test_deadline_and_warranty():
    c = parse("наушники Sony за 2 дня, гарантия от 12 месяцев", MON)
    assert c.category == "headphones" and c.deliver_by == date(2026, 11, 4) and c.min_warranty_months == 12
    assert parse("ssd до 06.11", MON).deliver_by == date(2026, 11, 6)
    assert parse("ssd завтра", MON).deliver_by == date(2026, 11, 3)


def test_model_keyword():
    c = parse("Samsung 990 EVO на 2TB", MON)
    assert c.keywords == ["990 evo"] and c.capacity_gb == 2000


def test_grounding_drops_hallucinations():
    from app.agent.grounding import merge
    from app.models import Constraints
    text = "Найди SSD Samsung 2 ТБ до пятницы"
    llm_c = Constraints(category="ssd", brand="Samsung", capacity_gb=2000, deliver_by=date(2026, 11, 13),
                        max_total_rub=50000, keywords=["990 pro", "nvme"])
    c, fixes = merge(llm_c, parse(text, MON), text)
    assert c.deliver_by == date(2026, 11, 6)  # дату считает код
    assert c.max_total_rub is None  # бюджета в тексте нет
    assert c.keywords == []  # придуманная модель и характеристика отброшены
    assert fixes


def test_impossible_dates_do_not_crash():
    # Несуществующая дата в поручении — не условие и не ошибка сервера
    for text in ["ssd до 31.02", "ssd до 00.11", "ssd до 32 ноября", "ssd до 29.02", "ssd до 15.13"]:
        assert parse(text, MON).deliver_by is None, text


def test_impossible_date_via_api(client):
    r = client.post("/api/tasks", json={"text": "SSD Samsung 2 ТБ до 31.02"})
    assert r.status_code == 200 and r.json()["constraints"]["deliver_by"] is None


def test_gpu_category():
    # Видеокарт в тестовых каталогах нет, но категория должна распознаваться — иначе поручение считалось «любым товаром».
    assert parse("Видеокарта RTX 4090", MON).category == "gpu"
    assert parse("нужна geforce до 60 тысяч", MON).category == "gpu"


@pytest.mark.parametrize("text", ["SSD 0 ТБ", "ssd на 0,4 гб", "ssd 100000 тб", "ssd до 99999999 рублей", "ssd гарантия 999 лет",
                                  "ssd за 999999999 дней", "ssd гарантия от 11 лет"])
def test_out_of_range_values_do_not_crash_server(client, text):
    # Найдено hypothesis: значения вне диапазона схемы давали 500 на POST /api/tasks.
    assert client.post("/api/tasks", json={"text": text}).status_code == 200


def test_out_of_range_condition_is_kept_strict():
    # Условие не теряется: «гарантия от 11 лет» не превращается в «любая гарантия» и не урезается до 10 лет.
    assert parse("ssd гарантия от 11 лет", MON).min_warranty_months == 132
    assert parse("ssd до 99999999 рублей", MON).max_total_rub == 10_000_000  # верхняя граница бюджета — строже, а не мягче
    assert parse("ssd за 999999999 дней", MON).deliver_by == MON + timedelta(days=366)
    assert parse("SSD 0 ТБ", MON).capacity_gb == 1  # такого товара нет — проверка объёма никого не пропустит
