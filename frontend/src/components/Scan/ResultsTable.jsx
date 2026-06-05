import { useMemo, useRef, useState, useCallback, memo } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import {
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TablePagination,
  TableSortLabel,
  Paper,
  Chip,
  Tooltip,
  Typography,
  Box,
  CircularProgress,
  IconButton,
} from '@mui/material';
import CheckIcon from '@mui/icons-material/Check';
import CloseIcon from '@mui/icons-material/Close';
import ShowChartIcon from '@mui/icons-material/ShowChart';
import RSSparkline from './RSSparkline';
import PriceSparkline from './PriceSparkline';
import FieldAvailabilityChip from './FieldAvailabilityChip';
import MarketBadge from './MarketBadge';
import MarketThemesList from '../Stock/MarketThemesList';
import AddToWatchlistMenu from '../common/AddToWatchlistMenu';
import {
  getStageColor,
  getRatingColor,
  getGrowthColor,
  getEpsRatingColor,
  getGroupRankColor,
} from '../../utils/colorUtils';
import {
  formatLargeNumber,
  formatIpoAge,
  getIpoAgeColor,
  getCurrencyPrefix,
  formatLocalCurrency,
} from '../../utils/formatUtils';

// Row height constant for virtualization
const ROW_HEIGHT = 48;
const SYMBOL_COLUMN_WIDTH = 210;

// MCap column display modes. USD is the default per 3axp: cross-market
// parity is the common case; local is one click away. Kept as constants
// (rather than bare strings) so callers grep-reliably and typos fail fast.
const MCAP_DISPLAY = Object.freeze({
  USD: 'usd',
  LOCAL: 'local',
});

// Column definitions with explicit widths
const columnHelp = {
  chart: { zh: '圖表', description: '開啟個股圖表。' },
  symbol: { zh: '代號', description: '股票或 ETF 交易代號。美國 ETF 會在代號旁顯示 ETF 標記。' },
  rs_trend: { zh: '相對強度趨勢', description: '近期價格相對 SPY 的 RS line 趨勢；上升代表近期跑贏 SPY。' },
  price_change_1d: { zh: '日漲跌', description: '最新交易日價格變化百分比。' },
  gics_sector: { zh: '板塊', description: '標的所屬 sector / 板塊。' },
  ibd_industry_group: { zh: 'IBD 產業', description: '標的所屬產業組；目前 US 使用 foundation/provider 或 artifact surrogate group。' },
  market_themes: { zh: '主題', description: '投資主題或市場敘事；目前 US static pipeline 尚未接入 themes artifact。' },
  ibd_group_rank: { zh: '產業排名', description: '產業組強度排名，數字越小越強；目前為 artifact-native surrogate rank。' },
  composite_score: { zh: '綜合分', description: '多個技術/相對強度/型態指標的綜合分數。' },
  minervini_score: { zh: 'Minervini 分', description: 'Minervini 趨勢模板條件的通過程度。' },
  canslim_score: { zh: 'CANSLIM 分', description: '以 CANSLIM 風格綜合 RS、EPS、趨勢等因素的分數。' },
  ipo_score: { zh: 'IPO 分', description: '新股/上市時間相關的分數。' },
  custom_score: { zh: '自訂分', description: '自訂策略綜合分數。' },
  volume_breakthrough_score: { zh: '放量突破', description: '成交量相對近期均量的放大程度；越高代表越明顯放量。' },
  se_setup_score: { zh: 'SE 型態分', description: 'Setup Engine 對目前型態與突破準備度的綜合分。' },
  se_pattern_primary: { zh: '型態', description: 'Setup Engine 偵測到的主要型態。' },
  se_distance_to_pivot_pct: { zh: '距樞紐%', description: '現價距離 pivot / 樞紐價的百分比；接近 0 代表接近樞紐。' },
  se_bb_width_pctile_252: { zh: '壓縮度', description: '252 日布林帶寬度百分位；越低代表波動越收縮。' },
  se_volume_vs_50d: { zh: '量比50日', description: '當日成交量除以 50 日平均成交量。' },
  se_rs_line_new_high: { zh: 'RS 新高', description: 'RS line 是否創近期新高，用來確認相對強勢。' },
  se_pivot_price: { zh: '樞紐價', description: 'Setup Engine 計算的 pivot price。' },
  rs_rating: { zh: '相對強度', description: '12 個月相對強度排名，通常越高代表長期表現越強。' },
  rs_rating_1m: { zh: '1月強度', description: '1 個月相對強度排名。' },
  rs_rating_3m: { zh: '3月強度', description: '3 個月相對強度排名。' },
  rs_rating_12m: { zh: '12月強度', description: '12 個月相對強度排名。' },
  beta: { zh: 'Beta', description: '相對市場的波動度；越高代表對市場波動更敏感。' },
  beta_adj_rs: { zh: 'Beta調整RS', description: '以 Beta 調整後的相對強度。' },
  eps_rating: { zh: 'EPS評級', description: '由 EPS growth 派生的評級；目前受 foundation coverage 限制。' },
  stage: { zh: '階段', description: '技術趨勢階段；Stage 2 通常代表較健康上升趨勢，Stage 4 偏弱。' },
  current_price: { zh: '現價', description: '最新可用收盤價或價格。' },
  volume: { zh: '成交量', description: '最新交易日成交股數，用於判斷標的交易活躍度。' },
  market_cap: { zh: '市值/AUM', description: '股票為市值；ETF 使用 AUM / net assets fallback。' },
  adv_usd: { zh: '日均成交額', description: '美元日成交額，用於判斷標的流動性。' },
  ipo_date: { zh: '上市日期', description: 'IPO 或 first trade date。' },
  eps_growth_qq: { zh: 'EPS成長', description: '近期 EPS 成長率。' },
  sales_growth_qq: { zh: '營收成長', description: '近期營收成長率。' },
  adr_percent: { zh: '平均日振幅', description: 'Average Daily Range 百分比，用於衡量日內波動。' },
  ma_alignment: { zh: '均線排列', description: '價格與主要均線是否呈多頭排列。' },
  vcp_detected: { zh: 'VCP型態', description: '是否偵測到波動收縮型態 VCP。' },
  vcp_score: { zh: 'VCP分', description: 'VCP 型態品質分數。' },
  vcp_pivot: { zh: 'VCP樞紐', description: 'VCP 型態的 pivot price。' },
  vcp_ready_for_breakout: { zh: '突破準備', description: '是否接近可突破狀態。' },
  passes_template: { zh: '通過模板', description: '是否通過 Minervini / 趨勢模板條件。' },
  rating: { zh: '評級', description: '由 scan metrics 派生的綜合評級。' },
};

const ColumnHeaderLabel = ({ column }) => {
  const help = columnHelp[column.id];
  const label = <Box component="span" sx={{ borderBottom: help ? '1px dotted currentColor' : 'none' }}>{column.label}</Box>;
  if (!help) return label;
  return (
    <Tooltip
      arrow
      placement="top"
      title={(
        <Box>
          <Typography variant="subtitle2" component="div">{help.zh}</Typography>
          <Typography variant="caption" component="div">{help.description}</Typography>
        </Box>
      )}
    >
      {label}
    </Tooltip>
  );
};

const columns = [
  { id: 'chart', label: '', sortable: false, width: 60 },
  // Width fits "0700.HK" + MarketBadge + FieldAvailabilityChip on a single
  // line without overflow (nowrap guards the rest).
  { id: 'symbol', label: 'Sym', sortable: true, width: SYMBOL_COLUMN_WIDTH },
  { id: 'rs_trend', label: 'RS Trend', sortable: true, width: 110 },
  { id: 'price_change_1d', label: 'Price', sortable: true, width: 110 },
  { id: 'gics_sector', label: 'Sector', sortable: true, width: 80 },
  { id: 'ibd_industry_group', label: 'IBD Industry', sortable: true, width: 140 },
  { id: 'market_themes', label: 'Themes', sortable: false, width: 180 },
  { id: 'ibd_group_rank', label: 'Grp', sortable: true, width: 45 },
  { id: 'composite_score', label: 'Comp', sortable: true, width: 50 },
  { id: 'minervini_score', label: 'Min', sortable: true, width: 45 },
  { id: 'canslim_score', label: 'CAN', sortable: true, width: 45 },
  { id: 'ipo_score', label: 'IPO', sortable: true, width: 45 },
  { id: 'custom_score', label: 'Cust', sortable: true, width: 45 },
  { id: 'volume_breakthrough_score', label: 'VolB', sortable: true, width: 50 },
  { id: 'se_setup_score', label: 'SE', sortable: true, width: 45 },
  { id: 'se_pattern_primary', label: 'Pat', sortable: true, width: 55 },
  { id: 'se_distance_to_pivot_pct', label: 'Pvt%', sortable: true, width: 50 },
  { id: 'se_bb_width_pctile_252', label: 'Sqz', sortable: true, width: 45 },
  { id: 'se_volume_vs_50d', label: 'V50', sortable: true, width: 45 },
  { id: 'se_rs_line_new_high', label: 'RSH', sortable: false, width: 35 },
  { id: 'se_pivot_price', label: 'Pvt$', sortable: true, width: 55 },
  { id: 'rs_rating', label: 'RS', sortable: true, width: 40 },
  { id: 'rs_rating_1m', label: '1M', sortable: true, width: 40 },
  { id: 'rs_rating_3m', label: '3M', sortable: true, width: 40 },
  { id: 'rs_rating_12m', label: '12M', sortable: true, width: 45 },
  { id: 'beta', label: 'β', sortable: true, width: 45 },
  { id: 'beta_adj_rs', label: 'βRS', sortable: true, width: 45 },
  { id: 'eps_rating', label: 'EPS Rtg', sortable: true, width: 55 },
  { id: 'stage', label: 'Stg', sortable: true, width: 40 },
  { id: 'current_price', label: 'Price', sortable: true, width: 65 },
  { id: 'volume', label: 'Vol', sortable: true, width: 60 },
  // MCap column header label is overridden per-render based on the USD/Local
  // toggle; keep the underlying sort key stable at 'market_cap' so the
  // sort-by dropdown / URL state doesn't shift when the user flips modes.
  { id: 'market_cap', label: 'MCap', sortable: true, width: 75 },
  { id: 'adv_usd', label: 'ADV ($)', sortable: true, width: 70 },
  { id: 'ipo_date', label: 'IPO', sortable: true, width: 50 },
  { id: 'eps_growth_qq', label: 'EPS', sortable: true, width: 50 },
  { id: 'sales_growth_qq', label: 'Sales', sortable: true, width: 50 },
  { id: 'adr_percent', label: 'ADR', sortable: true, width: 50 },
  { id: 'ma_alignment', label: 'MA', sortable: false, width: 35 },
  { id: 'vcp_detected', label: 'VCP', sortable: false, width: 40 },
  { id: 'vcp_score', label: 'VScr', sortable: true, width: 50 },
  { id: 'vcp_pivot', label: 'Pvt', sortable: true, width: 55 },
  { id: 'vcp_ready_for_breakout', label: 'Rdy', sortable: false, width: 35 },
  { id: 'passes_template', label: 'Pass', sortable: false, width: 40 },
  { id: 'rating', label: 'Rate', sortable: false, width: 80 },
];

const HIDDEN_SCAN_COLUMN_IDS = new Set([
  'market_themes',
  'composite_score',
  'minervini_score',
  'canslim_score',
  'ipo_score',
  'custom_score',
  'se_setup_score',
  'se_pattern_primary',
  'se_bb_width_pctile_252',
  'se_pivot_price',
  'beta_adj_rs',
  'eps_rating',
  'stage',
  'ipo_date',
  'sales_growth_qq',
  'vcp_detected',
  'vcp_score',
  'vcp_pivot',
  'vcp_ready_for_breakout',
  'passes_template',
  'rating',
]);

const getStatusChipProps = (row) => {
  const isInsufficientHistoryRow =
    row.data_status === 'insufficient_history' || row.rating === 'Insufficient Data';

  if (row.scan_mode === 'listing_only' && isInsufficientHistoryRow) {
    return {
      label: 'New IPO',
      color: 'warning',
      title: 'Visible in the scan table, but not yet scannable because price history is still limited.',
    };
  }
  if (row.scan_mode === 'ipo_weighted' && isInsufficientHistoryRow) {
    return {
      label: 'IPO Weighted',
      color: 'info',
      title: row.composite_reason === 'ipo_uplift'
        ? 'Composite uses applicable screeners plus an IPO uplift while the stock is still young.'
        : 'Composite uses only the screeners that have enough history to run.',
    };
  }
  return null;
};

/**
 * Memoized table row component to prevent unnecessary re-renders
 */
const VirtualTableRow = memo(function VirtualTableRow({
  row,
  onRowClick,
  onRowHover,
  onOpenChart,
  showActions,
  showWatchlistMenu,
  chartEnabled,
  mcapDisplay,
}) {
  const statusChip = getStatusChipProps(row);
  const isUsEtf = row.market === 'US' && (row.is_etf || String(row.security_type || '').toUpperCase() === 'ETF');
  const handleRowClick = useCallback(() => {
    if (!chartEnabled) {
      return;
    }
    onRowClick?.(row.symbol);
  }, [chartEnabled, onRowClick, row.symbol]);

  const handleRowHover = useCallback(() => {
    onRowHover?.(row.symbol);
  }, [onRowHover, row.symbol]);

  const handleChartClick = useCallback((e) => {
    e.stopPropagation();
    if (!chartEnabled) {
      return;
    }
    onOpenChart?.(row.symbol);
  }, [chartEnabled, onOpenChart, row.symbol]);

  const renderCell = (columnId, cell) => (
    HIDDEN_SCAN_COLUMN_IDS.has(columnId) ? null : cell
  );

  return (
    <TableRow
      hover
      onClick={handleRowClick}
      onMouseEnter={handleRowHover}
      sx={{ cursor: onRowClick && chartEnabled ? 'pointer' : 'default', height: ROW_HEIGHT }}
    >
      {showActions && (
        <TableCell align="center" onClick={(e) => e.stopPropagation()} sx={{ p: '2px', width: 60, minWidth: 60 }}>
          {chartEnabled ? (
            <IconButton
              size="small"
              onClick={handleChartClick}
              sx={{ color: 'primary.main', p: 0 }}
            >
              <ShowChartIcon sx={{ fontSize: 14 }} />
            </IconButton>
          ) : null}
          {showWatchlistMenu ? <AddToWatchlistMenu symbols={row.symbol} size="small" /> : null}
        </TableCell>
      )}

      <TableCell
        sx={{
          width: SYMBOL_COLUMN_WIDTH,
          minWidth: SYMBOL_COLUMN_WIDTH,
          maxWidth: SYMBOL_COLUMN_WIDTH,
          py: '4px',
          overflow: 'hidden',
        }}
      >
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25, minWidth: 0 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, minWidth: 0, whiteSpace: 'nowrap' }}>
            <Typography component="span" variant="body2" sx={{ fontWeight: 600, lineHeight: 1.2, flexShrink: 0 }}>
              {row.symbol}
            </Typography>
            <MarketBadge market={row.market} exchange={row.exchange} />
            {isUsEtf ? (
              <Chip
                label="ETF"
                size="small"
                variant="outlined"
                title="US exchange-traded fund"
                sx={{
                  height: 16,
                  fontSize: '0.62rem',
                  fontWeight: 700,
                  lineHeight: 1,
                  px: 0.25,
                  flexShrink: 0,
                  borderColor: 'info.main',
                  color: 'info.main',
                  '& .MuiChip-label': { px: 0.5 },
                }}
              />
            ) : null}
            <FieldAvailabilityChip
              fieldAvailability={row.field_availability}
              growthMetricBasis={row.growth_metric_basis}
            />
          </Box>
          {row.company_name || statusChip ? (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, minWidth: 0 }}>
              {row.company_name ? (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  noWrap
                  title={row.company_name}
                  sx={{ display: 'block', lineHeight: 1.2, minWidth: 0, flex: 1 }}
                >
                  {row.company_name}
                </Typography>
              ) : null}
              {statusChip ? (
                <Chip
                  label={statusChip.label}
                  color={statusChip.color}
                  size="small"
                  title={statusChip.title}
                  sx={{ height: 18, fontSize: 10, flexShrink: 0 }}
                />
              ) : null}
            </Box>
          ) : null}
        </Box>
      </TableCell>

      <TableCell align="center" sx={{ p: '4px', width: 110, minWidth: 110 }}>
        <RSSparkline
          data={row.rs_sparkline_data}
          trend={row.rs_trend}
          width={100}
          height={28}
        />
      </TableCell>

      <TableCell align="center" sx={{ p: '4px', width: 110, minWidth: 110 }}>
        <PriceSparkline
          data={row.price_sparkline_data}
          trend={row.price_trend}
          change1d={row.price_change_1d}
          industry={row.ibd_industry_group}
          width={100}
          height={28}
        />
      </TableCell>

      <TableCell align="center" sx={{ color: 'text.secondary', width: 80, minWidth: 80, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {row.gics_sector || '-'}
      </TableCell>

      <TableCell align="left" sx={{ color: 'text.secondary', width: 140, minWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {row.ibd_industry_group || '-'}
      </TableCell>

      {renderCell('market_themes', (
        <TableCell align="left" sx={{ color: 'text.secondary', width: 180, minWidth: 180, py: 0.5 }}>
          <MarketThemesList themes={row.market_themes} variant="compact" />
        </TableCell>
      ))}

      <TableCell align="center" sx={{
        fontFamily: 'monospace',
        color: getGroupRankColor(row.ibd_group_rank),
        fontWeight: row.ibd_group_rank && row.ibd_group_rank <= 20 ? 600 : 400,
        width: 45, minWidth: 45
      }}>
        {row.ibd_group_rank ?? '-'}
      </TableCell>

      {renderCell('composite_score', (
        <TableCell align="center" sx={{ fontWeight: 600, color: 'primary.main', fontFamily: 'monospace', width: 50, minWidth: 50 }}>
          {row.composite_score?.toFixed(1) || '-'}
        </TableCell>
      ))}

      {renderCell('minervini_score', (
        <TableCell align="center" sx={{ fontFamily: 'monospace', width: 45, minWidth: 45 }}>
          {row.minervini_score != null ? row.minervini_score.toFixed(1) : '-'}
        </TableCell>
      ))}

      {renderCell('canslim_score', (
        <TableCell align="center" sx={{ fontFamily: 'monospace', width: 45, minWidth: 45 }}>
          {row.canslim_score != null ? row.canslim_score.toFixed(1) : '-'}
        </TableCell>
      ))}

      {renderCell('ipo_score', (
        <TableCell align="center" sx={{ fontFamily: 'monospace', width: 45, minWidth: 45 }}>
          {row.ipo_score != null ? row.ipo_score.toFixed(1) : '-'}
        </TableCell>
      ))}

      {renderCell('custom_score', (
        <TableCell align="center" sx={{ fontFamily: 'monospace', width: 45, minWidth: 45 }}>
          {row.custom_score != null ? row.custom_score.toFixed(1) : '-'}
        </TableCell>
      ))}

      <TableCell align="center" sx={{ fontFamily: 'monospace', width: 50, minWidth: 50 }}>
        {row.volume_breakthrough_score != null ? row.volume_breakthrough_score.toFixed(1) : '-'}
      </TableCell>

      {renderCell('se_setup_score', (
        <TableCell align="center" sx={{ fontFamily: 'monospace', width: 45, minWidth: 45 }}>
          {row.se_setup_score != null ? row.se_setup_score.toFixed(1) : '-'}
        </TableCell>
      ))}

      {renderCell('se_pattern_primary', (
        <TableCell align="center" sx={{ color: 'text.secondary', width: 55, minWidth: 55, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {row.se_pattern_primary || '-'}
        </TableCell>
      ))}

      <TableCell align="center" sx={{ fontFamily: 'monospace', width: 50, minWidth: 50 }}>
        {row.se_distance_to_pivot_pct != null ? `${row.se_distance_to_pivot_pct.toFixed(1)}%` : '-'}
      </TableCell>

      {renderCell('se_bb_width_pctile_252', (
        <TableCell align="center" sx={{ fontFamily: 'monospace', width: 45, minWidth: 45 }}>
          {row.se_bb_width_pctile_252 != null ? row.se_bb_width_pctile_252.toFixed(0) : '-'}
        </TableCell>
      ))}

      <TableCell align="center" sx={{ fontFamily: 'monospace', width: 45, minWidth: 45 }}>
        {row.se_volume_vs_50d != null ? `${row.se_volume_vs_50d.toFixed(1)}x` : '-'}
      </TableCell>

      <TableCell align="center" sx={{ width: 35, minWidth: 35 }}>
        {row.se_rs_line_new_high == null ? '-' : row.se_rs_line_new_high ? (
          <CheckIcon sx={{ fontSize: 14, color: 'success.main' }} />
        ) : (
          <CloseIcon sx={{ fontSize: 14, color: 'text.disabled' }} />
        )}
      </TableCell>

      {renderCell('se_pivot_price', (
        <TableCell align="right" sx={{ fontFamily: 'monospace', width: 55, minWidth: 55 }}>
          {row.se_pivot_price != null ? `$${row.se_pivot_price.toFixed(2)}` : '-'}
        </TableCell>
      ))}

      <TableCell align="center" sx={{ fontFamily: 'monospace', width: 40, minWidth: 40 }}>
        {row.rs_rating?.toFixed(0) || '-'}
      </TableCell>

      <TableCell align="center" sx={{ fontFamily: 'monospace', width: 40, minWidth: 40 }}>
        {row.rs_rating_1m?.toFixed(0) || '-'}
      </TableCell>

      <TableCell align="center" sx={{ fontFamily: 'monospace', width: 40, minWidth: 40 }}>
        {row.rs_rating_3m?.toFixed(0) || '-'}
      </TableCell>

      <TableCell align="center" sx={{ fontFamily: 'monospace', width: 45, minWidth: 45 }}>
        {row.rs_rating_12m?.toFixed(0) || '-'}
      </TableCell>

      <TableCell align="center" sx={{ fontFamily: 'monospace', width: 45, minWidth: 45 }}>
        {row.beta != null ? row.beta.toFixed(2) : '-'}
      </TableCell>

      {renderCell('beta_adj_rs', (
        <TableCell align="center" sx={{ fontFamily: 'monospace', width: 45, minWidth: 45 }}>
          {row.beta_adj_rs != null ? row.beta_adj_rs.toFixed(0) : '-'}
        </TableCell>
      ))}

      {renderCell('eps_rating', (
        <TableCell align="center" sx={{ fontFamily: 'monospace', color: getEpsRatingColor(row.eps_rating), width: 55, minWidth: 55 }}>
          {row.eps_rating != null ? row.eps_rating : '-'}
        </TableCell>
      ))}

      {renderCell('stage', (
        <TableCell align="center" sx={{ width: 40, minWidth: 40 }}>
          {row.stage != null ? (
            <Box
              component="span"
              sx={{
                backgroundColor: getStageColor(row.stage),
                color: 'white',
                padding: '1px 4px',
                borderRadius: '2px',
                fontSize: '10px',
                fontWeight: 500,
              }}
            >
              S{row.stage}
            </Box>
          ) : (
            '-'
          )}
        </TableCell>
      ))}

      <TableCell align="right" sx={{ fontFamily: 'monospace', width: 65, minWidth: 65 }}>
        {formatLocalCurrency(row.current_price, row.currency)}
      </TableCell>

      <TableCell align="right" sx={{ fontFamily: 'monospace', width: 60, minWidth: 60 }}>
        {formatLargeNumber(row.volume)}
      </TableCell>

      <TableCell align="right" sx={{ fontFamily: 'monospace', width: 75, minWidth: 75 }}>
        {mcapDisplay === MCAP_DISPLAY.USD
          ? formatLargeNumber(row.market_cap_usd, '$')
          : formatLargeNumber(row.market_cap, getCurrencyPrefix(row.currency))}
      </TableCell>

      <TableCell align="right" sx={{ fontFamily: 'monospace', width: 70, minWidth: 70 }}>
        {formatLargeNumber(row.adv_usd, '$')}
      </TableCell>

      {renderCell('ipo_date', (
        <TableCell align="center" sx={{ fontFamily: 'monospace', color: getIpoAgeColor(row.ipo_date), width: 50, minWidth: 50 }}>
          {formatIpoAge(row.ipo_date)}
        </TableCell>
      ))}

      <TableCell align="center" sx={{ fontFamily: 'monospace', color: getGrowthColor(row.eps_growth_qq), width: 50, minWidth: 50 }}>
        {row.eps_growth_qq != null ? `${row.eps_growth_qq.toFixed(0)}%` : '-'}
      </TableCell>

      {renderCell('sales_growth_qq', (
        <TableCell align="center" sx={{ fontFamily: 'monospace', color: getGrowthColor(row.sales_growth_qq), width: 50, minWidth: 50 }}>
          {row.sales_growth_qq != null ? `${row.sales_growth_qq.toFixed(0)}%` : '-'}
        </TableCell>
      ))}

      <TableCell align="center" sx={{ fontFamily: 'monospace', width: 50, minWidth: 50 }}>
        {row.adr_percent != null ? `${row.adr_percent.toFixed(1)}%` : '-'}
      </TableCell>

      <TableCell align="center" sx={{ width: 35, minWidth: 35 }}>
        {row.ma_alignment ? (
          <CheckIcon sx={{ fontSize: 14, color: 'success.main' }} />
        ) : (
          <CloseIcon sx={{ fontSize: 14, color: 'error.main' }} />
        )}
      </TableCell>

      {renderCell('vcp_detected', (
        <TableCell align="center" sx={{ width: 40, minWidth: 40 }}>
          {row.vcp_detected ? (
            <CheckIcon sx={{ fontSize: 14, color: 'success.main' }} />
          ) : (
            <CloseIcon sx={{ fontSize: 14, color: 'text.disabled' }} />
          )}
        </TableCell>
      ))}

      {renderCell('vcp_score', (
        <TableCell align="center" sx={{ fontFamily: 'monospace', width: 50, minWidth: 50 }}>
          {row.vcp_score != null ? row.vcp_score.toFixed(1) : '-'}
        </TableCell>
      ))}

      {renderCell('vcp_pivot', (
        <TableCell align="right" sx={{ fontFamily: 'monospace', width: 55, minWidth: 55 }}>
          {row.vcp_pivot != null ? row.vcp_pivot.toFixed(2) : '-'}
        </TableCell>
      ))}

      {renderCell('vcp_ready_for_breakout', (
        <TableCell align="center" sx={{ width: 35, minWidth: 35 }}>
          {row.vcp_ready_for_breakout ? (
            <CheckIcon sx={{ fontSize: 14, color: 'success.main' }} />
          ) : (
            <CloseIcon sx={{ fontSize: 14, color: 'text.disabled' }} />
          )}
        </TableCell>
      ))}

      {renderCell('passes_template', (
        <TableCell align="center" sx={{ width: 40, minWidth: 40 }}>
          {row.passes_template ? (
            <CheckIcon sx={{ fontSize: 14, color: 'success.main' }} />
          ) : (
            <CloseIcon sx={{ fontSize: 14, color: 'text.disabled' }} />
          )}
        </TableCell>
      ))}

      {renderCell('rating', (
        <TableCell align="center" sx={{ width: 80, minWidth: 80 }}>
          <Chip
            label={row.rating}
            color={getRatingColor(row.rating)}
            size="small"
          />
        </TableCell>
      ))}
    </TableRow>
  );
}, (prevProps, nextProps) => {
  // Custom comparison - only re-render if the row data actually changed
  return prevProps.row.symbol === nextProps.row.symbol &&
         prevProps.row.company_name === nextProps.row.company_name &&
         prevProps.row.market === nextProps.row.market &&
         prevProps.row.exchange === nextProps.row.exchange &&
         prevProps.row.field_availability === nextProps.row.field_availability &&
         prevProps.row.growth_metric_basis === nextProps.row.growth_metric_basis &&
         prevProps.row.composite_score === nextProps.row.composite_score &&
         prevProps.row.rs_rating === nextProps.row.rs_rating &&
         prevProps.row.current_price === nextProps.row.current_price &&
         prevProps.row.price_change_1d === nextProps.row.price_change_1d &&
         prevProps.row.gics_sector === nextProps.row.gics_sector &&
         prevProps.row.ibd_industry_group === nextProps.row.ibd_industry_group &&
         prevProps.row.ibd_group_rank === nextProps.row.ibd_group_rank &&
         prevProps.row.scan_mode === nextProps.row.scan_mode &&
         prevProps.row.data_status === nextProps.row.data_status &&
         prevProps.row.is_scannable === nextProps.row.is_scannable &&
         prevProps.row.composite_reason === nextProps.row.composite_reason &&
         (prevProps.row.market_themes || []).join('|') === (nextProps.row.market_themes || []).join('|') &&
         prevProps.row.rating === nextProps.row.rating &&
         prevProps.mcapDisplay === nextProps.mcapDisplay &&
         prevProps.showActions === nextProps.showActions &&
         prevProps.showWatchlistMenu === nextProps.showWatchlistMenu &&
         prevProps.chartEnabled === nextProps.chartEnabled;
});

/**
 * Display scan results in a sortable, paginated table with row virtualization
 * @param {Function} onRowHover - Optional callback when hovering over a row (for prefetching)
 */
function ResultsTable({
  results,
  total,
  page,
  perPage,
  sortBy,
  sortOrder,
  onPageChange,
  onPerPageChange,
  onSortChange,
  onOpenChart,
  loading,
  onRowHover,
  showActions = true,
  showWatchlistMenu = true,
  sortingEnabled = true,
  isChartEnabled,
}) {
  const parentRef = useRef(null);
  // MCap column display mode — kept as local state; scan-level persistence
  // can lift this up later if users want it to survive navigation.
  const [mcapDisplay, setMcapDisplay] = useState(MCAP_DISPLAY.USD);
  const visibleColumns = useMemo(() => {
    const base = columns.filter((column) => {
      if (!showActions && column.id === 'chart') return false;
      return !HIDDEN_SCAN_COLUMN_IDS.has(column.id);
    });
    return base.map((column) =>
      column.id === 'market_cap'
        ? { ...column, label: mcapDisplay === MCAP_DISPLAY.USD ? 'MCap ($)' : 'MCap (local)' }
        : column,
    );
  }, [showActions, mcapDisplay]);

  const tableMinWidth = useMemo(
    () => visibleColumns.reduce((total, column) => total + Number(column.width || 0), 0),
    [visibleColumns],
  );

  const toggleMcapDisplay = useCallback(() => {
    setMcapDisplay((mode) =>
      mode === MCAP_DISPLAY.USD ? MCAP_DISPLAY.LOCAL : MCAP_DISPLAY.USD,
    );
  }, []);

  const handleChangePage = useCallback((event, newPage) => {
    onPageChange(newPage + 1); // Material-UI uses 0-based pages, API uses 1-based
  }, [onPageChange]);

  const handleRequestSort = useCallback((property) => {
    const isAsc = sortBy === property && sortOrder === 'asc';
    onSortChange(property, isAsc ? 'desc' : 'asc');
  }, [sortBy, sortOrder, onSortChange]);

  const handleRowClick = useCallback((symbol) => {
    onOpenChart?.(symbol);
  }, [onOpenChart]);

  // Virtualize rows - only render visible rows plus overscan
  const rowVirtualizer = useVirtualizer({
    count: results?.length || 0,
    getScrollElement: () => parentRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 10, // Render 10 extra rows above/below viewport
  });

  // Memoize virtual items to prevent recalculation
  const virtualRows = rowVirtualizer.getVirtualItems();

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 5 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (!results || results.length === 0) {
    return (
      <Paper sx={{ p: 3, textAlign: 'center' }}>
        <Typography variant="body1" color="text.secondary">
          No results found
        </Typography>
      </Paper>
    );
  }

  return (
    <Paper elevation={1}>
      <Box sx={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', px: 2, py: 0.5, borderBottom: 1, borderColor: 'divider' }}>
        <Typography variant="caption" color="text.secondary" sx={{ mr: 1 }}>
          Market Cap display:
        </Typography>
        <Chip
          label={mcapDisplay === MCAP_DISPLAY.USD ? 'USD' : 'Local'}
          size="small"
          variant="outlined"
          onClick={toggleMcapDisplay}
          data-testid="mcap-display-toggle"
          sx={{ cursor: 'pointer', fontSize: 11, height: 20 }}
        />
      </Box>
      <TableContainer
        ref={parentRef}
        sx={{
          maxHeight: 'calc(100vh - 280px)',
          overflow: 'auto',
        }}
      >
        <Table stickyHeader size="small" sx={{ minWidth: tableMinWidth }}>
          <TableHead>
            <TableRow>
              {visibleColumns.map((column) => (
                <TableCell
                  key={column.id}
                  align={column.id === 'symbol' ? 'left' : 'center'}
                  sx={{
                    width: column.width,
                    minWidth: column.width,
                    maxWidth: column.width,
                    whiteSpace: 'nowrap',
                  }}
                >
                  {column.sortable && sortingEnabled ? (
                    <TableSortLabel
                      active={sortBy === column.id}
                      direction={sortBy === column.id ? sortOrder : 'asc'}
                      onClick={() => handleRequestSort(column.id)}
                    >
                      <ColumnHeaderLabel column={column} />
                    </TableSortLabel>
                  ) : (
                    <ColumnHeaderLabel column={column} />
                  )}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {/* Spacer for virtualization - pushes content down to correct position */}
            {virtualRows.length > 0 && virtualRows[0].start > 0 && (
              <tr style={{ height: virtualRows[0].start }} />
            )}
            {virtualRows.map((virtualRow) => {
              const row = results[virtualRow.index];
              return (
                <VirtualTableRow
                  key={row.symbol}
                  row={row}
                  onRowClick={onOpenChart ? handleRowClick : null}
                  onRowHover={onRowHover}
                  onOpenChart={onOpenChart}
                  showActions={showActions}
                  showWatchlistMenu={showWatchlistMenu}
                  chartEnabled={
                    row.is_scannable !== false &&
                    (isChartEnabled ? isChartEnabled(row.symbol) : Boolean(onOpenChart))
                  }
                  mcapDisplay={mcapDisplay}
                />
              );
            })}
            {/* Bottom spacer for virtualization */}
            {virtualRows.length > 0 && (
              <tr style={{ height: rowVirtualizer.getTotalSize() - (virtualRows[virtualRows.length - 1]?.end || 0) }} />
            )}
          </TableBody>
        </Table>
      </TableContainer>

      <TablePagination
        rowsPerPageOptions={[10, 25, 50, 100]}
        component="div"
        count={total}
        rowsPerPage={perPage}
        page={page - 1} // Material-UI uses 0-based pages
        onPageChange={handleChangePage}
        onRowsPerPageChange={(e) => {
          const nextPerPage = Number(e.target.value);
          onPerPageChange?.(nextPerPage);
          onPageChange(1); // Reset to first page when changing per-page
        }}
      />
    </Paper>
  );
}

// Wrap with React.memo for component-level memoization
export default memo(ResultsTable, (prevProps, nextProps) => {
  // Only re-render if these key props change
  return (
    prevProps.results === nextProps.results &&
    prevProps.total === nextProps.total &&
    prevProps.page === nextProps.page &&
    prevProps.perPage === nextProps.perPage &&
    prevProps.sortBy === nextProps.sortBy &&
    prevProps.sortOrder === nextProps.sortOrder &&
    prevProps.loading === nextProps.loading &&
    prevProps.showActions === nextProps.showActions &&
    prevProps.showWatchlistMenu === nextProps.showWatchlistMenu &&
    prevProps.isChartEnabled === nextProps.isChartEnabled &&
    prevProps.sortingEnabled === nextProps.sortingEnabled
  );
});
