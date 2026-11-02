import GhostlineKit
import SwiftUI

/// A tab bar on iPhone that becomes a sidebar on Mac and iPad.
struct MainView: View {
    @Environment(SessionModel.self) private var session
    @Environment(LedgerModel.self) private var ledger
    @Environment(PracticeModel.self) private var practice

    var body: some View {
        @Bindable var ledger = ledger
        @Bindable var practice = practice
        TabView {
                    Tab("Today", systemImage: "sun.horizon") {
                        NavigationStack { TodayView() }
                    }
                    Tab("Ideas", systemImage: "lightbulb") {
                        NavigationStack { IdeasView() }
                    }
                    Tab("Watchlist", systemImage: "eye") {
                        NavigationStack { WatchlistView() }
                    }
                    Tab("Portfolio", systemImage: "chart.pie") {
                        NavigationStack { PortfolioView() }
                    }
                    Tab("Account", systemImage: "person.crop.circle") {
                        NavigationStack { AccountView() }
                    }
                    Tab(role: .search) {
                        NavigationStack { SearchView() }
                    }
        }
        .tabViewStyle(.sidebarAdaptable)
        .sheet(isPresented: $ledger.showingTradeForm) { TradeFormView() }
        .sheet(isPresented: $ledger.showingCashFlow) { CashFlowSheet() }
        .sheet(isPresented: $practice.showingTicket) { OrderTicketView() }
    }
}

struct TodayView: View {
    @Environment(SessionModel.self) private var session
    @Environment(LedgerModel.self) private var ledger
    @State private var investing = false
    @State private var todos: [TodoItem] = []
    @State private var watching = 0

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                GetStartedCard(watching: watching)
                PendingActionsCard()
                BriefCard()
                NavigationLink { EarningsCalendarView() } label: {
                    ContentCard {
                        VStack(alignment: .leading, spacing: 8) {
                            Label("Earnings Calendar", systemImage: "calendar").font(.headline)
                            Text("Report dates for your holdings, watchlist and ideas, with how each stock usually moves.")
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .buttonStyle(.plain)
                if !todos.isEmpty {
                    NavigationLink { TodoListView() } label: {
                        ContentCard {
                            Label("\(todos.count) to place at your broker", systemImage: "checklist")
                                .font(.headline)
                        }
                    }
                    .buttonStyle(.plain)
                }
                ContentCard {
                    VStack(alignment: .leading, spacing: 8) {
                        Label("Invest an Amount", systemImage: "dollarsign.circle").font(.headline)
                        Text("Say how much and when; the app splits it across today's ideas through your rules.")
                            .foregroundStyle(.secondary)
                        Button("Invest This") { investing = true }.buttonStyle(.bordered)
                    }
                }
                ContentCard {
                    VStack(alignment: .leading, spacing: 8) {
                        Label("Connected", systemImage: "checkmark.seal")
                            .font(.headline)
                        if let backend = session.backend {
                            Text("Ghostline \(backend.shortDescription) on \(session.configuration.environment).")
                        }
                        Text("Ideas live in their tab; charts and news in Watchlist; ask “Should I buy?” on any ticker.")
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .padding()
            .frame(maxWidth: 640, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .navigationTitle("Today")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                NavigationLink { GuideView() } label: { Label("How to Use Ghostline", systemImage: "questionmark.circle") }
            }
        }
        .sheet(isPresented: $investing) { InvestSheet() }
        .task { todos = (try? await session.client.todos()) ?? [] }
        .task { await ledger.loadRules() }
        .task {
            await ledger.loadPortfolio()
            watching = (try? await session.client.watchlist())?.count ?? 0
        }
        .refreshable {
            await session.refreshBackend()
            await ledger.loadRules()
            await ledger.loadPortfolio()
            watching = (try? await session.client.watchlist())?.count ?? watching
        }
    }
}

struct SettingsView: View {
    @Environment(SessionModel.self) private var session
    @Environment(LedgerModel.self) private var ledger
    @Environment(AppLock.self) private var lock

    var body: some View {
        @Bindable var lock = lock
        Form {
            Section {
                NavigationLink {
                    RiskRulesView()
                } label: {
                    Label {
                        VStack(alignment: .leading) {
                            Text("Risk Rules")
                            Text(ledger.rulesComplete ? "Set and enforced" : "Not set: no idea passes until they are")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    } icon: { Image(systemName: "shield.lefthalf.filled") }
                }
            }
            Section {
                Toggle("Lock with \(lock.method)", isOn: $lock.enabled)
            } header: {
                Text("Security")
            } footer: {
                Text("Asks when Ghostline opens and when you come back after more than a minute away.")
            }
            Section("Backend") {
                LabeledContent("Address", value: session.configuration.baseURL.host() ?? "")
                LabeledContent("Environment", value: session.configuration.environment)
                if let backend = session.backend {
                    LabeledContent("Version", value: backend.shortDescription)
                }
            }
        }
        .formStyle(.grouped)
        .navigationTitle("Settings")
        .task { await ledger.loadRules() }
    }
}

/// A content-layer card: a standard material, never glass (glass is for the controls that float above content).
struct ContentCard<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        content
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(18)
            .background(.regularMaterial, in: .rect(cornerRadius: 20))
    }
}
