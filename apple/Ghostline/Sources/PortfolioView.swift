import GhostlineKit
import SwiftUI

/// Portfolio: total value, each book's cash and positions, every price with its source and time.
struct PortfolioView: View {
    @Environment(LedgerModel.self) private var ledger

    var body: some View {
        List {
            if let p = ledger.portfolio {
                Section {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Total").font(.caption).foregroundStyle(.secondary)
                        Text(Show.money(p.total)).font(.system(.largeTitle, design: .rounded, weight: .semibold)).monospacedDigit()
                        if let note = p.note { Text(note).font(.footnote).foregroundStyle(.secondary) }
                    }
                    .padding(.vertical, 4)
                }
                ForEach(p.books) { book in
                    Section(book.name) {
                        LabeledContent("Cash", value: Show.money(book.cash))
                        LabeledContent("Invested", value: Show.money(book.invested))
                        LabeledContent("Total", value: Show.money(book.total))
                        if let pnl = book.totalPnl {
                            LabeledContent("Total P&L") {
                                Text(Show.signedMoney(pnl)).foregroundStyle(tint(pnl))
                            }
                        }
                        ForEach(book.positions) { PositionRow(position: $0) }
                        if !book.allocation.isEmpty {
                            HStack(spacing: 8) {
                                ForEach(book.allocation) { a in
                                    Text("\(a.name) \(Show.percentOfOne(a.share))")
                                        .font(.caption)
                                        .padding(.horizontal, 8).padding(.vertical, 4)
                                        .background(.fill.tertiary, in: .capsule)
                                }
                            }
                        }
                    }
                }
            } else if let error = ledger.error {
                Text(error).foregroundStyle(.secondary)
            } else {
                ProgressView()
            }
        }
        .navigationTitle("Portfolio")
        .task { await ledger.loadPortfolio() }
        .refreshable { await ledger.loadPortfolio() }
    }

    private func tint(_ s: String?) -> Color {
        switch Show.isGain(s) {
        case .some(true): .green
        case .some(false): .red
        case .none: .primary
        }
    }
}

struct PositionRow: View {
    let position: PortfolioPosition

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(position.ticker).font(.headline)
                if let lane = position.lane { Text(lane).font(.caption).foregroundStyle(.secondary) }
                Spacer()
                Text(Show.money(position.value ?? position.costBasis)).monospacedDigit()
            }
            HStack {
                Text("\(Show.quantity(position.quantity)) at \(Show.money(position.averageCost)) avg")
                Spacer()
                if let pnl = position.unrealizedPnl {
                    Text(Show.signedMoney(pnl)).foregroundStyle(Show.isGain(pnl) == true ? .green : .red)
                }
            }
            .font(.caption).foregroundStyle(.secondary)
            PriceLine(price: position.price, source: position.priceSource, asOf: position.priceAsOf)
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }
}

/// Every price shows its source and time; a missing price says so instead of showing a number.
struct PriceLine: View {
    let price: String?
    let source: String?
    let asOf: String?

    var body: some View {
        if let price {
            Text("\(Show.money(price)) · \(source ?? "?") · \(Stamp.short(asOf))")
                .font(.caption2).foregroundStyle(.tertiary)
        } else {
            Text("No stored price yet; shown at cost").font(.caption2).foregroundStyle(.tertiary)
        }
    }
}

extension Show {
    static func percentOfOne(_ s: String?) -> String {
        guard let s, let d = Decimal(string: s) else { return "—" }
        return d.formatted(.percent.precision(.fractionLength(0...1)))
    }
}
