---
name: mcp-integration-audit
description: Audit or plan Claude MCP usage for this project. Use when deciding whether a tool should be local, project-scoped, or user-scoped, when reviewing .mcp.json plans, or when adding shared integrations like GitHub, Sentry, databases, or docs sources.
argument-hint: "[integration or server name]"
disable-model-invocation: true
---

Audit this MCP integration topic: $ARGUMENTS

Return:
1. whether the integration should be `local`, `project`, or `user` scope
2. whether secrets can safely stay out of source control
3. whether a shared `.mcp.json` is appropriate for the repo
4. Windows-specific concerns for stdio servers, especially `cmd /c npx` usage
5. the smallest safe config pattern for the team

Prefer project-scoped MCP only for genuinely shared, repo-specific tools.
Do not recommend checking credentials into the repo.