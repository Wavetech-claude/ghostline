# tradermonty/claude-trading-skills — the methods Ghostline turned into code

- Repository: https://github.com/tradermonty/claude-trading-skills (MIT, 3,463 stars, pushed 2026-10-26)
- Commit read and copied from: `c9b84196a44a56a553293114fdae1f30bdd08c58` (2026-10-26)
- Read on 2026-10-30 (the five checks of `requirements.md` § borrowed skills): every SKILL.md of the eleven skills
  below plus these methodology references, line by line. They are prompt workflows around Python scripts that
  call FMP and yfinance; none asks for shell, file or network access beyond those data calls, and none pipes a
  download into a shell. The scripts are NOT copied: they depend on FMP endpoints and paid tiers Ghostline does not
  use. The methods are re-implemented in `ghostline/modules/*.py` from the references in this folder, which are
  copied unchanged with the licence.
- Skills read: vcp-screener, canslim-screener, pead-screener, earnings-trade-analyzer, macro-regime-detector,
  position-sizer, drawdown-circuit-breaker, pre-trade-discipline-gate, options-strategy-advisor, signal-postmortem,
  trade-performance-coach.
- Snyk agent-scan: not run (it needs a Snyk account to confirm; nothing here is executed, only read).
