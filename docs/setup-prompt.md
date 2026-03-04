# AI-Assisted Setup

No coding experience? No problem. Let AI set up your Mozaiks app for you.

---

## What You'll Need

Before starting, make sure you have:

1. **An AI coding agent** - Any IDE-integrated AI assistant that can run terminal commands:
    - [Claude Code](https://claude.ai/download) (recommended)
    - [Cursor](https://cursor.com)
    - [GitHub Copilot](https://github.com/features/copilot) in VS Code
    - Or any similar tool

2. **An OpenAI API key** - For the AI features ([Get one here](https://platform.openai.com/api-keys))

That's it! Your AI assistant will help you install everything else.

---

## The Setup Prompt

Copy this entire prompt and paste it into your AI coding agent:

```text
I want to set up Mozaiks, an open-source AI application stack. I have no coding experience, so please guide me step by step.

First, fetch the setup instructions from:
https://raw.githubusercontent.com/BlocUnited-LLC/mozaiks/main/SETUP_INSTRUCTIONS.md

Follow those instructions to help me:
1. Check what operating system I'm using
2. Check if I have the required tools installed (Docker, Python, Node.js, Git)
3. Help me install anything that's missing
4. Clone the Mozaiks repository
5. Walk me through the full setup

Be patient with me - explain things simply and wait for my confirmation at each step.
```

---

## What Happens Next

After you paste the prompt, your AI assistant will:

1. **Check your system** - Detect Windows, Mac, or Linux
2. **Verify prerequisites** - Check for Docker, Python, Node.js, Git
3. **Help install missing tools** - With step-by-step instructions
4. **Clone the repository** - Download Mozaiks to your computer
5. **Configure your app** - Set up your OpenAI API key
6. **Start all services** - Database, auth system, backend, frontend
7. **Verify everything works** - Test that your app is running
8. **Explain next steps** - How to customize and use your new app

The whole process takes about 10-15 minutes, depending on your internet speed and what tools you already have installed.

---

## Troubleshooting

### "My AI assistant doesn't know what Mozaiks is"

Make sure you copied the entire prompt above, including the link to the GitHub repository. The AI will read the setup instructions from there.

### "I don't have an AI coding agent"

We recommend [Claude Code](https://claude.ai/download) — it's free to get started and available for Windows, Mac, and Linux. Other options include [Cursor](https://cursor.com) or [GitHub Copilot](https://github.com/features/copilot).

### "I got stuck somewhere"

Tell your AI assistant what error you're seeing. It can help troubleshoot most issues. If you're still stuck, [open an issue on GitHub](https://github.com/BlocUnited-LLC/mozaiks/issues).

### "Can I do this manually instead?"

Absolutely! Check out the [Getting Started](getting-started.md) guide for traditional setup instructions.

---

## What You're Building

When setup is complete, you'll have a fully working AI application with:

- **Chat Interface** - Users can talk to AI agents
- **User Authentication** - Login system with user accounts
- **Database** - Stores conversations and data
- **Customizable UI** - Change colors, logo, branding
- **Extensible Workflows** - Add your own AI capabilities

All running on your own computer, ready to customize and eventually deploy.

---

## Next Steps After Setup

Once your app is running:

1. **[Customize your branding](guides/custom-brand-integration/01-overview.md)** - Colors, logo, fonts
2. **[Add AI workflows](guides/adding-workflows/01-overview.md)** - Create new AI capabilities
3. **[Read the architecture docs](architecture/keycloak-auth.md)** - Understand how it all works

---

<div class="grid cards" markdown>

-   :material-rocket-launch: **Ready to start?**

    ---

    Copy the prompt above and paste it into your AI coding agent. Your AI app awaits!

</div>
