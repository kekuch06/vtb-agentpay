import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import {
  account, api, ApiError, ddmm, errorText, hhmm, money, rub,
  type AuditEntry, type Challenge, type CheckedOffer, type DeviceMessage, type Health, type Limits, type Order,
  type OrderStatus, type Payment, type PolicyResult, type Task,
} from "./api";
import { Icon, type IconName } from "./icons";

type Tone = "ok" | "warn" | "bad";
type NewMsg =
  | { role: "user"; text: string }
  | { role: "agent"; task: Task }
  | { role: "event"; tone: Tone; text: string };
type Msg = NewMsg & { id: number };

type Tab = "offers" | "order" | "audit" | "sandbox";
// На телефоне виден один экран: чат или вкладка панели. На компьютере чат и панель стоят рядом.
type View = "chat" | "panel";

const EXAMPLES = [
  "Найди SSD Samsung 2 ТБ с гарантией и доставкой до пятницы",
  "Наушники Sony WH-1000XM5 до 29 тысяч",
  "Оперативка DDR5 32 ГБ Kingston до 9500 рублей",
  "SSD 2 ТБ до 12 тысяч с гарантией",
];

const TABS: [Tab, string, IconName][] = [
  ["offers", "Предложения", "bag"], ["order", "Заказ", "receipt"], ["audit", "Журнал", "journal"], ["sandbox", "Sandbox", "flask"],
];

const RULE_RU: Record<string, string> = {
  order_integrity: "Целостность заказа",
  merchant_allowed: "Продавец в реестре",
  payee_match: "Получатель платежа",
  price_recheck: "Перепроверка цены",
  stock_recheck: "Перепроверка наличия",
  offer_recheck: "Перепроверка условий",
  task_open: "Одна покупка на поручение",
  task_budget: "Бюджет поручения",
  per_operation_limit: "Лимит на операцию",
  daily_limit: "Дневной лимит",
  not_expired: "Срок заказа",
  not_paid: "Защита от повтора",
  order_open: "Статус заказа",
  confirmation_limit: "Лимит запросов кода",
};

const ACTION_RU: Record<string, string> = {
  "task.created": "Поручение получено",
  "constraints.extracted": "Условия извлечены из текста",
  "catalog.searched": "Каталог продавца опрошен",
  "offers.verified": "Предложения проверены кодом",
  "recommendation.made": "Рекомендация",
  "order.created": "Заказ создан",
  "policy.evaluated": "Проверка Policy Engine",
  "confirmation.requested": "Код отправлен на устройство",
  "confirmation.rejected": "Подтверждение отклонено",
  "confirmation.approved": "Клиент подтвердил оплату",
  "policy.blocked_at_payment": "Платёж остановлен перед списанием",
  "payment.succeeded": "Платёж проведён в sandbox",
  "settings.changed": "Лимит изменён",
  "sandbox.price_spike": "Sandbox: рост цены",
  "sandbox.out_of_stock": "Sandbox: товар закончился",
  "sandbox.payee_swapped": "Sandbox: подмена реквизитов",
  "sandbox.code_expired": "Sandbox: код истёк",
};

const CATEGORY_RU: Record<string, string> = {
  ssd: "SSD", hdd: "HDD", ram: "Оперативная память", monitor: "Монитор", headphones: "Наушники", gpu: "Видеокарта",
};
const ZONE_RU: Record<string, string> = { untrusted: "Недоверенная зона", trusted: "Доверенная зона", user: "Клиент" };

const STATUS: Record<OrderStatus, { title: string; hint: string; icon: IconName }> = {
  draft: { title: "Заказ создан", hint: "Проверьте условия и перейдите к оплате", icon: "receipt" },
  awaiting_confirmation: { title: "Ждёт подтверждения", hint: "Код отправлен на ваше устройство", icon: "phone" },
  paid: { title: "Оплачен (sandbox)", hint: "Тестовый платёж, деньги не списывались", icon: "check" },
  rejected: { title: "Отклонён", hint: "Платёж не проведён. Отправьте поручение заново", icon: "cross" },
  expired: { title: "Истёк", hint: "Заказ действует 15 минут. Отправьте поручение заново", icon: "clock" },
};

const LIMIT_OPTIONS = [5000, 10000, 20000, 30000, 50000];
const isPhone = () => window.matchMedia("(max-width: 959px)").matches;

export default function App() {
  const [health, setHealth] = useState<Health | null | undefined>(undefined);  // undefined — ещё не знаем
  const [limits, setLimits] = useState<Limits | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [task, setTask] = useState<Task | null>(null);
  const [order, setOrder] = useState<Order | null>(null);
  const [policy, setPolicy] = useState<PolicyResult[] | null>(null);
  const [policyFresh, setPolicyFresh] = useState(false);
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [deviceMsg, setDeviceMsg] = useState<DeviceMessage | null>(null);
  const [showConfirm, setShowConfirm] = useState(false);
  const [payment, setPayment] = useState<Payment | null>(null);
  const [tab, setTab] = useState<Tab>("offers");
  const [view, setView] = useState<View>("chat");
  const [audit, setAudit] = useState<{ entries: AuditEntry[]; ok: boolean } | "error" | null>(null);
  const chatEnd = useRef<HTMLDivElement>(null);
  const nextId = useRef(1);

  async function loadStatus() {
    api.health().then(setHealth).catch(() => setHealth(null));
    api.settings().then(setLimits).catch(() => {});
  }
  useEffect(() => { loadStatus(); }, []);
  useEffect(() => {
    if (!msgs.length && !busy) return;  // первый экран не прокручиваем: герой с лимитом должен быть виден
    if (isPhone() && view !== "chat") return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    chatEnd.current?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "end" });
  }, [msgs, busy, view]);
  useEffect(() => {
    // На телефоне вкладка — отдельный экран: открывается с начала, а не с места, до которого прокручен чат.
    // После перерисовки и мгновенно — иначе продолжалась бы плавная прокрутка чата.
    if (isPhone() && view === "panel") window.scrollTo({ top: 0, behavior: "instant" });
  }, [view, tab]);
  useEffect(() => {
    if (tab === "audit") refreshAudit();
  }, [tab, task, order, payment, policy]);

  const push = (m: NewMsg) => setMsgs((xs) => [...xs, { ...m, id: nextId.current++ }]);
  const event = (tone: Tone, text: string) => push({ role: "event", tone, text });

  function show(t: Tab) {
    setTab(t);
    setView("panel");
  }

  async function refreshAudit() {
    try {
      const a = await api.audit(task?.task_id);
      setAudit({ entries: a.entries, ok: a.chain.ok });
    } catch {
      setAudit("error");
    }
  }

  async function send(text: string) {
    text = text.trim();
    if (!text || busy) return;
    if (text.length < 3) {
      event("warn", "Опишите покупку подробнее: товар, бюджет или срок доставки");
      return;
    }
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
      event("bad", errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function makeOrder(offerId: string) {
    if (!task || busy) return;
    setBusy(true);
    try {
      const r = await api.order(task.task_id, offerId);
      setOrder(r.order); setPolicy(r.policy); setPolicyFresh(false); setChallenge(null); setPayment(null);
      event("ok", `Заказ на ${money(r.order.amount_kop)} создан. Цену агент запросил у продавца заново, счёт получателя взят из реестра банка`);
      show("order");
    } catch (e) {
      event("bad", errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function checkout() {
    if (!order || busy) return;
    setBusy(true);
    try {
      const r = await api.checkout(order.order_id);
      setOrder(r.order); setPolicy(r.policy); setPolicyFresh(true);
      if (r.challenge) {
        const id = r.challenge.challenge_id;
        setChallenge(r.challenge);
        // Код приходит отдельным каналом — sandbox-имитацией push-уведомления на устройство клиента.
        setDeviceMsg((await api.device().catch(() => ({ messages: [] }))).messages.find((m) => m.challenge_id === id) ?? null);
        setShowConfirm(true);
        event("ok", `Policy Engine: все ${r.policy.length} правил пройдены. Код подтверждения отправлен на устройство`);
      } else {
        setChallenge(null); setShowConfirm(false);
        const failed = r.policy.filter((p) => !p.passed);
        event("bad", `Policy Engine отклонил заказ: ${failed.map((p) => p.detail).join("; ")}`);
      }
    } catch (e) {
      event("bad", errorText(e));
    } finally {
      setBusy(false);
    }
  }

  // Ответ окну подтверждения: null — окно закрывается, иначе текст ошибки и можно ли запросить новый код.
  async function confirm(code: string): Promise<{ error: string; resend: boolean } | null> {
    if (!order || !challenge) return null;
    try {
      const r = await api.confirm(order.order_id, challenge.challenge_id, code);
      setOrder(r.order); setPayment(r.payment); setChallenge(null); setShowConfirm(false);
      event("ok", `Оплачено в sandbox: ${money(r.payment.amount_kop)}, получатель ${r.order.merchant_name}`);
      api.settings().then(setLimits).catch(() => {});
      return null;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const results: PolicyResult[] = e.body?.detail?.policy ?? [];
        setChallenge(null); setShowConfirm(false);
        if (results.length) { setPolicy(results); setPolicyFresh(true); }
        await syncOrder(order);
        event("bad", `Платёж остановлен после подтверждения: ${results.filter((p) => !p.passed).map((p) => p.detail).join("; ")}`);
        return null;
      }
      if (!(e instanceof ApiError) || e.status !== 403) return { error: errorText(e), resend: false };
      // Статус заказа после отказа — только со слов сервера: истёкший код оставляет заказ открытым,
      // а исчерпанные попытки его закрывают.
      const fresh = await syncOrder(order);
      if (fresh && fresh.status !== "awaiting_confirmation") {
        setChallenge(null); setShowConfirm(false);
        event("bad", errorText(e));
        return null;
      }
      const msg = errorText(e);
      return { error: msg, resend: /истёк|использовано|изменился/.test(msg) };
    }
  }

  async function syncOrder(o: Order): Promise<Order | null> {
    try {
      const r = await api.getOrder(o.order_id);
      setOrder(r.order);
      return r.order;
    } catch {
      return null;
    }
  }

  async function runScenario(name: string) {
    try {
      const r = await api.scenario(name, { offer_id: order?.offer_id ?? task?.recommended_offer_id ?? undefined, order_id: order?.order_id });
      event("warn", `Sandbox: ${r.message}`);
    } catch (e) {
      event("bad", errorText(e));
    }
  }

  async function saveLimit(v: number) {
    // Показываем только то, что сохранил сервер: иначе при ошибке на экране был бы лимит, которого нет в Policy Engine.
    try {
      const s = await api.setLimit(v);
      setLimits(s);
      event("ok", `Лимит на одну операцию: ${rub(s.per_operation_limit_rub)}`);
    } catch (e) {
      event("bad", `Лимит не изменён. ${errorText(e)}`);
    }
  }

  const passedCount = task ? task.offers.filter((o) => o.passed).length : 0;
  const parserLabel = health === undefined ? "Проверяем связь с сервером"
    : health === null ? "Нет связи с сервером"
    : health.llm.available ? `Разбор поручений: модель ${health.llm.model}` : "Разбор поручений: правила";
  const active: "chat" | Tab = view === "chat" ? "chat" : tab;

  return (
    <div className={`app view-${view}`}>
      <header className="hero">
        <div className="hero-in">
          <div className="hero-top">
            <div className="brand">
              <span className="brand-mark" aria-hidden="true"><Icon name="wallet" /></span>
              <div>
                <h1>AgentPay</h1>
                <small>Концепт команды SYNQ, не продукт Банка ВТБ</small>
              </div>
            </div>
            <span className="hero-chip desk"><Icon name="lock" size={16} />Sandbox: деньги не списываются</span>
          </div>
          <div className="hero-main">
            <div>
              <p className="hero-label">Лимит на одну покупку</p>
              <p className="hero-amount">{limits ? rub(limits.per_operation_limit_rub) : "—"}</p>
              <ul className="hero-facts">
                <li className="mob"><Icon name="lock" size={16} />Sandbox: деньги не списываются</li>
                {limits && <li>Сегодня оплачено {money(limits.spent_today_kop)} из {rub(limits.daily_limit_rub)}</li>}
                <li>{parserLabel}</li>
              </ul>
            </div>
            <ol className="hero-steps" aria-label="Как проходит покупка">
              <li><Icon name="sparkle" /><b>Агент подбирает</b><small>Ищет и сравнивает предложения продавцов</small></li>
              <li><Icon name="shield" /><b>Код проверяет</b><small>Условия, цену, получателя и лимиты</small></li>
              <li><Icon name="phone" /><b>Вы подтверждаете</b><small>Кодом с устройства. Сам агент не платит</small></li>
            </ol>
          </div>
        </div>
      </header>

      <main className="layout">
        <section className="sheet chat" aria-labelledby="chat-title">
          <div className="sheet-head">
            <span className="agent-avatar" aria-hidden="true"><Icon name="sparkle" /></span>
            <div>
              <h2 id="chat-title">Агент покупок</h2>
              <small>{busy ? "Работает…" : "Видит только текст поручения"}</small>
            </div>
          </div>
          <div className="msgs" role="log" aria-label="Переписка с агентом">
            <div className="msg agent intro">
              <span className="sender">Агент</span>
              <p>Опишите покупку обычным языком. Я найду предложения у трёх тестовых продавцов и проверю условия. Заказ оформляете вы, оплату подтверждаете кодом с устройства.</p>
            </div>
            {health === null && (
              <div className="event bad">
                <Icon name="alert" size={20} />
                <span>Нет связи с сервером. Проверьте, что стенд запущен.</span>
                <button className="quiet" onClick={loadStatus}>Повторить</button>
              </div>
            )}
            {msgs.length === 0 && (
              <div className="suggest" aria-label="Примеры поручений">
                {EXAMPLES.map((x) => <button key={x} onClick={() => send(x)} disabled={busy}>{x}</button>)}
              </div>
            )}
            {msgs.map((m) => (
              <Message key={m.id} m={m} current={task} order={order} busy={busy} onOrder={makeOrder}
                       onOpenOffers={() => show("offers")} onOpenOrder={() => show("order")} />
            ))}
            {busy && <div className="msg agent typing" role="status" aria-label="Агент работает"><span /><span /><span /></div>}
            <div ref={chatEnd} className="chat-end" />
          </div>
          <form className="composer" onSubmit={(e) => { e.preventDefault(); send(input); }}>
            <label htmlFor="task-input" className="sr-only">Поручение агенту</label>
            <input id="task-input" value={input} onChange={(e) => setInput(e.target.value)} maxLength={500} autoComplete="off"
                   placeholder="Опишите покупку" enterKeyHint="send" />
            <button className="send" disabled={busy || !input.trim()} aria-label="Отправить поручение"><Icon name="send" /></button>
          </form>
        </section>

        <section className="sheet panel" aria-label="Покупка">
          <Segmented tab={tab} onChange={setTab} order={order} offersCount={passedCount} />
          <div className="panel-body" id="panel-body" role="tabpanel" aria-labelledby={`tab-${tab}`}>
            {tab === "offers" && <Offers task={task} onOrder={makeOrder} busy={busy} onChat={() => setView("chat")} />}
            {tab === "order" && (
              <OrderView order={order} policy={policy} fresh={policyFresh} payment={payment} onCheckout={checkout} busy={busy}
                         onOpenConfirm={challenge ? () => setShowConfirm(true) : undefined} />
            )}
            {tab === "audit" && <AuditView audit={audit} hasTask={!!task} onRefresh={refreshAudit} />}
            {tab === "sandbox" && <SandboxView order={order} task={task} limits={limits} onLimit={saveLimit} onScenario={runScenario} />}
          </div>
        </section>
      </main>

      <footer className="foot">
        Конкурсный проект команды SYNQ, Академия Финтеха 2026. Не является продуктом Банка ВТБ (ПАО). Все платежи — sandbox, каталоги продавцов тестовые.
      </footer>

      <nav className="tabbar" aria-label="Разделы">
        <NavButton icon="chat" label="Агент" active={active === "chat"} onClick={() => setView("chat")} />
        {TABS.map(([k, label, icon]) => (
          <NavButton key={k} icon={icon} label={label} active={active === k} onClick={() => show(k)}
                     badge={k === "offers" && passedCount ? passedCount : undefined}
                     dot={k === "order" && order ? order.status : undefined} />
        ))}
      </nav>

      {challenge && showConfirm && order && (
        <ConfirmModal key={challenge.challenge_id} ch={challenge} device={deviceMsg} order={order} onConfirm={confirm}
                      onResend={checkout} onCancel={() => setShowConfirm(false)} />
      )}
    </div>
  );
}

function NavButton({ icon, label, active, onClick, badge, dot }: {
  icon: IconName; label: string; active: boolean; onClick: () => void; badge?: number; dot?: OrderStatus;
}) {
  return (
    <button className="tab" aria-current={active ? "page" : undefined} onClick={onClick}>
      <span className="tab-icon">
        <Icon name={icon} />
        {badge ? <span className="badge" aria-hidden="true">{badge}</span> : null}
        {dot ? <span className={`dot s-${dot}`} aria-hidden="true" /> : null}
      </span>
      <span className="tab-label">{label}</span>
      {badge ? <span className="sr-only">, подходят {badge}</span> : null}
      {dot ? <span className="sr-only">, {STATUS[dot].title.toLowerCase()}</span> : null}
    </button>
  );
}

function Segmented({ tab, onChange, order, offersCount }: {
  tab: Tab; onChange: (t: Tab) => void; order: Order | null; offersCount: number;
}) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  // Стрелки влево и вправо переключают вкладки, как в нативном переключателе (WAI-ARIA Tabs).
  function onKey(e: ReactKeyboardEvent) {
    const i = TABS.findIndex(([k]) => k === tab);
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = TABS[(i + step + TABS.length) % TABS.length][0];
    onChange(next);
    refs.current[next]?.focus();
  }
  return (
    <div className="segmented" role="tablist" aria-label="Покупка" onKeyDown={onKey}>
      {TABS.map(([k, label]) => (
        <button key={k} id={`tab-${k}`} role="tab" aria-selected={tab === k} aria-controls="panel-body" tabIndex={tab === k ? 0 : -1}
                ref={(el) => { refs.current[k] = el; }} onClick={() => onChange(k)}>
          {label}
          {k === "offers" && offersCount ? <span className="count">{offersCount}</span> : null}
          {k === "order" && order ? <span className={`dot s-${order.status}`} aria-hidden="true" /> : null}
        </button>
      ))}
    </div>
  );
}

function initials(name: string) {
  const caps = name.replace(/\(.*\)/, "").match(/\p{Lu}/gu) ?? [];
  return caps.slice(0, 2).join("") || name.slice(0, 2);
}

function MerchantAvatar({ id, name }: { id: string; name: string }) {
  return <span className={`merchant m-${id}`} aria-hidden="true">{initials(name)}</span>;
}

function constraintChips(t: Task): string[] {
  const c = t.constraints;
  return [
    c.category !== "other" && (CATEGORY_RU[c.category] ?? c.category),
    c.brand,
    ...c.keywords,
    c.capacity_gb && (c.capacity_gb >= 1000 ? `${c.capacity_gb / 1000} ТБ` : `${c.capacity_gb} ГБ`),
    c.max_total_rub && `до ${rub(c.max_total_rub)}`,
    c.deliver_by && `доставка до ${ddmm(c.deliver_by)}`,
    c.min_warranty_months && (c.min_warranty_months === 1 ? "с гарантией" : `гарантия от ${c.min_warranty_months} мес.`),
  ].filter(Boolean) as string[];
}

function Message({ m, current, order, busy, onOrder, onOpenOffers, onOpenOrder }: {
  m: Msg; current: Task | null; order: Order | null; busy: boolean; onOrder: (id: string) => void;
  onOpenOffers: () => void; onOpenOrder: () => void;
}) {
  if (m.role === "user") return <div className="msg user">{m.text}</div>;
  if (m.role === "event") {
    const icon: IconName = m.tone === "ok" ? "check" : m.tone === "warn" ? "alert" : "cross";
    return <div className={`event ${m.tone}`}><Icon name={icon} size={20} /><span>{m.text}</span></div>;
  }
  const t = m.task;
  const chips = constraintChips(t);
  const best = t.offers.find((o) => o.offer.offer_id === t.recommended_offer_id);
  const isCurrent = current?.task_id === t.task_id;
  // Заказ по поручению уже есть и не закрыт отказом — вместо повторного «Оформить заказ» ведём к нему.
  const ordered = isCurrent && order?.task_id === t.task_id && !["rejected", "expired"].includes(order.status);
  return (
    <article className="msg agent reply">
      <span className="sender">Агент</span>
      {chips.length > 0 && (
        <div className="understood">
          <small>Понял так:</small>
          <ul className="chips">{chips.map((x) => <li key={x}>{x}</li>)}</ul>
        </div>
      )}
      <p>{t.explanation}</p>
      {best && (
        <div className="reco">
          <div className="reco-head">
            <MerchantAvatar id={best.offer.merchant_id} name={best.offer.merchant_name} />
            <div>
              <b>{best.offer.title}</b>
              <small>{best.offer.merchant_name}</small>
            </div>
          </div>
          <div className="reco-facts">
            <span className="reco-price">{money(best.offer.total_kop)}</span>
            <span>Доставка {ddmm(best.offer.delivery_date)}</span>
            <span>Гарантия {best.offer.warranty_months} мес.</span>
          </div>
          {isCurrent && !ordered && <button className="primary block" disabled={busy} onClick={() => onOrder(best.offer.offer_id)}>Оформить заказ</button>}
          {ordered && <button className="secondary block" onClick={onOpenOrder}>Открыть заказ</button>}
        </div>
      )}
      <div className="msg-actions">
        {isCurrent && t.offers.length > 0 && <button className="quiet" onClick={onOpenOffers}>Все предложения ({t.offers.length})</button>}
      </div>
      <details className="trace-box">
        <summary>Как работал агент</summary>
        <ol className="trace">
          {t.trace.map((s, i) => (
            <li key={i} className={`z-${s.zone}`}>
              <span className="zone">{ZONE_RU[s.zone]}</span>
              <b>{s.step}</b>
              <small>{s.detail}{s.ms ? `, ${s.ms} мс` : ""}</small>
            </li>
          ))}
        </ol>
      </details>
    </article>
  );
}

function Empty({ icon, title, text, children }: { icon: IconName; title: string; text: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <span className="empty-icon" aria-hidden="true"><Icon name={icon} size={28} /></span>
      <b>{title}</b>
      <p>{text}</p>
      {children}
    </div>
  );
}

function Offers({ task, onOrder, busy, onChat }: { task: Task | null; onOrder: (id: string) => void; busy: boolean; onChat: () => void }) {
  const [showRejected, setShowRejected] = useState(false);
  useEffect(() => setShowRejected(false), [task?.task_id]);
  if (!task) {
    return (
      <>
        <h2 className="sheet-title">Предложения</h2>
        <Empty icon="bag" title="Предложений пока нет" text="Опишите покупку в чате: агент соберёт предложения трёх продавцов и проверит условия.">
          <button className="secondary mob" onClick={onChat}>Написать агенту</button>
        </Empty>
      </>
    );
  }
  const passed = task.offers.filter((o) => o.passed);
  const rejected = task.offers.filter((o) => !o.passed);
  return (
    <div className="offers">
      <h2 className="sheet-title">Предложения</h2>
      <div className="lead">
        <p>{task.offers.length ? <><b>{passed.length} из {task.offers.length}</b> выполняют все условия поручения</> : "Подходящих товаров в каталогах нет"}</p>
        <small>«{task.text}»</small>
      </div>
      {passed.map((o) => <OfferCard key={o.offer.offer_id} o={o} best={o.offer.offer_id === task.recommended_offer_id} onOrder={onOrder} busy={busy} />)}
      {rejected.length > 0 && (
        <button className="quiet toggle" aria-expanded={showRejected} onClick={() => setShowRejected(!showRejected)}>
          {showRejected ? "Скрыть" : "Показать"} отклонённые ({rejected.length})
          <Icon name="chevronDown" size={18} className={showRejected ? "flip" : ""} />
        </button>
      )}
      {showRejected && rejected.map((o) => <OfferCard key={o.offer.offer_id} o={o} best={false} onOrder={onOrder} busy={busy} />)}
    </div>
  );
}

function OfferCard({ o, best, onOrder, busy }: { o: CheckedOffer; best: boolean; onOrder: (id: string) => void; busy: boolean }) {
  const f = o.offer;
  const suspicious = !o.checks[0]?.passed;
  const firstFail = o.checks.find((c) => !c.passed);
  const okCount = o.checks.filter((c) => c.passed).length;
  return (
    <article className={`offer${best ? " best" : ""}${o.passed ? "" : " failed"}${suspicious ? " suspicious" : ""}`}>
      <div className="offer-head">
        <MerchantAvatar id={f.merchant_id} name={f.merchant_name} />
        <div className="offer-name">
          {best && <span className="tag">Рекомендация</span>}
          {suspicious && <span className="tag bad">Подозрительная карточка</span>}
          {!o.passed && !suspicious && <span className="tag muted">Не подходит</span>}
          <h3>{f.title}</h3>
          <small>{f.merchant_name}, арт. {f.sku}</small>
        </div>
        <div className="price">
          <b>{money(f.total_kop)}</b>
          <small>{f.delivery_kop ? `доставка ${money(f.delivery_kop)}` : "доставка бесплатно"}</small>
        </div>
      </div>
      {firstFail && <p className="why"><Icon name={suspicious ? "alert" : "cross"} size={18} />{firstFail.name}: {firstFail.detail}</p>}
      <ul className="facts">
        <li><Icon name="truck" size={18} />Доставка {ddmm(f.delivery_date)}</li>
        <li><Icon name="shield" size={18} />{f.warranty_months ? `Гарантия ${f.warranty_months} мес.` : "Без гарантии"}</li>
        <li><Icon name="box" size={18} />{f.in_stock ? "В наличии" : "Нет в наличии"}</li>
      </ul>
      <details className="checks-box" open={best || undefined}>
        <summary>Проверки кодом: {okCount} из {o.checks.length}</summary>
        <CheckList items={o.checks.map((c) => ({ key: c.name, name: c.name, passed: c.passed, detail: c.detail }))} />
      </details>
      {o.passed && <button className={best ? "primary" : "secondary"} disabled={busy} onClick={() => onOrder(f.offer_id)}>Оформить заказ</button>}
    </article>
  );
}

function CheckList({ items, reveal }: { items: { key: string; name: string; passed: boolean; detail: string }[]; reveal?: boolean }) {
  return (
    <ul className={`checks${reveal ? " reveal" : ""}`}>
      {items.map((c) => (
        <li key={c.key} className={c.passed ? "ok" : "bad"}>
          <span className="mark"><Icon name={c.passed ? "check" : "cross"} size={16} /><span className="sr-only">{c.passed ? "пройдено:" : "не пройдено:"}</span></span>
          <b>{c.name}</b>
          <small>{c.detail}</small>
        </li>
      ))}
    </ul>
  );
}

function OrderView({ order, policy, fresh, payment, onCheckout, busy, onOpenConfirm }: {
  order: Order | null; policy: PolicyResult[] | null; fresh: boolean; payment: Payment | null; onCheckout: () => void; busy: boolean;
  onOpenConfirm?: () => void;
}) {
  if (!order) {
    return (
      <>
        <h2 className="sheet-title">Заказ</h2>
        <Empty icon="receipt" title="Заказа пока нет" text="Выберите предложение и нажмите «Оформить заказ». Заказ ни к чему не обязывает: оплата только после вашего подтверждения." />
      </>
    );
  }
  const s = STATUS[order.status];
  const passed = policy?.filter((p) => p.passed).length ?? 0;
  return (
    <div className="order">
      <h2 className="sheet-title">Заказ</h2>
      {/* Оплаченный заказ: квитанция и есть статус — фокус после закрытия окна переходит сюда. */}
      <div className={`status-banner s-${order.status}${payment ? " receipt" : ""}`} tabIndex={payment ? -1 : undefined}
           data-return-focus={payment ? "" : undefined}>
        <span className="status-icon" aria-hidden="true"><Icon name={s.icon} /></span>
        <div>
          <b className="status-title">{s.title}</b>
          <small>{payment ? `Платёж ${payment.payment_id}, ${new Date(payment.created_at).toLocaleString("ru-RU")}. ${s.hint}` : s.hint}</small>
        </div>
      </div>
      <div className="order-card">
        <div className="row"><span>Товар</span><b>{order.title}</b></div>
        <div className="row"><span>Продавец</span><b>{order.merchant_name}</b></div>
        <div className="row"><span>Счёт получателя</span><b className="num">{account(order.payee_account)}</b></div>
        <div className="row big"><span>Сумма</span><b>{money(order.amount_kop)}</b></div>
      </div>
      <div className="order-actions">
        {order.status === "draft" && <button className="primary" disabled={busy} onClick={onCheckout}>Проверить и перейти к оплате</button>}
        {order.status === "awaiting_confirmation" && onOpenConfirm && (
          <button className="primary" onClick={onOpenConfirm} data-return-focus>Ввести код подтверждения</button>
        )}
        {order.status === "awaiting_confirmation" && !onOpenConfirm && (
          <button className="secondary" disabled={busy} onClick={onCheckout} data-return-focus>Запросить новый код</button>
        )}
      </div>
      {policy && (
        <section className="policy" aria-labelledby="policy-title">
          <div className="policy-head">
            <h3 id="policy-title">Policy Engine</h3>
            <span className={`pill ${passed === policy.length ? "ok" : "bad"}`}>{passed} из {policy.length} правил</span>
          </div>
          <p className="note">Правила проверяет код, а не модель. Перед списанием они проверяются ещё раз.</p>
          <CheckList reveal={fresh} items={policy.map((p) => ({ key: p.rule, name: RULE_RU[p.rule] ?? p.rule, passed: p.passed, detail: p.detail }))} />
        </section>
      )}
    </div>
  );
}

// Фокус заперт внутри окна, Esc закрывает, после закрытия фокус возвращается к кнопке, которая открыла окно,
// или, если её уже нет (статус заказа изменился), к следующему действию по заказу.
function useDialogFocus(ref: { current: HTMLElement | null }, onEscape: () => void) {
  const esc = useRef(onEscape);
  esc.current = onEscape;
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const node = ref.current;
    document.body.classList.add("modal-open");
    const focusable = () => Array.from(node?.querySelectorAll<HTMLElement>(
      "button:not([disabled]), input:not([disabled]), select:not([disabled]), [href], [tabindex]:not([tabindex='-1'])") ?? []);
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") { e.preventDefault(); esc.current(); return; }
      if (e.key !== "Tab" || !node) return;
      const f = focusable();
      if (!f.length) return;
      const [first, last] = [f[0], f[f.length - 1]];
      if (!node.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
      else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.classList.remove("modal-open");
      // Кнопка, открывшая окно, к этому моменту может исчезнуть: возвращаем фокус после перерисовки.
      requestAnimationFrame(() => {
        const usable = opener && opener !== document.body && opener.isConnected && !opener.matches(":disabled");
        const target = usable ? opener : document.querySelector<HTMLElement>("[data-return-focus]");
        target?.focus();
      });
    };
  }, [ref]);
}

function ConfirmModal({ ch, device, order, onConfirm, onResend, onCancel }: {
  ch: Challenge; device: DeviceMessage | null; order: Order;
  onConfirm: (code: string) => Promise<{ error: string; resend: boolean } | null>; onResend: () => void; onCancel: () => void;
}) {
  const [code, setCode] = useState("");
  const [err, setErr] = useState<{ error: string; resend: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const inFlight = useRef(false);  // два Enter в одном кадре: busy обновится только к следующему, ref — сразу
  useDialogFocus(box, onCancel);

  async function submit() {
    if (inFlight.current || code.length !== 6 || err?.resend) return;
    inFlight.current = true;
    setBusy(true);
    try {
      const r = await onConfirm(code);
      setErr(r);
      if (r) setCode("");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-note" ref={box}>
        <span className="grabber" aria-hidden="true" />
        <div className="push" role="status">
          <div className="push-head">
            <span className="push-app" aria-hidden="true"><Icon name="wallet" size={14} /></span>
            <span>Уведомление на устройстве</span>
            <time>сейчас</time>
          </div>
          {device ? (
            <>
              <b>Код для оплаты {money(device.amount_kop)}</b>
              <p>Получатель {device.merchant_name}. Код <span className="push-code">{device.code}</span>, никому его не сообщайте.</p>
            </>
          ) : <p>Уведомление не пришло. Запросите код ещё раз.</p>}
        </div>
        <h2 id="confirm-title">Подтвердите оплату</h2>
        <p className="amount">{money(ch.amount_kop)}</p>
        <dl className="confirm-rows">
          <div><dt>Получатель</dt><dd>{ch.merchant_name}</dd></div>
          <div><dt>Счёт получателя</dt><dd className="num">{account(ch.payee_account)}</dd></div>
          <div><dt>Назначение</dt><dd>Оплата заказа: {order.title}</dd></div>
          <div><dt>Списать с</dt><dd>Тестовый счёт sandbox</dd></div>
          <div><dt>Код действует до</dt><dd className="num">{hhmm(ch.expires_at)}</dd></div>
        </dl>
        <p className="note lock" id="confirm-note">
          <Icon name="lock" size={18} />Сумму и получателя проверил Policy Engine. Агент не может подтвердить платёж за вас.
        </p>
        <form onSubmit={(e) => { e.preventDefault(); submit(); }}>
          <label htmlFor="confirm-code" className="field-label">Код из уведомления</label>
          <input id="confirm-code" className="code" inputMode="numeric" autoComplete="one-time-code" autoFocus maxLength={6} value={code}
                 disabled={!!err?.resend} aria-invalid={err && !err.resend ? true : undefined} aria-describedby={err ? "confirm-error" : undefined}
                 onChange={(e) => { setCode(e.target.value.replace(/\D/g, "")); if (err && !err.resend) setErr(null); }} placeholder="6 цифр" />
          {err && <p className="error" id="confirm-error" role="alert">{err.error}</p>}
          <div className="actions">
            <button type="button" className="secondary" onClick={onCancel}>Отмена</button>
            {err?.resend
              ? <button type="button" className="primary" onClick={onResend}>Запросить новый код</button>
              : <button type="submit" className="primary" disabled={code.length !== 6 || busy}>Подтвердить оплату</button>}
          </div>
        </form>
      </div>
    </div>
  );
}

function AuditView({ audit, hasTask, onRefresh }: {
  audit: { entries: AuditEntry[]; ok: boolean } | "error" | null; hasTask: boolean; onRefresh: () => void;
}) {
  if (audit === "error") {
    return (
      <>
        <h2 className="sheet-title">Журнал</h2>
        <Empty icon="alert" title="Журнал не загрузился" text="Сервер не ответил. Повторите запрос.">
          <button className="secondary" onClick={onRefresh}>Повторить</button>
        </Empty>
      </>
    );
  }
  if (!audit || audit.entries.length === 0) {
    return (
      <>
        <h2 className="sheet-title">Журнал</h2>
        <Empty icon="journal" title="Журнал пуст" text="Здесь появятся действия агента, проверок и платежа. Записи нельзя изменить или удалить: каждая связана с предыдущей подписью HMAC." />
      </>
    );
  }
  return (
    <div className="audit">
      <h2 className="sheet-title">Журнал</h2>
      <div className="audit-summary">
        <span className={`pill ${audit.ok ? "ok" : "bad"}`}>{audit.ok ? "Хеш-цепочка цела" : "Цепочка нарушена"}</span>
        <span className="muted">{audit.entries.length} записей {hasTask ? "по текущему поручению" : "за сессию"}</span>
        <button className="quiet" onClick={onRefresh}><Icon name="refresh" size={18} />Обновить</button>
      </div>
      <ol className="audit-list">
        {audit.entries.map((e) => (
          <li key={e.id} className={`z-${e.zone}`}>
            <span className="zone">{ZONE_RU[e.zone] ?? e.zone}</span>
            <div className="audit-main">
              <b>{ACTION_RU[e.action] ?? e.action}</b>
              <small>{e.action}, {e.actor}, {new Date(e.ts).toLocaleTimeString("ru-RU")}</small>
              {Object.keys(e.data).length > 0 && <code>{JSON.stringify(e.data)}</code>}
            </div>
            <small className="hash" title={e.hash}>#{e.hash.slice(0, 8)}</small>
          </li>
        ))}
      </ol>
    </div>
  );
}

function SandboxView({ order, task, limits, onLimit, onScenario }: {
  order: Order | null; task: Task | null; limits: Limits | null; onLimit: (v: number) => void; onScenario: (n: string) => void;
}) {
  const target = order ?? (task?.recommended_offer_id ? task.offers.find((o) => o.offer.offer_id === task.recommended_offer_id)?.offer : null);
  const tiles: { name: string; title: string; text: string; icon: IconName; tone: string; enabled: boolean }[] = [
    { name: "price_spike", title: "Рост цены на 25%", text: "Продавец меняет цену между выбором и оплатой", icon: "trendUp", tone: "t-cyan", enabled: !!target },
    { name: "out_of_stock", title: "Товар закончился", text: "Наличие проверяется ещё раз перед списанием", icon: "box", tone: "t-lime", enabled: !!target },
    { name: "payee_swap", title: "Подмена реквизитов", text: "Счёт получателя в заказе заменён на чужой", icon: "swap", tone: "t-peach", enabled: !!order },
    { name: "code_expired", title: "Код истёк", text: "Прошло пять минут после отправки кода", icon: "clock", tone: "t-lilac", enabled: order?.status === "awaiting_confirmation" },
  ];
  return (
    <div className="sandbox">
      <h2 className="sheet-title">Sandbox</h2>
      <p className="note">
        События у продавца и атаки, которые должен остановить платёжный контур.{" "}
        {target ? <>Сценарий применяется к {order ? "заказу" : "рекомендованному предложению"}: {target.title}.</> : "Сначала отправьте поручение агенту."}
      </p>
      <div className="scen">
        {tiles.map((t) => (
          <button key={t.name} className={`tile ${t.tone}`} disabled={!t.enabled} onClick={() => onScenario(t.name)}>
            <b>{t.title}</b>
            <small>{t.text}</small>
            <Icon name={t.icon} size={44} className="tile-icon" />
          </button>
        ))}
        <button className="tile wide" onClick={() => onScenario("reset")}>
          <b>Сбросить каталоги</b>
          <small>Вернуть исходные цены и наличие у всех продавцов</small>
          <Icon name="refresh" size={28} className="tile-icon" />
        </button>
      </div>
      <div className="limit">
        <label htmlFor="limit-select">Лимит на одну операцию</label>
        <select id="limit-select" value={limits?.per_operation_limit_rub ?? 30000} onChange={(e) => onLimit(Number(e.target.value))} disabled={!limits}>
          {LIMIT_OPTIONS.map((v) => <option key={v} value={v}>{rub(v)}</option>)}
        </select>
      </div>
      <p className="note">
        Prompt injection уже лежит в каталоге «Комплект-Про»: карточка «СУПЕРЦЕНА» с инструкциями для агента и чужим счётом.
        Проверка целостности карточки её исключает.
      </p>
    </div>
  );
}

