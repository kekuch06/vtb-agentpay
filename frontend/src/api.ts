export type Check = { name: string; passed: boolean; detail: string };
export type Offer = {
  offer_id: string; merchant_id: string; merchant_name: string; sku: string; title: string; brand: string;
  category: string; capacity_gb: number | null; price_kop: number; delivery_kop: number; total_kop: number;
  delivery_date: string; warranty_months: number; in_stock: boolean; description: string;
};
export type CheckedOffer = { offer: Offer; checks: Check[]; passed: boolean; score: number | null };
export type TraceStep = { step: string; zone: "untrusted" | "trusted" | "user"; detail: string; ms: number };
export type Constraints = {
  category: string; brand: string | null; capacity_gb: number | null; max_total_rub: number | null;
  deliver_by: string | null; min_warranty_months: number | null; keywords: string[];
};
export type Task = {
  task_id: string; text: string; parser: string; constraints: Constraints; offers: CheckedOffer[];
  recommended_offer_id: string | null; explanation: string; trace: TraceStep[];
};
export type OrderStatus = "draft" | "awaiting_confirmation" | "paid" | "rejected" | "expired";
export type Order = {
  order_id: string; task_id: string; offer_id: string; merchant_id: string; merchant_name: string;
  payee_account: string; sku: string; title: string; amount_kop: number; status: OrderStatus; expires_at: string;
};
export type PolicyResult = { rule: string; passed: boolean; detail: string };
export type Challenge = {
  challenge_id: string; order_id: string; amount_kop: number; payee_account: string; merchant_name: string;
  expires_at: string;
};
// Push на устройство клиента. Код не приходит в ответе checkout — только этим каналом.
export type DeviceMessage = {
  kind: "payment" | "limit"; challenge_id: string; order_id: string; merchant_name: string; amount_kop: number; code: string; expires_at: string;
};
export type Payment = { payment_id: string; order_id: string; amount_kop: number; payee_account: string; created_at: string };
export type AuditEntry = {
  id: number; ts: string; actor: string; zone: string; action: string; task_id: string | null;
  order_id: string | null; data: Record<string, unknown>; hash: string;
};
export type Health = { llm: { mode: string; model: string; available: boolean }; today: string; sandbox: boolean };
export type Limits = { per_operation_limit_rub: number; daily_limit_rub: number; spent_today_kop: number };
// Повышение лимита ждёт кода с устройства: сервер отвечает 202 и запросом подтверждения.
export type LimitRequest = { request_id: string; value: number; from: number; expires_at: string };

export class ApiError extends Error {
  constructor(public status: number, public body: any) {
    super(typeof body?.detail === "string" ? body.detail : body?.detail?.message ?? `HTTP ${status}`);
  }
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(r.status, data);
  return data as T;
}

export const api = {
  health: () => req<Health>("GET", "/api/health"),
  task: (text: string) => req<Task>("POST", "/api/tasks", { text }),
  order: (task_id: string, offer_id: string) =>
    req<{ order: Order; policy: PolicyResult[] }>("POST", "/api/orders", { task_id, offer_id }),
  getOrder: (id: string) => req<{ order: Order }>("GET", `/api/orders/${encodeURIComponent(id)}`),
  checkout: (id: string) =>
    req<{ order: Order; policy: PolicyResult[]; challenge: Challenge | null }>("POST", `/api/orders/${encodeURIComponent(id)}/checkout`),
  confirm: (id: string, challenge_id: string, code: string) =>
    req<{ order: Order; payment: Payment }>("POST", `/api/orders/${encodeURIComponent(id)}/confirm`, { challenge_id, code }),
  audit: (task_id?: string) =>
    req<{ entries: AuditEntry[]; chain: { ok: boolean } }>(
      "GET", task_id ? `/api/audit?task_id=${encodeURIComponent(task_id)}` : "/api/audit"),
  settings: () => req<Limits>("GET", "/api/settings"),
  setLimit: (v: number) => req<Limits & { confirmation?: LimitRequest }>("PUT", "/api/settings", { per_operation_limit_rub: v }),
  confirmLimit: (request_id: string, code: string) => req<Limits>("POST", "/api/settings/confirm", { request_id, code }),
  device: () => req<{ messages: DeviceMessage[] }>("GET", "/api/sandbox/device"),
  scenario: (name: string, extra: { offer_id?: string; order_id?: string } = {}) =>
    req<{ message: string }>("POST", "/api/sandbox/scenario", { name, ...extra }),
};

/** Текст ошибки для человека: что случилось и что делать, без кодов HTTP и внутренних адресов. */
export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status >= 500 || e.message.startsWith("HTTP ")) return "Сервер ответил ошибкой. Повторите через минуту";
    if (e.status === 422) return "Сервер не принял запрос: проверьте введённые данные";
    return capitalize(e.message);
  }
  return "Сервер недоступен. Проверьте подключение и повторите";
}

export const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
export const rub = (v: number) => v.toLocaleString("ru-RU") + " ₽";
// Суммы заказа и платежа приходят в копейках: 1848750 → «18 487,50 ₽», 1479000 → «14 790 ₽».
export const money = (kop: number) =>
  (kop / 100).toLocaleString("ru-RU", { minimumFractionDigits: kop % 100 ? 2 : 0, maximumFractionDigits: 2 }) + " ₽";
export const ddmm = (iso: string) => iso.slice(8, 10) + "." + iso.slice(5, 7);
export const hhmm = (iso: string) => new Date(iso).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
// Номер счёта группами, как в платёжных документах: 40702 810 9 0000 0000001 (балансовый счёт, валюта, ключ, остальное).
export const account = (n: string) =>
  /^\d{20}$/.test(n) ? `${n.slice(0, 5)} ${n.slice(5, 8)} ${n.slice(8, 9)} ${n.slice(9, 13)} ${n.slice(13)}` : n;
