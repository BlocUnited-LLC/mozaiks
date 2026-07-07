# Secrets Rotation Runbook

Zero-downtime procedure for rotating all runtime secrets. Follow in order.

---

## 1. OpenAI API Key

**Risk:** If old key is deleted before new key is deployed, all LLM calls fail.

**Procedure:**
1. Create a new key in the OpenAI dashboard (do NOT delete the old one yet).
2. Set the new key as an environment variable or in Azure Key Vault:
   ```bash
   # Azure Key Vault
   az keyvault secret set --vault-name <vault> --name OPENAI-API-KEY --value <new-key>
   # OR update .env / deployment secret
   OPENAI_API_KEY=<new-key>
   ```
3. Perform a rolling restart of all runtime instances (one at a time).
4. Verify LLM calls succeed by checking `/api/health/readiness` and workflow logs.
5. Delete the old key from the OpenAI dashboard.
6. Verify again.

**Rollback:** If step 4 fails, revert the secret to the old key value and restart.

---

## 2. MongoDB Password

**Risk:** If credentials change before all instances update, DB connections fail.

**Procedure:**
1. On the MongoDB cluster, create a new user or update the existing user's password:
   ```js
   db.updateUser("mozaiks_user", { pwd: "<new-password>" })
   ```
2. Construct the new connection string:
   ```
   mongodb+srv://mozaiks_user:<new-password>@<cluster>/<db>
   ```
3. Update the secret in your secret store (Azure Key Vault, AWS Secrets Manager, etc.):
   ```bash
   az keyvault secret set --vault-name <vault> --name MONGO-URI --value "<new-uri>"
   ```
4. Rolling-restart runtime instances one at a time, verifying `/api/health/readiness` after each.
5. Once all instances are running with the new credential, revoke the old password from MongoDB.

**Rollback:** Restore the old password in MongoDB and revert the secret value.

---

## 3. JWT Signing Key

**Risk:** Tokens signed with the old key become invalid immediately when the key rotates. Active user sessions will be logged out.

**Strategy:** Overlapping validity window — keep old key valid for 15 minutes after new key is active.

**Procedure:**
1. Generate a new RS256 key pair (or symmetric secret for HS256):
   ```bash
   # RS256
   openssl genrsa -out jwt_private_new.pem 2048
   openssl rsa -in jwt_private_new.pem -pubout -out jwt_public_new.pem
   # HS256
   openssl rand -base64 64
   ```
2. If using OIDC (Keycloak, Supabase): rotate the signing key in the provider's admin panel. The provider handles JWKS refresh automatically.
3. If using a custom JWT setup:
   a. Add the new public key to your JWKS endpoint alongside the old key.
   b. Update `JWT_SECRET` or `JWT_PRIVATE_KEY` to the new key.
   c. Restart instances rolling.
   d. After 15 minutes (old token TTL), remove the old public key from JWKS.
4. Verify `/api/health` returns 200 and test a login flow end-to-end.

**Rollback:** Restore the old key value and restart. Users may need to re-login.

---

## 4. Internal API Key (`INTERNAL_API_KEY`)

Used for service-to-service authentication between runtime instances and the app backend.

**Procedure:**
1. Generate a new key: `openssl rand -hex 32`
2. Update the secret in all services that use it simultaneously (both sender and receiver).
3. Restart all services in a coordinated rolling restart.

---

## 5. Azure Key Vault Access

If the managed identity or service principal used to access Key Vault needs rotation:

1. Create a new service principal or rotate the managed identity credentials.
2. Grant it the same Key Vault access policies as the old one.
3. Update `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID` in the deployment.
4. Rolling-restart instances.
5. Revoke the old principal's access.

---

## Verification Checklist

After any rotation:
- [ ] `GET /api/health/readiness` returns `{"status": "ok"}`
- [ ] Workflow start succeeds (POST to `/api/chats/{app_id}/start`)
- [ ] Module action dispatches succeed
- [ ] LLM call completes (check workflow logs)
- [ ] No `AUTH_FAILED` or `backend_unavailable` errors in logs
- [ ] Audit log shows successful operations after rotation

---

## Emergency Contact

If a rotation causes an outage, restore the previous secret value immediately and file an incident report. Do not attempt to debug with the new key while the system is down.
