import GhostlineKit
import SwiftUI

/// The journal: how the app's ideas did once settled against the S&P 500, what that did to each module's weight and
/// to each lane's graduation, the lessons, and the weekly test of whether Claude reads the stored data faithfully.
struct JournalView: View {
    @Environment(SessionModel.self) private var session
    @State private var journal: Journal?
    @State private var lessons: [Lesson] = []
    @State private var error: String?
    @State private var busy = false
    @State private var graduationN = 30

    var body: some View {
        List {
            if let j = journal {
                Section {
                    LabeledContent("Settled", value: "\(j.counts.settled)")
                    LabeledContent("Waiting for Their Horizon", value: "\(j.counts.pending)")
                    if j.counts.unpriced > 0 { LabeledContent("No Stored Price", value: "\(j.counts.unpriced)") }
                } footer: {
                    Text("Every idea is paper-traded at the next open and settled at the next open after its horizon, against SPY over the same days, after estimated costs.")
                }
                Section("Lanes") {
                    ForEach(j.lanes) { lane in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack {
                                Text(lane.lane.capitalized).font(.headline)
                                Spacer()
                                if lane.graduated {
                                    Label("Graduated", systemImage: "checkmark.seal.fill").foregroundStyle(.green).font(.caption)
                                }
                            }
                            ProgressView(value: Double(min(lane.settledN, j.graduationN)), total: Double(j.graduationN))
                            Text("\(lane.settledN) of \(j.graduationN) settled · edge over SPY \(pct(lane.expectancy))")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        .accessibilityElement(children: .combine)
                    }
                }
                if !j.modules.isEmpty {
                    Section("Modules") {
                        ForEach(j.modules) { m in
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(m.module.replacingOccurrences(of: "_", with: " ")).font(.body)
                                    Text("\(m.n) settled · \(m.wins) beat SPY · edge \(pct(m.expectancy))")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Text("× \(Show.plainNumber(m.weight))").monospacedDigit()
                                if m.graduated { Image(systemName: "checkmark.seal.fill").foregroundStyle(.green) }
                            }
                            .accessibilityElement(children: .combine)
                        }
                    }
                }
                Section("Weekly Test Set") {
                    if let e = j.lastEval {
                        LabeledContent("Last Score", value: "\(e.correct) of \(e.questions) (\(Show.plainNumber(e.scorePct))%)")
                        Text(Stamp.short(e.at)).font(.caption2).foregroundStyle(.tertiary)
                    } else {
                        Text("Not run yet. It runs every Sunday at 07:00 PT.").foregroundStyle(.secondary)
                    }
                    Button("Run It Now") { Task { await runTestSet() } }.disabled(busy)
                }
                if let d = j.digest {
                    Section("This Week") {
                        Text(LocalizedStringKey(d.content)).font(.callout)
                    }
                }
                if !j.recent.isEmpty {
                    Section("Recently Settled") {
                        ForEach(j.recent) { r in
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text("\(r.ticker) · \(r.module.replacingOccurrences(of: "_", with: " "))").font(.callout)
                                    Text("\(Stamp.day(r.openedOn)) → \(Stamp.day(r.closesOn)) · SPY \(pct(r.spyPct))")
                                        .font(.caption2).foregroundStyle(.tertiary)
                                }
                                Spacer()
                                Text(pct(r.excessPct)).monospacedDigit()
                                    .foregroundStyle(Show.isGain(r.excessPct) == true ? Color.green : Color.red)
                            }
                        }
                    }
                }
                if !lessons.isEmpty {
                    Section("Lessons") {
                        ForEach(lessons) { l in
                            VStack(alignment: .leading, spacing: 3) {
                                Text("\(l.ticker) · \(l.module.replacingOccurrences(of: "_", with: " "))").font(.caption).foregroundStyle(.secondary)
                                Text(l.text).font(.callout)
                            }
                        }
                    }
                }
                Section {
                    Stepper("Graduation Needs \(graduationN) Settled", value: $graduationN, in: 5...200, step: 5)
                    Button("Save") { Task { await saveN() } }.disabled(busy || graduationN == j.graduationN)
                } footer: {
                    Text("A lane or a module graduates only from settled results: this many, with a positive edge over SPY.")
                }
            } else if let error {
                Text(error).foregroundStyle(.secondary)
            } else {
                ProgressView()
            }
        }
        .navigationTitle("Journal")
        .task { await load() }
        .refreshable { await load() }
    }

    private func pct(_ s: String?) -> String {
        guard let s, let d = Decimal(string: s) else { return "—" }
        return "\(d > 0 ? "+" : "")\(d.formatted(.number.precision(.fractionLength(2))))%"
    }

    private func load() async {
        do {
            let j = try await session.client.journal()
            journal = j
            graduationN = j.graduationN
            lessons = try await session.client.lessons()
            error = nil
        } catch {
            self.error = (error as? GhostlineError)?.userMessage ?? error.localizedDescription
        }
    }

    private func runTestSet() async {
        busy = true
        defer { busy = false }
        if let t = try? await session.client.runTestSet() {
            _ = try? await session.client.waitForJob(id: t.jobId)
        }
        await load()
    }

    private func saveN() async {
        busy = true
        defer { busy = false }
        try? await session.client.setGraduationN(graduationN)
        await load()
    }
}
