// Действия пользователя в интерфейсе. Селекторы собраны здесь: при изменении разметки правится только этот файл.
import { expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

export const MAIN = "Найди SSD Samsung 2 ТБ с гарантией и доставкой до пятницы";
export const EXAMPLES = [
  MAIN,
  "Наушники Sony WH-1000XM5 до 29 тысяч",
  "Оперативка DDR5 32 ГБ Kingston до 9500 рублей",
  "SSD 2 ТБ до 12 тысяч с гарантией",
];

// Ошибки консоли (в том числе нарушения CSP) и необработанные исключения страницы — провал теста.
// Ответы 4xx — ожидаемые отказы контура (неверный код, Policy Engine), браузер пишет их в консоль сам.
export function watchConsole(page) {
  const errors = [];
  const expected = /Failed to load resource: the server responded with a status of 4\d\d/;
  page.on("console", (m) => m.type() === "error" && !expected.test(m.text()) && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(String(e)));
  return errors;
}

export async function resetCatalogs(request) {
  const r = await request.post("/api/sandbox/scenario", { data: { name: "reset" } });
  expect(r.ok()).toBeTruthy();
}

export const composer = (page) => page.locator("form.composer input");
export const agentReplies = (page) => page.locator(".msg.agent.reply");
export const events = (page) => page.locator(".event");
export const dialog = (page) => page.getByRole("dialog");

export async function sendTask(page, text) {
  if (isPhone(page) && !(await composer(page).isVisible())) await openTab(page, "Агент");
  const before = await agentReplies(page).count();
  await composer(page).fill(text);
  await composer(page).press("Enter");
  await expect(agentReplies(page)).toHaveCount(before + 1);
  return agentReplies(page).last();
}

export const isPhone = (page) => page.viewportSize().width < 960;

// Телефон: плавающая навигация внизу (в имени кнопки может быть счётчик). Компьютер: вкладки панели, чат виден всегда.
export async function openTab(page, name) {
  const re = new RegExp(`^${name}`);
  if (isPhone(page)) await page.getByRole("navigation", { name: "Разделы" }).getByRole("button", { name: re }).click();
  else if (name !== "Агент") await page.getByRole("tab", { name: re }).click();
}

export async function orderRecommended(page) {
  await openTab(page, "Предложения");
  await page.getByRole("button", { name: "Оформить заказ" }).filter({ visible: true }).first().click();
  await expect(page.locator(".order-card")).toBeVisible();
}

export async function checkout(page) {
  await page.getByRole("button", { name: "Проверить и перейти к оплате" }).click();
}

export async function codeFromDevice(page) {
  const push = await page.locator(".modal .push").innerText();
  const code = push.match(/\b\d{6}\b/)?.[0];
  expect(code, `код не пришёл на «устройство»: ${push}`).toBeTruthy();
  return code;
}

export async function enterCode(page, code) {
  await page.locator("input.code").fill(code);
  await page.getByRole("button", { name: "Подтвердить оплату" }).click();
}

export const orderStatus = (page) => page.locator(".status-banner .status-title");
export const orderAmount = (page) => page.locator(".order .row.big b");

export async function pay(page) {
  await orderRecommended(page);
  await checkout(page);
  const code = await codeFromDevice(page);
  await enterCode(page, code);
  await expect(page.locator(".receipt")).toBeVisible();
}

export async function sandbox(page, title) {
  await openTab(page, "Sandbox");
  await page.getByRole("button", { name: new RegExp(title) }).click();
  await expect(events(page).last()).toContainText("Sandbox");
}

// axe-core: ноль нарушений уровня serious и critical (WCAG 2.1 A/AA).
export async function expectAccessible(page, label) {
  // Контраст считается по итоговому кадру: ждём конца анимаций появления (бесконечные — индикатор набора — не ждём).
  await page.waitForFunction(() => document.getAnimations().every(
    (a) => a.playState !== "running" || a.effect?.getComputedTiming().iterations === Infinity));
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  const report = bad.map((v) => `${v.id} (${v.impact}): ${v.help} — ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(", ")}`);
  expect(report, `axe, ${label}`).toEqual([]);
}

export async function expectNoHorizontalScroll(page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow, "горизонтальная прокрутка страницы").toBeLessThanOrEqual(0);
}
