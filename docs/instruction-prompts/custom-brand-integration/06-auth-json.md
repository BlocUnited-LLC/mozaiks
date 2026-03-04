# Instruction Prompt: Configuring auth.json

**Task:** Configure authentication page styling

**Complexity:** Low (JSON configuration)

---

## Context for AI Agent

You are helping a user configure `brand/public/auth.json` which controls the styling and content of login, registration, and password reset pages.

---

## Step 1: Understand Auth Page Needs

Ask the user:

1. **"What should the login page look like?"**
   - Background image or color
   - Logo placement
   - Custom tagline

2. **"What authentication options do you offer?"**
   - Email/password
   - Social login (Google, GitHub)
   - SSO/SAML

3. **"Do you need custom text on auth pages?"**
   - Welcome message
   - Terms acceptance
   - Help links

---

## Step 2: Basic auth.json Template

```json
{
  "login": {
    "title": "Welcome Back",
    "subtitle": "Sign in to continue",
    "background": {
      "type": "image",
      "src": "auth_bg.png"
    },
    "logo": {
      "src": "logo.svg",
      "width": "120px"
    },
    "showRememberMe": true,
    "showForgotPassword": true,
    "socialProviders": []
  },

  "register": {
    "title": "Create Account",
    "subtitle": "Get started for free",
    "showTermsCheckbox": true,
    "termsLink": "/terms",
    "privacyLink": "/privacy"
  },

  "forgotPassword": {
    "title": "Reset Password",
    "subtitle": "We'll send you a reset link"
  },

  "shared": {
    "cardStyle": "glass",
    "cardWidth": "400px"
  }
}
```

---

## Step 3: Configure Login Page

### Basic Login

```json
"login": {
  "title": "Welcome Back",
  "subtitle": "Sign in to your account",
  "logo": {
    "src": "logo.svg",
    "width": "100px"
  },
  "showRememberMe": true,
  "showForgotPassword": true
}
```

### With Background Image

```json
"login": {
  "background": {
    "type": "image",
    "src": "auth_bg.png",
    "overlay": "rgba(0,0,0,0.5)"
  }
}
```

### With Gradient Background

```json
"login": {
  "background": {
    "type": "gradient",
    "from": "#1e3a8a",
    "to": "#7c3aed",
    "direction": "to-br"
  }
}
```

### With Social Login

```json
"login": {
  "socialProviders": [
    {
      "id": "google",
      "label": "Continue with Google",
      "icon": "google.svg"
    },
    {
      "id": "github",
      "label": "Continue with GitHub",
      "icon": "github.svg"
    }
  ]
}
```

---

## Step 4: Configure Registration

```json
"register": {
  "title": "Create Account",
  "subtitle": "Start your free trial",
  "fields": {
    "showName": true,
    "showCompany": false,
    "showPhone": false
  },
  "showTermsCheckbox": true,
  "termsText": "I agree to the [Terms of Service](/terms) and [Privacy Policy](/privacy)",
  "buttonText": "Create Account"
}
```

### With Required Fields

```json
"register": {
  "fields": {
    "showName": true,
    "nameRequired": true,
    "showCompany": true,
    "companyRequired": false
  }
}
```

---

## Step 5: Configure Password Reset

```json
"forgotPassword": {
  "title": "Forgot Password?",
  "subtitle": "Enter your email and we'll send you a reset link",
  "buttonText": "Send Reset Link",
  "successMessage": "Check your email for a reset link"
}
```

---

## Step 6: Shared Styling Options

### Card Styles

```json
"shared": {
  "cardStyle": "glass",      // glass, solid, minimal
  "cardWidth": "400px",
  "cardPadding": "32px",
  "borderRadius": "16px"
}
```

### Card Style Options

| Style | Description |
|-------|-------------|
| `glass` | Semi-transparent with blur |
| `solid` | Opaque with shadow |
| `minimal` | No background, just form |

---

## Step 7: Custom Help Links

```json
"shared": {
  "helpLinks": [
    {
      "label": "Need help?",
      "href": "/support"
    },
    {
      "label": "Contact support",
      "href": "mailto:support@yourapp.com"
    }
  ]
}
```

---

## Step 8: Complete Example

```json
{
  "login": {
    "title": "Welcome to AppName",
    "subtitle": "Sign in to continue",
    "background": {
      "type": "gradient",
      "from": "#0f172a",
      "to": "#1e3a8a",
      "direction": "to-br"
    },
    "logo": {
      "src": "logo.svg",
      "width": "100px"
    },
    "showRememberMe": true,
    "showForgotPassword": true,
    "socialProviders": [
      {
        "id": "google",
        "label": "Continue with Google",
        "icon": "google.svg"
      }
    ]
  },

  "register": {
    "title": "Create Account",
    "subtitle": "Get started in minutes",
    "fields": {
      "showName": true,
      "nameRequired": true
    },
    "showTermsCheckbox": true,
    "termsLink": "/terms",
    "privacyLink": "/privacy",
    "buttonText": "Create Free Account"
  },

  "forgotPassword": {
    "title": "Reset Password",
    "subtitle": "We'll send you a reset link",
    "buttonText": "Send Reset Link"
  },

  "shared": {
    "cardStyle": "glass",
    "cardWidth": "420px",
    "helpLinks": [
      { "label": "Need help?", "href": "/support" }
    ]
  }
}
```

---

## Step 9: Verify Configuration

```powershell
# Check JSON is valid
node -e "console.log(JSON.parse(require('fs').readFileSync('brand/public/auth.json')))"

# Start dev server
npm run dev

# Visit login page and verify styling
```

---

## Summary Template

```markdown
## auth.json Configured

### Login Page
- Title: [text]
- Background: [image/gradient/color]
- Social providers: [list]
- Remember me: [yes/no]
- Forgot password: [yes/no]

### Registration
- Title: [text]
- Required fields: [list]
- Terms checkbox: [yes/no]

### Password Reset
- Title: [text]
- Custom message: [yes/no]

### Styling
- Card style: [glass/solid/minimal]
- Card width: [value]

### Files Modified
- ✅ brand/public/auth.json
- ✅ brand/public/assets/ (background image, if any)
```

---

## Troubleshooting

### "Background image not loading"

1. Check file exists in assets folder
2. Verify path is correct in auth.json
3. Check image is not too large (< 1MB recommended)

### "Social login buttons not showing"

1. Verify Keycloak has social providers configured
2. Check socialProviders array syntax
3. Ensure icon files exist in assets

### "Styling not applying"

1. Clear browser cache
2. Check JSON syntax is valid
3. Verify shared.cardStyle is valid option
