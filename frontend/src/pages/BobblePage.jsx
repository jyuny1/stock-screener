import { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from 'echarts/core';
import { ScatterChart } from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  MarkLineComponent,
  TitleComponent,
  ToolboxComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  Container,
  FormControl,
  Grid,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from '@mui/material';

import { fetchOptionChain } from '../api/options';
import { fetchSupportResistance, fetchSoxlSupportSnapshot } from '../api/supportResistance';

echarts.use([
  CanvasRenderer,
  ScatterChart,
  GridComponent,
  TooltipComponent,
  VisualMapComponent,
  DataZoomComponent,
  ToolboxComponent,
  MarkLineComponent,
  TitleComponent,
]);

const DEFAULT_FILTERS = {
  minDte: 0,
  maxDte: 14,
  maxSpread: 100,
  minOi: 100,
  volumeMode: 'all',
  optionType: 'PUT',
  yMode: 'value',
  sizeMode: 'quantileRatio',
  showLevels: 'auto',
  minLevelStrength: 60,
  strikeMin: '',
  strikeMax: '',
  viewMinDte: '',
  viewMaxDte: '',
};

const formatNumber = (value, digits = 0) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '';
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
};

const parseNumber = (value, fallback = null) => {
  if (value === '' || value === null || value === undefined) return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const padRange = (min, max, ratio = 0.04) => {
  if (min === null || max === null || min === undefined || max === undefined) return [null, null];
  if (min === max) return [Math.max(0, min - 5), max + 5];
  const pad = (max - min) * ratio;
  return [Math.max(0, min - pad), max + pad];
};

const weightedStrikeQuantile = (rows, quantile) => {
  const sorted = [...rows].sort((a, b) => a.strike - b.strike);
  const total = sorted.reduce((sum, row) => sum + row.openInterest, 0);
  let running = 0;
  for (const row of sorted) {
    running += row.openInterest;
    if (running >= total * quantile) return row.strike;
  }
  return sorted.length ? sorted[sorted.length - 1].strike : null;
};

const buildRatioRankMap = (rows) => {
  const ratios = [...new Set(rows
    .map((row) => (row.strike ? row.mid / row.strike : 0))
    .filter((value) => Number.isFinite(value)))]
    .sort((a, b) => a - b);
  const denominator = Math.max(ratios.length - 1, 1);
  return new Map(ratios.map((value, index) => [value, index / denominator]));
};

const buildSymbolSize = ({ maxRatio, rankMap, sizeMode }) => (value) => {
  const strike = value[0];
  const mid = value[7];
  const ratio = strike ? mid / strike : 0;
  const normalized = maxRatio > 0 ? Math.max(0, ratio / maxRatio) : 0;
  if (sizeMode === 'quantileRatio') return 6 + (rankMap.get(ratio) ?? 0) * 38;
  if (sizeMode === 'power035Ratio') return 5 + Math.pow(normalized, 0.35) * 38;
  if (sizeMode === 'linearRatio') return 5 + normalized * 36;
  return 5 + Math.sqrt(normalized) * 36;
};

const LEVEL_LABELS = {
  support: '支撐',
  resistance: '壓力',
};

const LEVEL_COLORS = {
  support: '#1976d2',
  resistance: '#d32f2f',
};

const BUCKET_COLORS = {
  avoid: '#b91c1c',
  watch: '#f59e0b',
  conditional_sell: '#2563eb',
  conservative_sell: '#16a34a',
};

const BUCKET_LABELS = {
  avoid: '避免',
  watch: '觀察',
  conditional_sell: '條件可賣',
  conservative_sell: '保守可賣',
};

const bucketLabel = (classification) => BUCKET_LABELS[classification] || classification || '支撐區';

const bucketToLevel = (bucket) => {
  const price = Number(bucket?.center);
  if (!Number.isFinite(price)) return null;
  return {
    type: 'support',
    kind: 'sell_put_bucket',
    price,
    strength: Number(bucket?.score ?? 100),
    classification: bucket?.classification,
    role: bucket?.role,
    range: bucket?.range,
    reason: bucket?.reason,
    distanceToSpotPct: bucket?.distanceToSpotPct,
  };
};

const normalizeSoxlSupportSnapshot = (snapshot) => {
  const buckets = snapshot?.sellPutSupportBuckets || [];
  return {
    symbol: 'SOXL',
    status: 'snapshot',
    asOf: snapshot?.asOf,
    spot: snapshot?.spot,
    sellPutSupportBuckets: buckets,
    levels: buckets.map(bucketToLevel).filter(Boolean),
  };
};

const buildNearestStrike = (price, strikes) => {
  if (!price || !strikes.length) return { nearestStrike: null, nearestStrikeDistancePct: null };
  const nearestStrike = strikes.reduce((best, strike) => (
    Math.abs(strike - price) < Math.abs(best - price) ? strike : best
  ), strikes[0]);
  return {
    nearestStrike,
    nearestStrikeDistancePct: Math.abs(nearestStrike - price) / price * 100,
  };
};

const enrichLevelsWithStrikes = (levels, strikes) => levels.map((level) => {
  if (level.nearestStrike !== null && level.nearestStrike !== undefined) return level;
  return { ...level, ...buildNearestStrike(level.price, strikes) };
});

const shouldShowLevel = ({ level, mode, optionType, currentPrice, minStrength }) => {
  if (!level || mode === 'off') return false;
  if ((level.strength ?? 0) < minStrength) return false;
  if (mode === 'support') return level.type === 'support';
  if (mode === 'resistance') return level.type === 'resistance';
  if (mode === 'all') return true;
  if (currentPrice === null || currentPrice === undefined) return true;
  if (optionType === 'PUT') return level.type === 'support' && level.price <= currentPrice;
  if (optionType === 'CALL') return level.type === 'resistance' && level.price >= currentPrice;
  return true;
};

const selectDisplayLevels = ({ levels, filters, currentPrice, xMin, xMax }) => {
  const minStrength = parseNumber(filters.minLevelStrength, 60);
  return [...levels]
    .filter((level) => level.price >= xMin && level.price <= xMax)
    .filter((level) => shouldShowLevel({
      level,
      mode: filters.showLevels,
      optionType: filters.optionType,
      currentPrice,
      minStrength,
    }))
    .sort((a, b) => (b.strength ?? 0) - (a.strength ?? 0))
    .slice(0, 5);
};

const findNearestLevel = (strike, levels) => {
  if (!strike || !levels.length) return null;
  return levels.reduce((best, level) => {
    if (!best) return level;
    return Math.abs(level.price - strike) < Math.abs(best.price - strike) ? level : best;
  }, null);
};

function BobblePage() {
  const chartRef = useRef(null);
  const chartInstanceRef = useRef(null);
  const [symbolInput, setSymbolInput] = useState('SOXL');
  const [loadedSymbol, setLoadedSymbol] = useState('');
  const [chain, setChain] = useState(null);
  const [supportResistance, setSupportResistance] = useState(null);
  const [levelsError, setLevelsError] = useState('');
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const setFilter = (key) => (event) => {
    setFilters((current) => ({ ...current, [key]: event.target.value }));
  };

  const loadSymbol = async (event) => {
    event?.preventDefault?.();
    const normalizedSymbol = symbolInput.trim().toUpperCase();
    if (!normalizedSymbol) return;
    setLoading(true);
    setError('');
    setLevelsError('');
    setSupportResistance(null);
    try {
      const [chainResult, levelsResult] = await Promise.allSettled([
        fetchOptionChain(normalizedSymbol),
        normalizedSymbol === 'SOXL' ? fetchSoxlSupportSnapshot() : fetchSupportResistance(normalizedSymbol),
      ]);
      if (chainResult.status !== 'fulfilled') throw chainResult.reason;

      const payload = chainResult.value;
      setChain(payload);
      setLoadedSymbol(payload.symbol || normalizedSymbol);

      if (levelsResult.status === 'fulfilled') {
        setSupportResistance(normalizedSymbol === 'SOXL' ? normalizeSoxlSupportSnapshot(levelsResult.value) : levelsResult.value);
      } else {
        setLevelsError(levelsResult.reason?.response?.data?.detail || levelsResult.reason?.message || '支撐/壓力線載入失敗');
      }
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load option chain');
      setChain(null);
      setSupportResistance(null);
      setLoadedSymbol('');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadSymbol();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!chartRef.current) return undefined;
    chartInstanceRef.current = echarts.init(chartRef.current, null, { renderer: 'canvas' });
    const resize = () => chartInstanceRef.current?.resize();
    window.addEventListener('resize', resize);
    return () => {
      window.removeEventListener('resize', resize);
      chartInstanceRef.current?.dispose();
      chartInstanceRef.current = null;
    };
  }, []);

  const filteredRows = useMemo(() => {
    const rows = chain?.contracts || [];
    const minDte = parseNumber(filters.minDte, 0);
    const maxDte = parseNumber(filters.maxDte, 9999);
    const maxSpread = parseNumber(filters.maxSpread, 9999);
    const minOi = parseNumber(filters.minOi, 0);
    const xMin = parseNumber(filters.strikeMin, null);
    const xMax = parseNumber(filters.strikeMax, null);
    const yMin = parseNumber(filters.viewMinDte, null);
    const yMax = parseNumber(filters.viewMaxDte, null);
    return rows.filter((row) => (
      row.optionType === filters.optionType
      && row.strike !== null
      && row.mid !== null
      && row.openInterest > 0
      && row.dte >= minDte
      && row.dte <= maxDte
      && row.openInterest >= minOi
      && (row.spreadPct === null || row.spreadPct <= maxSpread)
      && (filters.volumeMode === 'all' || row.volume > 0)
      && (xMin === null || row.strike >= xMin)
      && (xMax === null || row.strike <= xMax)
      && (yMin === null || row.dte >= yMin)
      && (yMax === null || row.dte <= yMax)
    ));
  }, [chain, filters]);

  const currentPrice = chain?.underlying?.last ?? chain?.underlying?.mark ?? null;
  const currentPriceAsOf = chain?.underlying?.quoteTimeIso || chain?.snapshotUtc || '';
  const allStrikes = useMemo(() => [...new Set((chain?.contracts || [])
    .map((row) => row.strike)
    .filter((strike) => Number.isFinite(strike) && strike > 0))]
    .sort((a, b) => a - b), [chain]);
  const priceLevels = useMemo(() => enrichLevelsWithStrikes(supportResistance?.levels || [], allStrikes), [allStrikes, supportResistance]);

  useEffect(() => {
    const chart = chartInstanceRef.current;
    if (!chart) return;
    if (!filteredRows.length) {
      chart.clear();
      chart.setOption({
        title: {
          text: chain ? '沒有符合目前條件的合約' : '請輸入 symbol 並載入 option chain',
          left: 'center',
          top: 'middle',
          textStyle: { color: chain ? '#d32f2f' : '#888' },
        },
      }, true);
      return;
    }

    const strikes = filteredRows.map((row) => row.strike);
    const dtes = filteredRows.map((row) => row.dte);
    const maxRatio = Math.max(...filteredRows.map((row) => (row.strike ? row.mid / row.strike : 0)));
    const rankMap = buildRatioRankMap(filteredRows);
    let [xAutoMin, xAutoMax] = padRange(Math.min(...strikes), Math.max(...strikes), 0.05);
    const hasManualXMin = filters.strikeMin !== '';
    const hasManualXMax = filters.strikeMax !== '';
    if (currentPrice !== null && !hasManualXMin) xAutoMin = Math.min(xAutoMin, currentPrice);
    if (currentPrice !== null && !hasManualXMax) xAutoMax = Math.max(xAutoMax, currentPrice);
    const [yAutoMin, yAutoMax] = padRange(Math.min(...dtes), Math.max(...dtes), 0.12);
    const xMin = parseNumber(filters.strikeMin, xAutoMin);
    const xMax = parseNumber(filters.strikeMax, xAutoMax);
    const yMin = parseNumber(filters.viewMinDte, Math.floor(yAutoMin));
    const yMax = parseNumber(filters.viewMaxDte, Math.ceil(yAutoMax));
    const dteCategories = [...new Set(filteredRows.map((row) => row.dte))].sort((a, b) => a - b).map(String);

    const data = filteredRows.map((row) => ({
      value: filters.yMode === 'category'
        ? [row.strike, String(row.dte), row.openInterest, row.spreadPct ?? 999, row.volume, row.bid, row.ask, row.mid]
        : [row.strike, row.dte, row.openInterest, row.spreadPct ?? 999, row.volume, row.bid, row.ask, row.mid],
      itemStyle: {
        borderColor: row.volume > 0 ? '#111' : 'transparent',
        borderWidth: row.volume > 0 ? 1 : 0,
        opacity: 0.72,
      },
      raw: row,
    }));

    const displayLevels = selectDisplayLevels({ levels: priceLevels, filters, currentPrice, xMin, xMax });
    const markLines = [];
    if (currentPrice !== null && currentPrice >= xMin && currentPrice <= xMax) {
      markLines.push({
        xAxis: currentPrice,
        name: `${loadedSymbol} 現價`,
        label: {
          formatter: `${loadedSymbol} 現價 ${formatNumber(currentPrice, 2)}\n${currentPriceAsOf}`,
          position: 'insideEndTop',
        },
      });
    }
    displayLevels.forEach((level) => {
      const label = LEVEL_LABELS[level.type] || level.type;
      const isBucket = level.kind === 'sell_put_bucket';
      markLines.push({
        xAxis: level.price,
        name: `${isBucket ? bucketLabel(level.classification) : label} ${formatNumber(level.price, 2)}`,
        lineStyle: {
          type: isBucket || level.strength >= 75 ? 'solid' : 'dashed',
          color: isBucket ? (BUCKET_COLORS[level.classification] || '#555') : (LEVEL_COLORS[level.type] || '#555'),
          width: isBucket || level.strength >= 75 ? 2 : 1,
        },
        label: {
          formatter: isBucket
            ? `${bucketLabel(level.classification)} ${level.range || formatNumber(level.price, 2)}`
            : `${label} ${formatNumber(level.price, 2)}\n強度 ${formatNumber(level.strength)}`,
          position: level.type === 'support' ? 'insideEndBottom' : 'insideEndTop',
        },
      });
    });

    chart.setOption({
      animation: false,
      title: {
        text: `${loadedSymbol || filters.optionType} ${filters.optionType}｜Strike × DTE × Mid/Strike × Bid/Ask Spread`,
        left: 16,
        top: 10,
      },
      grid: { left: 72, right: 128, top: 80, bottom: 105 },
      tooltip: {
        trigger: 'item',
        confine: true,
        formatter: (params) => {
          const row = params.data.raw;
          const bidStrike = row.strike ? row.bid / row.strike : null;
          const midStrike = row.strike ? row.mid / row.strike : null;
          const nearestLevel = findNearestLevel(row.strike, displayLevels);
          const levelDistancePct = nearestLevel && row.strike ? Math.abs(nearestLevel.price - row.strike) / row.strike * 100 : null;
          const levelText = nearestLevel
            ? `<br/>最近${LEVEL_LABELS[nearestLevel.type] || nearestLevel.type}：${formatNumber(nearestLevel.price, 2)}｜強度 ${formatNumber(nearestLevel.strength)}｜距 Strike ${formatNumber(levelDistancePct, 2)}%`
            : '';
          return `<b>${row.contractSymbol}</b><br/>Expiration: ${row.expirationDate}｜DTE: ${row.dte}<br/>Strike: ${formatNumber(row.strike, 2)} ${row.optionType}<br/>OI: ${formatNumber(row.openInterest)}｜Volume: ${formatNumber(row.volume)}<br/>Bid / Ask / Mid: ${formatNumber(row.bid, 2)} / ${formatNumber(row.ask, 2)} / ${formatNumber(row.mid, 2)}<br/>權利金/Strike：${formatNumber(bidStrike, 4)}（Bid/Strike）｜${formatNumber(midStrike, 4)}（Mid/Strike）<br/>Spread: ${formatNumber(row.spreadPct, 1)}%<br/>Delta: ${row.delta ?? ''}｜IV: ${row.iv ?? ''}${levelText}`;
        },
      },
      toolbox: {
        right: 16,
        top: 10,
        feature: {
          dataZoom: { yAxisIndex: filters.yMode === 'category' ? false : 'none' },
          restore: {},
          saveAsImage: {},
        },
      },
      visualMap: {
        type: 'piecewise',
        right: 15,
        top: 120,
        dimension: 3,
        itemWidth: 14,
        itemHeight: 14,
        pieces: [
          { lte: 10, label: 'spread ≤10%', color: '#2ca25f' },
          { gt: 10, lte: 30, label: '10–30%', color: '#fdae61' },
          { gt: 30, lte: 60, label: '30–60%', color: '#f46d43' },
          { gt: 60, label: '>60%', color: '#b2182b' },
        ],
      },
      xAxis: {
        type: 'value',
        name: 'Strike',
        min: xMin,
        max: xMax,
        scale: true,
        splitLine: { lineStyle: { color: '#333', opacity: 0.22 } },
      },
      yAxis: filters.yMode === 'category'
        ? {
          type: 'category',
          name: 'DTE',
          data: dteCategories,
          boundaryGap: true,
          splitLine: { show: true, lineStyle: { color: '#333', opacity: 0.22 } },
        }
        : {
          type: 'value',
          name: 'DTE',
          min: yMin,
          max: yMax,
          scale: true,
          splitLine: { lineStyle: { color: '#333', opacity: 0.22 } },
        },
      dataZoom: [
        { type: 'slider', xAxisIndex: 0, bottom: 35, height: 22, filterMode: 'none' },
        { type: 'inside', xAxisIndex: 0, filterMode: 'none' },
        ...(filters.yMode === 'category' ? [] : [
          { type: 'slider', yAxisIndex: 0, right: 78, width: 18, filterMode: 'none' },
          { type: 'inside', yAxisIndex: 0, filterMode: 'none' },
        ]),
      ],
      series: [{
        name: `${filters.optionType} contracts`,
        type: 'scatter',
        data,
        encode: { x: 0, y: 1, tooltip: [0, 1, 2, 3, 4, 5, 6, 7] },
        symbolSize: buildSymbolSize({ maxRatio, rankMap, sizeMode: filters.sizeMode }),
        markLine: {
          symbol: 'none',
          lineStyle: { type: 'dashed', color: '#111' },
          data: markLines,
        },
      }],
    }, true);
  }, [chain, currentPrice, currentPriceAsOf, filteredRows, filters, loadedSymbol, priceLevels]);

  const applyCoreZoom = () => {
    const baseRows = (chain?.contracts || []).filter((row) => (
      row.optionType === filters.optionType
      && row.strike !== null
      && row.openInterest > 0
      && row.dte >= parseNumber(filters.minDte, 0)
      && row.dte <= parseNumber(filters.maxDte, 9999)
      && row.openInterest >= parseNumber(filters.minOi, 0)
      && (row.spreadPct === null || row.spreadPct <= parseNumber(filters.maxSpread, 9999))
      && (filters.volumeMode === 'all' || row.volume > 0)
    ));
    if (!baseRows.length) return;
    const [low, high] = padRange(weightedStrikeQuantile(baseRows, 0.05), weightedStrikeQuantile(baseRows, 0.95), 0.08);
    setFilters((current) => ({ ...current, strikeMin: String(Math.floor(low)), strikeMax: String(Math.ceil(high)) }));
  };

  const totalOi = filteredRows.reduce((sum, row) => sum + row.openInterest, 0);
  const totalVolume = filteredRows.reduce((sum, row) => sum + row.volume, 0);

  return (
    <Container maxWidth={false} sx={{ py: 3 }}>
      <Stack spacing={2} alignItems="center">
        <Box sx={{ width: '100%', maxWidth: 1250 }}>
          <Typography variant="h5" sx={{ mb: 1, fontWeight: 700 }}>
            Bobble｜Option Chain 宏觀泡泡圖
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            輸入 symbol 後由後端代呼 Schwab API。X 軸 = Strike，Y 軸 = DTE，泡泡大小 = Mid/Strike，顏色 = bid/ask spread%；支撐/壓力線由後端用一年 OHLCV 計算。
          </Typography>
          <Card variant="outlined">
            <CardContent>
              <Box component="form" onSubmit={loadSymbol} sx={{ mb: 2 }}>
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} alignItems={{ xs: 'stretch', sm: 'center' }}>
                  <TextField
                    label="Symbol"
                    value={symbolInput}
                    onChange={(event) => setSymbolInput(event.target.value.toUpperCase())}
                    size="small"
                    inputProps={{ 'aria-label': 'symbol' }}
                  />
                  <Button type="submit" variant="contained" disabled={loading}>
                    {loading ? '載入中' : '載入 Schwab 期權鏈'}
                  </Button>
                  {loading && <CircularProgress size={24} />}
                </Stack>
              </Box>
              <Grid container spacing={1.5}>
                <Grid item xs={6} md={1.4}><TextField fullWidth size="small" label="最小 DTE" value={filters.minDte} onChange={setFilter('minDte')} /></Grid>
                <Grid item xs={6} md={1.4}><TextField fullWidth size="small" label="最大 DTE" value={filters.maxDte} onChange={setFilter('maxDte')} /></Grid>
                <Grid item xs={6} md={1.5}><TextField fullWidth size="small" label="最大 spread %" value={filters.maxSpread} onChange={setFilter('maxSpread')} /></Grid>
                <Grid item xs={6} md={1.4}><TextField fullWidth size="small" label="最小 OI" value={filters.minOi} onChange={setFilter('minOi')} /></Grid>
                <Grid item xs={6} md={1.6}>
                  <FormControl fullWidth size="small"><InputLabel>類型</InputLabel><Select label="類型" value={filters.optionType} onChange={setFilter('optionType')}><MenuItem value="PUT">PUT</MenuItem><MenuItem value="CALL">CALL</MenuItem></Select></FormControl>
                </Grid>
                <Grid item xs={6} md={1.8}>
                  <FormControl fullWidth size="small"><InputLabel>成交量</InputLabel><Select label="成交量" value={filters.volumeMode} onChange={setFilter('volumeMode')}><MenuItem value="all">全部</MenuItem><MenuItem value="positive">只看有成交量</MenuItem></Select></FormControl>
                </Grid>
                <Grid item xs={6} md={1.8}>
                  <FormControl fullWidth size="small"><InputLabel>Y 軸</InputLabel><Select label="Y 軸" value={filters.yMode} onChange={setFilter('yMode')}><MenuItem value="value">連續 DTE</MenuItem><MenuItem value="category">DTE 分列</MenuItem></Select></FormControl>
                </Grid>
                <Grid item xs={12} md={2.3}>
                  <FormControl fullWidth size="small"><InputLabel>點大小</InputLabel><Select label="點大小" value={filters.sizeMode} onChange={setFilter('sizeMode')}><MenuItem value="quantileRatio">Quantile(Mid/Strike)</MenuItem><MenuItem value="power035Ratio">Power 0.35</MenuItem><MenuItem value="sqrtRatio">sqrt(Mid/Strike)</MenuItem><MenuItem value="linearRatio">linear(Mid/Strike)</MenuItem></Select></FormControl>
                </Grid>
                <Grid item xs={6} md={1.8}>
                  <FormControl fullWidth size="small"><InputLabel>支撐/壓力線</InputLabel><Select label="支撐/壓力線" value={filters.showLevels} onChange={setFilter('showLevels')}><MenuItem value="auto">自動</MenuItem><MenuItem value="support">只支撐</MenuItem><MenuItem value="resistance">只壓力</MenuItem><MenuItem value="all">全部</MenuItem><MenuItem value="off">關閉</MenuItem></Select></FormControl>
                </Grid>
                <Grid item xs={6} md={1.4}><TextField fullWidth size="small" label="線強度 ≥" value={filters.minLevelStrength} onChange={setFilter('minLevelStrength')} /></Grid>
                <Grid item xs={6} md={1.4}><TextField fullWidth size="small" label="Strike Min" value={filters.strikeMin} onChange={setFilter('strikeMin')} /></Grid>
                <Grid item xs={6} md={1.4}><TextField fullWidth size="small" label="Strike Max" value={filters.strikeMax} onChange={setFilter('strikeMax')} /></Grid>
                <Grid item xs={6} md={1.4}><TextField fullWidth size="small" label="DTE Min" value={filters.viewMinDte} onChange={setFilter('viewMinDte')} /></Grid>
                <Grid item xs={6} md={1.4}><TextField fullWidth size="small" label="DTE Max" value={filters.viewMaxDte} onChange={setFilter('viewMaxDte')} /></Grid>
                <Grid item xs={12} md={3.5}>
                  <Stack direction="row" spacing={1}>
                    <Button variant="outlined" onClick={() => setFilters((current) => ({ ...current, strikeMin: '', strikeMax: '', viewMinDte: '', viewMaxDte: '' }))}>清除軸範圍</Button>
                    <Button variant="outlined" onClick={applyCoreZoom}>縮放到 OI 核心區</Button>
                  </Stack>
                </Grid>
              </Grid>
            </CardContent>
          </Card>
        </Box>

        {error && <Alert severity="error" sx={{ width: '100%', maxWidth: 1250 }}>{error}</Alert>}
        {levelsError && <Alert severity="warning" sx={{ width: '100%', maxWidth: 1250 }}>期權鏈已載入，但支撐/壓力線未載入：{levelsError}</Alert>}
        {supportResistance?.status === 'degraded' && !levelsError && (
          <Alert severity="info" sx={{ width: '100%', maxWidth: 1250 }}>
            支撐/壓力線為降級結果：{(supportResistance.warnings || []).join('、') || '資料不足或強度篩選後無高品質價位'}
          </Alert>
        )}

        <Box sx={{ width: '100%', maxWidth: 1250 }}>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            顯示 <strong>{formatNumber(filteredRows.length)}</strong> 張 {filters.optionType}；總 OI <strong>{formatNumber(totalOi)}</strong>；總成交量 <strong>{formatNumber(totalVolume)}</strong>
            {chain?.summary ? `；全鏈 ${formatNumber(chain.summary.contracts)} 張，${formatNumber(chain.summary.expirations)} 個到期日` : ''}
            {currentPrice !== null ? `；現價 ${formatNumber(currentPrice, 2)}（${currentPriceAsOf}）` : ''}
            {supportResistance?.levels ? `；支撐/壓力線 ${formatNumber(priceLevels.length)} 條（${supportResistance.status}）` : ''}
            {supportResistance?.sellPutSupportBuckets ? `；Sell Put 支撐區 ${formatNumber(supportResistance.sellPutSupportBuckets.length)} 個（${supportResistance.asOf || ''}）` : ''}
          </Typography>
          <Box ref={chartRef} sx={{ width: '100%', height: 760, border: 1, borderColor: 'divider', borderRadius: 1, bgcolor: 'background.paper' }} />
        </Box>
      </Stack>
    </Container>
  );
}

export default BobblePage;
