export type Check = { name: string; passed: boolean; detail: string };
export type Offer = {
  offer_id: string; merchant_id: string; merchant_name: string; sku: string; title: string; brand: string;
  category: string; capacity_gb: number | null; price_rub: number; delivery_rub: number; total_rub: number;
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
export type Order = {
  order_id: string; task_id: string; offer_id: string; merchant_id: string; merchant_name: string;
  payee_account: string; sku: string; title: string; amount_rub: number; status: string; expires_at: string;
};
export type PolicyResult = { rule: string; passed: boolean; detail: string };
export type Challenge = {
  challenge_id: string; order_id: string; amount_rub: number; payee_account: string; merchant_name: string;
  expires_at: string; sandbox_code: string;
};
export type Payment = { payment_id: string; order_id: string; amount_rub: number; payee_account: string; created_at: string };
export type AuditEntry = {
  id: number; ts: string; actor: string; zone: string; action: string; task_id: string | null;
  order_id: string | null; data: Record<string, unknown>; hash: string;
};
export type Health = { llm: { mode: string; model: string; available: boolean }; today: string; sandbox: boolean };

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
  checkout: (id: string) =>
    req<{ order: Order; policy: PolicyResult[]; challenge: Challenge | null }>("POST", `/api/orders/${id}/checkout`),
  confirm: (id: string, challenge_id: string, code: string) =>
    req<{ order: Order; payment: Payment }>("POST", `/api/orders/${id}/confirm`, { challenge_id, code }),
  audit: (task_id?: string) =>
    req<{ entries: AuditEntry[]; chain: { ok: boolean; entries: number } }>(
      "GET", task_id ? `/api/audit?task_id=${task_id}` : "/api/audit"),
  settings: () => req<{ per_operation_limit_rub: number; daily_limit_rub: number }>("GET", "/api/settings"),
  setLimit: (v: number) => req("PUT", "/api/settings", { per_operation_limit_rub: v }),
  scenario: (name: string, extra: { offer_id?: string; order_id?: string } = {}) =>
    req<{ message: string }>("POST", "/api/sandbox/scenario", { name, ...extra }),
};

export const rub = (v: number) => v.toLocaleString("ru-RU") + " ₽";
export const ddmm = (iso: string) => iso.slice(8, 10) + "." + iso.slice(5, 7);
