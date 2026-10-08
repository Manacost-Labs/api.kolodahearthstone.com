import { spawn } from 'node:child_process';
import { server } from './fixture-server.mjs';
import net from 'node:net';
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const reserve = net.createServer();
await new Promise(resolve => reserve.listen(0, '127.0.0.1', resolve));
const port = reserve.address().port;
await new Promise(resolve => reserve.close(resolve));
const origin = 'http://127.0.0.1:' + port;
const app = spawn(process.execPath, ['.next/standalone/server.js'], {
  env: {
    ...process.env,
    NODE_ENV: 'production',
    HOSTNAME: '127.0.0.1',
    PORT: String(port),
    PANEL_BACKEND_ORIGIN: 'http://127.0.0.1:' + server.address().port,
    PANEL_PUBLIC_ORIGIN: origin,
  },
  stdio: ['ignore', 'ignore', 'pipe'],
});
let diagnostics = '';
app.stderr.on('data', chunk => {
  diagnostics = (diagnostics + chunk).slice(-4000);
});
try {
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      const response = await fetch(origin + '/', { redirect: 'manual' });
      if (response.status === 307) {
        ready = true;
        break;
      }
    } catch {}
    if (app.exitCode !== null) break;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  if (!ready) throw Error('Standalone server did not start: ' + diagnostics);
  const test = spawn(process.execPath, ['--test', 'tests/http.test.mjs'], {
    env: { ...process.env, PANEL_TEST_ORIGIN: origin },
    stdio: 'inherit',
  });
  const status = await new Promise(resolve => test.once('exit', resolve));
  if (status !== 0) process.exitCode = 1;
} finally {
  const closed = new Promise(resolve => app.once('exit', resolve));
  app.kill('SIGTERM');
  await Promise.race([closed, new Promise(resolve => setTimeout(resolve, 2000))]);
  if (app.exitCode === null) app.kill('SIGKILL');
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}
