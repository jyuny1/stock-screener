export const STATIC_SITE_MODE = String(import.meta.env.VITE_STATIC_SITE || '').toLowerCase() === 'true';

// VITE_STATIC_DATA_BASE_URL: override to serve static-data from an external
// origin such as Cloudflare R2.  Falls back to a relative path so local dev
// and same-origin deployments keep working without any extra configuration.
const _staticDataBase = (
  import.meta.env.VITE_STATIC_DATA_BASE_URL ||
  `${import.meta.env.BASE_URL}static-data`
).replace(/\/+$/, '');

export const getStaticDataUrl = (relativePath = 'manifest.json') => {
  const normalizedPath = String(relativePath).replace(/^\/+/, '');
  return `${_staticDataBase}/${normalizedPath}`;
};
