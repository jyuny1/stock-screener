import { memo, useMemo } from 'react';
import { Box, Tooltip, Typography } from '@mui/material';
import { formatLargeNumber } from '../../utils/formatUtils';

const BAR_GLYPHS = ['▁', '▂', '▃', '▄', '▅', '▆', '▇', '█'];

const BUCKETS = [
  { key: 'dte0_30', label: '0-30' },
  { key: 'dte31_60', label: '31-60' },
  { key: 'dte61_90', label: '61-90' },
];

const TOTAL_KEY = 'dte0_90_total';

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function cleanHistory(history) {
  if (!Array.isArray(history)) return [];
  return history.map(finiteNumber).filter((value) => value != null);
}

function trendSymbol(current, previous) {
  if (current == null || previous == null || previous === 0) return '—';
  const pct = (current - previous) / previous;
  if (pct >= 0.2) return '↑↑';
  if (pct >= 0.05) return '↑';
  if (pct <= -0.2) return '↓↓';
  if (pct <= -0.05) return '↓';
  return '→';
}

function formatPcr(value) {
  return value == null ? '-' : value.toFixed(2);
}

function formatChange(value) {
  if (value == null) return '—';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}`;
}

function barSparkline(values, { width = 18 } = {}) {
  const clean = cleanHistory(values).slice(-30);
  if (!clean.length) return '—';
  const sampled = clean.length > width
    ? Array.from({ length: width }, (_, index) => clean[Math.round(index * (clean.length - 1) / (width - 1))])
    : clean;
  const min = Math.min(...sampled);
  const max = Math.max(...sampled);
  const range = max - min;
  if (!range) return sampled.map(() => '▄').join('');
  return sampled.map((value) => {
    const index = Math.max(0, Math.min(BAR_GLYPHS.length - 1, Math.round(((value - min) / range) * (BAR_GLYPHS.length - 1))));
    return BAR_GLYPHS[index];
  }).join('');
}

function normalizeBucket(row, key) {
  const trend = row.option_pcr_trend_30d && typeof row.option_pcr_trend_30d === 'object'
    ? row.option_pcr_trend_30d[key]
    : null;
  const history = cleanHistory(
    trend?.history
      ?? row[`option_pcr_volume_${key}_history`]
      ?? row[`option_pcr_${key}_history`],
  );
  const current = finiteNumber(
    trend?.current
      ?? row[`option_pcr_volume_${key}`]
      ?? row[`option_pcr_${key}`]
      ?? history[history.length - 1],
  );
  const previous30d = finiteNumber(
    trend?.previous30d
      ?? trend?.previous
      ?? history[0],
  );
  const change = finiteNumber(
    trend?.change30d
      ?? trend?.change
      ?? (current != null && previous30d != null ? current - previous30d : null),
  );
  const putVol = finiteNumber(trend?.putVol ?? trend?.put_volume ?? row[`option_put_volume_${key}`]);
  const callVol = finiteNumber(trend?.callVol ?? trend?.call_volume ?? row[`option_call_volume_${key}`]);
  return {
    current,
    previous30d,
    change,
    symbol: trendSymbol(current, previous30d),
    history,
    putVol,
    callVol,
  };
}

function normalizeTotal(row) {
  const total = normalizeBucket(row, TOTAL_KEY);
  if (total.current != null || total.history.length) return total;

  // Legacy fallback while the backend migrates from DTE≤45 to bucketed DTE≤90.
  const history = cleanHistory(row.option_pcr_volume_dte45_history);
  const current = finiteNumber(row.option_pcr_volume_dte45 ?? history[history.length - 1]);
  const previous30d = finiteNumber(history[0]);
  return {
    current,
    previous30d,
    change: current != null && previous30d != null ? current - previous30d : null,
    symbol: trendSymbol(current, previous30d),
    history,
    putVol: finiteNumber(row.option_put_volume_dte45),
    callVol: finiteNumber(row.option_call_volume_dte45),
    legacy: current != null || history.length > 0,
  };
}

function BucketSummary({ label, bucket }) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 0.35, minWidth: 0 }}>
      <Typography variant="caption" sx={{ fontFamily: 'monospace', fontSize: 10, lineHeight: 1, color: 'text.secondary' }}>
        {label}
      </Typography>
      <Typography variant="caption" sx={{ fontFamily: 'monospace', fontSize: 11, lineHeight: 1, fontWeight: 700 }}>
        {formatPcr(bucket.current)}
      </Typography>
    </Box>
  );
}

function BucketRow({ label, bucket }) {
  return (
    <Box sx={{ display: 'grid', gridTemplateColumns: '74px 48px 48px 54px 1fr', alignItems: 'center', columnGap: 1 }}>
      <Typography variant="caption" sx={{ fontFamily: 'monospace', fontSize: 11, lineHeight: 1.15 }}>{label}</Typography>
      <Typography variant="caption" sx={{ fontFamily: 'monospace', fontSize: 11, lineHeight: 1.15, textAlign: 'right', fontWeight: 700 }}>{formatPcr(bucket.current)}</Typography>
      <Typography variant="caption" sx={{ fontFamily: 'monospace', fontSize: 11, lineHeight: 1.15, textAlign: 'right' }}>{formatChange(bucket.change)}</Typography>
      <Typography variant="caption" sx={{ fontFamily: 'monospace', fontSize: 11, lineHeight: 1.15, textAlign: 'center' }}>{bucket.symbol}</Typography>
      <Typography variant="caption" sx={{ fontFamily: 'monospace', fontSize: 10, lineHeight: 1.15, letterSpacing: -0.4 }} title="PCR 30D bar sparkline">
        {barSparkline(bucket.history, { width: 18 })}
      </Typography>
    </Box>
  );
}

function TooltipContent({ total, buckets, dates }) {
  const startDate = dates?.[0] || '-';
  const endDate = dates?.[dates.length - 1] || '-';
  return (
    <Box sx={{ maxWidth: 520 }}>
      <Typography variant="subtitle2" component="div">PCR 30D Trend by DTE Bucket</Typography>
      <Typography variant="caption" component="div" color="text.secondary" sx={{ mb: 1 }}>
        x 軸為近 30 個 snapshot，y 軸為每日 PCR；期間 {startDate} → {endDate}
      </Typography>
      <Box sx={{ display: 'grid', gridTemplateColumns: '74px 48px 48px 54px 1fr', columnGap: 1, mb: 0.5 }}>
        {['Bucket', 'PCR', '30D Δ', 'Dir', 'Trend'].map((label) => (
          <Typography key={label} variant="caption" color="text.secondary" sx={{ fontFamily: 'monospace', fontSize: 10, textAlign: label === 'Bucket' ? 'left' : label === 'Trend' ? 'left' : 'right' }}>
            {label}
          </Typography>
        ))}
      </Box>
      {[{ key: TOTAL_KEY, label: total.legacy ? 'Legacy≤45' : 'DTE≤90', bucket: total }, ...buckets].map(({ key, label, bucket }) => (
        <Box key={key} sx={{ mb: 0.75 }}>
          <BucketRow label={label} bucket={bucket} />
          <Typography variant="caption" component="div" color="text.secondary" sx={{ fontFamily: 'monospace', fontSize: 10, ml: 0.1 }}>
            current {formatPcr(bucket.current)}｜30D Δ {formatChange(bucket.change)}｜Put/Call Vol {formatLargeNumber(bucket.putVol)} / {formatLargeNumber(bucket.callVol)}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}

function PcrTrendCell({ row }) {
  const { total, buckets, dates } = useMemo(() => {
    const totalBucket = normalizeTotal(row);
    return {
      total: totalBucket,
      buckets: BUCKETS.map((bucket) => ({ ...bucket, bucket: normalizeBucket(row, bucket.key) })),
      dates: row.option_pcr_trend_30d?.dates ?? row.option_put_liquidity_history_dates ?? [],
    };
  }, [row]);

  const hasData = total.current != null || total.history.length || buckets.some(({ bucket }) => bucket.current != null || bucket.history.length);
  if (!hasData) {
    return <Typography variant="caption" color="text.disabled">-</Typography>;
  }

  return (
    <Tooltip
      arrow
      placement="top"
      title={<TooltipContent total={total} buckets={buckets} dates={dates} />}
    >
      <Box sx={{ width: 132, cursor: 'help', mx: 'auto', py: 0.25 }}>
        <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 0.75, whiteSpace: 'nowrap' }}>
          <Typography variant="caption" sx={{ fontFamily: 'monospace', fontSize: 10, lineHeight: 1, color: 'text.secondary' }}>
            {total.legacy ? '≤45' : '90D'}
          </Typography>
          <Typography variant="caption" sx={{ fontFamily: 'monospace', fontSize: 15, fontWeight: 800, lineHeight: 1 }}>
            {formatPcr(total.current)}
          </Typography>
          <Typography variant="caption" sx={{ fontFamily: 'monospace', fontSize: 11, lineHeight: 1 }}>
            {total.symbol} {formatChange(total.change)}
          </Typography>
        </Box>
        <Box sx={{ mt: 0.45, display: 'flex', flexWrap: 'wrap', columnGap: 0.85, rowGap: 0.25 }}>
          {buckets.map(({ key, label, bucket }) => (
            <BucketSummary key={key} label={label} bucket={bucket} />
          ))}
        </Box>
      </Box>
    </Tooltip>
  );
}

export default memo(PcrTrendCell);
