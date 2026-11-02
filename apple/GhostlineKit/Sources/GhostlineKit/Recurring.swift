import Foundation

// Recurring buys in practice: "buy $X of TICKER every day, week, two weeks or month", placed by the worker on
// trading days as ordinary practice market orders. A closed day moves to the next session.

public struct RecurringRun: Codable, Sendable, Equatable, Identifiable {
    public let id: Int
    public let day: String
    public let scheduled: String
    public let status: String          // placed | skipped
    public let orderId: Int?
    public let reason: String?
    public let orderStatus: String?
    public let fillPrice: String?
    public let quantity: String?
}

public struct RecurringPlan: Codable, Sendable, Equatable, Identifiable {
    public let id: Int
    public let ticker: String
    public let dollars: String
    public let every: String           // day | week | two_weeks | month
    public let weekday: Int?           // Monday 0 … Friday 4
    public let monthDay: Int?
    public let startsOn: String
    public let status: String          // active | paused
    public let note: String?
    public let nextRun: String?
    public let runs: [RecurringRun]

    public var isActive: Bool { status == "active" }

    /// "Every Monday", "Every other Wednesday", "Monthly on the 15th", "Every trading day".
    public var scheduleText: String {
        let days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        let day = weekday.flatMap { days.indices.contains($0) ? days[$0] : nil } ?? "?"
        switch every {
        case "day": return "Every trading day"
        case "week": return "Every \(day)"
        case "two_weeks": return "Every other \(day)"
        case "month": return "Monthly on the \(monthDay.map(Self.ordinal) ?? "?")"
        default: return every
        }
    }

    static func ordinal(_ n: Int) -> String {
        let suffix = (11...13).contains(n % 100) ? "th" : [1: "st", 2: "nd", 3: "rd"][n % 10] ?? "th"
        return "\(n)\(suffix)"
    }
}

public struct RecurringPlans: Codable, Sendable, Equatable {
    public let plans: [RecurringPlan]
}

public struct RecurringInput: Encodable, Sendable, Equatable {
    public var ticker: String
    public var dollars: String
    public var every: String
    public var weekday: Int?
    public var monthDay: Int?
    public var note: String?

    public init(ticker: String, dollars: String, every: String, weekday: Int? = nil, monthDay: Int? = nil,
                note: String? = nil) {
        self.ticker = ticker
        self.dollars = dollars
        self.every = every
        self.weekday = every == "week" || every == "two_weeks" ? weekday : nil
        self.monthDay = every == "month" ? monthDay : nil
        self.note = note
    }

    private enum CodingKeys: String, CodingKey { case ticker, dollars, every, weekday, monthDay, note }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(ticker, forKey: .ticker)
        try c.encode(dollars, forKey: .dollars)
        try c.encode(every, forKey: .every)
        try c.encodeIfPresent(weekday, forKey: .weekday)
        try c.encodeIfPresent(monthDay, forKey: .monthDay)
        try c.encodeIfPresent(note, forKey: .note)
    }
}

public extension APIClient {
    private struct StatusBody: Encodable { let status: String }
    private struct Deleted: Decodable { let id: Int; let status: String }

    func recurringPlans() async throws -> RecurringPlans {
        try await get("/recurring", authenticated: true)
    }

    func createRecurring(_ input: RecurringInput) async throws -> RecurringPlan {
        try await post("/recurring", body: input, authenticated: true)
    }

    func setRecurring(id: Int, active: Bool) async throws -> RecurringPlan {
        try await patch("/recurring/\(id)", body: StatusBody(status: active ? "active" : "paused"), authenticated: true)
    }

    func deleteRecurring(id: Int) async throws {
        var r = try request("/recurring/\(id)", method: "DELETE", authenticated: true)
        r.setValue("application/json", forHTTPHeaderField: "Accept")
        let _: Deleted = try await send(r)
    }
}
