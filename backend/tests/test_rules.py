from datetime import date

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
