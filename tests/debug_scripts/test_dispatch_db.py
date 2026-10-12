#!/usr/bin/env python3
"""Test dispatch with custom database path."""

import asyncio
import sys

from kajenn_mail_proxy.core import MailProxy

DB_PATH = "/tmp/mail_service_test.db"

async def main():
    print("🧪 Test manuale dispatch messaggio (test DB)")
    print()

    # Initialize core with test mode (no background loops)
    core = MailProxy(
        db_path=DB_PATH,
        test_mode=True
    )

    print("✅ Core inizializzato")
    print()

    # Get current timestamp
    now_ts = core._utc_now_epoch()
    print(f"🕐 Now timestamp: {now_ts}")
    print()

    # Fetch one ready message
    messages = await core.db.fetch_ready_messages(limit=1, now_ts=now_ts)

    if not messages:
        print("❌ Nessun messaggio pronto per invio")
        sys.exit(1)

    msg = messages[0]
    print(f"✅ Trovato messaggio: {msg['id']}")
    print()
    print("📋 Dettagli:")
    print(f"   Account ID: {msg.get('account_id')}")
    print(f"   Priority: {msg.get('priority')}")
    print(f"   Message: {msg.get('message')}")
    print()
    print("🚀 Tento dispatch...")
    print()

    # Dispatch the message
    try:
        await core._dispatch_message(msg, now_ts)
        print()
        print("✅ Dispatch completato senza eccezioni!")
    except Exception as e:
        print()
        print(f"❌ Eccezione durante dispatch: {e}")
        import traceback
        traceback.print_exc()

    # Check final state
    print()
    print("📊 Stato messaggio dopo dispatch:")
    conn = await core.db._connect()
    async with conn.execute(
        "SELECT sent_ts, error_ts, error, deferred_ts FROM messages WHERE id = ?",
        (msg['id'],)
    ) as cur:
        row = await cur.fetchone()
        if row:
            print(f"   sent_ts: {row[0]}")
            print(f"   error_ts: {row[1]}")
            print(f"   error: {row[2]}")
            print(f"   deferred_ts: {row[3]}")

            if row[0]:  # sent_ts
                print()
                print("🎉 MESSAGGIO INVIATO CON SUCCESSO!")
            elif row[1]:  # error_ts
                print()
                print(f"❌ MESSAGGIO IN ERRORE: {row[2]}")
            elif row[3]:  # deferred_ts
                print()
                print(f"⏱️  MESSAGGIO DEFERITO FINO A: {row[3]}")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
