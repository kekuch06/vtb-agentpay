// Сквозные сценарии в браузере на ширине телефона (390) и компьютера (1280).
// Против поднятого стенда: E2E_BASE_URL=http://localhost:8080 npx playwright test (так идёт в CI на Docker Compose).
// Без E2E_BASE_URL поднимается локальный сервер на собранном фронтенде (make e2e-local): разбор правилами,
// фиксированная дата, свежая база SQLite и случайный ключ на каждый прогон.
import { randomBytes } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig, devices } from "@playwright/test";

const external = process.env.E2E_BASE_URL;
const port = Number(process.env.E2E_PORT ?? 8765);
const db = join(tmpdir(), `agentpay-e2e-${process.pid}.sqlite3`);

export default defineConfig({
  testDir: "tests",
  // Сценарии sandbox меняют общий тестовый каталог продавцов, поэтому тесты идут по одному.
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  use: {
    baseURL: external ?? `http://127.0.0.1:${port}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    locale: "ru-RU",
    timezoneId: "Europe/Moscow",
  },
  projects: [
    { name: "phone-390", use: { ...devices["iPhone 14"], browserName: "chromium", viewport: { width: 390, height: 844 } } },
    { name: "desktop-1280", use: { browserName: "chromium", viewport: { width: 1280, height: 800 } } },
  ],
  webServer: external ? undefined : {
    command: `rm -f ${db} && cd ../backend && .venv/bin/uvicorn app.main:app --port ${port}`,
    url: `http://127.0.0.1:${port}/api/health`,
    reuseExistingServer: false,
    env: {
      AGENTPAY_LLM: "rules",
      AGENTPAY_TODAY: "2026-11-02",
      AGENTPAY_DATABASE_PATH: db,
      AGENTPAY_SECRET_KEY: randomBytes(32).toString("hex"),
    },
  },
});
