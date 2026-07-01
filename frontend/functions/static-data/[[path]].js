const STATIC_DATA_BASE_URL = 'https://pub-63141bbf046a4c3b97ef34b7176421eb.r2.dev/static-data';

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
  'Access-Control-Allow-Headers': 'Accept, Content-Type',
};

function resolvePath(pathParam) {
  if (Array.isArray(pathParam)) {
    return pathParam.join('/');
  }
  return String(pathParam || 'manifest.json').replace(/^\/+/, '') || 'manifest.json';
}

async function proxyStaticData({ request, params }, method = 'GET') {
  const path = resolvePath(params.path);
  const upstreamUrl = `${STATIC_DATA_BASE_URL.replace(/\/+$/, '')}/${path}`;
  const upstream = await fetch(upstreamUrl, {
    method,
    headers: {
      Accept: request.headers.get('Accept') || 'application/json,*/*',
      'User-Agent': 'stock-screener-pages-static-data-proxy/1.0',
    },
  });

  const headers = new Headers(corsHeaders);
  const contentType = upstream.headers.get('Content-Type');
  const etag = upstream.headers.get('ETag');
  const lastModified = upstream.headers.get('Last-Modified');
  if (contentType) headers.set('Content-Type', contentType);
  if (etag) headers.set('ETag', etag);
  if (lastModified) headers.set('Last-Modified', lastModified);
  headers.set('Cache-Control', upstream.ok ? 'public, max-age=60, s-maxage=300' : 'no-store');

  return new Response(method === 'HEAD' ? null : upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers,
  });
}

export function onRequestOptions() {
  return new Response(null, { status: 204, headers: corsHeaders });
}

export function onRequestHead(context) {
  return proxyStaticData(context, 'HEAD');
}

export function onRequestGet(context) {
  return proxyStaticData(context, 'GET');
}
