// Root dist socket placeholder copied from test/src/socket.js
// This allows the WebSocket server to start if it expects dist/socket.js at repo root.

const WebSocket = require('ws');

function createSocketServer(server) {
  const wss = new WebSocket.Server({ server });
  wss.on('connection', (ws) => {
    console.log('WebSocket client connected');
    ws.on('message', (msg) => {
      console.log('Received:', msg.toString());
      ws.send(`echo: ${msg}`);
    });
  });
  return wss;
}

module.exports = { createSocketServer };
