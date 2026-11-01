import { useEffect, useRef, useState } from "react";
import {
  api, ApiError, ddmm, rub,
  type AuditEntry, type Challenge, type CheckedOffer, type Health, type Order, type Payment, type PolicyResult, type Task,
} from "./api";

type Msg =
  | { role: "user"; text: string }
  | { role: "agent"; task: Task }
  | { role: "event"; tone: "ok" | "warn" | "bad"; text: string };

type Tab = "offers" | "order" | "audit" | "sandbox";

const EXAMPLES = [
  "Найди SSD Samsung 2 ТБ с гарантией и доставкой до пятницы",
  "Наушники Sony WH-1000XM5 до 29 тысяч",
  "Оперативка DDR5 32 ГБ Kingston до 9500 рублей",
  "SSD 2 ТБ до 12 тысяч с гарантией",
];

const RULE_RU: Record<string, string> = {
  order_integrity: "Целостность заказа",
  merchant_allowed: "Продавец в реестре",
  payee_match: "Получатель платежа",
  price_recheck: "Перепроверка цены",
  stock_recheck: "Перепроверка наличия",
  offer_recheck: "Перепроверка условий",
  task_budget: "Бюджет поручения",
  per_operation_limit: "Лимит на операцию",
  daily_limit: "Дневной лимит",
  not_expired: "Срок заказа",
  not_paid: "Защита от повтора",
};

const CATEGORY_RU: Record<string, string> = {
  ssd: "SSD", hdd: "HDD", ram: "Оперативная память", monitor: "Монитор", headphones: "Наушники", gpu: "Видеокарта",
};

const ZONE_RU: Record<string, string> = { untrusted: "недоверенная", trusted: "доверенная", user: "пользователь" };

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [task, setTask] = useState<Task | null>(null);
  const [order, setOrder] = useState<Order | null>(null);
  const [policy, setPolicy] = useState<PolicyResult[] | null>(null);
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [showConfirm, setShowConfirm] = useState(false);
  const [payment, setPayment] = useState<Payment | null>(null);
  const [tab, setTab] = useState<Tab>("offers");
  const [audit, setAudit] = useState<{ entries: AuditEntry[]; ok: boolean } | null>(null);
  const [limit, setLimit] = useState(30000);
  const chatEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
    api.settings().then((s) => setLimit(s.per_operation_limit_rub)).catch(() => {});
  }, []);
  useEffect(() => {
    chatEnd.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs]);
  useEffect(() => {
    if (tab === "audit") refreshAudit();
  }, [tab, task, order, payment, policy]);

  const push = (m: Msg) => setMsgs((xs) => [...xs, m]);
  const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Сервер недоступен");

  async function refreshAudit() {
    try {
      const a = await api.audit(task?.task_id);
      setAudit({ entries: a.entries, ok: a.chain.ok });
    } catch {
      setAudit(null);
    }
  }

  async function send(text: string) {
    if (!text.trim() || busy) return;
    setInput("");
    push({ role: "user", text });
    setBusy(true);
    try {
      const t = await api.task(text);
      setTask(t);
      setOrder(null); setPolicy(null); setChallenge(null); setPayment(null);
      push({ role: "agent", task: t });
      setTab("offers");
    } catch (e) {
      push({ role: "event", tone: "bad", text: errText(e) });
    } finally {
      setBusy(false);
    }
  }

  async function makeOrder(offerId: string) {
    if (!task) return;
    setBusy(true);
    try {
      const r = await api.order(task.task_id, offerId);
      setOrder(r.order); setPolicy(r.policy); setChallenge(null); setPayment(null);
      push({ role: "event", tone: "ok", text: `Создан заказ ${r.order.order_id} на ${rub(r.order.amount_rub)}. Цена запрошена у продавца повторно, реквизиты взяты из реестра банка.` });
      setTab("order");
    } catch (e) {
      push({ role: "event", tone: "bad", text: errText(e) });
    } finally {
      setBusy(false);
    }
  }

  async function checkout() {
    if (!order) return;
    setBusy(true);
    try {
      const r = await api.checkout(order.order_id);
      setOrder(r.order); setPolicy(r.policy);
      const failed = r.policy.filter((p) => !p.passed);
      if (r.challenge) {
        setChallenge(r.challenge);
        setShowConfirm(true);
        push({ role: "event", tone: "ok", text: `Policy Engine: все ${r.policy.length} правил пройдены. Нужно ваше подтверждение.` });
      } else {
        push({ role: "event", tone: "bad", text: `Policy Engine отклонил заказ: ${failed.map((p) => p.detail).join("; ")}.` });
      }
    } catch (e) {
      push({ role: "event", tone: "bad", text: errText(e) });
    } finally {
      setBusy(false);
    }
  }

  async function confirm(code: string): Promise<string | null> {
    if (!order || !challenge) return null;
    try {
      const r = await api.confirm(order.order_id, challenge.challenge_id, code);
      setOrder(r.order); setPayment(r.payment); setChallenge(null);
      push({ role: "event", tone: "ok", text: `Оплачено в sandbox: ${rub(r.payment.amount_rub)} → ${r.order.merchant_name}. Платёж ${r.payment.payment_id}.` });
      return null;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setChallenge(null);
        setPolicy(e.body?.detail?.policy ?? policy);
        setOrder({ ...order, status: "rejected" });
        const failed = (e.body?.detail?.policy ?? []).filter((p: PolicyResult) => !p.passed);
        push({ role: "event", tone: "bad", text: `Платёж заблокирован после подтверждения: ${failed.map((p: PolicyResult) => p.detail).join("; ")}.` });
        return null;
      }
      const msg = errText(e);
      if (msg.includes("отклонён") || msg.includes("использовано")) {
        setChallenge(null);
        setOrder({ ...order, status: "rejected" });
        push({ role: "event", tone: "bad", text: msg });
        return null;
      }
      return msg;
    }
  }

  async function runScenario(name: string) {
    try {
      const r = await api.scenario(name, { offer_id: order?.offer_id ?? task?.recommended_offer_id ?? undefined, order_id: order?.order_id });
      push({ role: "event", tone: "warn", text: `Sandbox: ${r.message}.` });
    } catch (e) {
      push({ role: "event", tone: "bad", text: errText(e) });
    }
  }

  async function saveLimit(v: number) {
    setLimit(v);
    await api.setLimit(v).catch(() => {});
  }

  const llmLabel = health
    ? health.llm.available ? `LLM on-prem: ${health.llm.model}` : "Разбор правилами (LLM не запущена)"
    : "Сервер недоступен";

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <span className="logo">A</span>
          <div>
            <b>AgentPay</b>
            <small>Конкурсный концепт команды SYNQ · не продукт Банка ВТБ</small>
          </div>
        </div>
        <div className="status">
          <span className={`pill ${health?.llm.available ? "ok" : "muted"}`}>{llmLabel}</span>
          <span className="pill warn">Sandbox · без реальных списаний</span>
        </div>
      </header>

      <main className="grid">
        <section className="chat">
          <div className="msgs">
            {msgs.length === 0 && (
              <div className="hello">
                <h1>Опишите покупку обычным языком</h1>
                <p>Агент найдёт предложения в тестовых каталогах трёх продавцов, проверит условия кодом и подготовит заказ. Оплата — только после вашего подтверждения.</p>
                <div className="examples">
                  {EXAMPLES.map((x) => <button key={x} onClick={() => send(x)}>{x}</button>)}
                </div>
              </div>
            )}
            {msgs.map((m, i) => <Message key={i} m={m} onOpen={() => setTab("offers")} />)}
            {busy && <div className="msg agent typing"><span /><span /><span /></div>}
            <div ref={chatEnd} />
          </div>
          <form className="composer" onSubmit={(e) => { e.preventDefault(); send(input); }}>
            <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Например: SSD Samsung 2 ТБ до 16 тысяч к пятнице" maxLength={500} />
            <button disabled={busy || !input.trim()}>Отправить</button>
          </form>
        </section>

        <section className="panel">
          <nav className="tabs">
            {([["offers", "Предложения"], ["order", "Заказ"], ["audit", "Журнал"], ["sandbox", "Sandbox"]] as [Tab, string][]).map(([k, l]) => (
              <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>
                {l}{k === "order" && order ? <i className={`dot ${order.status}`} /> : null}
              </button>
            ))}
          </nav>
          <div className="panel-body">
            {tab === "offers" && <Offers task={task} onOrder={makeOrder} busy={busy} />}
            {tab === "order" && <OrderView order={order} policy={policy} payment={payment} onCheckout={checkout} busy={busy}
                                            onOpenConfirm={challenge ? () => setShowConfirm(true) : undefined} />}
            {tab === "audit" && <AuditView audit={audit} onRefresh={refreshAudit} />}
            {tab === "sandbox" && <SandboxView order={order} task={task} limit={limit} onLimit={saveLimit} onScenario={runScenario} />}
          </div>
        </section>
      </main>

      {challenge && showConfirm && order && <ConfirmModal ch={challenge} order={order} onConfirm={confirm} onCancel={() => setShowConfirm(false)} />}
    </div>
  );
}

function Message({ m, onOpen }: { m: Msg; onOpen: () => void }) {
  const [open, setOpen] = useState(false);
  if (m.role === "user") return <div className="msg user">{m.text}</div>;
  if (m.role === "event") return <div className={`event ${m.tone}`}>{m.text}</div>;
  const t = m.task;
  const c = t.constraints;
  const chips = [
    c.category !== "other" && (CATEGORY_RU[c.category] ?? c.category),
    c.brand,
    ...c.keywords,
    c.capacity_gb && (c.capacity_gb >= 1000 ? `${c.capacity_gb / 1000} ТБ` : `${c.capacity_gb} ГБ`),
    c.max_total_rub && `до ${rub(c.max_total_rub)}`,
    c.deliver_by && `доставка до ${ddmm(c.deliver_by)}`,
    c.min_warranty_months && (c.min_warranty_months === 1 ? "с гарантией" : `гарантия от ${c.min_warranty_months} мес.`),
  ].filter(Boolean) as string[];
  return (
    <div className="msg agent">
      <div className="chips">{chips.map((x) => <span key={x}>{x}</span>)}</div>
      <p>{t.explanation}</p>
      {t.recommended_offer_id && <button className="link" onClick={onOpen}>Открыть сравнение →</button>}
      <button className="link muted" onClick={() => setOpen(!open)}>{open ? "Скрыть" : "Как работал агент"}</button>
      {open && (
        <ol className="trace">
          {t.trace.map((s, i) => (
            <li key={i}><span className={`zone ${s.zone}`}>{ZONE_RU[s.zone]}</span><b>{s.step}</b><small>{s.detail}{s.ms ? ` · ${s.ms} мс` : ""}</small></li>
          ))}
        </ol>
      )}
    </div>
  );
}

function Offers({ task, onOrder, busy }: { task: Task | null; onOrder: (id: string) => void; busy: boolean }) {
  const [showRejected, setShowRejected] = useState(false);
  if (!task) return <Empty text="Здесь появятся проверенные предложения после первого поручения." />;
  const passed = task.offers.filter((o) => o.passed);
  const rejected = task.offers.filter((o) => !o.passed);
  return (
    <div className="offers">
      <div className="summary">
        <span><b>{passed.length}</b> из {task.offers.length} предложений выполняют все условия</span>
      </div>
      {passed.map((o) => <OfferCard key={o.offer.offer_id} o={o} best={o.offer.offer_id === task.recommended_offer_id} onOrder={onOrder} busy={busy} />)}
      {rejected.length > 0 && (
        <button className="link muted" onClick={() => setShowRejected(!showRejected)}>
          {showRejected ? "Скрыть" : "Показать"} отклонённые ({rejected.length})
        </button>
      )}
      {showRejected && rejected.map((o) => <OfferCard key={o.offer.offer_id} o={o} best={false} onOrder={onOrder} busy={busy} />)}
    </div>
  );
}

function OfferCard({ o, best, onOrder, busy }: { o: CheckedOffer; best: boolean; onOrder: (id: string) => void; busy: boolean }) {
  const [open, setOpen] = useState(best);
  const f = o.offer;
  const suspicious = !o.checks[0]?.passed;
  return (
    <div className={`offer ${best ? "best" : ""} ${o.passed ? "" : "failed"} ${suspicious ? "suspicious" : ""}`}>
      <div className="offer-head">
        <div>
          {best && <span className="tag">Рекомендация</span>}
          {suspicious && <span className="tag bad">Подозрительная карточка</span>}
          {!o.passed && !suspicious && <span className="tag muted">Не подходит</span>}
          <h3>{f.title}</h3>
          <small>{f.merchant_name} · арт. {f.sku}</small>
        </div>
        <div className="price">
          <b>{rub(f.total_rub)}</b>
          <small>{f.delivery_rub ? `вкл. доставку ${rub(f.delivery_rub)}` : "доставка бесплатно"}</small>
        </div>
      </div>
      <div className="facts">
        <span>Доставка {ddmm(f.delivery_date)}</span>
        <span>Гарантия {f.warranty_months ? `${f.warranty_months} мес.` : "нет"}</span>
        <span>{f.in_stock ? "В наличии" : "Нет в наличии"}</span>
      </div>
      <button className="link muted" onClick={() => setOpen(!open)}>{open ? "Скрыть проверки" : `Проверки (${o.checks.filter((c) => c.passed).length}/${o.checks.length})`}</button>
      {open && (
        <ul className="checks">
          {o.checks.map((c) => (
            <li key={c.name} className={c.passed ? "ok" : "bad"}><span>{c.passed ? "✓" : "✕"}</span><b>{c.name}</b><small>{c.detail}</small></li>
          ))}
        </ul>
      )}
      {o.passed && <button className={best ? "primary" : "secondary"} disabled={busy} onClick={() => onOrder(f.offer_id)}>Оформить заказ</button>}
    </div>
  );
}

function OrderView({ order, policy, payment, onCheckout, busy, onOpenConfirm }: {
  order: Order | null; policy: PolicyResult[] | null; payment: Payment | null; onCheckout: () => void; busy: boolean;
  onOpenConfirm?: () => void;
}) {
  if (!order) return <Empty text="Выберите предложение и нажмите «Оформить заказ»." />;
  const status: Record<string, string> = {
    draft: "Черновик", awaiting_confirmation: "Ждёт подтверждения", paid: "Оплачен (sandbox)", rejected: "Отклонён", expired: "Истёк",
  };
  return (
    <div className="order">
      <div className={`order-card ${order.status}`}>
        <div className="row"><span>Статус</span><b>{status[order.status]}</b></div>
        <div className="row"><span>Товар</span><b>{order.title}</b></div>
        <div className="row"><span>Продавец</span><b>{order.merchant_name}</b></div>
        <div className="row"><span>Счёт получателя</span><code>{order.payee_account}</code></div>
        <div className="row big"><span>Сумма</span><b>{rub(order.amount_rub)}</b></div>
      </div>
      {payment && (
        <div className="receipt">
          <b>✓ Платёж выполнен в sandbox</b>
          <small>{payment.payment_id} · {new Date(payment.created_at).toLocaleString("ru-RU")}</small>
        </div>
      )}
      {policy && (
        <>
          <h4>Policy Engine <small>правила проверяет код, не модель</small></h4>
          <ul className="checks">
            {policy.map((p) => (
              <li key={p.rule} className={p.passed ? "ok" : "bad"}><span>{p.passed ? "✓" : "✕"}</span><b>{RULE_RU[p.rule] ?? p.rule}</b><small>{p.detail}</small></li>
            ))}
          </ul>
        </>
      )}
      {order.status === "draft" && <button className="primary" disabled={busy} onClick={onCheckout}>Проверить и перейти к оплате</button>}
      {order.status === "awaiting_confirmation" && onOpenConfirm && <button className="primary" onClick={onOpenConfirm}>Ввести код подтверждения</button>}
      {order.status === "rejected" && <p className="note">Заказ отклонён. Отправьте поручение заново, чтобы агент собрал актуальные предложения.</p>}
    </div>
  );
}

function ConfirmModal({ ch, order, onConfirm, onCancel }: {
  ch: Challenge; order: Order; onConfirm: (code: string) => Promise<string | null>; onCancel: () => void;
}) {
  const [code, setCode] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit() {
    setBusy(true);
    setErr(await onConfirm(code));
    setBusy(false);
  }
  return (
    <div className="overlay">
      <div className="modal" role="dialog" aria-modal="true">
        <div className="push">
          <small>Push-уведомление · sandbox</small>
          <div>Код подтверждения: <b>{ch.sandbox_code}</b></div>
        </div>
        <h2>Подтвердите оплату</h2>
        <div className="amount">{rub(ch.amount_rub)}</div>
        <div className="row"><span>Получатель</span><b>{ch.merchant_name}</b></div>
        <div className="row"><span>Счёт</span><code>{ch.payee_account}</code></div>
        <div className="row"><span>Назначение</span><b>{order.title}</b></div>
        <p className="note">Сумма и получатель проверены Policy Engine. Агент не может подтвердить платёж за вас.</p>
        <input className="code" inputMode="numeric" autoFocus maxLength={6} value={code}
               onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} placeholder="Код из уведомления"
               onKeyDown={(e) => e.key === "Enter" && code.length === 6 && submit()} />
        {err && <div className="error">{err}</div>}
        <div className="actions">
          <button className="secondary" onClick={onCancel}>Отмена</button>
          <button className="primary" disabled={code.length !== 6 || busy} onClick={submit}>Подтвердить оплату</button>
        </div>
      </div>
    </div>
  );
}

function AuditView({ audit, onRefresh }: { audit: { entries: AuditEntry[]; ok: boolean } | null; onRefresh: () => void }) {
  if (!audit) return <Empty text="Журнал пуст." />;
  return (
    <div className="audit">
      <div className="summary">
        <span className={`pill ${audit.ok ? "ok" : "bad"}`}>{audit.ok ? "Хеш-цепочка цела" : "Цепочка нарушена"}</span>
        <span>{audit.entries.length} записей по текущему поручению</span>
        <button className="link" onClick={onRefresh}>Обновить</button>
      </div>
      <ol>
        {audit.entries.map((e) => (
          <li key={e.id}>
            <span className={`zone ${e.zone}`}>{ZONE_RU[e.zone] ?? e.zone}</span>
            <div>
              <b>{e.action}</b> <small>{e.actor} · {new Date(e.ts).toLocaleTimeString("ru-RU")}</small>
              <code>{JSON.stringify(e.data)}</code>
            </div>
            <small className="hash" title={e.hash}>#{e.hash.slice(0, 8)}</small>
          </li>
        ))}
      </ol>
    </div>
  );
}

function SandboxView({ order, task, limit, onLimit, onScenario }: {
  order: Order | null; task: Task | null; limit: number; onLimit: (v: number) => void; onScenario: (n: string) => void;
}) {
  const target = order?.offer_id ?? task?.recommended_offer_id;
  return (
    <div className="sandbox">
      <p className="note">Имитация событий у продавца и атак, которые должен перехватить платёжный контур. Сценарий применяется к текущему заказу{target ? ` (${target})` : ""}.</p>
      <div className="scen">
        <button disabled={!target} onClick={() => onScenario("price_spike")}><b>Рост цены на 25%</b><small>Продавец меняет цену между выбором и оплатой</small></button>
        <button disabled={!target} onClick={() => onScenario("out_of_stock")}><b>Товар закончился</b><small>Наличие перепроверяется перед списанием</small></button>
        <button disabled={!order} onClick={() => onScenario("payee_swap")}><b>Подмена реквизитов</b><small>Счёт получателя в заказе заменён на чужой</small></button>
        <button onClick={() => onScenario("reset")}><b>Сбросить каталоги</b><small>Вернуть исходные цены и наличие</small></button>
      </div>
      <label className="limit">
        Лимит на одну операцию
        <select value={limit} onChange={(e) => onLimit(Number(e.target.value))}>
          {[5000, 10000, 20000, 30000, 50000].map((v) => <option key={v} value={v}>{rub(v)}</option>)}
        </select>
      </label>
      <p className="note">Prompt injection уже есть в каталоге «Комплект-Про»: карточка «СУПЕРЦЕНА» с инструкциями для агента и чужим счётом. Она исключается проверкой целостности.</p>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="empty">{text}</div>;
}
