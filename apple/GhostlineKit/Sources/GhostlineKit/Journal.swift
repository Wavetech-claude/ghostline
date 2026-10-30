import Foundation

// The journal: every idea paper-traded and settled against SPY, what the results did to the modules' weights and
// the lanes' graduation, the lessons, the weekly test set and the weekly digest.

public struct JournalCounts: Codable, Sendable, Equatable {
    public let pending: Int
    public let settled: Int
    public let unpriced: Int
}

public struct ModuleWeight: Codable, Sendable, Equatable, Identifiable {
    public var id: String { module }
    public let module: String
    public let n: Int
    public let wins: Int
    public let expectancy: String?
    public let weight: String
    public let graduated: Bool
}

public struct LaneProgress: Codable, Sendable, Equatable, Identifiable {
    public var id: String { lane }
    public let lane: String
    public let settledN: Int
    public let expectancy: String?
    public let positiveExpectancy: Bool
    public let graduated: Bool
}

public struct PaperResult: Codable, Sendable, Equatable, Identifiable {
    public var id: Int { ideaId }
    public let ideaId: Int
    public let ticker: String
    public let lane: String
    public let module: String
    public let openedOn: String
    public let closesOn: String
    public let status: String
    public let entry: String?
    public let exit: String?
    public let resultPct: String?
    public let spyPct: String?
    public let excessPct: String?
    public let note: String?
}

public struct EvalSummary: Codable, Sendable, Equatable {
    public let id: Int
    public let questions: Int
    public let correct: Int
    public let scorePct: String
    public let at: String
}

public struct WeeklyDigest: Codable, Sendable, Equatable {
    public let id: Int
    public let weekOf: String
    public let content: String
    public let pushedAt: String?
    public let createdAt: String
}

public struct Journal: Codable, Sendable, Equatable {
    public let counts: JournalCounts
    public let settings: [String: String]
    public let modules: [ModuleWeight]
    public let lanes: [LaneProgress]
    public let recent: [PaperResult]
    public let lastEval: EvalSummary?
    public let digest: WeeklyDigest?

    /// Settled ideas a lane or module needs before it can graduate.
    public var graduationN: Int { Int(Double(settings["graduation_n"] ?? "") ?? 30) }
}

public struct Lesson: Codable, Sendable, Equatable, Identifiable {
    public let id: Int
    public let ideaId: Int
    public let ticker: String
    public let module: String
    public let text: String
    public let grounded: Bool
    public let createdAt: String
}

public extension APIClient {
    func journal() async throws -> Journal {
        try await get("/journal", authenticated: true)
    }

    func lessons(module: String? = nil) async throws -> [Lesson] {
        struct Answer: Decodable { let lessons: [Lesson] }
        let q = module.map { [URLQueryItem(name: "module", value: $0)] } ?? []
        return try await (get("/lessons", query: q, authenticated: true) as Answer).lessons
    }

    func runJournal() async throws -> JSONValue {
        struct Empty: Encodable {}
        return try await post("/journal/run", body: Empty(), authenticated: true)
    }

    func runTestSet() async throws -> JobTicket {
        struct Empty: Encodable {}
        return try await post("/journal/eval", body: Empty(), authenticated: true)
    }

    func setGraduationN(_ n: Int) async throws {
        struct Body: Encodable { let graduationN: Int }
        _ = try await put("/learning/settings", body: Body(graduationN: n), authenticated: true) as [String: String]
    }
}
