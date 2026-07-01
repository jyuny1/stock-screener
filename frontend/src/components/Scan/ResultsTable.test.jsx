import { fireEvent, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '../../test/renderWithProviders';
import { fullSeRow, nullSeRow } from '../../test/fixtures/setupEngineFixtures';
import ResultsTable from './ResultsTable';

/*
 * Module mocks — required because:
 * - @tanstack/react-virtual: useVirtualizer depends on DOM layout measurements
 *   (getBoundingClientRect, scrollHeight) that jsdom doesn't implement -> zero rows render
 * - RSSparkline/PriceSparkline: Recharts ResponsiveContainer requires real DOM dimensions
 * - AddToWatchlistMenu: uses React Query (QueryClientProvider) which is not needed here
 */
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count }) => ({
    getVirtualItems: () =>
      Array.from({ length: count }, (_, i) => ({
        index: i,
        start: i * 32,
        end: (i + 1) * 32,
        size: 32,
        key: i,
      })),
    getTotalSize: () => count * 32,
  }),
}));
vi.mock('./RSSparkline', () => ({
  default: ({ data, trend, tooltipLabel = 'RS', disableTooltip = false }) => (
    <span data-testid="rs-sparkline">{tooltipLabel}:{data?.length ?? 'none'}:{trend}:{disableTooltip ? 'no-tip' : 'tip'}</span>
  ),
}));
vi.mock('./PriceSparkline', () => ({
  default: ({ data, trend, change1d }) => (
    <span data-testid="price-sparkline">Price:{data?.length ?? 'none'}:{trend}:{change1d}</span>
  ),
}));
vi.mock('../common/AddToWatchlistMenu', () => ({ default: () => null }));

/** Default props for a basic render — 1 row, page 1. */
const defaultProps = {
  results: [fullSeRow],
  total: 1,
  page: 1,
  perPage: 25,
  sortBy: 'composite_score',
  sortOrder: 'desc',
  onPageChange: vi.fn(),
  onPerPageChange: vi.fn(),
  onSortChange: vi.fn(),
  onOpenChart: vi.fn(),
  loading: false,
};

describe('ResultsTable', () => {
  // ── 12-column screening table rendering ─────────────────────────────
  describe('12-column screening table rendering', () => {
    const screeningRow = {
      ...fullSeRow,
      rs_sparkline_data: Array.from({ length: 30 }, (_, index) => 1 + index / 100),
      rs_trend: 1,
      price_sparkline_data: Array.from({ length: 30 }, (_, index) => 1 + index / 200),
      price_trend: 1,
      price_change_1d: 1.23,
      adr_percent: 3.4,
    };

    beforeEach(() => {
      renderWithProviders(<ResultsTable {...defaultProps} results={[screeningRow]} />);
    });

    it('renders price and trend sparkline columns', () => {
      expect(screen.getByText('RS:30:1:tip')).toBeInTheDocument();
      expect(screen.getByText('Price:30:1:1.23')).toBeInTheDocument();
    });

    it('renders core retained numeric fields', () => {
      expect(screen.getByText('92')).toBeInTheDocument();
      expect(screen.getByText('3.4%')).toBeInTheDocument();
    });

    it('renders MA boolean and hides setup-specific values', () => {
      expect(screen.getAllByTestId('CheckIcon').length).toBe(1);
      expect(screen.queryByText('-3.2%')).not.toBeInTheDocument();
      expect(screen.queryByText('1.8x')).not.toBeInTheDocument();
      expect(screen.queryByText('78.3')).not.toBeInTheDocument();
      expect(screen.queryByText('cup_with_handle')).not.toBeInTheDocument();
      expect(screen.queryByText('$198.50')).not.toBeInTheDocument();
    });
  });

  describe('young IPO partial metrics', () => {
    it('renders calculable short-history values while long-history values stay blank', () => {
      const youngIpoRow = {
        ...nullSeRow,
        symbol: 'NEWIPO',
        company_name: 'New IPO Inc.',
        composite_score: null,
        rating: 'Insufficient Data',
        data_status: 'insufficient_history',
        scan_mode: 'listing_only',
        is_scannable: false,
        history_bars: 45,
        rs_sparkline_data: Array.from({ length: 30 }, (_, index) => 1 + index / 100),
        rs_trend: 1,
        price_sparkline_data: Array.from({ length: 30 }, (_, index) => 1 + index / 200),
        price_trend: 1,
        price_change_1d: 2.5,
        adr_percent: 10.0,
        rs_rating_1m: 50.0,
        rs_rating: null,
        rs_rating_3m: null,
        rs_rating_12m: null,
        stage: null,
        ma_alignment: null,
      };

      renderWithProviders(<ResultsTable {...defaultProps} results={[youngIpoRow]} />);

      expect(screen.getByText('New IPO')).toBeInTheDocument();
      expect(screen.getByText('RS:30:1:tip')).toBeInTheDocument();
      expect(screen.getByText('Price:30:1:2.5')).toBeInTheDocument();
      expect(screen.queryByText('50')).not.toBeInTheDocument();
      expect(screen.getByText('10.0%')).toBeInTheDocument();
      expect(screen.queryByText('S2')).not.toBeInTheDocument();
    });
  });

  // ── curated scan columns ────────────────────────────────────────────
  describe('curated scan columns', () => {
    it('renders the 12 selected header labels and hides removed labels', () => {
      renderWithProviders(<ResultsTable {...defaultProps} />);
      ['Sym', 'Price', 'Vol', 'ADV ($)', 'Price Trend', 'RS Trend', 'RS', 'ADR', 'MA', 'MCap ($)', 'Sector', 'IBD Industry'].forEach((label) => {
        expect(screen.getByText(label)).toBeInTheDocument();
      });
      ['Pvt%', 'V50', 'RSH', 'SE', 'Pat', 'Sqz', 'Pvt$', 'Themes', '1M', '3M', '12M', 'β', 'EPS'].forEach((label) => {
        expect(screen.queryByText(label)).not.toBeInTheDocument();
      });
    });

    it('renders a readable IBD industry group column', () => {
      renderWithProviders(
        <ResultsTable
          {...defaultProps}
          results={[{ ...fullSeRow, ibd_industry_group: 'Semiconductors' }]}
        />
      );

      expect(screen.getByText('IBD Industry')).toBeInTheDocument();
      expect(screen.getByText('Semiconductors')).toBeInTheDocument();
    });

    it('does not render the hidden market themes column', () => {
      renderWithProviders(
        <ResultsTable
          {...defaultProps}
          results={[{
            ...fullSeRow,
            ibd_industry_group: 'Semiconductors',
            market_themes: ['AI Infrastructure', 'Foundry'],
          }]}
        />
      );

      expect(screen.queryByText('Themes')).not.toBeInTheDocument();
      expect(screen.queryByText('AI Infrastructure')).not.toBeInTheDocument();
      expect(screen.queryByText('+1')).not.toBeInTheDocument();
    });

    it('renders bucketed PCR 30D trend cell', () => {
      const dates = ['2026-06-24', '2026-06-25', '2026-06-26', '2026-06-27', '2026-06-28', '2026-06-29', '2026-06-30'];
      renderWithProviders(
        <ResultsTable
          {...defaultProps}
          results={[{
            ...fullSeRow,
            option_pcr_trend_30d: {
              dates,
              dte0_90_total: { current: 1.73, previous30d: 1.45, change30d: 0.28, history: [1.45, 1.5, 1.6, 1.73] },
              dte0_30: { current: 1.91, previous30d: 1.42, change30d: 0.49, history: [1.42, 1.6, 1.91] },
              dte31_60: { current: 1.35, previous30d: 1.32, change30d: 0.03, history: [1.32, 1.34, 1.35] },
              dte61_90: { current: 1.12, previous30d: 1.30, change30d: -0.18, history: [1.30, 1.2, 1.12] },
            },
            option_put_liquidity_history_dates: dates,
          }]}
        />
      );

      expect(screen.getByText('90D')).toBeInTheDocument();
      expect(screen.getByText('1.73')).toBeInTheDocument();
      expect(screen.getByText('0-30')).toBeInTheDocument();
      expect(screen.getByText('31-60')).toBeInTheDocument();
      expect(screen.getByText('61-90')).toBeInTheDocument();
    });
  });

  // ── structural ───────────────────────────────────────────────────────
  describe('structural', () => {
    it('shows "No results found" when results is empty', () => {
      renderWithProviders(
        <ResultsTable {...defaultProps} results={[]} total={0} />
      );
      expect(screen.getByText('No results found')).toBeInTheDocument();
    });

    it('shows loading spinner when loading=true', () => {
      renderWithProviders(
        <ResultsTable {...defaultProps} loading={true} />
      );
      expect(screen.getByRole('progressbar')).toBeInTheDocument();
    });

    it('renders pagination controls', () => {
      renderWithProviders(
        <ResultsTable {...defaultProps} results={[fullSeRow]} total={50} />
      );
      // MUI TablePagination renders "Rows per page:" text
      expect(screen.getByText(/rows per page/i)).toBeInTheDocument();
    });

    it('rerenders when showActions changes so the action column is removed', () => {
      const results = [fullSeRow];
      const { rerender } = renderWithProviders(
        <ResultsTable {...defaultProps} results={results} showActions={true} />
      );

      expect(screen.getByTestId('ShowChartIcon')).toBeInTheDocument();

      rerender(<ResultsTable {...defaultProps} results={results} showActions={false} />);

      expect(screen.queryByTestId('ShowChartIcon')).not.toBeInTheDocument();
    });

    it('keeps the action column but hides the chart button when a row is not chart-enabled', () => {
      renderWithProviders(
        <ResultsTable
          {...defaultProps}
          results={[fullSeRow]}
          showActions={true}
          showWatchlistMenu={false}
          isChartEnabled={() => false}
        />
      );

      expect(screen.queryByTestId('ShowChartIcon')).not.toBeInTheDocument();
      expect(screen.getByText('FULL')).toBeInTheDocument();
    });

    it('renders a young-IPO status chip and suppresses chart actions for non-scannable rows', () => {
      renderWithProviders(
        <ResultsTable
          {...defaultProps}
          results={[{
            ...fullSeRow,
            symbol: '0100.HK',
            company_name: 'MINIMAX-W',
            scan_mode: 'listing_only',
            data_status: 'insufficient_history',
            is_scannable: false,
          }]}
          showActions={true}
          showWatchlistMenu={false}
          isChartEnabled={() => true}
        />
      );

      expect(screen.getByText('New IPO')).toBeInTheDocument();
      expect(screen.queryByTestId('ShowChartIcon')).not.toBeInTheDocument();
    });

    it('does not relabel generic error rows as young IPOs', () => {
      renderWithProviders(
        <ResultsTable
          {...defaultProps}
          results={[{
            ...fullSeRow,
            symbol: 'BROKEN',
            rating: 'Error',
            scan_mode: 'listing_only',
            data_status: 'error',
            is_scannable: false,
          }]}
          showActions={true}
          showWatchlistMenu={false}
          isChartEnabled={() => true}
        />
      );

      expect(screen.queryByText('New IPO')).not.toBeInTheDocument();
      expect(screen.queryByText('Error')).not.toBeInTheDocument();
      expect(screen.queryByTestId('ShowChartIcon')).not.toBeInTheDocument();
    });
  });

  // ── interactions ─────────────────────────────────────────────────────
  describe('interactions', () => {
    it('calls onOpenChart when row is clicked', async () => {
      const onOpenChart = vi.fn();
      renderWithProviders(
        <ResultsTable {...defaultProps} onOpenChart={onOpenChart} results={[fullSeRow]} />
      );

      fireEvent.click(screen.getByText('FULL'));
      expect(onOpenChart).toHaveBeenCalledWith('FULL');
    });

    it('calls onSortChange when a sortable header is clicked', async () => {
      const onSortChange = vi.fn();
      renderWithProviders(
        <ResultsTable {...defaultProps} onSortChange={onSortChange} />
      );

      const user = userEvent.setup();
      await user.click(screen.getByText('Vol'));
      expect(onSortChange).toHaveBeenCalledWith('volume', 'asc');
    });

    it('toggles sort direction when same header is clicked twice', async () => {
      const onSortChange = vi.fn();
      // Start sorted by a retained column asc
      renderWithProviders(
        <ResultsTable
          {...defaultProps}
          sortBy="volume"
          sortOrder="asc"
          onSortChange={onSortChange}
        />
      );

      const user = userEvent.setup();
      await user.click(screen.getByText('Vol'));
      // Since current is asc, clicking again should flip to desc
      expect(onSortChange).toHaveBeenCalledWith('volume', 'desc');
    });
  });
});
