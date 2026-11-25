import sqlite3, time, math, uuid, re, threading, os
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
DB=Path(__file__).with_name('warehouse.db')
LOCK=threading.RLock()
@contextmanager
def conn():
    c=sqlite3.connect(DB,timeout=20); c.row_factory=sqlite3.Row
    try:
        with c: yield c
    finally: c.close()
def init():
    with LOCK, conn() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, owner TEXT, item TEXT, amount REAL, payment TEXT, status TEXT, refund TEXT DEFAULT 'NONE'); CREATE TABLE IF NOT EXISTS tickets(id TEXT PRIMARY KEY, order_id TEXT, intent TEXT, status TEXT, reason TEXT, trace TEXT); CREATE TABLE IF NOT EXISTS logs(trace TEXT, timestamp TEXT, node TEXT, detail TEXT); CREATE TABLE IF NOT EXISTS metrics(trace TEXT, ms REAL, outcome TEXT); CREATE TABLE IF NOT EXISTS inventory(item TEXT PRIMARY KEY, stock INTEGER);''')
        cols={r[1] for r in c.execute('PRAGMA table_info(tickets)')}
        for name in ('customer_request','evidence','policy','classification'):
            if name not in cols:c.execute('ALTER TABLE tickets ADD COLUMN '+name+" TEXT DEFAULT ''")
        c.executemany('INSERT OR IGNORE INTO orders(id,owner,item,amount,payment,status) VALUES(?,?,?,?,?,?)',[('ORD-99182','cust_402','Wireless Headphones',89.99,'PAID','PROCESSING'),('ORD-4092','cust_402','Keyboard',59.99,'FAILED','HOLD'),('ORD-5001','cust_402','Mouse',29.99,'PAID','SHIPPED'),('ORD-6001','cust_999','Monitor',199.99,'PAID','PROCESSING')]); c.executemany('INSERT OR IGNORE INTO inventory VALUES(?,?)',[('Wireless Headphones',12),('Keyboard',5),('Mouse',20),('Monitor',2)])
def log(trace,node,detail):
    with LOCK,conn() as c:c.execute('INSERT INTO logs VALUES(?,?,?,?)',(trace,datetime.now(timezone.utc).isoformat(),node,detail))
def rows(sql,args=()):
    with LOCK,conn() as c:return [dict(r) for r in c.execute(sql,args)]
def request(owner,order,message,timeout=False):
    t=uuid.uuid4().hex[:10]; start=time.perf_counter(); log(t,'Ingress','Request received (raw text not logged)')
    def finish(outcome,text,ticket=None):
        log(t,'Dispatch',outcome)
        with LOCK,conn() as c:c.execute('INSERT INTO metrics VALUES(?,?,?)',(t,(time.perf_counter()-start)*1000,outcome))
        return dict(trace=t,outcome=outcome,response=text,ticket=ticket)
    if not all(isinstance(x,str) for x in (owner,order,message)) or not owner or not re.fullmatch(r'ORD-\d+',order or '') or not message or len(message)>500:return finish('INVALID','400: Invalid request.')
    if re.search(r'ignore .*rules|credentials|system prompt|set .*price|ignore .*instructions',message,re.I):
        log(t,'Guardrail','Suspicious input blocked'); return finish('DENIED','Request blocked by security policy.')
    data=rows('SELECT * FROM orders WHERE id=? AND owner=?',(order,owner))
    if not data:return finish('DENIED','Order unavailable for this customer.')
    o=data[0]; m=message.lower(); intents=[x for x,pat in [('CANCEL','cancel'),('REFUND','refund'),('EDIT','edit|change|update'),('INVENTORY','stock|inventory|available'),('PAYMENT','payment|paid'),('STATUS','status|track|shipping|where')] if re.search(pat,m)]
    sensitive=[x for x in intents if x in ('CANCEL','REFUND','EDIT')]; intent='REVIEW' if len(sensitive)>1 or re.search(r'\b(if|otherwise)\b',m) else (sensitive or intents or ['REVIEW'])[0]
    policy='Local business rules: sensitive actions require human approval.'; classification='Rule-based'
    if os.getenv('WAREHOUSE_AI','0') == '1':
        from orchestrator import analyze
        result=analyze(message,o)
        predicted=result['intent']
        # Explicit sensitive or conditional requests cannot be downgraded by AI.
        if result['model_status'] != 'ok' or intent=='REVIEW' and (len(sensitive)>1 or re.search(r'\b(if|otherwise)\b',m)):
            intent='REVIEW'
        elif sensitive and predicted!=intent:
            intent='REVIEW'
        else:
            intent=predicted
        log(t,'LLM classifier',f"{result['model_status']}; predicted={predicted}; tokens={result['tokens']}")
        policy=result['policy']; classification=f"{result['model_status']}; predicted={predicted}; final={intent}"
        log(t,'LangGraph policy',policy)
    log(t,'Triage Agent',intent); log(t,'Researcher Agent','SQLite order lookup; ownership verified'); log(t,'Policy Evaluator',f"payment={o['payment']}; status={o['status']}")
    def ticket(kind,reason,status='PENDING'):
        tid='T-'+uuid.uuid4().hex[:8]
        # Store a bounded, redacted customer request, separate from audit logs.
        safe=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[email redacted]', message)
        safe=re.sub(r'(?<!\d)(?:\+?\d[ .()-]?){7,19}(?!\d)', '[number redacted]', safe)
        evidence=f"{o['item']} | ${o['amount']:.2f} | payment={o['payment']} | status={o['status']} | owner={o['owner']}"
        with LOCK,conn() as c:c.execute('INSERT INTO tickets(id,order_id,intent,status,reason,trace,customer_request,evidence,policy,classification) VALUES(?,?,?,?,?,?,?,?,?,?)',(tid,order,kind,status,reason,t,safe,evidence,policy,classification))
        log(t,'HITL Gate',f'{tid} {status}'); return finish(status,reason,tid)
    if timeout:
        for i in range(1,4):log(t,'External adapter',f'Simulated timeout attempt {i}');time.sleep(.02)
        return ticket('RETRY','External service unavailable; queued for manual follow-up.','QUEUED')
    if intent=='STATUS':return finish('RESOLVED',f"{order}: {o['status']}. Payment: {o['payment']}.")
    if intent=='PAYMENT':return finish('RESOLVED',f"Payment: {o['payment']}. Unpaid orders cannot ship.")
    if intent=='INVENTORY':return finish('RESOLVED',f"{o['item']}: {rows('SELECT stock FROM inventory WHERE item=?',(o['item'],))[0]['stock']} in stock.")
    if intent=='CANCEL' and o['status'] not in ('PROCESSING','HOLD'):return finish('DENIED','Order is no longer eligible for cancellation.')
    reason=f"Cancellation eligible; proposed simulated refund ${o['amount']:.2f}." if intent=='CANCEL' and o['payment']=='PAID' else 'Human review required; no order changes made.'
    return ticket(intent,reason)
def decide(tid,approve):
    with LOCK,conn() as c:
        c.execute('BEGIN IMMEDIATE'); t=c.execute('SELECT * FROM tickets WHERE id=?',(tid,)).fetchone()
        if not t or t['status']!='PENDING':return 'Ticket not pending.'
        status='REJECTED'
        if approve:
            if t['intent']=='CANCEL':
                cur=c.execute("UPDATE orders SET status='CANCELLED',refund=CASE WHEN payment='PAID' THEN 'SIMULATED_APPROVED' ELSE 'NONE' END WHERE id=? AND status IN ('PROCESSING','HOLD')",(t['order_id'],));status='APPROVED' if cur.rowcount else 'STALE_DENIED'
            else:status='MANUAL_FOLLOWUP' # no unsupported writes
        c.execute('UPDATE tickets SET status=? WHERE id=?',(status,tid));c.execute('INSERT INTO logs VALUES(?,?,?,?)',(t['trace'],datetime.now(timezone.utc).isoformat(),'Human decision',status))
    return status
def close_followup(tid):
    with LOCK,conn() as c:
        c.execute('BEGIN IMMEDIATE'); t=c.execute('SELECT * FROM tickets WHERE id=?',(tid,)).fetchone()
        if not t or t['status'] not in ('QUEUED','MANUAL_FOLLOWUP'):return 'Ticket not eligible for closure.'
        c.execute("UPDATE tickets SET status='DISMISSED' WHERE id=?",(tid,))
        c.execute('INSERT INTO logs VALUES(?,?,?,?)',(t['trace'],datetime.now(timezone.utc).isoformat(),'Human follow-up','DISMISSED; no order changes'))
    return 'DISMISSED'
def ship(order):
    with LOCK,conn() as c:
        cur=c.execute("UPDATE orders SET status='SHIPPED' WHERE id=? AND payment='PAID' AND status='PROCESSING' AND NOT EXISTS (SELECT 1 FROM tickets WHERE order_id=? AND status IN ('PENDING','MANUAL_FOLLOWUP','QUEUED'))",(order,order)); ok=bool(cur.rowcount)
    log(uuid.uuid4().hex[:10],'Warehouse guard','Shipment allowed' if ok else 'Shipment blocked');return ok
def scale(n):
    n=max(0,min(500,int(n))); workers=max(1,math.ceil(n/50)); start=time.perf_counter()
    def task(i):time.sleep(.002);return i
    with ThreadPoolExecutor(max_workers=workers) as pool: done=list(pool.map(task,range(n)))
    return dict(requests=n,workers=workers,completed=len(done),ms=round((time.perf_counter()-start)*1000,2))

def reset_demo():
    with LOCK:
        with conn() as c:
            for name in ('tickets','logs','metrics','orders','inventory'):
                c.execute('DELETE FROM '+name)
        init()
