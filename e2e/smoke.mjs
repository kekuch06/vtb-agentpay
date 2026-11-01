// Сквозной сценарий в настоящем браузере: поручение → заказ → Policy Engine → код с «устройства» → оплата → журнал.
// Запуск против поднятого стенда: E2E_BASE_URL=http://localhost:8080 npm run smoke (по умолчанию — make up).
// Кнопки нажимаются сразу после загрузки страницы, как при быстром клике пользователя.
import { chromium } from "playwright";

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:8080";
const TASK = "Найди SSD Samsung 2 ТБ с гарантией и доставкой до пятницы";

const browser = await chromium.launch();
const page = await browser.newPage();
const errors = [];
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));  // в том числе нарушения CSP
page.on("pageerror", (e) => errors.push(String(e)));
page.setDefaultTimeout(30_000);

try {
  await page.goto(BASE);
  await page.getByRole("button", { name: TASK }).click();
  await page.getByRole("button", { name: "Оформить заказ" }).first().click();
  await page.getByRole("button", { name: "Проверить и перейти к оплате" }).click();
  const push = await page.locator(".modal .push").innerText();
  const code = push.match(/\b\d{6}\b/)?.[0];
  if (!code) throw new Error(`код не пришёл на «устройство»: ${push}`);
  await page.locator("input.code").fill(code);
  await page.getByRole("button", { name: "Подтвердить оплату" }).click();
  const receipt = await page.locator(".receipt b").innerText();
  const amount = await page.locator(".order .row.big b").innerText();
  await page.getByRole("button", { name: "Журнал" }).click();
  const chain = await page.locator(".audit .pill").innerText();
  if (chain !== "Хеш-цепочка цела") throw new Error(`журнал: ${chain}`);
  if (errors.length) throw new Error(`ошибки в консоли: ${errors.join(" | ")}`);
  console.log(`OK: ${receipt}, ${amount}, ${chain}`);
} catch (e) {
  await page.screenshot({ path: "smoke-failure.png", fullPage: true }).catch(() => {});
  console.error(`E2E FAIL: ${e.message}`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
