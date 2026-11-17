# WhatsApp Cloud API Setup (Meta)

This guide connects the backend to Meta's **official WhatsApp Cloud API**, first with Meta's **free test number** and later with a **real business number**.

> 💰 **Cost:** setting up a Meta developer app and using the test number is free. Do **not** add a payment method or upgrade anything without approval. Messaging charges only apply to real production traffic. Meta's pricing has changed recently: from Oct 1, 2026, utility messages inside the 24h window are billed. Check Meta's pricing page before going live.

Official docs: https://developers.facebook.com/docs/whatsapp/cloud-api/

---

## 0. How it fits together

```
Customer's WhatsApp ──► Meta Cloud API ──POST (signed)──► /api/v1/webhooks/whatsapp ──► PostgreSQL
                                        ◄──── POST /{phone-number-id}/messages ──── backend (replies)
```

Until you finish this guide, keep `WA_MOCK_MODE=true`. The whole app works with simulated messages, and no real message is ever sent.

---

## 1. Create the Meta app (development / test number)

1. Go to https://developers.facebook.com/ and log in. Register as a developer if asked; it's free.
2. **My Apps → Create App**. Choose the use case for **WhatsApp / Business messaging** and connect (or create) a Business portfolio.
3. In the app, open **WhatsApp → API Setup**. Meta shows you:
   - a **test phone number** (the "From" number),
   - its **Phone number ID**: copy it into `WA_PHONE_NUMBER_ID`,
   - the **WhatsApp Business Account ID**: copy it into `WA_BUSINESS_ACCOUNT_ID`,
   - a **temporary access token**: copy it into `WA_ACCESS_TOKEN`. It **expires after about 24 hours**.
4. Under **"To"**, add **your own WhatsApp number** as a recipient and confirm the code Meta sends you. The test number can only message recipients you add here. Meta limits how many you can add; the exact number wasn't confirmed in the docs we checked.
5. **App settings → Basic → App secret → Show**: copy it into `WA_APP_SECRET`. This is what proves webhooks really come from Meta.

Your `.env` (never commit this file):
```env
WA_MOCK_MODE=false
WA_ENV=test
WA_GRAPH_API_VERSION=v27.0
WA_PHONE_NUMBER_ID=123456789012345
WA_BUSINESS_ACCOUNT_ID=123456789012345
WA_ACCESS_TOKEN=EAAG...            # temporary token for now
WA_APP_SECRET=abc123...
WA_VERIFY_TOKEN=<already generated for you, keep it>
```
With `WA_MOCK_MODE=false`, the backend **refuses to start** if any of these are missing. That's intentional.

---

## 2. Give your laptop a public HTTPS address (tunnel)

Meta must reach your webhook over **HTTPS** from the internet. Your laptop is not public, so use a free tunnel:

```powershell
winget install --id Cloudflare.cloudflared      # one-time install (free, no account needed)
.\scripts\start-backend.ps1                     # terminal 1
cloudflared tunnel --url http://127.0.0.1:8000  # terminal 2 -> prints https://<random>.trycloudflare.com
```
The address changes every time you restart cloudflared. Update it in Meta when it does.

---

## 3. Register the webhook in Meta

1. **WhatsApp → Configuration → Webhook → Edit**.
2. **Callback URL:** `https://<random>.trycloudflare.com/webhook/whatsapp`. This is **n8n** Workflow 1, which forwards Meta's exact bytes to the backend, so the signature check still works. See [N8N_SETUP.md](N8N_SETUP.md); the tunnel then points at port 5690. If you run without n8n, use the backend URL `…/api/v1/webhooks/whatsapp` on port 8000 instead.
3. **Verify token:** the value of `WA_VERIFY_TOKEN` from your `.env`.
4. Click **Verify and save**. Meta sends a `GET` with `hub.mode=subscribe`, `hub.verify_token` and `hub.challenge`, and our backend replies with the challenge.
5. Under **Webhook fields**, **Subscribe** to `messages`. This field covers both incoming messages and delivery statuses.

---

## 4. Test it

1. From your phone, send a WhatsApp message to the **test number**.
2. You should see it in the database or dashboard: a new customer, conversation and message.
3. To send a reply from the dashboard: take over the conversation, then reply (`POST /api/v1/conversations/{id}/reply`).
4. Delivery statuses (`sent` → `delivered` → `read`) arrive by webhook and update the message.

If nothing arrives:
| Symptom | Check |
|---|---|
| "Verify and save" fails | Is the backend running? Does the tunnel URL end in `/api/v1/webhooks/whatsapp`? Does the verify token match? |
| Webhook returns 401 | `WA_APP_SECRET` is wrong (copy it again from App settings → Basic). |
| Sending fails with code **190** | Access token expired. Generate a new one (temporary tokens last ~24h). |
| Sending fails with code **131047** | More than 24h since the customer's last message: only templates are allowed. |
| Sending fails with code **130429 / 80007 / 131056** | Rate limited. The backend retries automatically with backoff. |

---

## 5. Production: real business number + permanent token

| | Development | Production |
|---|---|---|
| Phone number | Meta test number | Your verified business number |
| Token | Temporary (~24h) | **System User** token (no daily expiry) |
| `WA_ENV` | `test` | `production` |
| `APP_ENV` | `development` | `production` (hides `/docs`, refuses mock mode) |
| Webhook URL | cloudflared tunnel | Your server's domain with a real certificate |

**Permanent token (System User):**
1. Go to Meta **Business Settings → Users → System users → Add**, with the Admin role.
2. **Assign assets**: your app and your WhatsApp account.
3. **Generate token**: choose your app and the permissions `whatsapp_business_messaging` and `whatsapp_business_management`.
4. Store it only in the server's environment variables or a secret manager, never in code or Git.
5. **Token management:** if a token ever leaks, revoke it in Business Settings straight away and generate a new one. Only the `WA_ACCESS_TOKEN` variable needs to change; no code changes.

**Real number:** add and verify it under WhatsApp → API Setup → Add phone number. New business portfolios can register up to 2 numbers at first. Then change `WA_PHONE_NUMBER_ID`.

**Templates:** messages sent **more than 24h** after the customer's last message must use a **Meta-approved template**. Create templates in WhatsApp Manager and wait for approval. The follow-up automation in Stage 7 uses them.

---

## 6. Security checklist
- [ ] `.env` is not in Git (`git check-ignore .env` prints `.env`)
- [ ] `WA_APP_SECRET` is set, so every webhook POST is signature-checked
- [ ] `WA_VERIFY_TOKEN` is long and random
- [ ] Production uses a System User token, not a personal or temporary one
- [ ] `APP_ENV=production` and `WA_MOCK_MODE=false` on the server
- [ ] HTTPS only (Meta requires it)

## Facts confirmed in Meta's docs (checked 2026-11-16)
- Latest Graph API version: **v27.0** (Nov 4, 2026).
- Verification uses `hub.mode`, `hub.verify_token` and `hub.challenge`: reply with the challenge and HTTP 200.
- Signature: `X-Hub-Signature-256: sha256=<HMAC-SHA256 of the payload using the App secret>`.
- Meta **retries** failed webhooks for up to **7 days**, so **duplicates can happen**. Batches of up to 1000 updates and out-of-order delivery are also possible. This is why `wa_message_id` is UNIQUE.
- 24h customer service window: free-form messages only within 24h of the user's last message, otherwise templates only.
