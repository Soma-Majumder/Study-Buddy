// Forwards the student page's /api/* calls to the workbook server behind a tunnel.
// BACKEND_URL and PROXY_SECRET live in Vercel env vars; the browser never sees either.
const ALLOWED = new Set(['/api/config', '/api/lessons', '/api/start', '/api/start-progress', '/api/session', '/api/submit', '/api/continue', '/api/cancel']);

module.exports = async function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store');
  const base = (process.env.BACKEND_URL || '').replace(/\/+$/, '');
  const secret = process.env.PROXY_SECRET || '';
  // vercel.json rewrites /api/:path* to /api/proxy?route=:path*, so the route arrives as a query param.
  const query = new URLSearchParams(req.url.split('?')[1] || '');
  const route = query.get('route') || '';
  query.delete('route');
  const url = { pathname: '/api/' + route, search: query.toString() ? '?' + query.toString() : '' };
  if (!/^(https:\/\/|http:\/\/(127\.0\.0\.1|localhost)[:/])/.test(base) || secret.length < 32) {
    return res.status(503).json({ error: 'The study backend is not configured. Ask the site owner to start it.' });
  }
  if (!ALLOWED.has(url.pathname)) return res.status(404).json({ error: 'Not found.' });
  if (req.method !== (url.pathname === '/api/config' || /^\/api\/(lessons|start-progress|session)$/.test(url.pathname) ? 'GET' : 'POST')) {
    return res.status(405).json({ error: 'Method not allowed.' });
  }
  const headers = { 'X-Proxy-Secret': secret };
  if (req.headers['x-workbook-token']) headers['X-Workbook-Token'] = String(req.headers['x-workbook-token']);
  const init = { method: req.method, headers, signal: AbortSignal.timeout(290000) };
  if (req.method === 'POST') {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(req.body ?? {});
  }
  try {
    const upstream = await fetch(base + url.pathname + url.search, init);
    const body = await upstream.text();
    res.status(upstream.status).setHeader('Content-Type', upstream.headers.get('content-type') || 'application/json').send(body);
  } catch {
    res.status(502).json({ error: 'The study backend is offline. Ask the site owner to start it, then reload.' });
  }
};
