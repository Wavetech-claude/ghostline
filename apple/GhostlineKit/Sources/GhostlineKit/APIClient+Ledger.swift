import Foundation

public extension APIClient {
    func books() async throws -> [BookSummary] {
        struct Answer: Decodable { let books: [BookSummary] }
        return try await (get("/books", authenticated: true) as Answer).books
    }

    func addCashFlow(_ input: CashFlowInput) async throws -> CashFlowRow {
        try await post("/cash-flows", body: input, authenticated: true)
    }

    func cashFlows(book: String? = nil) async throws -> [CashFlowRow] {
        struct Answer: Decodable { let cashFlows: [CashFlowRow] }
        let q = book.map { [URLQueryItem(name: "book", value: $0)] } ?? []
        return try await (get("/cash-flows", query: q, authenticated: true) as Answer).cashFlows
    }

    func trades(_ filters: TradeFilters = TradeFilters()) async throws -> [TradeRow] {
        struct Answer: Decodable { let trades: [TradeRow] }
        return try await (get("/trades", query: filters.queryItems, authenticated: true) as Answer).trades
    }

    func addTrade(_ input: TradeInput) async throws -> TradeRow {
        try await post("/trades", body: input, authenticated: true)
    }

    func trade(id: Int) async throws -> TradeDetail {
        try await get("/trades/\(id)", authenticated: true)
    }

    func history(_ filters: TradeFilters = TradeFilters()) async throws -> History {
        try await get("/history", query: filters.queryItems, authenticated: true)
    }

    func portfolio() async throws -> Portfolio {
        try await get("/portfolio", authenticated: true)
    }

    func position(book: String, ticker: String) async throws -> PositionDetail {
        try await get("/positions/\(book)/\(ticker)", authenticated: true)
    }

    /// A book's value each trading day, its time-weighted return, and SPY's return over the same days.
    func series(book: String) async throws -> BookSeries {
        try await get("/series", query: [URLQueryItem(name: "book", value: book)], authenticated: true)
    }

    func riskRules() async throws -> RiskRules {
        try await get("/risk-rules", authenticated: true)
    }

    func saveRiskRules(_ rules: [RuleInput]) async throws -> RiskRules {
        struct Body: Encodable { let rules: [RuleInput] }
        return try await put("/risk-rules", body: Body(rules: rules), authenticated: true)
    }

    /// A confirmation screenshot or PDF for a trade (multipart; the backend keeps it in the private bucket).
    func uploadAttachment(tradeId: Int, data: Data, filename: String, contentType: String) async throws -> AttachmentRow {
        let boundary = "ghostline-\(UUID().uuidString)"
        var body = Data()
        body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n".utf8))
        body.append(Data("Content-Type: \(contentType)\r\n\r\n".utf8))
        body.append(data)
        body.append(Data("\r\n--\(boundary)--\r\n".utf8))
        return try await postRaw("/attachments", query: [URLQueryItem(name: "trade_id", value: String(tradeId))],
                                 body: body, contentType: "multipart/form-data; boundary=\(boundary)")
    }

    func attachment(id: Int) async throws -> (Data, String) {
        try await getBytes("/attachments/\(id)")
    }
}
