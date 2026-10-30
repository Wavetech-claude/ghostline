import GhostlineKit
import SwiftUI

/// The morning brief on Today: the latest one, with its date and whether every number matched stored data.
struct BriefCard: View {
    @Environment(SessionModel.self) private var session
    @State private var answer: BriefAnswer?
    @State private var writing = false
    @State private var error: String?

    var body: some View {
        ContentCard {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Label("Morning Brief", systemImage: "sunrise").font(.headline)
                    Spacer()
                    if let b = answer?.brief { Text(Stamp.day(b.forDate)).font(.caption).foregroundStyle(.secondary) }
                }
                if let b = answer?.brief {
                    Text(LocalizedStringKey(b.content)).font(.callout)
                    if !b.grounded, let struck = b.struck, !struck.isEmpty {
                        Label("Numbers not in stored data were struck: \(struck.joined(separator: ", "))", systemImage: "exclamationmark.triangle")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                } else if writing || answer?.pendingJob != nil {
                    HStack(spacing: 8) { ProgressView(); Text("Writing the brief…").foregroundStyle(.secondary) }
                } else if let error {
                    Text(error).foregroundStyle(.secondary)
                } else if answer != nil {
                    Text("No brief yet. The worker writes one every trading day at 05:30 PT, or write one now.")
                        .foregroundStyle(.secondary)
                    Button("Write It Now") { Task { await writeNow() } }.buttonStyle(.bordered)
                } else {
                    ProgressView()
                }
            }
        }
        .task { await load() }
    }

    private func load() async {
        do {
            answer = try await session.client.brief()
            error = nil
            if let pending = answer?.pendingJob {
                _ = try await session.client.waitForJob(id: pending)
                answer = try await session.client.brief()
            }
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }

    private func writeNow() async {
        writing = true
        defer { writing = false }
        do {
            let t = try await session.client.writeBriefNow()
            let job = try await session.client.waitForJob(id: t.jobId)
            if job.status == "failed" { error = job.error ?? "The brief could not be written." }
            answer = try await session.client.brief()
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }
}

/// "Should I buy?" for one ticker: the deep dive's memo (Buy, Wait or Avoid, sized to the balance, what would change
/// the call) and the chat that follows it.
struct DeepDiveView: View {
    @Environment(SessionModel.self) private var session
    let ticker: String
    @State private var memos: Memos?
    @State private var thread: ChatThread?
    @State private var running = false
    @State private var question = ""
    @State private var asking = false
    @State private var error: String?

    var body: some View {
        ScrollViewReader { proxy in
            List {
                Section {
                    if let m = memos?.memos.first {
                        MemoCard(memo: m)
                    } else if running || memos?.running != nil {
                        HStack(spacing: 8) { ProgressView(); Text("Four analysts, a bull, a bear and a judge are reading the stored data…").foregroundStyle(.secondary) }
                    } else if memos != nil {
                        Text("No deep dive yet for \(ticker).").foregroundStyle(.secondary)
                    } else {
                        ProgressView()
                    }
                    Button { Task { await dive() } } label: {
                        Label(memos?.memos.isEmpty == false ? "Ask Again" : "Should I Buy?", systemImage: "sparkles")
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(running || memos?.running != nil)
                    if let error { Text(error).foregroundStyle(.secondary).font(.footnote) }
                }
                if let thread, !thread.messages.isEmpty {
                    Section("Follow-Ups") {
                        ForEach(thread.messages) { m in
                            ChatBubble(message: m).id(m.id)
                        }
                        if thread.pendingJob != nil || asking {
                            HStack(spacing: 8) { ProgressView(); Text("Thinking…").foregroundStyle(.secondary) }
                        }
                    }
                }
            }
            .navigationTitle("\(ticker): Should I Buy?")
            .safeAreaInset(edge: .bottom) {
                HStack(spacing: 8) {
                    TextField("Ask a follow-up", text: $question, axis: .vertical).textFieldStyle(.roundedBorder)
                    Button { Task { await ask() } } label: { Image(systemName: "arrow.up.circle.fill").font(.title2) }
                        .disabled(question.trimmingCharacters(in: .whitespaces).isEmpty || asking)
                        .accessibilityLabel("Send")
                }
                .padding()
                .background(.bar)
            }
            .task { await load() }
            .onChange(of: thread?.messages.count) { if let last = thread?.messages.last { withAnimation { proxy.scrollTo(last.id) } } }
        }
    }

    private func load() async {
        do {
            memos = try await session.client.memos(ticker: ticker)
            thread = try await session.client.thread(ticker: ticker)
            if let r = memos?.running {
                _ = try await session.client.waitForJob(id: r.id)
                memos = try await session.client.memos(ticker: ticker)
            }
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }

    private func dive() async {
        running = true
        defer { running = false }
        do {
            let t = try await session.client.deepDive(ticker: ticker)
            let job = try await session.client.waitForJob(id: t.jobId)
            if job.status == "failed" { error = job.error ?? "The deep dive failed." }
            memos = try await session.client.memos(ticker: ticker)
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }

    private func ask() async {
        let q = question.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return }
        asking = true
        question = ""
        defer { asking = false }
        do {
            let t = try await session.client.ask(q, ticker: ticker)
            thread = try await session.client.thread(ticker: ticker)
            let job = try await session.client.waitForJob(id: t.jobId)
            if job.status == "failed" { error = job.error ?? "No answer." }
            thread = try await session.client.thread(ticker: ticker)
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }
}

struct MemoCard: View {
    let memo: Memo

    private var tint: Color {
        switch memo.call {
        case "buy": .green
        case "avoid": .red
        default: .orange
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text(memo.call.capitalized).font(.system(.title, design: .rounded, weight: .semibold)).foregroundStyle(tint)
                if let size = memo.sizeMoney { Text("· \(Show.money(size))").font(.title3).foregroundStyle(.secondary) }
                Spacer()
                if let lo = memo.confidenceLow, let hi = memo.confidenceHigh {
                    Text("\(Show.score(lo))–\(Show.score(hi))%").font(.caption).foregroundStyle(.secondary)
                }
            }
            memoLine("Bull", memo.bull)
            memoLine("Base", memo.base)
            memoLine("Bear", memo.bear)
            memoLine("Catalysts", memo.catalysts)
            memoLine("Wrong if", memo.invalidation)
            memoLine("Would change the call", memo.whatWouldChange)
            HStack(spacing: 6) {
                Image(systemName: memo.grounded ? "checkmark.seal" : "exclamationmark.triangle")
                Text(memo.grounded ? "Every number matches stored data" : "Numbers not in stored data were struck: \((memo.struck ?? []).joined(separator: ", "))")
                Spacer()
                Text(Stamp.short(memo.createdAt))
            }
            .font(.caption2).foregroundStyle(.tertiary)
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private func memoLine(_ title: String, _ text: String?) -> some View {
        if let text, !text.isEmpty {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.caption).foregroundStyle(.secondary)
                Text(text).font(.callout)
            }
        }
    }
}

struct ChatBubble: View {
    let message: ChatMessage

    var body: some View {
        VStack(alignment: message.role == "owner" ? .trailing : .leading, spacing: 3) {
            Text(message.content)
                .padding(10)
                .background(message.role == "owner" ? AnyShapeStyle(Color.accentColor.opacity(0.15)) : AnyShapeStyle(.fill.secondary),
                            in: .rect(cornerRadius: 12))
            if message.role == "ghostline", message.grounded == false, let struck = message.struck, !struck.isEmpty {
                Text("struck: \(struck.joined(separator: ", "))").font(.caption2).foregroundStyle(.tertiary)
            }
        }
        .frame(maxWidth: .infinity, alignment: message.role == "owner" ? .trailing : .leading)
        .accessibilityElement(children: .combine)
    }
}
