# Changelog

Every release is one `## [x.y.z]` section; the section is the release notes. The version is the file `VERSION`.

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
