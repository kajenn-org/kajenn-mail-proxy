Architecture Overview
=====================

This document explains the architectural benefits of using kajenn-mail-proxy
as an email proxy instead of directly connecting to SMTP servers from your application.

.. contents::
   :local:
   :depth: 2

Why Use an Email Proxy?
-----------------------

When building enterprise applications, sending emails directly from the application
to SMTP servers introduces several challenges:

1. **Tight coupling** between business logic and mail delivery
2. **Synchronous operations** that block request handlers
3. **No built-in retry** mechanisms for transient failures
4. **Rate limiting** must be implemented in every service
5. **Connection management** overhead on each send
6. **No centralized monitoring** of email delivery
7. **Difficult debugging** of delivery issues

kajenn-mail-proxy solves these problems by introducing a **decoupled, asynchronous
email delivery layer** that sits between your application and SMTP servers.

Architecture Pattern
--------------------

Traditional Direct SMTP
^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: text

   ┌─────────────────────────────────────────────────────┐
   │              Your Application                         │
   │                                                      │
   │  HTTP Request Handler                                │
   │    ↓                                                 │
   │  1. Process business logic                           │
   │  2. Open SMTP connection (500ms) ⏱                  │
   │  3. Authenticate                                     │
   │  4. Send email                                       │
   │  5. Handle errors/retries                            │
   │  6. Close connection                                 │
   │    ↓                                                 │
   │  HTTP Response (1-2 seconds later) ❌                │
   └──────────────────┬──────────────────────────────────┘
                      │
                      ▼
            ┌─────────────────┐
            │   SMTP Server   │
            │  (Gmail/SES)    │
            └─────────────────┘

**Problems:**

- ❌ Request handler blocked for 1-2 seconds
- ❌ User waits for email to be sent
- ❌ SMTP errors crash the request
- ❌ No retry on transient failures
- ❌ Connection overhead on every send
- ❌ Rate limiting in application code

Proxy-Based Architecture
^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: text

   ┌─────────────────────────────────────────────────────┐
   │              Your Application                         │
   │                                                      │
   │  HTTP Request Handler                                │
   │    ↓                                                 │
   │  1. Process business logic                           │
   │  2. INSERT into email.message (10ms)                 │
   │  3. db.commit()                                      │
   │  4. POST /commands/run-now (optional, 2ms)           │
   │    ↓                                                 │
   │  HTTP Response (50ms later) ✅                       │
   └──────────────────┬──────────────────────────────────┘
                      │
                      ▼ (async, decoupled)
   ┌─────────────────────────────────────────────────────┐
   │         kajenn-mail-proxy                             │
   │                                                      │
   │  ┌────────────┐    ┌──────────────┐                │
   │  │  Messages  │───→│  SMTP Pool   │───→ Send       │
   │  │   Queue    │    │  (reuse)     │                │
   │  └────────────┘    └──────────────┘                │
   │                                                      │
   │  - Rate limiting                                     │
   │  - Retry logic                                       │
   │  - Connection pooling                                │
   │  - Monitoring                                        │
   │  - Delivery reports                                  │
   └──────────────────┬──────────────────────────────────┘
                      │
                      ▼
            ┌─────────────────┐
            │   SMTP Server   │
            │  (Gmail/SES)    │
            └─────────────────┘

**Benefits:**

- ✅ Request handler returns in ~50ms
- ✅ User doesn't wait for email
- ✅ SMTP errors don't affect request
- ✅ Automatic retry on failures
- ✅ Connection pooling (10-50x faster)
- ✅ Centralized rate limiting

Key Benefits
------------

1. Decoupling (Write vs Send Concern)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Separation of Responsibilities:**

.. code-block:: python

   # Your Application
   def create_order(order_data):
       # 1. Business logic
       order = db.table('orders').insert(order_data)

       # 2. Email persistence (ALWAYS committed)
       email = db.table('email.message').insert({
           'from': 'sales@company.com',
           'to': order['customer_email'],
           'subject': f'Order #{order["id"]} Confirmation',
           'body': render_template('order_confirmation.html', order)
       })
       db.commit()  # ✅ Guaranteed persistence

       # 3. Trigger async send (best effort)
       try:
           httpx.post("http://localhost:8000/mailproxy/v1/commands/run-now", timeout=2)
       except:
           pass  # Non-blocking, polling will handle it

       return order

**What You Get:**

- ✅ **Email record always saved** - audit trail guaranteed
- ✅ **Request completes fast** - no SMTP blocking
- ✅ **Delivery decoupled** - SMTP issues don't affect business logic
- ✅ **Retry capability** - can resend failed emails from DB

**Traditional Approach Problems:**

.. code-block:: python

   # ❌ Problematic direct SMTP
   def create_order(order_data):
       order = db.table('orders').insert(order_data)

       try:
           # ❌ Blocks request for 500-2000ms
           smtp = smtplib.SMTP('smtp.gmail.com', 587)
           smtp.login(user, password)
           smtp.send_message(email)
           smtp.quit()

           db.commit()  # Only commits if SMTP succeeds
       except smtplib.SMTPException as e:
           # ❌ Business transaction rolls back due to email error
           db.rollback()
           raise HTTPError(500, "Email failed")

       return order

2. Resilience and Reliability
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Failure Scenarios Handled:**

+-----------------------------------+----------------------------------+
| Scenario                          | Proxy Behavior                   |
+===================================+==================================+
| SMTP server temporarily down      | Retries every 1-5 min until OK   |
+-----------------------------------+----------------------------------+
| Network timeout                   | Queues message, retries later    |
+-----------------------------------+----------------------------------+
| Rate limit exceeded               | Defers message automatically     |
+-----------------------------------+----------------------------------+
| Authentication failure            | Marks error, alerts operator     |
+-----------------------------------+----------------------------------+
| Invalid recipient                 | Marks error, preserves record    |
+-----------------------------------+----------------------------------+
| Proxy service down                | Messages safe in DB, sent later  |
+-----------------------------------+----------------------------------+

**Example: SMTP Server Maintenance**

.. code-block:: text

   T=0:00  → User creates order
            Email saved to DB ✅
            Commit successful ✅
            User sees "Order created" ✅

   T=0:01  → Proxy tries to send
            SMTP connection refused (maintenance)
            Message marked for retry

   T=1:00  → Proxy retries (polling)
            Still down, retry again

   T=5:00  → Proxy retries
            Still down, retry again

   T=30:00 → SMTP server back online
            Proxy retries
            Email sent successfully ✅
            Customer receives email

**With Direct SMTP:** User would have seen "Order creation failed" at T=0:01 ❌

3. Performance Optimization
^^^^^^^^^^^^^^^^^^^^^^^^^^^

Connection Pooling
""""""""""""""""""

The proxy maintains persistent SMTP connections (5 min TTL):

.. code-block:: text

   ┌─────────────────────────────────────────────────────┐
   │ Message Batch Performance                            │
   ├─────────────────┬───────────────────┬───────────────┤
   │                 │ Direct SMTP       │ With Proxy    │
   ├─────────────────┼───────────────────┼───────────────┤
   │ Message 1       │ 500ms (connect)   │ 500ms (init)  │
   │ Message 2       │ 500ms (reconnect) │  50ms (reuse) │
   │ Message 3       │ 500ms (reconnect) │  50ms (reuse) │
   │ Message 4       │ 500ms (reconnect) │  50ms (reuse) │
   │ Message 5       │ 500ms (reconnect) │  50ms (reuse) │
   ├─────────────────┼───────────────────┼───────────────┤
   │ **Total**       │ **2.5 seconds**   │ **0.7 seconds**│
   │ **Improvement** │                   │ **3.5x faster**│
   └─────────────────┴───────────────────┴───────────────┘

**For high-volume scenarios (100 messages):**

- Direct SMTP: ~50 seconds (100 × 500ms)
- With Proxy: ~5 seconds (1 × 500ms + 99 × 50ms)
- **Improvement: 10x faster** ⚡

Async Processing
""""""""""""""""

.. code-block:: text

   Request Latency Comparison

   Direct SMTP:
   ├─ Business logic: 20ms
   ├─ SMTP connect: 300ms
   ├─ SMTP auth: 200ms
   ├─ Send email: 100ms
   └─ Total: 620ms ❌

   With Proxy:
   ├─ Business logic: 20ms
   ├─ DB insert: 5ms
   ├─ Commit: 5ms
   ├─ Trigger run-now: 2ms
   └─ Total: 32ms ✅ (19x faster)

**User Experience:**

- Direct SMTP: "Processing..." spinner for 1-2 seconds
- With Proxy: Instant response, email sent in background

4. Centralized Rate Limiting
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Problem with Direct SMTP:**

.. code-block:: python

   # ❌ Rate limiting in every service/instance
   class EmailService:
       def __init__(self):
           self.rate_limiter = RateLimiter(
               limit_per_minute=10,
               limit_per_hour=500
           )

       def send(self, email):
           if not self.rate_limiter.check():
               raise RateLimitError()
           # Send email...

**Issues:**

- ❌ Each service instance has separate limiter (no coordination)
- ❌ Scaling to 10 servers → 10x rate limit (unintended)
- ❌ Manual implementation in every codebase
- ❌ No automatic deferral/retry

**With Proxy:**

.. code-block:: python

   # ✅ Configure once, works everywhere
   POST /account
   {
     "id": "smtp-main",
     "host": "smtp.gmail.com",
     "limit_per_minute": 10,
     "limit_per_hour": 500,
     "limit_behavior": "defer"  // or "error"
   }

**Benefits:**

- ✅ Single source of truth for rate limits
- ✅ Shared across all application instances
- ✅ Automatic deferral when limit reached
- ✅ Respects SMTP server policies

**Deferred Message Example:**

.. code-block:: text

   T=0:00 → Message 1-10 sent (10/min limit)
   T=0:05 → Message 11 arrives
            Rate limit check: 10 sent in last minute
            Action: Defer until T=1:00
            Status: {"status": "deferred", "deferred_until": 1735689660}
   T=1:00 → Automatic retry
            Rate limit OK: 0 sent in last minute
            Message 11 sent successfully ✅

5. Monitoring and Observability
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Centralized Metrics:**

The proxy exposes Prometheus metrics at ``GET /mailproxy/v1/metrics``:

.. code-block:: text

   # HELP gmp_sent_total Total emails sent
   gmp_sent_total{account_id="smtp-main"} 1523

   # HELP gmp_errors_total Total emails failed
   gmp_errors_total{account_id="smtp-main"} 12

   # HELP gmp_deferred_total Total emails deferred
   gmp_deferred_total{account_id="smtp-main"} 45

   # HELP gmp_rate_limited_total Rate limit hits
   gmp_rate_limited_total{account_id="smtp-main"} 45

   # HELP gmp_pending_messages Current queue size
   gmp_pending_messages 3

**Grafana Dashboard Example:**

.. code-block:: text

   ┌──────────────────────────────────────────────┐
   │  Email Delivery Dashboard                    │
   ├──────────────────────────────────────────────┤
   │  📊 Throughput                               │
   │  ▓▓▓▓▓▓▓▓░░ 145 emails/hour                  │
   │                                              │
   │  ✅ Success Rate                             │
   │  ████████░░ 98.7%                            │
   │                                              │
   │  ⚠️  Error Rate                              │
   │  ▓░░░░░░░░░ 1.3% (2 failures)                │
   │                                              │
   │  📈 Queue Size                               │
   │  ▓▓░░░░░░░░ 3 pending                        │
   │                                              │
   │  ⏱️  Avg Latency                             │
   │  52ms (last hour)                            │
   └──────────────────────────────────────────────┘

**Alerting Rules:**

.. code-block:: yaml

   # Alert if error rate > 5%
   - alert: HighEmailErrorRate
     expr: |
       rate(gmp_errors_total[5m]) /
       rate(gmp_sent_total[5m]) > 0.05

   # Alert if queue growing
   - alert: EmailQueueBacklog
     expr: gmp_pending_messages > 100

**With Direct SMTP:** No centralized visibility, must check logs on each server ❌

6. Multi-Tenant Support
^^^^^^^^^^^^^^^^^^^^^^^

**Multiple SMTP Accounts:**

.. code-block:: python

   # Configure accounts for different purposes
   accounts = [
       {
           "id": "transactional",
           "host": "smtp.sendgrid.com",
           "limit_per_minute": 100,
           "use_tls": True
       },
       {
           "id": "marketing",
           "host": "smtp.mailgun.com",
           "limit_per_minute": 50,
           "use_tls": True
       },
       {
           "id": "notifications",
           "host": "email-smtp.eu-central-1.amazonaws.com",
           "limit_per_minute": 10,
           "use_tls": True
       }
   ]

**Route by Purpose:**

.. code-block:: python

   # Transactional emails (high priority)
   order_email = {
       "account_id": "transactional",
       "priority": 0,  # immediate
       "from": "orders@company.com",
       "to": customer_email,
       "subject": "Order Confirmation"
   }

   # Marketing emails (lower priority)
   newsletter = {
       "account_id": "marketing",
       "priority": 3,  # low
       "from": "newsletter@company.com",
       "to": subscriber_email,
       "subject": "Monthly Newsletter"
   }

   # System notifications
   alert = {
       "account_id": "notifications",
       "priority": 0,  # immediate
       "from": "alerts@company.com",
       "to": admin_email,
       "subject": "System Alert"
   }

**Benefits:**

- ✅ Independent rate limits per account
- ✅ Different SMTP providers for different purposes
- ✅ Isolated failure domains
- ✅ Cost optimization (cheap provider for bulk, reliable for transactional)

7. Debugging and Troubleshooting
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Diagnostic Tools Included:**

.. code-block:: bash

   # Check system state
   python3 diagnose.py
   # Output:
   # 📊 Messaggi pending: 3
   # 📊 Messaggi inviati: 1523
   # 📊 Messaggi con errore: 2
   # 🔐 Account configurati: 3

   # Monitor real-time activity
   python3 check_loop.py
   # Output:
   # ✅ Loop sta processando messaggi

   # Test specific message
   python3 test_dispatch.py
   # Output:
   # 🎉 MESSAGGIO INVIATO CON SUCCESSO!

**Detailed Logs:**

.. code-block:: text

   # With delivery_activity=true
   [INFO] Attempting delivery for message msg-001 to user@example.com
   [INFO] Delivery succeeded for message msg-001 (account=smtp-main)

   # Error case
   [WARNING] Delivery failed for message msg-002: Authentication failed

**Database Inspection:**

.. code-block:: sql

   -- Find stuck messages
   SELECT id, subject, error, created_at
   FROM messages
   WHERE sent_ts IS NULL
     AND error_ts IS NOT NULL;

   -- Check rate limiting
   SELECT account_id, COUNT(*) as sends_last_hour
   FROM send_log
   WHERE timestamp > UNIX_TIMESTAMP() - 3600
   GROUP BY account_id;

**With Direct SMTP:** Must check application logs, no centralized view ❌

8. Attachment Handling
^^^^^^^^^^^^^^^^^^^^^^

**Flexible Attachment Sources:**

.. code-block:: python

   message = {
       "attachments": [
           # HTTP endpoint (proxy fetches via POST)
           {
               "filename": "invoice.pdf",
               "storage_path": "doc_id=123",
               "fetch_mode": "endpoint"
           },
           # External URL (proxy fetches)
           {
               "filename": "report.pdf",
               "storage_path": "https://storage.company.com/reports/monthly.pdf",
               "fetch_mode": "http_url"
           },
           # Base64 inline
           {
               "filename": "logo.png",
               "storage_path": "iVBORw0KGgoAAAANSUhEUgAA...",
               "fetch_mode": "base64"
           },
           # Local filesystem
           {
               "filename": "contract.pdf",
               "storage_path": "/var/attachments/contracts/2025/contract.pdf",
               "fetch_mode": "filesystem"
           }
       ]
   }

**Benefits:**

- ✅ Proxy handles URL fetching with timeout/retry
- ✅ Unified interface for different sources
- ✅ Memory efficient (streaming)
- ✅ Caching with MD5 markers

9. Priority Queuing
^^^^^^^^^^^^^^^^^^^

**Message Prioritization:**

.. code-block:: python

   # Immediate (priority=0) - sent ASAP
   password_reset = {
       "priority": 0,  # or "immediate"
       "subject": "Password Reset",
       # ... processed within seconds
   }

   # High (priority=1) - important transactional
   order_confirmation = {
       "priority": 1,  # or "high"
       "subject": "Order Confirmation",
       # ... processed within minute
   }

   # Medium (priority=2) - default
   notification = {
       "priority": 2,  # or "medium"
       "subject": "New Comment",
       # ... processed normally
   }

   # Low (priority=3) - bulk/marketing
   newsletter = {
       "priority": 3,  # or "low"
       "subject": "Monthly Newsletter",
       # ... processed when idle
   }

**Queue Processing Order:**

.. code-block:: sql

   -- Internal query (priority first, then FIFO)
   SELECT * FROM messages
   WHERE sent_ts IS NULL
   ORDER BY priority ASC,    -- 0, 1, 2, 3
            created_at ASC   -- oldest first

10. Scheduled Sending
^^^^^^^^^^^^^^^^^^^^^

**Defer Messages to Future:**

.. code-block:: python

   # Send tomorrow morning
   import time
   tomorrow_9am = int(time.mktime(
       datetime(2025, 10, 24, 9, 0).timetuple()
   ))

   reminder = {
       "subject": "Appointment Reminder",
       "body": "Your appointment is today at 10 AM",
       "deferred_ts": tomorrow_9am  # Unix timestamp
   }

**Use Cases:**

- ✅ Appointment reminders (send 1 hour before)
- ✅ Scheduled newsletters (send at optimal time)
- ✅ Follow-up emails (send 3 days after signup)
- ✅ Trial expiration warnings (send 7 days before)

Comparison Summary
------------------

.. list-table:: Direct SMTP vs Proxy Architecture
   :header-rows: 1
   :widths: 30 35 35

   * - Aspect
     - Direct SMTP
     - kajenn-mail-proxy
   * - **Request Latency**
     - 500-2000ms ❌
     - 20-50ms ✅
   * - **Resilience**
     - Fails on SMTP error ❌
     - Retries automatically ✅
   * - **Rate Limiting**
     - Manual per service ❌
     - Centralized, automatic ✅
   * - **Connection Reuse**
     - No (reconnect each time) ❌
     - Yes (pooled, 10-50x faster) ✅
   * - **Monitoring**
     - Application logs ❌
     - Prometheus metrics ✅
   * - **Debugging**
     - Scattered logs ❌
     - Diagnostic tools ✅
   * - **Decoupling**
     - Tight coupling ❌
     - Fully decoupled ✅
   * - **Priority Queuing**
     - No ❌
     - Yes (4 levels) ✅
   * - **Multi-Account**
     - Manual switching ❌
     - Built-in routing ✅
   * - **Scheduled Send**
     - Manual cron jobs ❌
     - Native support ✅
   * - **Delivery Reports**
     - No tracking ❌
     - Automatic reporting ✅
   * - **Attachment Handling**
     - Manual download ❌
     - URL/base64/filesystem ✅

When to Use This Architecture
-----------------------------

**Ideal For:**

✅ **Enterprise applications** with high reliability requirements
✅ **Multi-tenant systems** with different email providers
✅ **High-volume senders** (>100 emails/day)
✅ **Transactional emails** where user experience matters
✅ **Systems requiring audit trails** and delivery reports
✅ **Microservices architectures** needing centralized email

**Not Necessary For:**

⚠️ **Single-script tools** sending 1-2 emails
⚠️ **Development/testing** with mock SMTP
⚠️ **Ultra-low latency requirements** (<10ms end-to-end)

Migration Path
--------------

**Step 1: Deploy Proxy (No Code Changes)**

.. code-block:: bash

   # Deploy kajenn-mail-proxy
   docker run -p 8000:8000 \
     -v /data:/data \
     -e GMP_API_TOKEN=secret \
     kajenn-mail-proxy

**Step 2: Add SMTP Account**

.. code-block:: bash

   curl -X POST http://localhost:8000/mailproxy/v1/account \
     -H "X-API-Token: secret" \
     -d '{
       "id": "smtp-main",
       "host": "smtp.gmail.com",
       "port": 587,
       "user": "user@gmail.com",
       "password": "app-password"
     }'

**Step 3: Update Application Code**

.. code-block:: python

   # Before (direct SMTP)
   def send_email(from_addr, to_addr, subject, body):
       smtp = smtplib.SMTP('smtp.gmail.com', 587)
       smtp.login(user, password)
       smtp.send_message(...)
       smtp.quit()

   # After (via proxy)
   def send_email(from_addr, to_addr, subject, body):
       # 1. Persist
       email_id = db.table('email.message').insert({
           'from_address': from_addr,
           'to_address': to_addr,
           'subject': subject,
           'body': body
       })
       db.commit()

       # 2. Trigger (optional)
       try:
           httpx.post("http://localhost:8000/mailproxy/v1/commands/run-now")
       except:
           pass  # Polling handles it

**Step 4: Monitor and Tune**

.. code-block:: bash

   # Check metrics
   curl http://localhost:8000/mailproxy/v1/metrics

   # Adjust rate limits if needed
   curl -X POST http://localhost:8000/mailproxy/v1/account \
     -d '{"id": "smtp-main", "limit_per_minute": 50}'

Conclusion
----------

kajenn-mail-proxy provides a **production-ready email delivery layer** that
solves common problems in enterprise email sending:

1. ⚡ **Performance** - 10-50x faster via connection pooling
2. 🔄 **Resilience** - Automatic retries, never loses messages
3. 🎯 **Decoupling** - Business logic separated from delivery
4. 📊 **Observability** - Centralized metrics and monitoring
5. 🛡️ **Rate Limiting** - Automatic, shared across instances
6. 🎛️ **Control** - Priority queuing, scheduled sending, multi-account

By introducing an email proxy layer, you gain **enterprise-grade reliability**
without the complexity of implementing these features in every service.

**Next Steps:**

- See :doc:`installation` for deployment guide
- See :doc:`usage` for API reference
- See :doc:`protocol` for integration details
- See ``TROUBLESHOOTING.md`` for debugging guide
