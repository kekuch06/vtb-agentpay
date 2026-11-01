# Передача работы следующей сессии

Рабочая записка для следующего, кто продолжит доводку проекта (человек или Claude Code). Сначала прочитайте её, потом [REVIEW.md](REVIEW.md) и [README](../README.md).

## Правила работы (обязательны)

- Не выдумывать данные исследований, цифры, источники и результаты. Чего нет — помечать «нужно от команды».
- Не менять LICENSE и смысл продукта: **ИИ готовит покупку, платёж проверяет код и подтверждает человек.** Модель видит только текст поручения, у неё нет инструментов заказа и оплаты.
- Каждый дефект — тест, который падает до исправления и проходит после. Сначала воспроизвести, потом чинить.
- Отдельный коммит на каждую логическую правку, сообщения на русском (заголовок + что было, как воспроизведено, что стало).
- Сохранять стиль кода и комментариев проекта (плотный код, комментарии по-русски, объясняют «почему»).
- Никаких секретов в репозитории (`.env`, ключи, пароли). Пример — `.env.example`.
- Не пушить без подтверждения владельца; в конце показать список коммитов и что осталось.

## Состояние на 01.11.2026 (после ревизии 2)

Ревизия 1 — ветка `claude/eager-pascal-0jcjh5` (PR #1). Ревизия 2 — ветка `claude/epic-hamilton-i824eb` (PR #2 и #3): новый интерфейс по [DESIGN.md](../DESIGN.md), тестирование, аудит по десяти линзам ([reviews/audit-plan-full.md](../reviews/audit-plan-full.md)), закрытые пункты «Что осталось». Что найдено и исправлено — в [REVIEW.md](REVIEW.md#ревизия-2-тестирование-аудит-доработки).

| Проверка | Результат |
|---|---|
| `make lint` | ruff и tsc без ошибок |
| `make test` | 199 passed, 2 skipped (SQLite; пропущены тесты только для PostgreSQL) |
| `make test-pg` | 201 passed (PostgreSQL 16) |
| `AGENTPAY_LLM=rules make eval` | TSR 100% (20/20), CC 100% (19/19), Payment Safety 0 из 17 атак |
| `make e2e-local` | 52 passed на 390 и 1280 px (2 пропущены — сценарии одной ширины) |
| Lighthouse, мобильный | Performance 98–99, Accessibility 100, Best Practices 100 |
| CI | по PR #2: backend, frontend, стенд и smoke — зелёные, 2 проверки axe на телефоне упали; исправление — PR #3 |

## Подготовка окружения

```bash
make install          # backend по хешам (uv, Python 3.12) + frontend (npm ci)
make test
```

**PostgreSQL для `make test-pg`.** В облачной среде Claude Code PostgreSQL 16 уже установлен, но не запущен:

```bash
PGD=/var/tmp/agentpay-pgdata; mkdir -p $PGD && chown postgres $PGD && chmod 700 $PGD
su postgres -c "/usr/lib/postgresql/16/bin/initdb -D $PGD -U postgres --auth=trust -E UTF8 && /usr/lib/postgresql/16/bin/pg_ctl -D $PGD -o '-p 55432 -k /tmp' -l $PGD/log start"
psql -h 127.0.0.1 -p 55432 -U postgres -c "CREATE USER agentpay PASSWORD 'agentpay'"
psql -h 127.0.0.1 -p 55432 -U postgres -c "CREATE DATABASE agentpay_test OWNER agentpay"
make test-pg PG=postgresql://agentpay:agentpay@127.0.0.1:55432/agentpay_test
```

**Docker в облачной среде.** Демон не запущен: `nohup dockerd >/tmp/dockerd.log 2>&1 &`. Docker Hub отвечает 429 на частые запросы — базовые образы тянуть по одному (`docker pull …`) с паузой и повтором. Исходящий HTTPS идёт через TLS-прокси среды, поэтому `pip` и `npm` внутри сборки не доверяют сертификату. Только для локальной проверки (в репозиторий не коммитить!) соберите поверх базовых образов вариант с CA прокси под тем же тегом:

```bash
mkdir -p /tmp/ca && cp /root/.ccr/ca-bundle.crt /tmp/ca/proxy-ca.crt
printf 'FROM python:3.12.16-slim-bookworm\nCOPY proxy-ca.crt /etc/ssl/proxy-ca.crt\nENV PIP_CERT=/etc/ssl/proxy-ca.crt\n' > /tmp/ca/Dockerfile.py
printf 'FROM node:24.22.2-alpine\nCOPY proxy-ca.crt /etc/ssl/proxy-ca.crt\nENV NODE_EXTRA_CA_CERTS=/etc/ssl/proxy-ca.crt\n' > /tmp/ca/Dockerfile.node
docker build -f /tmp/ca/Dockerfile.py -t python:3.12.16-slim-bookworm /tmp/ca
docker build -f /tmp/ca/Dockerfile.node -t node:24.22.2-alpine /tmp/ca
```

Секреты для стенда — во временном файле вне репозитория: `docker compose --env-file /tmp/compose.env up -d --build --wait`, затем `cd e2e && E2E_BASE_URL=http://127.0.0.1:<порт> npm run smoke`. Chromium для Playwright в облачной среде уже установлен (`PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`).

**Модель.** Реестры моделей (registry.ollama.ai, huggingface.co) в облачной среде закрыты сетевой политикой, поэтому `make eval-llm` там не выполнить. Нужно либо разрешить эти домены в настройках окружения (Network access → Allowed domains), либо прогнать на своём компьютере. Для `api.deepseek.com` в облачной среде нужно то же разрешение.

**Удобнее всего работать на Mac владельца** (локальный Claude Code в клоне репозитория): там установлены плагины дизайна и аудита, крутится Qwen3 MLX, Docker работает без прокси и лимитов Docker Hub. Из облачной сессии компьютер можно подключить через Remote Control.

## Что сделать дальше

Сделано в ревизии 2: интерфейс (пункт 1 прежнего плана), поддержка DeepSeek и Qwen3 в коде (пункт 2 без прогонов), тестирование (пункт 3), аудит (пункт 4), из «оставшегося» — ограничение частоты, повышение лимита кодом, роли PostgreSQL и миграции, сценарии sandbox на клиента, SHA в CI, httpx2.

1. **Прогоны моделей** — `make eval-llm` с DeepSeek и Qwen3-8B MLX (команды — в README), цифры в `backend/eval/SUMMARY.md` и README. Нужны ключ DeepSeek в `.env` и разрешённый `api.deepseek.com` (или прогон на Mac владельца); модель на Mac — уточнить у владельца (ожидается `mlx-community/Qwen3-8B-4bit`).
2. **Голова цепочки журнала во внешнее хранилище** — регулярная выгрузка (id, hash) в SIEM или WORM и проверка, что выгруженная голова есть в базе: обнаружение обрезки хвоста.
3. **Восстановление после перезагрузки страницы** (UX3 аудита): хранить id поручения и заказа в sessionStorage и поднимать через `GET /api/tasks/{id}` и `GET /api/orders/{id}`.
4. **Ограничение частоты для нескольких процессов** — общий счётчик (Redis или шлюз API) и доверенный прокси в `--forwarded-allow-ips` вместо `*`.
5. **Российская модель** — прогон метрик на YandexGPT, GigaChat через прокси или open-weights модели внутри контура.

e2e опирается на роли и тексты; селекторы собраны в `e2e/tests/ui.mjs`, smoke — в `e2e/smoke.mjs`. Меняя разметку, обновляйте их в том же коммите.

### Нужно от команды (не выдумывать)

- Ключ DeepSeek (через `.env` или секреты окружения) и доступ к Mac с Qwen3-8B MLX для прогонов метрик.
- Документы `submission/`, презентация, финансовая модель, оценка рынка — в репозиторий не входят.
- Пользовательское исследование: Time-to-Checkout, User Preference.

## Как проверять перед каждым коммитом

```bash
make lint && make test && make test-pg PG=… && AGENTPAY_LLM=rules make eval && make build && make e2e-local
```

Перед завершением — стенд и `make e2e`. Отчёт `backend/eval/report_rules.md` коммитить, только если изменились цифры, а не одно время ответа.
