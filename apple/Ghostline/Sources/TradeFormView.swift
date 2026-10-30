import GhostlineKit
import SwiftUI
import UniformTypeIdentifiers

/// Log a trade you placed with your broker (or a practice one): the fields the ledger needs, and the confirmation.
struct TradeFormView: View {
    @Environment(LedgerModel.self) private var ledger
    @Environment(\.dismiss) private var dismiss
    @State private var book = "real"
    @State private var asset = "stock"
    @State private var side = "buy"
    @State private var ticker = ""
    @State private var quantity = ""
    @State private var fill = ""
    @State private var at = Date()
    @State private var fees = "0"
    @State private var broker = ""
    @State private var note = ""
    @State private var lane = ""
    @State private var optionSymbol = ""
    @State private var importing = false
    @State private var attachment: (Data, String, String)?
    @State private var problem: String?

    private var sides: [(String, String)] {
        asset == "stock" ? [("buy", "Buy"), ("sell", "Sell")]
                         : [("sell_to_open", "Sell to open"), ("buy_to_close", "Buy to close")]
    }

    private var valid: Bool {
        !ticker.trimmingCharacters(in: .whitespaces).isEmpty
            && (Decimal(string: quantity).map { $0 > 0 } ?? false)
            && (Decimal(string: fill).map { $0 >= 0 } ?? false)
            && (Decimal(string: fees).map { $0 >= 0 } ?? false)
            && (asset == "stock" || !optionSymbol.isEmpty)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker("Book", selection: $book) {
                        Text("Real").tag("real")
                        Text("Practice").tag("practice")
                    }
                    Picker("Asset", selection: $asset) {
                        Text("Stock").tag("stock")
                        Text("Option").tag("option")
                    }
                    .pickerStyle(.segmented)
                    .onChange(of: asset) { side = sides[0].0 }
                    Picker("Side", selection: $side) {
                        ForEach(sides, id: \.0) { Text($0.1).tag($0.0) }
                    }
                }
                Section {
                    TextField("Ticker", text: $ticker)
                        #if os(iOS)
                        .textInputAutocapitalization(.characters)
                        #endif
                        .autocorrectionDisabled()
                    if asset == "option" {
                        TextField("Option symbol (OCC)", text: $optionSymbol).autocorrectionDisabled()
                    }
                    numberField(asset == "stock" ? "Shares (fractions allowed)" : "Contracts", $quantity)
                    numberField(asset == "stock" ? "Fill price" : "Premium per share", $fill)
                    DatePicker("Filled", selection: $at)
                    numberField("Fees", $fees)
                }
                Section {
                    TextField("Broker", text: $broker)
                    Picker("Lane", selection: $lane) {
                        Text("None").tag("")
                        Text("Long Term").tag("long")
                        Text("Swing").tag("swing")
                        Text("Sell Puts").tag("puts")
                        Text("Day").tag("day")
                    }
                    TextField("Note", text: $note, axis: .vertical)
                }
                Section("Confirmation") {
                    if let attachment {
                        Label("\(attachment.1) · \(attachment.0.count.formatted(.byteCount(style: .file)))", systemImage: "paperclip")
                        Button("Remove", role: .destructive) { self.attachment = nil }
                    } else {
                        Button { importing = true } label: { Label("Attach a Screenshot or PDF", systemImage: "paperclip") }
                    }
                }
                if let problem { Text(problem).foregroundStyle(.red).font(.footnote) }
            }
            .formStyle(.grouped)
            .navigationTitle("Log a Trade")
            .fileImporter(isPresented: $importing, allowedContentTypes: [.png, .jpeg, .heic, .pdf]) { result in
                if case .success(let url) = result { load(url) }
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { Task { await save() } }.disabled(!valid || ledger.busy)
                }
            }
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

    private func load(_ url: URL) {
        let secured = url.startAccessingSecurityScopedResource()
        defer { if secured { url.stopAccessingSecurityScopedResource() } }
        guard let data = try? Data(contentsOf: url) else { problem = "Could not read the file."; return }
        let type = UTType(filenameExtension: url.pathExtension)?.preferredMIMEType ?? "application/octet-stream"
        attachment = (data, url.lastPathComponent, type)
    }

    private func save() async {
        let input = TradeInput(book: book, ticker: ticker.trimmingCharacters(in: .whitespaces).uppercased(), asset: asset,
                               side: side, quantity: quantity, fill: fill, at: Stamp.string(at), fees: fees,
                               broker: broker.isEmpty ? nil : broker, note: note.isEmpty ? nil : note,
                               optionSymbol: asset == "option" ? optionSymbol : nil, lane: lane.isEmpty ? nil : lane)
        if await ledger.addTrade(input, attachment: attachment) != nil {
            dismiss()
        } else {
            problem = ledger.error
        }
    }
}
