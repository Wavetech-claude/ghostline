import GhostlineKit
import SwiftUI

/// My account: the books, their cash, deposits of any amount at any time, and the sign-in.
struct AccountView: View {
    @Environment(SessionModel.self) private var session
    @Environment(LedgerModel.self) private var ledger

    var body: some View {
        List {
            Section("Books") {
                ForEach(ledger.books) { book in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(book.name).font(.body)
                            Text("\(book.trades) trades · in \(Show.money(book.deposits)) · out \(Show.money(book.withdrawals))")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text(Show.money(book.cash)).monospacedDigit()
                    }
                    .accessibilityElement(children: .combine)
                }
                if ledger.books.isEmpty {
                    if let error = ledger.error { Text(error).foregroundStyle(.secondary) } else { ProgressView() }
                }
            }
            if case .signedIn(let account) = session.state {
                Section("Signed in") {
                    LabeledContent("Apple ID", value: account.emailMasked.isEmpty ? "hidden" : account.emailMasked)
                    LabeledContent("Since", value: AccountView.day(account.createdAt))
                }
            }
            Section {
                Button("Sign Out", role: .destructive) { Task { await session.signOut() } }
            }
        }
        .navigationTitle("Account")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button { ledger.showingCashFlow = true } label: { Label("Add Money", systemImage: "plus") }
                    .keyboardShortcut("n", modifiers: [.command, .shift])
            }
        }
        .task { await ledger.loadBooks() }
        .refreshable { await ledger.loadBooks() }
    }

    static func day(_ iso: String) -> String {
        Stamp.date(iso).map { $0.formatted(date: .abbreviated, time: .omitted) } ?? iso
    }
}

/// A deposit or a withdrawal: any amount, any date, into any book.
struct CashFlowSheet: View {
    @Environment(LedgerModel.self) private var ledger
    @Environment(\.dismiss) private var dismiss
    @State private var book = "real"
    @State private var direction = "deposit"
    @State private var amount = ""
    @State private var at = Date()
    @State private var note = ""
    @State private var problem: String?

    private var parsed: Decimal? {
        guard let d = Decimal(string: amount.replacingOccurrences(of: ",", with: "")), d > 0 else { return nil }
        return direction == "deposit" ? d : -d
    }

    var body: some View {
        NavigationStack {
            Form {
                Picker("Book", selection: $book) {
                    ForEach(ledger.books) { Text($0.name).tag($0.kind) }
                }
                Picker("Direction", selection: $direction) {
                    Text("Deposit").tag("deposit")
                    Text("Withdraw").tag("withdraw")
                }
                .pickerStyle(.segmented)
                LabeledContent("Amount") {
                    TextField("any amount", text: $amount)
                        .multilineTextAlignment(.trailing)
                        #if os(iOS)
                        .keyboardType(.decimalPad)
                        #endif
                }
                DatePicker("When", selection: $at)
                TextField("Note", text: $note)
                if let problem { Text(problem).foregroundStyle(.red).font(.footnote) }
            }
            .formStyle(.grouped)
            .navigationTitle(direction == "deposit" ? "Add Money" : "Take Money Out")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        guard let value = parsed else { problem = "Enter an amount above zero."; return }
                        Task {
                            if await ledger.addCashFlow(book: book, amount: value, at: at, note: note.isEmpty ? nil : note) {
                                dismiss()
                            } else {
                                problem = ledger.error
                            }
                        }
                    }
                    .disabled(parsed == nil || ledger.busy)
                }
            }
        }
        .presentationDetents([.medium, .large])
    }
}
