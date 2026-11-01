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
  await expect(events(page).last()).toContainText(/превышено число попыток/i);
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

test("sandbox: лимит на операцию ниже суммы — заказ отклонён", async ({ page }) => {
  await openTab(page, "Sandbox");
  await page.getByLabel("Лимит на одну операцию").selectOption("5000");
  await sendTask(page, MAIN);
  await orderRecommended(page);
  await checkout(page);
  await expect(page.locator(".checks li.bad")).toContainText(["Лимит на операцию"]);
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
  const select = page.getByLabel("Лимит на одну операцию");
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
  await expect(orderStatus(page)).toHaveText("Заказ создан");
});
