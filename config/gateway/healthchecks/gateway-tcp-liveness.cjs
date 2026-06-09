'use strict';

const net = require('node:net');

const socket = net.connect({ host: '127.0.0.1', port: 18789 });
const timeout = setTimeout(() => {
  socket.destroy();
  process.exit(1);
}, 3000);

socket.once('connect', () => {
  clearTimeout(timeout);
  socket.end();
  process.exit(0);
});

socket.once('error', () => {
  clearTimeout(timeout);
  process.exit(1);
});
