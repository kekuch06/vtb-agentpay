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

## Состояние на 01.11.2026

Ревизия кода — 23 коммита поверх `1f174c7`, ветка `claude/eager-pascal-0jcjh5`. Что найдено и исправлено — в [REVIEW.md](REVIEW.md).

| Проверка | Результат |
|---|---|
| `make lint` | ruff и tsc без ошибок |
| `make test` | 90 passed (SQLite) |
| `make test-pg` | 90 passed (PostgreSQL 16.15) |
| `AGENTPAY_LLM=rules make eval` | TSR 100% (20/20), CC 100% (19/19), Payment Safety 0 из 16 атак |
| `make up` + `make e2e` | стенд из четырёх сервисов поднимается, сквозной сценарий в браузере проходит |
| CI (GitHub Actions) | см. вкладку Actions по ветке; workflow впервые запущен при публикации ветки |

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

**Модель.** Реестры моделей (registry.ollama.ai, huggingface.co) в облачной среде закрыты сетевой политикой, поэтому `make eval-llm` там не выполнить. Нужно либо разрешить эти домены в настройках окружения (Network access → Allowed domains), либо прогнать на своём компьютере с Ollama.

## Что сделать дальше

### 1. Интерфейс: дизайн, который не выглядит шаблоном

Навыки: **frontend-design** (Anthropic), **Website Design** (UXKIN: `/design-system`, `/review-page`), **Design** (Anthropic: разбор, тексты интерфейса, доступность).

- Сначала `DESIGN.md`: аудитория (эксперты банка, клиент банка с телефона), характер — банковский продукт, которому доверяют деньги, спокойная уверенность, а не «ИИ-стартап». Не копировать фирменный стиль ВТБ: проект не является продуктом банка (см. дисклеймер в интерфейсе).
- Ограничения, которые нельзя ломать:
  - CSP — только свои ресурсы (`default-src 'self'`; `style-src 'self'` — без inline-стилей и `style=`). Шрифты — только локальные файлы в сборке (woff2 с открытой лицензией, например OFL), никаких Google Fonts и CDN: это закрытый контур.
  - Те же заголовки в `frontend/nginx.conf`.
  - Окно подтверждения должно явно показывать сумму, получателя, счёт и назначение, а код — как push «с устройства» (данные берутся из `/api/sandbox/device`, не из ответа checkout).
  - Дисклеймеры «Sandbox · без реальных списаний» и «не продукт Банка ВТБ» остаются заметными.
- Мобильная вёрстка, фокус внутри модального окна, `aria-live` для событий в чате, контраст по WCAG AA.
- `e2e/smoke.mjs` опирается на селекторы и тексты: кнопки «Оформить заказ», «Проверить и перейти к оплате», «Подтвердить оплату», «Журнал», пример поручения; классы `.modal .push`, `input.code`, `.receipt b`, `.order .row.big b`, `.audit .pill`. Меняя разметку, обновляйте e2e в том же коммите.
- После правок: `make build`, `make lint`, стенд и `make e2e`, скриншоты на ширине 390 и 1280 px.

### 2. Аудит готового продукта

**audit-suite** (Orbit): `/full-audit` — дизайнер, UX, фронтенд, бэкенд, производительность, доступность и т. д. Находки разложить по приоритетам A–D, как в REVIEW.md, каждую подтвердить воспроизведением и закрыть отдельным коммитом с тестом. REVIEW.md дополнить.

### 3. Оставшееся из ревизии (см. REVIEW.md → «Что осталось»)

- Ограничение частоты запросов (`POST /api/tasks` нагружает модель; новые сессии).
- Повышение лимита на операцию — только с подтверждением кодом (понижение — сразу).
- Роли PostgreSQL: приложению только `INSERT`/`SELECT` в `audit`, владелец таблиц — отдельная роль; миграции отдельно от приложения.
- Выгрузка головы цепочки журнала во внешнее хранилище (обнаружение обрезки хвоста).
- Сценарии sandbox на клиента, а не на общий каталог.
- Действия GitHub закрепить по SHA; предупреждение Starlette о `httpx` в TestClient.

### 4. Нужно от команды (не выдумывать)

- `make eval-llm` с qwen2.5:3b и замена строк LLM в `backend/eval/SUMMARY.md` и README.
- Документы `submission/` и файл аудита команды — в репозиторий не входят; если нужна их правка, их нужно передать в сессию.
- Пользовательское исследование: Time-to-Checkout, User Preference.

## Как проверять перед каждым коммитом

```bash
make lint && make test && make test-pg PG=… && AGENTPAY_LLM=rules make eval && make build
```

Перед завершением — стенд и `make e2e`. Отчёт `backend/eval/report_rules.md` коммитить, только если изменились цифры, а не одно время ответа.
