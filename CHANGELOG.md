# Changelog

Every release is one `## [x.y.z]` section; the section is the release notes. The version is the file `VERSION`.

## [0.11.1] — unreleased

- Only production pulls market data. Staging reads production's database, copied over staging's every day after the
  close by a job on the host, so one set of keys is spent once. Staging's worker registers no pull job, and
  production takes each source's whole free limit again (it took 80 % while staging pulled too). GDELT's two
  environments no longer need different minutes.

## [0.11.0] — unreleased

- Market holidays and early closes. The app reads the exchange calendar (Alpaca's, a year ahead, refreshed every
  morning) instead of treating every weekday as a trading day. On a holiday (the next is Thanksgiving, 2026-11-26)
  the scan, the AI trader, practice fills, "Invest this" and the brief do nothing and say why. A plan for a closed
  day moves to the next session. Practice day orders expire, and fills stop, at the real close, 10:00 PT on an
  early-close day. Sale proceeds settle on the exchange's settlement date, so a sale on 11-25 settles on 11-27.
  Paper trades open at the next real session, and the value-over-time chart has no point on a holiday.

## [0.10.1] — unreleased

- Fixed: a data source's key could be written into the pull log and the worker's log. A failed call's error text
  named the whole request address, and FMP, Finnhub, Massive and FRED carried their keys in it (FMP's key reached
  staging's pull log on 2026-10-30, the first day with real keys; those rows are masked). FMP, Finnhub and Massive
  now get their keys in a request header. FRED accepts its key only in the address, so a failed call now records
  only the source, the path, the status and the source's own reason, and the HTTP library's per-request log lines
  are off.
- FMP's free plan gives at most 5 quarters of earnings history, and not for every stock. The app now asks for 5, and
  only for your holdings and watchlist, so FMP's 250 calls a day go to your names. The scan list keeps Finnhub's
  earnings calendar.
- Staging and production use the same data keys, so each now takes a fixed share of every free limit: production
  80 %, staging 20 %. A job now waits out a full minute instead of dropping the rest of its list. A day's limit is
  never waited for.

## [0.10.0] — unreleased

- GDELT news tone now arrives. GDELT's real limit is much stricter than the "one request every 5 seconds" it
  publishes: it refused even our first request in 46 minutes, for about 12 minutes. The tone is now its own job: at
  most one topic per run, every 30 minutes from midnight to 04:30 PT, never a retry inside a run, at most one call a
  minute and 12 a day. Staging asks 15 minutes after production, because GDELT sees both as one address. One answer
  holds a week of days, so a refused night fills itself in.
- Fixed: two option contracts on one stock (say a 100 put and a 90 put) shared one queue of lots, so closing one
  could book the other's premium. Each contract now closes against its own lots.
- Fixed: on a chart, a daily bar sat under the previous day's label west of New York (bars are stamped at New York
  midnight). Bars and value-over-time points now sit on their own trading day in every time zone.
- The S&P 500 line on the return charts is gray: green and red mean gain and loss everywhere else in the app.
  A holding's row labels its open P&L beside today's.

## [0.9.0] — unreleased

- Day P&L: the portfolio shows each position's and each book's change since the previous session's close. A share
  bought in the session counts from its fill, a share sold in it up to its fill. A position with no close before its
  session is left out of the book's day P&L, and the screen says so.
- A position screen: tap a holding in the portfolio or the history to see shares, average cost, the price with its
  source and time, day, open, closed and total P&L, the thesis it was bought on (the idea's reason, stop and target),
  Ghostline's latest call, recent news and the next earnings date.
- Value over time: each book's time-weighted return beside SPY's return over the same days, on the portfolio screen.
- A closed trade in the history opens the idea and the memo behind it. A sale by hand keeps the idea its shares were
  bought on.
- Earnings show how the stock moved around each past report: the close before it to the close after it.
- The worker stores option chains every weekday at 06:12 PT, before the scan, for every stock held, watched or in an
  open long-term idea: the expiry 30 to 45 days out nearest 38 days. The sell-puts module reads only stored chains.
- The morning macro pull adds GDELT's daily world news tone on three topics, one request every 6 seconds (GDELT asks
  for at most one every 5). The brief reads it with the other macro numbers.
- Fixed: a date with no time, such as an earnings day, showed one day early west of Greenwich.

## [0.8.0] — unreleased

- The journal and the learning loop. Every idea the scan makes is paper-traded at the next open and settled at the
  next open after its horizon, against SPY over the same sessions, after estimated costs (10 basis points a fill, a
  setting); put ideas settle on the premium kept against their collateral. An idea with no stored price is marked
  unpriced, never settled on a guess. Each module's weight follows its edge over SPY, pulled toward equal weight until
  it has 20 settled ideas, and the scan multiplies its scores by that weight. A module's ideas show as Live, and a lane
  opens "copy with real money", only after 30 settled ideas (a setting) with a positive edge.
- Claude writes a one-paragraph lesson per settled idea, grounded like every memo; deep dives, chat and the brief
  read the last twenty lessons. A weekly test set (Sunday 07:00 PT) asks Claude up to twelve questions whose answers
  are stored numbers and scores the answers. A weekly digest (Sunday 07:30 PT) is written in code from stored numbers
  and pushed in production. A Journal screen shows all of it.

## [0.7.0] — unreleased

- Invest this: any amount and a date, split across the current long-term and swing ideas (the best score takes the
  biggest share), in fractional shares, every split through the risk rules against the book the money goes into; a
  refused split shows its rule and its money moves to the splits that pass. Approved into practice (deposited, then
  placed at the open of its date) or onto a to-do list for real money; "I Placed It" opens the trade form filled in.
- The AI trader: a practice book funded with any amount. It takes the app's open ideas, each sized from the rules as a
  share of its own balance, and exits at the idea's stop, target or horizon; every trade carries its idea and reason.
  Its S&P 500 ghost buys SPY with the same deposits on the same dates; both are compared by time-weighted return.
  Copy repeats a trade in practice at the same share of the balance; for real money only once its lane graduates.
- Options practice on any amount: its own book; each weekday it sells the put ideas the amount can cover.
- Price alerts (above, below, a day's move, crossing a moving average, earnings coming), checked every half hour in
  the session and pushed once. Monthly statements per book as JSON, CSV and PDF (dividends are not tracked yet).

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
