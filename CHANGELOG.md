# Changelog

Every release is one `## [x.y.z]` section; the section is the release notes. The version is the file `VERSION`.

## [0.6.0] — unreleased

- Claude in the worker: headless `claude -p` on the subscription token, on an allow-listed environment, never an
  API key. Jobs queue in the database and the worker drains them one at a time; inside a job up to four calls run
  at once. "Should I buy?" on any ticker: four analysts (fundamentals, technical, news, macro) read one bundle of
  stored numbers, a bull and a bear argue, a judge decides Buy, Wait or Avoid, sized from the owner's rules, with
  what would change the call. Every number in a memo, a chat answer or the brief must match a stored value; any
  other number is struck and shown as struck, the judge gets one retry, and a buy on an unstored number becomes a
  wait. Follow-up chat per ticker. The morning brief at 05:30 PT on weekdays, pushed to the apps in production.
  "Read It and Fill the Form" on a confirmation screenshot or PDF: Claude may read that one file and nothing else.
  Not wired: Langfuse tracing (the worker cannot reach it with certificate checking; see the build report).

## [0.5.0] — unreleased

- Ideas and modules: the borrowed methods are code (`backend/ghostline/modules`, their sources and licences in
  `modules/vendor`): Minervini's VCP, O'Neil's CANSLIM, post-earnings drift, the earnings-reaction score, the
  macro regime from cross-asset ratios and the yield curve, and cash-secured puts rechecked with Black–Scholes.
  Every module writes the same signal record with its reason and evidence, and names what it could not measure.
  The weekday scan (06:20 PT) reads one Massive grouped pull for the day's movers plus the tracked tickers, and
  turns actionable signals into ideas in three lanes (long term, swings, sell puts), sized to the balance through
  the risk rules; a refused idea stays visible with the rule and reason. An Ideas tab with the lanes, each idea's
  levels and evidence, and a way into practice. History moved under Portfolio; Settings under Account on iPhone.
  Not built: the dual-listed gap (NSE data).

## [0.4.0] — unreleased

- The practice account: order tickets (market, limit, stop, stop-limit; day or good-till-cancelled) filled at the
  real bid and ask from the stored quotes, next-business-day settlement in a cash account (unsettled proceeds cannot
  buy), cash-secured puts with their collateral locked, assignment or expiry from the stored price, a fee per order
  as a setting (default 0). Every opening order goes through the risk rules and a refusal names the rule; closing
  orders only need the position. Option expirations and chains from Tradier with the collateral each put needs and
  whether the book covers it. Screens: Practice account, the order ticket, the options chain.

## [0.3.0] — unreleased

- Market data: the worker pulls daily bars, delayed quotes, news, earnings and SEC filings for every holding and
  watched ticker from the sources whose keys are set (Tradier, Alpaca, Finnhub, FMP, Massive, FRED, EDGAR), inside
  each source's free-tier limit, and stores every row with its source and as-of time. Charts with candles or a line,
  timeframes from a week to five years, volume, SMA 20/50/200, RSI, MACD, Bollinger bands and support and resistance
  from pivot clusters. A Watchlist tab: any ticker, its latest quote, its chart, news, earnings and filings.

## [0.2.0] — unreleased

- Account and ledger: the four books (Real, Practice, AI trader, S&P 500 ghost), deposits and withdrawals of any
  amount at any time, trades with fractional quantities and fees, option legs, confirmation screenshots and PDFs
  stored privately with the trade, history with filters and totals (win rate, average win and loss, net P&L after
  fees), the portfolio with every price's source and time, and the risk rules the owner sets in Settings: shares of
  the balance, counts and switches, never a dollar amount; every change logged; until they are saved, no idea passes.

## [0.1.0] — unreleased

- The skeleton: `/health`, `/version`, Sign in with Apple for one account, the first migration, and the iPhone and
  Mac app that signs in and shows the backend's version.
