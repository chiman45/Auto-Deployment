v0 MCP demo

This demo shows how to surface MCP server information in a tiny Node/Express app.

How to run:

1. Open a terminal and change to `prototype/v0-mcp`:

```powershell
cd d:/Programing/Projects/Custom-CICD/prototype/v0-mcp
npm install
npm start
```

2. Open http://localhost:3001 in your browser.

Notes:
- The demo will read `lovable-mcp.yaml` from the repository root (two levels up). If it contains an `api.endpoint` value it will be shown; otherwise the app displays demo data.
- If you want the app to call the real v0 MCP, you'll need to install and configure the `v0-sdk` usage in `server.js` and provide credentials.
