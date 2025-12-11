const express = require('express');
const fs = require('fs');
const path = require('path');
const YAML = require('yaml');
const cors = require('cors');

const app = express();
app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// Try to load lovable-mcp.yaml from repo root
const rootConfigPath = path.resolve(__dirname, '..', '..', 'lovable-mcp.yaml');
let lovableConfig = null;
try {
  const raw = fs.readFileSync(rootConfigPath, 'utf8');
  lovableConfig = YAML.parse(raw);
} catch (e) {
  // ignore - keep null
}

// Demo fallback server list
const demoServers = [
  { id: '1', name: 'MCP - Demo Server A', url: 'https://mcp-demo-a.example' },
  { id: '2', name: 'MCP - Demo Server B', url: 'https://mcp-demo-b.example' }
];

app.get('/api/servers', async (req, res) => {
  // If lovable-mcp.yaml has an endpoint, show it; otherwise return demo
  if (lovableConfig && lovableConfig.api && lovableConfig.api.endpoint) {
    return res.json({ source: 'config', endpoint: lovableConfig.api.endpoint, servers: demoServers });
  }
  res.json({ source: 'demo', servers: demoServers });
});

app.listen(3001, () => console.log('v0-mcp demo listening on http://localhost:3001'));
