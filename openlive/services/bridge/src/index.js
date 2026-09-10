#!/usr/bin/env node
/**
 * Lilly Bridge — placeholder service
 * 
 * This is a minimal placeholder. Replace with actual OpenLive bridge
 * implementation when the openlive repo is available.
 */

const http = require('http');

const PORT = process.env.BRIDGE_PORT || 3003;

const server = http.createServer((req, res) => {
  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ status: 'ok', service: 'lilly-bridge', placeholder: true }));
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`Lilly Bridge placeholder listening on :${PORT}`);
});
