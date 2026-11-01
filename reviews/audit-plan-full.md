# Full Spectrum Audit: VTB AgentPay — ветка claude/epic-hamilton-i824eb

**Дата**: 01.11.2026
**Режим**: Branch vs Main + Repo Health (охват задан владельцем)
**Источник**: `claude/epic-hamilton-i824eb` против `origin/main` (`b480842`), весь репозиторий
**Линз**: 10/10
**Пропущено**: нет. Линзы audit-suite написаны для проекта на Rust/Tauri; здесь они применены по смыслу к FastAPI, React и Docker Compose (линза «Backend» — Python вместо Rust, IPC — HTTP API).

Каждая находка ниже подтверждена воспроизведением (команда, скрипт или тест) — непроверенных предположений в таблицах нет. Приоритеты — как в [docs/REVIEW.md](../docs/REVIEW.md): **A** — платёж и ИБ, **B** — корректность, **C** — устойчивость и инфраструктура, **D** — документация и гигиена.

---

## Master Verdict: APPROVE WITH CHANGES

Платёжный контур и разделение зон доверия выдержали все десять линз: доверенная зона не импортирует недоверенную, ни один путь не ведёт к платежу без кода, машина состояний hypothesis и фаззинг API не нашли нарушений. Находки — устойчивость (журнал, проверка здоровья, перезапуск PostgreSQL, размер запроса) и несколько дефектов интерфейса; ни одна не позволяет провести платёж в обход правил, но четыре из них (C6–C9) видны на стенде под нагрузкой или при сбое зависимостей и должны быть закрыты до показа.

## Verdict by Lens

| # | Линза | Вердикт | Critical | Рекомендации |
|---|------|---------|----------|-----------------|
| 1 | Design Engineer | APPROVE WITH CHANGES | 0 | 1 |
| 2 | UX Engineer | APPROVE WITH CHANGES | 0 | 3 |
| 3 | Frontend Engineer | APPROVE WITH CHANGES | 0 | 3 |
| 4 | Backend Engineer | APPROVE WITH CHANGES | 2 | 3 |
| 5 | Structural Engineer | APPROVE WITH CHANGES | 0 | 3 |
| 6 | Repo Maintainer | APPROVE WITH CHANGES | 0 | 3 |
| 7 | Production Readiness | APPROVE WITH CHANGES | 2 | 2 |
| 8 | DX Engineer | APPROVE WITH CHANGES | 0 | 2 |
| 9 | Performance Engineer | APPROVE WITH CHANGES | 1 | 1 |
| 10 | Accessibility Engineer | APPROVE WITH CHANGES | 0 | 1 |

## All Critical Issues (Must Fix)

| # | Находка | Линзы | Файлы | Воспроизведение | Исправление |
|---|-------|----------|---------|--------|-----|
| C6 | `GET /api/audit` на каждый запрос пересчитывает HMAC всего журнала банка и отдаёт любому клиенту общее число записей всех клиентов | Backend, Performance | `app/main.py`, `app/audit.py` | 100 000 записей других клиентов → 1,29 с на открытие журнала, растёт линейно; в ответе `"entries": 100001` | Проверка цепочки с кешем: новые записи — от последней проверенной головы, полная проверка — не чаще раза в 5 минут. Клиенту — только `ok` |
| C7 | Проверка здоровья ждёт ответа модели | Production Readiness, Backend | `app/agent/llm.py`, `app/main.py` | модель принимает соединение и молчит → `/api/health` 3,11 с при таймауте healthcheck 3 с; раз в 15 с ту же задержку получает поручение | Доступность модели проверяется в фоне; health и поручение берут последнее известное значение |
| C8 | Первый запрос после перезапуска PostgreSQL — 500 | Production Readiness | `app/db.py` | `pg_ctl restart` → запрос 1: 500, запросы 2–3: 200 | Разорванное соединение обнаруживается на `BEGIN` и переоткрывается до первой операции транзакции |
| C9 | Тело запроса не ограничено при запуске без nginx (`make run`) | Backend, Production Readiness | `app/main.py` | `POST /api/tasks` с телом 30 МБ читается и разбирается целиком | 413 для тел API больше 64 КБ |

## Cross-Cutting Concerns

| Проблема | Линзы | Причина | Общее исправление |
|---------|-----------|------------|-------------|
| Запрос пользователя платит за чужую работу: полная проверка журнала (C6), синхронный опрос модели (C7) | Backend, Performance, Production Readiness | Дорогие проверки выполняются в обработчике запроса | Кеш и фоновая проверка; запрос читает готовый результат |
| Глобальное состояние sandbox: сценарии меняют общий каталог, фаззинг одного клиента ломал заказ другого | Structural, Production Readiness (найдено фаззингом) | Каталоги — словарь процесса | Сценарии на клиента — пункт 5 плана (REVIEW «Что осталось») |
| Статус после действия не доходит до пользователя: на телефоне чат скрыт, live-регион молчит (B14); журнал перезаписывается опоздавшим ответом (B15) | UX, Frontend, Accessibility | Обратная связь только через ленту чата; ответы не упорядочены | `role="status"` у статуса заказа; игнорировать устаревшие ответы |

## All Recommendations

| # | Рекомендация | Линза | Файлы | Исправление |
|---|---------------|------|---------|-----|
| B14 | На телефоне отказ Policy Engine не объявляется экранным диктором: чат скрыт, а статус заказа не live-регион | Accessibility, UX | `App.tsx` | `role="status"` у баннера статуса заказа |
| B15 | Журнал: опоздавший ответ перезаписывает более свежий | Frontend | `App.tsx` | Счётчик запросов, устаревший ответ отбрасывается |
| C10 | Каждое нажатие клавиши в поле поручения перерисовывает весь интерфейс со всей перепиской | Frontend, Performance | `App.tsx` | Поле ввода — отдельный компонент со своим состоянием |
| D1 | `/openapi.json` открыт при выключенной документации API | Backend | `app/main.py` | Схема — только при `AGENTPAY_API_DOCS=1`; фаззинг берёт схему из приложения |
| D2 | `make test-pg` без `PG=` молча прогоняет SQLite и пишет «passed» | DX | `Makefile` | Ошибка с подсказкой, если `PG` не задан |
| D3 | Короткий `AGENTPAY_SECRET_KEY`: сервер стартует и отвечает 500 на каждый запрос | DX, Production Readiness | `app/main.py`, `app/keys.py` | Проверка ключа при старте: процесс не поднимается, сообщение называет переменную |
| D4 | 8 цветов в стилях мимо токенов; неиспользуемая иконка; приватные `_money`/`_rub` импортируются между модулями | Design, Repo Maintainer, Structural | `styles.css`, `icons.tsx`, `verifier.py` | Токены `--on-accent`, `--scrim`, `--shadow-seg`; удалить иконку; форматирование денег — публичные функции |
| D5 | README и NEXT_SESSION устарели: «шрифты системные», «90 тестов» | Repo Maintainer | `README.md`, `docs/` | Обновить по фактическому состоянию |
| D6 | Логика сценариев sandbox работает с базой прямо в HTTP-слое | Structural | `app/main.py` | Модуль `app/sandbox.py` (вместе со сценариями на клиента) |
| UX1 | Пока модель разбирает поручение (5–30 с с LLM), видны только три точки | UX | `App.tsx` | Подпись «Разбираю поручение и опрашиваю продавцов» |
| UX2 | После отказа заказа поручение нужно набирать заново | UX | `App.tsx` | Кнопка «Повторить поручение» в статусе отклонённого заказа |
| UX3 | После перезагрузки страницы открытый заказ не восстанавливается | UX | `App.tsx` | Хранить id поручения и заказа в sessionStorage и поднимать через GET — оставлено на следующий этап |

---

# Lens Reports

---

## 1. Design Engineer

**Verdict**: APPROVE WITH CHANGES

**Что меняется визуально**: новый интерфейс по DESIGN.md — синий герой, листы, нижняя навигация, окно подтверждения листом снизу, пастельные плитки sandbox, шрифт Onest.

| Файл | Визуальная роль | Риск |
|------|-------------|------|
| `frontend/src/styles.css` | токены и компоненты | низкий |
| `frontend/src/App.tsx` | разметка экранов | низкий |
| `frontend/src/icons.tsx` | набор иконок | низкий |
| `DESIGN.md` | система | — |

**Critical**: нет.

**Recommendations**

| # | Проблема | Файлы | Почему | Исправление |
|---|-------|---------|-----|-----|
| D4 | 8 значений мимо токенов: `#FFFFFF` ×4 (текст на насыщенной заливке), `#F0A742`, `#6B9DFF` (точки статуса), `rgba(8,14,40,.55)` (подложка окна), тень сегмента | `styles.css:85,222,345,385,390,392,333,443` | Тёмная тема и будущие правки палитры их не увидят | Токены `--on-accent`, `--dot-warn`, `--dot-info`, `--scrim`, `--shadow-seg` |

**Design System Compliance**: остальные цвета, радиусы (12/16/24/28/999), отступы на шкале 4 и шрифтовая шкала соответствуют DESIGN.md; inline-стилей нет (CSP).

**Visual State Coverage**

| Элемент | Hover | Focus | Active | Disabled | Loading | Empty | Error | Dark |
|---|---|---|---|---|---|---|---|---|
| Основная/вторичная кнопка | ✓ | ✓ кольцо 3 px | ✓ scale .98 | ✓ 45% | — | — | — | ✓ |
| Поле поручения | — | ✓ | — | ✓ | ✓ индикатор | — | ✓ событие | ✓ |
| Карточка предложения | — | ✓ (кнопка) | ✓ | ✓ | — | ✓ | ✓ причина отказа | ✓ |
| Окно подтверждения | ✓ | ✓ ловушка фокуса | ✓ | ✓ | ✓ | — | ✓ role=alert | ✓ |
| Плитки sandbox | ✓ | ✓ | ✓ | ✓ | — | ✓ подсказка | ✓ событие | ✓ |
| Журнал | ✓ | ✓ | — | — | — | ✓ | ✓ «не загрузился» | ✓ |

**Verdict Details**: Token Fidelity — CONCERNS (D4); State Coverage — PASS; Dark Mode — PASS (axe в тёмной теме); Animation — PASS (transform/opacity, ≤ 280 мс, reduced-motion); Consistency — PASS.

---

## 2. UX Engineer

**Verdict**: APPROVE WITH CHANGES

**User Journey Map**

1. Открыть страницу → герой с лимитом, приветствие агента, примеры — **OK**
2. Отправить поручение → индикатор набора — **WARN** (UX1: с моделью ожидание 5–30 с без пояснения)
3. Ответ: «Понял так», объяснение, карточка рекомендации — **OK**
4. «Оформить заказ» → вкладка заказа, статус, Policy Engine — **OK**
5. «Проверить и перейти к оплате» → окно подтверждения с push — **OK**
6. Код → «Оплачен (sandbox)», сумма за день в герое — **OK**
7. Отказ (Policy Engine, неверный код, истёкший код) → понятный текст, новый код из окна — **OK**; повтор поручения — **WARN** (UX2)
8. Перезагрузка посреди оплаты → заказ на сервере есть, в интерфейсе — нет — **WARN** (UX3)

**Critical**: нет. **Recommendations**: UX1, UX2, UX3 (таблица выше).

**Interaction Consistency Check**: действие и результат названы одинаково («Оформить заказ» → «Заказ оформлен»), один термин для лимита, примеры поручений в чате — контурные подсказки, на телефоне — ряд быстрых действий, как в референсе.

**Verdict Details**: Discoverability — PASS; Flow Completeness — CONCERNS (UX3); Error Recovery — CONCERNS (UX2); Feedback & Status — CONCERNS (UX1, B14); Consistency — PASS.

---

## 3. Frontend Engineer

**Verdict**: APPROVE WITH CHANGES

| Файл | Роль | Риск |
|------|------|------|
| `App.tsx` (869 строк) | всё состояние и экраны | средний |
| `api.ts` | клиент API, форматирование | низкий |

**Critical**: нет.

**Recommendations**: B15 (гонка ответов журнала: `refreshAudit` вызывается на каждое изменение заказа и политики, опоздавший ответ перезаписывает свежий — воспроизводится e2e с задержкой первого ответа), C10 (перерисовка всего дерева на каждый символ), разбиение `App.tsx` на файлы — nice-to-have.

**Render Path Analysis**: состояние `input` живёт в `App` → каждый символ перерисовывает герой, все сообщения, панель. Остальные обновления — по действиям пользователя, лишних эффектов нет; эффект прокрутки не срабатывает на первом экране.

**Type Safety Assessment**: `tsc` строгий без ошибок; `ApiError.body: any` — единственная дыра, типы ответов API продублированы вручную (рантайм-валидации нет, для MVP допустимо).

**Verdict Details**: Component Architecture — CONCERNS (монолит); State — CONCERNS (B15); Type Safety — PASS; Rendering — CONCERNS (C10); Bundle — PASS (58 КБ gzip).

---

## 4. Backend Engineer

**Verdict**: APPROVE WITH CHANGES

| Файл | Роль | Риск |
|------|------|------|
| `app/main.py` | HTTP, сессии, sandbox | средний |
| `app/audit.py` | журнал с цепочкой HMAC | средний (C6) |
| `app/db.py` | транзакции SQLite/PostgreSQL | средний (C8) |
| `app/agent/llm.py` | модель | средний (C7) |
| `app/trust/*` | Policy Engine, платежи | низкий |

**Critical**: C6, C9 (см. master). **Recommendations**: D1, D3, C7.

**Error Path Analysis**: ошибки модели → `LLMUnavailable` с категорией без адресов → запасной разбор правилами (проверено тестами). Отказы подтверждения → 403 с понятным текстом, фиксируются в журнале до исключения. Ошибка БД на середине транзакции → 500 без утечки деталей; разорванное соединение после перезапуска PostgreSQL → 500 (C8). Невалидный ввод → 422 (фаззинг: 5xx нет).

**Resource Lifecycle Audit**

| Ресурс | Где создаётся | Где освобождается | При ошибке | Риск |
|---|---|---|---|---|
| Соединение БД | `db._connect` (одно на процесс) | при `db.use` | разорванное PG-соединение не переоткрывается до первой ошибки | C8 |
| Транзакция | `db.tx` | COMMIT/ROLLBACK | ROLLBACK, исключение наружу | низкий |
| HTTP-клиент к модели | `httpx.post` на запрос | автоматически | таймаут 60 с | низкий |
| Тело запроса | Starlette | — | не ограничено | C9 |

**Verdict Details**: Error Handling — PASS; Concurrency — PASS (транзакции под блокировкой, машина состояний hypothesis); Resource Management — CONCERNS (C8, C9); API Correctness — PASS; File System Safety — PASS (A1 закрыт ранее).

---

## 5. Structural Engineer

**Verdict**: APPROVE WITH CHANGES

**Dependency Map**

```
main (HTTP) ──► agent.orchestrator ──► agent.{rules,llm,grounding}
     │                 └──► trust.verifier (проверка и форматирование)
     ├──► trust.payments ──► trust.verifier, store, audit, catalogs, keys
     └──► store, audit, db, catalogs (сценарии sandbox прямо здесь — D6)
trust ✗──► agent   ← главный инвариант: текст модели не доходит до платёжного кода
```

**Boundary Crossing Analysis**

| Переход | Откуда → куда | Чисто? | Заметки |
|---|---|---|---|
| Модель → ограничения | `llm` → `Constraints` | да | схема Pydantic, сверка с текстом |
| Агент → проверка | `orchestrator` → `verifier.check_offer` | да | плюс приватные `_money`, `_rub`, `_gb` (D4) |
| HTTP → БД | `main` → `store` (payee_swap, code_expired) | нет | D6 |

**Critical**: нет. **Recommendations**: D6, D4 (приватные функции), разбиение `App.tsx`.

**Verdict Details**: Dependency Direction — PASS; Coupling — CONCERNS (глобальный каталог sandbox); Cohesion — CONCERNS (D6); Layer Boundaries — PASS; Change Propagation — PASS.

---

## 6. Repo Maintainer

**Verdict**: APPROVE WITH CHANGES

**Convention Compliance Checklist**

| Соглашение | Статус | Заметки |
|---|---|---|
| Размещение файлов | ✓ | новые: `icons.tsx`, `fonts/`, `tests/test_{llm,properties,api_fuzz}.py`, `e2e/tests/` |
| Имена | ✓ | |
| Комментарии по-русски, «почему» | ✓ | |
| Импорты (ruff I) | ✓ | ruff без ошибок |
| Нет мёртвого кода | ✗ | иконка `chevronRight` не используется (D4) |
| Логирование | ✓ | `logging` в backend |
| Подавления линтера | ✓ | только `noqa: E402` в eval, как было |
| Зависимости по хешам / точные версии | ✓ | hypothesis, schemathesis, lighthouse, axe |
| Документация | ✗ | README и NEXT_SESSION устарели (D5) |

**Reuse Opportunities**: форматирование денег во фронтенде (`money`) и бэкенде (`_money`) — два источника правды; совпадение проверено тестом свойств только для бэкенда.

**Verdict Details**: File Organization — PASS; Naming — PASS; Import Hygiene — PASS; Code Reuse — CONCERNS; Lint — PASS.

---

## 7. Production Readiness

**Verdict**: APPROVE WITH CHANGES

**Failure Mode Analysis**

| Сценарий | Что ломается | Влияние | Восстановление | Закрыто? |
|---|---|---|---|---|
| Модель медленная или молчит | `/api/health` 3,1 с, поручение +3 с раз в 15 с | healthcheck на грани, задержка | запасной разбор правилами | нет → C7 |
| Модель недоступна | — | разбор правилами | автоматически | да |
| Перезапуск PostgreSQL | первый запрос 500 | клиент не знает, прошёл ли платёж | со второго запроса | нет → C8 |
| Большой журнал | открытие журнала 1,3 с на 100 тыс. записей | медленно у всех | — | нет → C6 |
| Огромное тело запроса без nginx | память | — | — | нет → C9 |
| Перезапуск backend | — | платежи и журнал на месте | — | да (ревизия) |
| Несколько процессов uvicorn | сценарии sandbox только в своём процессе | демо | — | пункт 5 плана |

**Environment Checklist**

| Тема | Dev | Prod (стенд) | Разрыв? |
|---|---|---|---|
| Секреты | `.env`/генерация | обязательны в compose | D3: короткий ключ не останавливает старт |
| Сеть к модели | localhost | Ollama/внешний API | C7 |
| Ограничение тела | нет | nginx 1 МБ | C9 |
| Сжатие | — | nginx gzip | закрыто (`e4f35aa`) |

**Verdict Details**: E2E Flow — PASS; Error Recovery — CONCERNS (C8); Data Integrity — PASS; Environment Parity — CONCERNS (C9, D3); Observability — PASS (журнал, логи uvicorn).

---

## 8. DX Engineer

**Verdict**: APPROVE WITH CHANGES

**Error Message Audit**

| Сценарий | Сейчас | Нужно разработчику | Предлагаемое |
|---|---|---|---|
| `make test-pg` без `PG` | тесты идут на SQLite, «passed» | понять, что PostgreSQL не проверялся | «задайте PG=postgresql://…» и остановка |
| Короткий `AGENTPAY_SECRET_KEY` | сервер стартует, каждый запрос 500 | узнать при старте | процесс не стартует: «AGENTPAY_SECRET_KEY короче 32 символов» |
| `make eval-llm`, модель недоступна | (исправлено в этой ветке) | — | «модель … недоступна (провайдер, адрес)» |

**Developer Journey**: `make install` → `make test` → `make e2e-local` работают без Docker; `.env` подхватывается; трение — D2, D3.

**Verdict Details**: Error Quality — CONCERNS (D2, D3); Defaults — PASS; Keyboard — PASS; Discoverability — PASS (README); Workflow — PASS (CI, make).

---

## 9. Performance Engineer

**Verdict**: APPROVE WITH CHANGES

**Resource Budget**

| Ресурс | Сейчас | Замечание |
|---|---|---|
| Запросов на поручение | 1 (`POST /api/tasks`) + журнал при открытой вкладке | — |
| Записей журнала на поручение | ~8 транзакций | приемлемо |
| `GET /api/audit` | O(всех записей банка) | C6 |
| Опрос модели | раз в 15 с, синхронно в запросе | C7 |
| Бандл | 182 КБ, 58 КБ gzip | — |
| Lighthouse (мобильный) | Performance 98–99 | — |

**Hot Path Analysis**: поручение — разбор, три каталога, проверки — 0,02 с медианы на правилах; модель вызывается вне блокировки БД. Узкие места — C6 и C7; на фронтенде — C10.

**Verdict Details**: Memory — PASS; Network — CONCERNS (C7); I/O — CONCERNS (C6); Startup — PASS; Long-Session — PASS.

---

## 10. Accessibility Engineer

**Verdict**: APPROVE WITH CHANGES

**Keyboard Navigation Walkthrough**

| Шаг | Действие | Фокус | Объявление | Есть? |
|---|---|---|---|---|
| 1 | Tab с начала | первый пример поручения | «кнопка, Найди SSD…» | ✓ |
| 2 | Tab | поле поручения | «Поручение агенту, поле ввода» | ✓ |
| 3 | Enter | — | новое сообщение в role=log | ✓ |
| 4 | Tab к вкладкам, ← → | вкладки панели | «вкладка, выбрана» | ✓ |
| 5 | Открыть окно подтверждения | поле кода | push (role=status), заголовок окна | ✓ |
| 6 | Tab ×8 | внутри окна | — | ✓ |
| 7 | Esc | «Ввести код подтверждения» | — | ✓ |
| 8 | Отказ Policy Engine на телефоне | — | **не объявляется** | ✗ B14 |

**ARIA Checklist**

| Элемент | Роль | Подпись | Состояния | Клавиатура |
|---|---|---|---|---|
| Переписка | log | «Переписка с агентом» | — | — |
| Вкладки панели | tablist/tab/tabpanel | ✓ | aria-selected | ← → |
| Нижняя навигация | navigation | «Разделы» | aria-current | Tab |
| Окно подтверждения | dialog, aria-modal | заголовок | — | ловушка, Esc |
| Ошибка кода | alert | — | aria-invalid | — |
| Статус заказа | — | — | — | B14 |

**Verdict Details**: Screen Reader — CONCERNS (B14); Keyboard — PASS; Focus — PASS; Contrast — PASS (axe + WCAG 1.4.11 тестом); Motion — PASS.
