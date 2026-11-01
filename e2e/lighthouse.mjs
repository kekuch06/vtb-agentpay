// Lighthouse на мобильном профиле (по умолчанию: Moto G Power, медленный 4G, замедленный CPU) против стенда.
// Падает, если Performance, Accessibility или Best Practices ниже 90. Отчёт — lighthouse-report.html (в git не попадает).
// Запуск: E2E_BASE_URL=http://localhost:8080 npm run lighthouse
import { writeFileSync } from "node:fs";
import * as chromeLauncher from "chrome-launcher";
import lighthouse from "lighthouse";
import { chromium } from "playwright";

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:8080";
const MIN = 90;
const chrome = await chromeLauncher.launch({
  chromePath: process.env.CHROME_PATH ?? chromium.executablePath(),
  chromeFlags: ["--headless=new", "--no-sandbox", "--disable-gpu"],
});
try {
  const { lhr, report } = await lighthouse(BASE, {
    port: chrome.port, output: "html", logLevel: "error",
    onlyCategories: ["performance", "accessibility", "best-practices", "seo"],
  });
  writeFileSync("lighthouse-report.html", report);
  const scores = Object.fromEntries(Object.values(lhr.categories).map((c) => [c.id, Math.round(c.score * 100)]));
  const metrics = Object.fromEntries(["first-contentful-paint", "largest-contentful-paint", "total-blocking-time", "cumulative-layout-shift", "speed-index"]
    .map((id) => [id, lhr.audits[id].displayValue]));
  console.log(JSON.stringify({ url: BASE, formFactor: lhr.configSettings.formFactor, lighthouse: lhr.lighthouseVersion, scores, metrics }, null, 2));
  const failed = lhr.audits && Object.values(lhr.categories).flatMap((c) => c.auditRefs.filter((r) => r.weight > 0)
    .map((r) => lhr.audits[r.id]).filter((a) => a.score !== null && a.score < 0.9).map((a) => `${c.id}: ${a.id} (${a.displayValue ?? a.score})`));
  if (failed.length) console.log("Аудиты ниже 0.9:\n  " + [...new Set(failed)].join("\n  "));
  const low = ["performance", "accessibility", "best-practices"].filter((k) => scores[k] < MIN);
  if (low.length) {
    console.error(`Lighthouse ниже ${MIN}: ${low.map((k) => `${k} ${scores[k]}`).join(", ")}`);
    process.exitCode = 1;
  }
} finally {
  await chrome.kill();
}
