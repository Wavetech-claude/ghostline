import GhostlineKit
import SwiftUI

/// Settings → Risk rules. The owner sets each rule's value and switches it on or off; the backend enforces them and
/// the AI cannot change them. Until every rule is saved, no idea passes.
struct RiskRulesView: View {
    @Environment(LedgerModel.self) private var ledger
    @State private var values: [String: String] = [:]
    @State private var enabled: [String: Bool] = [:]
    @State private var saved = false
    @State private var problem: String?

    var body: some View {
        Form {
            if let rules = ledger.rules {
                if !rules.complete {
                    Section {
                        Label("Until every rule is saved, no idea passes. The values shown are suggestions only.",
                              systemImage: "shield.lefthalf.filled")
                            .foregroundStyle(.secondary)
                        Button("Use Suggested Values") { useSuggested(rules) }
                    }
                }
                ForEach(rules.rules) { rule in
                    Section {
                        Toggle(isOn: binding(rule)) { Text(rule.label) }
                        if rule.kind != "switch" {
                            LabeledContent(rule.kind == "share" ? "Share of the balance (%)" : "Count") {
                                TextField(placeholder(rule), text: textBinding(rule))
                                    .multilineTextAlignment(.trailing)
                                    #if os(iOS)
                                    .keyboardType(.decimalPad)
                                    #endif
                            }
                        }
                    } footer: {
                        if let at = rule.updatedAt { Text("Saved \(Stamp.short(at))") }
                    }
                }
                if let problem { Text(problem).foregroundStyle(.loss).font(.footnote) }
            } else if let error = ledger.error {
                Text(error).foregroundStyle(.secondary)
            } else {
                ProgressView()
            }
        }
        .formStyle(.grouped)
        .navigationTitle("Risk Rules")
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                Button("Save") { Task { await save() } }.disabled(ledger.busy || ledger.rules == nil)
            }
        }
        .task { await ledger.loadRules(); seed() }
        .sensoryFeedback(.success, trigger: saved)
    }

    private func seed() {
        for r in ledger.rules?.rules ?? [] {
            values[r.key] = r.value.map { display(r, $0) } ?? ""
            enabled[r.key] = r.enabled ?? true
        }
    }

    private func useSuggested(_ rules: RiskRules) {
        for r in rules.rules where (values[r.key] ?? "").isEmpty {
            values[r.key] = display(r, r.suggested)
        }
    }

    private func placeholder(_ r: RiskRuleRow) -> String { "suggested \(display(r, r.suggested))" }

    /// Shares are entered as a percent (1.5 for 0.015); counts as they are.
    private func display(_ r: RiskRuleRow, _ raw: String) -> String {
        guard let d = Decimal(string: raw) else { return raw }
        let shown = r.kind == "share" ? d * 100 : d
        return shown.formatted(.number.precision(.fractionLength(0...4)).grouping(.never))
    }

    private func stored(_ r: RiskRuleRow) -> String? {
        if r.kind == "switch" { return "1" }
        guard let d = Decimal(string: (values[r.key] ?? "").replacingOccurrences(of: ",", with: "")) else { return nil }
        return r.kind == "share" ? "\(d / 100)" : "\(d)"
    }

    private func binding(_ r: RiskRuleRow) -> Binding<Bool> {
        Binding(get: { enabled[r.key] ?? true }, set: { enabled[r.key] = $0 })
    }

    private func textBinding(_ r: RiskRuleRow) -> Binding<String> {
        Binding(get: { values[r.key] ?? "" }, set: { values[r.key] = $0 })
    }

    private func save() async {
        guard let rules = ledger.rules else { return }
        var inputs: [RuleInput] = []
        for r in rules.rules {
            guard let v = stored(r) else { problem = "\(r.label): enter a number."; return }
            inputs.append(RuleInput(key: r.key, value: v, enabled: enabled[r.key] ?? true))
        }
        if await ledger.saveRules(inputs) {
            problem = nil
            saved.toggle()
            seed()
        } else {
            problem = ledger.error
        }
    }
}
