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
            Section("Practice") {
                NavigationLink { PracticeAccountView() } label: {
                    Label("Practice Account", systemImage: "gamecontroller")
                }
                NavigationLink { AITraderView() } label: {
                    Label("AI Trader", systemImage: "cpu")
                }
                NavigationLink { OptionsPracticeView() } label: {
                    Label("Options Practice", systemImage: "list.number")
                }
            }
            Section("Records") {
                NavigationLink { TodoListView() } label: {
                    Label("To Place at Your Broker", systemImage: "checklist")
                }
                NavigationLink { AlertsView() } label: {
                    Label("Price Alerts", systemImage: "bell")
                }
                NavigationLink { StatementsView() } label: {
                    Label("Statements", systemImage: "doc.text")
                }
                NavigationLink { JournalView() } label: {
                    Label("Journal", systemImage: "book.closed")
                }
                NavigationLink { ReconcileView() } label: {
                    Label("Reconcile", systemImage: "arrow.left.arrow.right")
                }
            }
            Section {
                NavigationLink { GuideView() } label: { Label("How to Use Ghostline", systemImage: "questionmark.circle") }
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
            #if os(iOS)
            ToolbarItem(placement: .secondaryAction) {
                NavigationLink { SettingsView() } label: { Label("Settings", systemImage: "slider.horizontal.3") }
            }
            #endif
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
                    // the ghost is the system's: it mirrors the AI trader's deposits on its own
                    ForEach(ledger.books.filter { $0.kind != "ghost" }) { Text($0.name).tag($0.kind) }
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
                if let problem { Text(problem).foregroundStyle(.loss).font(.footnote) }
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
        .onAppear {
            if let b = ledger.cashFlowBook { book = b }
            ledger.cashFlowBook = nil
        }
    }
}
