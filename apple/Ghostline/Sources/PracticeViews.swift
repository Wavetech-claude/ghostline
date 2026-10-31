import GhostlineKit
import SwiftUI

/// The practice account: what it can spend right now, the puts it has sold and their collateral, open orders, and
/// every order with how it ended. Pretend money, real prices.
struct PracticeAccountView: View {
    @Environment(PracticeModel.self) private var practice

    var body: some View {
        List {
            if let a = practice.account {
                Section {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Available to Spend").font(.caption).foregroundStyle(.secondary)
                        Text(Show.money(a.available))
                            .font(.system(.largeTitle, design: .rounded, weight: .semibold)).monospacedDigit()
                        Text("Cash \(Show.money(a.cash)) · unsettled \(Show.money(a.unsettled)) · collateral \(Show.money(a.lockedCollateral))")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 4)
                    .accessibilityElement(children: .combine)
                    LabeledContent("Equity", value: Show.money(a.equity))
                    LabeledContent("Open Positions", value: "\(a.openPositions)")
                    LabeledContent("Drawdown From Peak", value: Show.percentOfOne(a.paperDrawdownShare))
                    LabeledContent("Fee per Order", value: Show.money(a.feePerOrder))
                }
                if !a.shortPuts.isEmpty {
                    Section("Puts Sold") {
                        ForEach(a.shortPuts) { p in
                            VStack(alignment: .leading, spacing: 2) {
                                Text("\(p.underlying ?? "?") \(Show.money(p.strike)) put · \(Show.quantity(p.contracts)) contract\(p.contracts == "1" ? "" : "s")")
                                Text("expires \(p.expiry ?? "?") · collateral \(Show.money(p.collateral))")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                if !a.pendingSettlements.isEmpty {
                    Section("Settling") {
                        ForEach(a.pendingSettlements) { s in
                            LabeledContent(Show.money(s.amount), value: "usable \(Stamp.short(s.settlesAt))")
                        }
                    }
                }
            } else if let error = practice.error {
                Text(error).foregroundStyle(.secondary)
            } else {
                ProgressView()
            }
            Section("Orders") {
                ForEach(practice.orders) { o in
                    OrderRowView(order: o)
                        .swipeActions {
                            if o.isOpen {
                                Button("Cancel", role: .destructive) { Task { await practice.cancel(o) } }
                            }
                        }
                }
                if practice.orders.isEmpty {
                    Text("No practice orders yet. Place one with the ticket, or from a ticker in Watchlist.")
                        .foregroundStyle(.secondary)
                }
            }
        }
        .navigationTitle("Practice")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button { practice.openTicket() } label: { Label("New Order", systemImage: "plus") }
                    .keyboardShortcut("n", modifiers: [.command, .option])
            }
        }
        .task { await practice.load() }
        .refreshable { await practice.load() }
    }
}

struct OrderRowView: View {
    let order: OrderRow

    private var title: String {
        let what = order.optionSymbol ?? order.ticker
        let side = order.side.replacingOccurrences(of: "_", with: " ")
        return "\(side.capitalized) \(Show.quantity(order.quantity)) \(what)"
    }

    private var detail: String {
        var parts = [order.type.replacingOccurrences(of: "_", with: "-"), order.tif.uppercased()]
        if let l = order.limitPrice { parts.append("limit \(Show.money(l))") }
        if let s = order.stopPrice { parts.append("stop \(Show.money(s))") }
        if let f = order.fillPrice { parts.append("filled at \(Show.money(f))") }
        return parts.joined(separator: " · ")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(title).font(.body)
                Spacer()
                Text(order.status.capitalized).font(.caption).foregroundStyle(tint)
            }
            Text(detail).font(.caption).foregroundStyle(.secondary)
            if let reason = order.reason, order.isRefused || order.status == "expired" {
                Text(reason).font(.caption2).foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private var tint: Color {
        switch order.status {
        case "filled": .gain
        case "refused": .loss
        case "open": .blue
        default: .secondary
        }
    }
}

/// The order ticket: market, limit, stop and stop-limit; day or good-till-cancelled; a stop loss for the risk
/// check; or a cash-secured put from the chain. The answer names the fill, or the rule that refused it.
struct OrderTicketView: View {
    @Environment(PracticeModel.self) private var practice
    @Environment(\.dismiss) private var dismiss
    @State private var book = "practice"
    @State private var asset = "stock"
    @State private var side = "buy"
    @State private var type = "market"
    @State private var tif = "day"
    @State private var ticker = ""
    @State private var optionSymbol = ""
    @State private var quantity = ""
    @State private var limitPrice = ""
    @State private var stopPrice = ""
    @State private var stopLoss = ""
    @State private var note = ""
    @State private var outcome: OrderRow?
    @State private var problem: String?

    private var sides: [(String, String)] {
        asset == "stock" ? [("buy", "Buy"), ("sell", "Sell")]
                         : [("sell_to_open", "Sell Put to Open"), ("buy_to_close", "Buy Put to Close")]
    }

    private var valid: Bool {
        (asset == "stock" ? !ticker.trimmingCharacters(in: .whitespaces).isEmpty : !optionSymbol.isEmpty)
            && (Decimal(string: quantity).map { $0 > 0 } ?? false)
            && (!["limit", "stop_limit"].contains(type) || (Decimal(string: limitPrice).map { $0 > 0 } ?? false))
            && (!["stop", "stop_limit"].contains(type) || (Decimal(string: stopPrice).map { $0 > 0 } ?? false))
    }

    var body: some View {
        NavigationStack {
            Form {
                if let outcome {
                    Section {
                        OrderOutcome(order: outcome)
                    }
                }
                Section {
                    Picker("Book", selection: $book) {
                        Text("Practice").tag("practice")
                        Text("AI Trader").tag("ai")
                    }
                    Picker("Asset", selection: $asset) {
                        Text("Stock").tag("stock")
                        Text("Put").tag("option")
                    }
                    .pickerStyle(.segmented)
                    .onChange(of: asset) { side = sides[0].0 }
                    Picker("Side", selection: $side) {
                        ForEach(sides, id: \.0) { Text($0.1).tag($0.0) }
                    }
                }
                Section {
                    if asset == "stock" {
                        TextField("Ticker", text: $ticker)
                            #if os(iOS)
                            .textInputAutocapitalization(.characters)
                            #endif
                            .autocorrectionDisabled()
                    } else {
                        TextField("Option symbol (OCC)", text: $optionSymbol).autocorrectionDisabled()
                    }
                    numberField(asset == "stock" ? "Shares (fractions allowed)" : "Contracts", $quantity)
                    Picker("Order", selection: $type) {
                        Text("Market").tag("market")
                        Text("Limit").tag("limit")
                        Text("Stop").tag("stop")
                        Text("Stop Limit").tag("stop_limit")
                    }
                    if ["limit", "stop_limit"].contains(type) { numberField("Limit price", $limitPrice) }
                    if ["stop", "stop_limit"].contains(type) { numberField("Stop price", $stopPrice) }
                    Picker("Time in Force", selection: $tif) {
                        Text("Day").tag("day")
                        Text("Good Till Cancelled").tag("gtc")
                    }
                }
                if side == "buy" || side == "sell_to_open" {
                    Section {
                        numberField(asset == "stock" ? "Stop loss (price)" : "Stop loss (underlying price)", $stopLoss)
                    } footer: {
                        Text("The risk check measures the worst case to this price. Without it, the whole position is the worst case.")
                    }
                }
                Section {
                    TextField("Note", text: $note, axis: .vertical)
                }
                if let problem { Text(problem).foregroundStyle(.loss).font(.footnote) }
            }
            .formStyle(.grouped)
            .navigationTitle(asset == "stock" ? "Practice Order" : "Practice Put")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button(outcome == nil ? "Cancel" : "Done") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Place") { Task { await place() } }.disabled(!valid || practice.busy)
                }
            }
            .onAppear(perform: prefill)
        }
    }

    private func prefill() {
        if let o = practice.ticketOption {
            asset = "option"
            side = "sell_to_open"
            optionSymbol = o.optionSymbol
            ticker = o.underlying
            quantity = quantity.isEmpty ? "1" : quantity
        } else if !practice.ticketTicker.isEmpty {
            ticker = practice.ticketTicker
        }
    }

    private func numberField(_ label: String, _ text: Binding<String>) -> some View {
        LabeledContent(label) {
            TextField("0", text: text)
                .multilineTextAlignment(.trailing)
                #if os(iOS)
                .keyboardType(.decimalPad)
                #endif
        }
    }

    private func place() async {
        let input = OrderInput(book: book, ticker: ticker.trimmingCharacters(in: .whitespaces).uppercased(), asset: asset,
                               side: side, type: type, tif: tif, quantity: quantity,
                               limitPrice: ["limit", "stop_limit"].contains(type) ? limitPrice : nil,
                               stopPrice: ["stop", "stop_limit"].contains(type) ? stopPrice : nil,
                               stopLoss: stopLoss.isEmpty ? nil : stopLoss,
                               optionSymbol: asset == "option" ? optionSymbol.uppercased() : nil,
                               note: note.isEmpty ? nil : note)
        if let row = await practice.place(input) {
            outcome = row
            problem = nil
        } else {
            problem = practice.error
        }
    }
}

/// What the backend answered: a fill with its price, an order waiting for the quote, or a refusal naming the rule.
struct OrderOutcome: View {
    let order: OrderRow

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            switch order.status {
            case "filled":
                Label("Filled at \(Show.money(order.fillPrice))", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.gain)
            case "open":
                Label("Open, waiting for the quote", systemImage: "clock")
                    .foregroundStyle(.blue)
            default:
                Label("Refused", systemImage: "hand.raised.fill").foregroundStyle(.loss)
            }
            if let reason = order.reason { Text(reason).font(.footnote).foregroundStyle(.secondary) }
        }
        .accessibilityElement(children: .combine)
    }
}

/// The option chain for one ticker and expiry: every put with its bid, ask, open interest, delta and the collateral
/// it needs, marked when the practice account covers it. Tap a put to sell it in practice.
struct OptionsChainView: View {
    @Environment(SessionModel.self) private var session
    @Environment(PracticeModel.self) private var practice
    let ticker: String
    @State private var expirations: Expirations?
    @State private var expiry = ""
    @State private var chain: OptionChain?
    @State private var error: String?
    @State private var showCalls = false

    var body: some View {
        List {
            if let ex = expirations, !ex.configured {
                Text(ex.reason ?? "Option chains need the Tradier key.").foregroundStyle(.secondary)
            } else if let ex = expirations {
                Section {
                    Picker("Expiration", selection: $expiry) {
                        ForEach(ex.expirations, id: \.self) { Text(Stamp.day($0)).tag($0) }
                    }
                    if let chain {
                        LabeledContent("Available for Collateral", value: Show.money(chain.available))
                        Text("\(chain.source) · as of \(Stamp.short(chain.asOf))").font(.caption2).foregroundStyle(.tertiary)
                    }
                }
                if let chain {
                    Section(showCalls ? "Calls" : "Puts") {
                        ForEach(showCalls ? chain.calls : chain.puts) { o in
                            Button { if o.kind == "put" { practice.openTicket(ticker: ticker, option: o) } } label: {
                                OptionRowView(option: o)
                            }
                            .buttonStyle(.plain)
                            .disabled(o.kind != "put")
                        }
                    }
                }
            } else if let error {
                Text(error).foregroundStyle(.secondary)
            } else {
                ProgressView()
            }
        }
        .navigationTitle("\(ticker) Options")
        .toolbar {
            ToolbarItem(placement: .secondaryAction) {
                Toggle("Show Calls", isOn: $showCalls)
            }
        }
        .task { await loadExpirations() }
        .task(id: expiry) { await loadChain() }
    }

    private func loadExpirations() async {
        do {
            let ex = try await session.client.expirations(ticker: ticker)
            expirations = ex
            if expiry.isEmpty, let first = ex.expirations.first { expiry = first }
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }

    private func loadChain() async {
        guard !expiry.isEmpty else { return }
        do {
            chain = try await session.client.chain(ticker: ticker, expiry: expiry)
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }
}

struct OptionRowView: View {
    let option: OptionRow

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text("\(Show.money(option.strike)) \(option.kind)").font(.headline)
                Spacer()
                Text("\(Show.money(option.bid)) / \(Show.money(option.ask))").monospacedDigit()
            }
            HStack {
                Text("OI \(option.openInterest ?? "—") · Δ \(option.delta ?? "—") · IV \(Show.percentOfOne(option.iv))")
                Spacer()
                if let c = option.collateral {
                    Label(Show.money(c), systemImage: option.covered ? "checkmark.shield" : "xmark.shield")
                        .foregroundStyle(option.covered ? Color.gain : Color.secondary)
                }
            }
            .font(.caption).foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
        .accessibilityHint(option.kind == "put" ? "Sell this put in practice" : "")
    }
}
