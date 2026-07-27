# OAuth Setup Guide for Gmail & Outlook Integration

## Gmail OAuth Setup

### Step 1: Create Google Cloud Project
1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Click "Select a project" → "New Project"
3. Name: `lilly-ai-email` → Create

### Step 2: Enable Gmail API
1. Go to "APIs & Services" → "Library"
2. Search for "Gmail API"
3. Click "Enable"

### Step 3: Create OAuth 2.0 Credentials
1. Go to "APIs & Services" → "Credentials"
2. Click "Create Credentials" → "OAuth client ID"
3. Application type: `Web application`
4. Name: `Lilly AI Gmail`
5. Authorized redirect URIs:
   - `http://localhost:3000/oauth/callback`
   - Add your production URL if deploying remotely
6. Click "Create"
7. **Save the Client ID and Client Secret**

### Step 4: Configure OAuth Consent Screen
1. Go to "APIs & Services" → "OAuth consent screen"
2. User type: `External` (or `Internal` for Workspace)
3. App name: `Lilly AI`
4. User support email: your email
5. Developer contact: your email
6. Save and continue through scopes and test users

### Step 5: Required Gmail Scopes
Add these scopes to your OAuth consent screen:
```
https://www.googleapis.com/auth/gmail.readonly
https://www.googleapis.com/auth/gmail.send
https://www.googleapis.com/auth/gmail.compose
https://www.googleapis.com/auth/gmail.modify
https://www.googleapis.com/auth/gmail.labels
https://www.googleapis.com/auth/gmail.settings.basic
https://www.googleapis.com/auth/gmail.settings.sharing
```

---

## Outlook OAuth Setup

### Step 1: Register Application in Azure
1. Go to [Azure Portal](https://portal.azure.com)
2. Navigate to "Azure Active Directory" → "App registrations"
3. Click "New registration"
4. Name: `Lilly AI Outlook`
5. Supported account types: Choose based on your needs
6. Redirect URI: `http://localhost:3000/oauth/callback`
7. Click "Register"
8. **Save the Application (client) ID**

### Step 2: Create Client Secret
1. Go to "Certificates & secrets"
2. Click "New client secret"
3. Description: `Lilly AI Secret`
4. Expires: Choose preference
5. Click "Add"
6. **Copy the secret value immediately (it won't be shown again)**

### Step 3: Configure API Permissions
1. Go to "API permissions" → "Add a permission"
2. Select "Microsoft Graph"
3. Choose "Delegated permissions"
4. Add these permissions:
   ```
   Mail.Read
   Mail.ReadWrite
   Mail.Send
   Calendars.Read
   Calendars.ReadWrite
   User.Read
   ```
5. Click "Grant admin consent" (if you're an admin)

### Step 4: Configure Authentication
1. Go to "Authentication"
2. Under "Advanced settings":
   - Allow public client flows: `No`
   - Supported account types: Based on your needs
3. Under "Web" → "Redirect URIs":
   - Ensure `http://localhost:3000/oauth/callback` is listed

---

## Environment Variables

Add to your `.env` file:

```bash
# Gmail OAuth
GMAIL_CLIENT_ID=123456789-abcdefg.apps.googleusercontent.com
GMAIL_CLIENT_SECRET=GOCSPX-your_client_secret_here

# Outlook OAuth
OUTLOOK_CLIENT_ID=12345678-abcd-efgh-ijkl-1234567890ab
OUTLOOK_CLIENT_SECRET=your_outlook_client_secret

# Open Connector Runtime Token (create in Open Connector UI)
OPENCONNECTOR_RUNTIME_TOKEN=oct_your_runtime_token_here
```

---

## Open Connector Setup

### Step 1: Start Open Connector
```bash
docker compose -f docker-compose.email.yml up -d connector
```

### Step 2: Access Open Connector UI
1. Open browser to `http://localhost:3000`
2. Go to Gmail provider page
3. Click "Configure OAuth Client"
4. Enter your Gmail Client ID and Client Secret
5. Click "Save OAuth Client"

### Step 3: Connect Gmail Account
1. Click "Connect Gmail"
2. Complete consent in browser
3. After redirect, Gmail is connected

### Step 4: Create Runtime Token
1. Go to "Access" page in Open Connector UI
2. Click "Create Token"
3. Name: `lilly-ai`
4. Copy the token (starts with `oct_`)
5. Add to `.env` as `OPENCONNECTOR_RUNTIME_TOKEN`

### Step 5: Repeat for Outlook
1. Go to Outlook provider page in Open Connector
2. Configure OAuth Client with Azure credentials
3. Connect Outlook account
4. The runtime token works for both providers

---

## Testing

### Test Gmail Connection
```bash
curl -s -X POST http://localhost:3000/v1/actions/gmail.get_profile \
  -H "Authorization: Bearer $OPENCONNECTOR_RUNTIME_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"input":{}}'
```

### Test Outlook Connection
```bash
curl -s -X POST http://localhost:3000/v1/actions/outlook.get_profile \
  -H "Authorization: Bearer $OPENCONNECTOR_RUNTIME_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"input":{}}'
```

---

## Security Notes

1. **Never commit secrets to git** - Use `.env` file and add to `.gitignore`
2. **Use HTTPS in production** - OAuth requires secure connections
3. **Rotate tokens periodically** - Open Connector UI allows token management
4. **Limit scopes** - Only request permissions you actually need
5. **Monitor API usage** - Google and Microsoft have quota limits
