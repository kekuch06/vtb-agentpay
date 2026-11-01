// Сквозные сценарии: каждое поручение-пример, все сценарии вкладки Sandbox, неверный и повторный ввод кода,
// отказ сервера, клавиатура и доступность. Каждый тест — новый клиент (своя cookie-сессия).
import { expect, test } from "@playwright/test";
import {
  EXAMPLES, MAIN, agentReplies, checkout, codeFromDevice, composer, dialog, enterCode, events, expectAccessible,
  expectNoHorizontalScroll, openTab, orderAmount, orderRecommended, orderStatus, pay, resetCatalogs, sandbox, sendTask,
  watchConsole,
} from "./ui.mjs";

let consoleErrors;
test.beforeEach(async ({ page, request }) => {
  await resetCatalogs(request);
  consoleErrors = watchConsole(page);
  await page.goto("/");
});
test.afterEach(async ({}, info) => {
  if (!info.annotations.some((a) => a.type === "console-errors-expected")) {
    expect(consoleErrors, "ошибки в консоли браузера").toEqual([]);
  }
});

for (const example of EXAMPLES) {
  test(`пример «${example}»: от поручения до оплаты или понятного отказа`, async ({ page }) => {
    await page.getByRole("button", { name: example, exact: true }).click();
    const reply = agentReplies(page).last();
    await expect(reply).toBeVisible();
    if (example.includes("до 12 тысяч")) {
      await expect(reply).toContainText("ни одно не выполняет все условия");
      await openTab(page, "Предложения");
      await expect(page.getByRole("button", { name: "Оформить заказ" })).toHaveCount(0);
      return;
    }
    await pay(page);
    await expect(orderStatus(page)).toHaveText("Оплачен (sandbox)");
    await expect(orderAmount(page)).toHaveText(/\d[\d\s ]*₽/);
    await openTab(page, "Журнал");
    await expect(page.locator(".audit .pill").first()).toHaveText("Хеш-цепочка цела");
    await expect(page.locator(".audit")).toContainText("payment.succeeded");
    await expectNoHorizontalScroll(page);
  });
}

test("неверный код трижды: заказ отклонён, правильный код больше не принимается", async ({ page }) => {
  await sendTask(page, MAIN);
  await orderRecommended(page);
  await checkout(page);
  const code = await codeFromDevice(page);
  const wrong = code === "000000" ? "111111" : "000000";
  await enterCode(page, wrong);
  await expect(dialog(page)).toContainText(/осталось попыток: 2/i);
  await enterCode(page, wrong);
  await expect(dialog(page)).toContainText(/осталось попыток: 1/i);
  await enterCode(page, wrong);
  await expect(events(page).last()).toContainText(/попытки закончились/i);
  await expect(orderStatus(page)).toHaveText("Отклонён");
  await expect(page.getByRole("button", { name: "Подтвердить оплату" })).toHaveCount(0);
  await openTab(page, "Журнал");
  await expect(page.locator(".audit")).not.toContainText("payment.succeeded");
});

test("sandbox: рост цены после заказа — Policy Engine отклоняет до запроса кода", async ({ page }) => {
  await sendTask(page, MAIN);
  await orderRecommended(page);
  await sandbox(page, "Рост цены");
  await openTab(page, "Заказ");
  await checkout(page);
  await expect(events(page).last()).toContainText("Policy Engine отклонил заказ");
  await expect(page.locator(".checks li.bad")).toContainText(["Перепроверка цены"]);
  await expect(orderStatus(page)).toHaveText("Отклонён");
  await expect(dialog(page)).toHaveCount(0);
});

test("sandbox: рост цены между кодом и подтверждением — платёж остановлен после ввода кода", async ({ page }) => {
  await sendTask(page, MAIN);
  await orderRecommended(page);
  await checkout(page);
  const code = await codeFromDevice(page);
  await page.getByRole("button", { name: "Отмена" }).click();
  await sandbox(page, "Рост цены");
  await openTab(page, "Заказ");
  await page.getByRole("button", { name: "Ввести код подтверждения" }).click();
  await enterCode(page, code);
  await expect(events(page).last()).toContainText("Платёж остановлен после подтверждения");
  await expect(orderStatus(page)).toHaveText("Отклонён");
  await openTab(page, "Журнал");
  await expect(page.locator(".audit")).toContainText("policy.blocked_at_payment");
  await expect(page.locator(".audit")).not.toContainText("payment.succeeded");
});

test("sandbox: товар закончился — оплата невозможна", async ({ page }) => {
  await sendTask(page, MAIN);
  await orderRecommended(page);
  await sandbox(page, "Товар закончился");
  await openTab(page, "Заказ");
  await checkout(page);
  await expect(page.locator(".checks li.bad")).toContainText(["Перепроверка наличия"]);
  await expect(orderStatus(page)).toHaveText("Отклонён");
});

test("sandbox: подмена реквизитов в заказе — обнаружена по HMAC и реестру", async ({ page }) => {
  await sendTask(page, MAIN);
  await orderRecommended(page);
  await sandbox(page, "Подмена реквизитов");
  await openTab(page, "Заказ");
  await checkout(page);
  await expect(page.locator(".checks li.bad")).toContainText(["Целостность заказа", "Получатель платежа"]);
  await expect(orderStatus(page)).toHaveText("Отклонён");
});

test("sandbox: лимит на одну покупку ниже суммы — заказ отклонён", async ({ page }) => {
  await openTab(page, "Sandbox");
  await page.getByLabel("Лимит на одну покупку").selectOption("5000");
  await sendTask(page, MAIN);
  await orderRecommended(page);
  await checkout(page);
  await expect(page.locator(".checks li.bad")).toContainText(["Лимит на одну покупку"]);
  await expect(orderStatus(page)).toHaveText("Отклонён");
});

test("отклонённые и подозрительные карточки видны, но заказать их нельзя", async ({ page }) => {
  await sendTask(page, MAIN);
  await openTab(page, "Предложения");
  await page.getByRole("button", { name: /Показать отклонённые/ }).click();
  const suspicious = page.locator(".offer", { hasText: "Подозрительная карточка" });
  await expect(suspicious).toHaveCount(1);
  await expect(suspicious.getByRole("button", { name: "Оформить заказ" })).toHaveCount(0);
});

test("сервер недоступен: понятное сообщение вместо зависания", async ({ page }) => {
  test.info().annotations.push({ type: "console-errors-expected" });
  await page.route("**/api/**", (r) => r.abort());
  await composer(page).fill(MAIN);
  await composer(page).press("Enter");
  await expect(events(page).last()).toContainText("Сервер недоступен");
  await expect(composer(page)).toBeEnabled();
});

test("товар, которого нет в каталогах, не подменяется другим", async ({ page }) => {
  const reply = await sendTask(page, "Видеокарта RTX 4090");
  await expect(reply).not.toContainText("Лучший вариант");
  await openTab(page, "Предложения");
  await expect(page.getByRole("button", { name: "Оформить заказ" })).toHaveCount(0);
});

test("двойной Enter в поле кода: оплаченный заказ не превращается в отклонённый", async ({ page }) => {
  await sendTask(page, MAIN);
  await orderRecommended(page);
  await checkout(page);
  const code = await codeFromDevice(page);
  const confirms = [];
  page.on("request", (r) => r.url().includes("/confirm") && confirms.push(r.url()));
  const input = page.locator("input.code");
  await input.fill(code);
  // Две отправки формы в одном кадре — как дребезг клавиши Enter или нетерпеливый пользователь.
  await input.evaluate((el) => { el.form.requestSubmit(); el.form.requestSubmit(); });
  await expect(page.locator(".receipt")).toBeVisible();
  await expect(orderStatus(page)).toHaveText("Оплачен (sandbox)");
  await expect(events(page).filter({ hasText: /отклонён|использовано|закрыт/i })).toHaveCount(0);
  expect(confirms, "запросов подтверждения").toHaveLength(1);
});

test("ошибка сохранения лимита не выглядит как успех", async ({ page }) => {
  test.info().annotations.push({ type: "console-errors-expected" });
  await openTab(page, "Sandbox");
  const select = page.getByLabel("Лимит на одну покупку");
  const before = await select.inputValue();
  await page.route("**/api/settings", (r) => r.request().method() === "PUT" ? r.fulfill({ status: 500, body: "{}" }) : r.continue());
  await select.selectOption("5000");
  await expect(select).toHaveValue(before);
  await expect(events(page).last()).toContainText("Лимит не изменён");
});

test("окно подтверждения: фокус на поле кода, Tab не уходит за окно, Esc закрывает", async ({ page }) => {
  await sendTask(page, MAIN);
  await orderRecommended(page);
  await checkout(page);
  await expect(page.locator("input.code")).toBeFocused();
  for (let i = 0; i < 8; i++) {
    await page.keyboard.press("Tab");
    expect(await page.evaluate(() => !!document.activeElement?.closest("[role=dialog]")), "фокус вышел из окна").toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(dialog(page)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Ввести код подтверждения" })).toBeFocused();
});

test("доступность: axe без serious и critical на всех экранах", async ({ page }) => {
  await expectAccessible(page, "стартовый экран");
  await sendTask(page, MAIN);
  await expectAccessible(page, "ответ агента");
  await openTab(page, "Предложения");
  await expectAccessible(page, "предложения");
  await orderRecommended(page);
  await expectAccessible(page, "заказ");
  await checkout(page);
  await expectAccessible(page, "окно подтверждения");
  await page.keyboard.press("Escape");
  await openTab(page, "Журнал");
  await expectAccessible(page, "журнал");
  await openTab(page, "Sandbox");
  await expectAccessible(page, "sandbox");
  await expectNoHorizontalScroll(page);
});

test("тёмная тема: axe без serious и critical", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await expectAccessible(page, "старт, тёмная тема");
  await sendTask(page, MAIN);
  await expectAccessible(page, "ответ агента, тёмная тема");
  await orderRecommended(page);
  await checkout(page);
  await expectAccessible(page, "окно подтверждения, тёмная тема");
  await page.keyboard.press("Escape");
  await openTab(page, "Sandbox");
  await expectAccessible(page, "sandbox, тёмная тема");
});

test("клавиатура: поручение с клавиатуры, вкладки панели переключаются стрелками", async ({ page }) => {
  test.skip(page.viewportSize().width < 960, "вкладки панели есть только на компьютере");
  await composer(page).focus();
  await page.keyboard.type(MAIN);
  await page.keyboard.press("Enter");
  await expect(agentReplies(page)).toHaveCount(1);
  await page.getByRole("tab", { name: /^Предложения/ }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: /^Заказ/ })).toBeFocused();
  await expect(page.getByRole("tab", { name: /^Заказ/ })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByRole("tab", { name: /^Sandbox/ })).toBeFocused();
});

test("на телефоне вкладка открывается с начала, а оформленный заказ не дублируется из чата", async ({ page }) => {
  test.skip(page.viewportSize().width >= 960, "только телефон");
  await sendTask(page, MAIN);
  await page.mouse.wheel(0, 2000);
  await page.waitForTimeout(800);  // прокрутка колесом плавная: даём ей закончиться, как пальцу перед нажатием
  await openTab(page, "Предложения");
  await expect.poll(() => page.evaluate(() => window.scrollY), { timeout: 3000 }).toBe(0);
  await page.getByRole("button", { name: "Оформить заказ" }).filter({ visible: true }).first().click();
  await openTab(page, "Агент");
  await expect(agentReplies(page).last().getByRole("button", { name: "Оформить заказ" })).toHaveCount(0);
  await agentReplies(page).last().getByRole("button", { name: "Открыть заказ" }).click();
  await expect(orderStatus(page)).toHaveText("Заказ оформлен");
});

test("код истёк: окно предлагает новый код, оплата проходит с ним", async ({ page }) => {
  await sendTask(page, MAIN);
  await orderRecommended(page);
  await checkout(page);
  const old = await codeFromDevice(page);
  await page.getByRole("button", { name: "Отмена" }).click();
  await sandbox(page, "Код истёк");
  await openTab(page, "Заказ");
  await page.getByRole("button", { name: "Ввести код подтверждения" }).click();
  await enterCode(page, old);
  await expect(dialog(page).getByRole("alert")).toContainText("истёк");
  await expect(orderStatus(page)).toHaveText("Ждёт подтверждения");  // заказ не закрыт, неверных попыток не добавилось
  // Push с новым кодом приходит с задержкой — как на настоящем устройстве. Пока его нет, старый код показывать нельзя:
  // введённый по ошибке, он сжёг бы одну из трёх попыток по заказу.
  await page.route("**/api/sandbox/device", async (r) => { await new Promise((ok) => setTimeout(ok, 700)); await r.continue(); });
  await dialog(page).getByRole("button", { name: "Запросить новый код" }).click();
  await expect(dialog(page).getByRole("button", { name: "Подтвердить оплату" })).toBeVisible();
  expect(await page.locator(".modal .push").innerText(), "в окне старый код").not.toContain(old);  // мгновенный снимок, без ожидания
  await page.unroute("**/api/sandbox/device");
  await expect(page.locator(".modal .push")).toContainText(/\b\d{6}\b/);
  const fresh = await codeFromDevice(page);
  expect(fresh).not.toBe(old);
  await enterCode(page, fresh);
  await expect(page.locator(".receipt")).toBeVisible();
  await expect(orderStatus(page)).toHaveText("Оплачен (sandbox)");
});

// WCAG 1.4.11: граница поля и кольцо фокуса отличимы от фона не меньше чем 3:1. axe-core это не проверяет.
test("контраст границ полей и кольца фокуса не ниже 3:1 (WCAG 1.4.11)", async ({ page }) => {
  for (const scheme of ["light", "dark"]) {
    await page.emulateMedia({ colorScheme: scheme });
    await composer(page).focus();
    const r = await composer(page).evaluate((el) => {
      const rgb = (c) => c.match(/[\d.]+/g).slice(0, 3).map(Number);
      const lum = ([r, g, b]) => [r, g, b].map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; })
        .reduce((s, v, i) => s + v * [0.2126, 0.7152, 0.0722][i], 0);
      const ratio = (a, b) => { const [x, y] = [lum(rgb(a)), lum(rgb(b))].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
      const bg = getComputedStyle(el.closest(".dock")).backgroundColor;
      el.blur();
      const border = getComputedStyle(el).borderTopColor;
      el.focus();
      const ring = getComputedStyle(el).outlineColor;
      return { border: ratio(border, bg), ring: ratio(ring, bg) };
    });
    expect(r.border, `${scheme}: граница поля`).toBeGreaterThanOrEqual(3);
    expect(r.ring, `${scheme}: кольцо фокуса`).toBeGreaterThanOrEqual(3);
  }
});

test("статус заказа объявляется экранным диктором, даже когда чат скрыт (телефон)", async ({ page }) => {
  // Было: на телефоне при открытой вкладке «Заказ» чат скрыт, и отказ Policy Engine не попадал ни в один live-регион.
  await sendTask(page, MAIN);
  await orderRecommended(page);
  await sandbox(page, "Товар закончился");
  await openTab(page, "Заказ");
  await checkout(page);
  await expect(page.getByRole("status").filter({ hasText: "Отклонён" })).toBeVisible();
});

test("журнал: опоздавший ответ не перезаписывает свежий", async ({ page }) => {
  const created = page.waitForResponse((r) => r.url().endsWith("/api/tasks") && r.request().method() === "POST");
  await sendTask(page, MAIN);
  const task = await (await created).json();
  let first = true;
  await page.route("**/api/audit*", async (route) => {
    if (!first) return route.continue();
    first = false;
    const response = await route.fetch();  // ответ снят сейчас, а отдан позже — как медленная сеть
    await new Promise((ok) => setTimeout(ok, 1500));
    await route.fulfill({ response });
  });
  await openTab(page, "Журнал");  // запрос A: медленный и без новой записи
  await page.request.post("/api/orders", { data: { task_id: task.task_id, offer_id: task.recommended_offer_id } });
  await openTab(page, "Предложения");
  await openTab(page, "Журнал");  // запрос B: быстрый, с записью о заказе, пока A ещё в пути
  await page.waitForTimeout(2000);  // A пришёл после B
  await expect(page.locator(".audit")).toContainText("order.created");
});

test("после отказа поручение повторяется одной кнопкой", async ({ page }) => {
  await sendTask(page, MAIN);
  await orderRecommended(page);
  await sandbox(page, "Рост цены");
  await openTab(page, "Заказ");
  await checkout(page);
  await expect(orderStatus(page)).toHaveText("Отклонён");
  const before = await agentReplies(page).count();
  await page.getByRole("button", { name: "Повторить поручение" }).click();
  await expect(agentReplies(page)).toHaveCount(before + 1);
});

test("лимит на одну покупку: повышение только кодом с устройства, неверный код не меняет лимит", async ({ page }) => {
  await openTab(page, "Sandbox");
  const select = page.getByLabel("Лимит на одну покупку");
  await select.selectOption("50000");
  await expect(dialog(page)).toContainText("Подтвердите новый лимит");
  await expect(select).toHaveValue("30000");  // без кода лимит прежний
  const code = (await page.locator(".modal .push").innerText()).match(/\b\d{6}\b/)[0];
  const wrong = code === "000000" ? "111111" : "000000";
  await page.locator("input.code").fill(wrong);
  await page.getByRole("button", { name: "Подтвердить лимит" }).click();
  await expect(dialog(page).getByRole("alert")).toContainText("осталось попыток: 2");
  await page.locator("input.code").fill(code);
  await page.getByRole("button", { name: "Подтвердить лимит" }).click();
  await expect(dialog(page)).toHaveCount(0);
  await expect(select).toHaveValue("50000");
  await expect(page.locator(".hero-amount")).toHaveText(/50\s000/);
  await select.selectOption("10000");  // понижение — сразу, без окна
  await expect(select).toHaveValue("10000");
  await expect(dialog(page)).toHaveCount(0);
});
