# Dokotela: direct-pay doctor booking (South Africa)

FastAPI on Lambda (SAM) + Neon Postgres + Paystack for wallet top-ups. Static frontend on Cloudflare Pages.

## Deploy

```bash
# 1. DB schema
cd backend && pip install -r requirements.txt
DATABASE_URL="postgres://user:pass@host/db?sslmode=require" python migrate.py && cd ..

# 2. API
sam build
sam deploy --guided   # DatabaseUrl, JwtSecret, PaystackSecret, FrontendUrl, AllowedOrigin, AdminEmails
# note the ApiUrl and PaystackWebhookUrl outputs

# 3. Frontend: put ApiUrl in frontend/config.js, then
npx wrangler pages deploy frontend --project-name dokotela
```

Paystack dashboard: add the `PaystackWebhookUrl` as your webhook URL (ZAR must be enabled on the account). Until you have keys, deploy with `DevTopup=1` to get an "Add R500 test credit" button. Turn it off before going live.

## How it works

- **Wallet:** balance lives on `users.balance_cents` with a `wallet_tx` ledger. Booking debits the balance, creates the appointment and writes the ledger row in one DB transaction. A partial unique index on `(doctor_id, starts_at)` makes double-booking impossible.
- **Top-up:** `/wallet/topup` creates a Paystack transaction; the webhook (HMAC-verified) and the return-page verify both credit it, exactly once.
- **Slots:** computed live from each doctor's weekly schedule minus booked slots (SAST). The page refreshes every 30s. Status: `online`, `dropin`, `resting` (resting = no same-day bookings).
- **Money flow:** patient pays at booking. On "Complete", the doctor is credited the fee less `COMMISSION_PCT` (default 10). Cancel more than 2h ahead = full refund; later = 50% refund, 50% to the doctor; doctor cancels = full refund. Doctor payouts to a bank account are not built, so withdraw manually from the ledger for now.
- **Accreditation:** doctors register with an HPCSA number and stay unlisted until an admin (emails in `AdminEmails`) approves them at `#/admin`. Check each number on the HPCSA iRegister. Changing the number resets verification.
- **Reviews:** only for completed appointments, one per visit (overall, wait time, clinic, communication).
- **Notifications:** stored in-app and polled every 30s. Add SES or WhatsApp later if you want push.

Handles health-related personal information, so look at POPIA obligations (privacy policy, consent, retention) before launch.
