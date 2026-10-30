import Foundation

// Claude's work as the backend serves it: queued jobs, memos (the deep dive's call), the chat thread, the brief,
// and a confirmation read into trade fields.

public struct JobTicket: Codable, Sendable, Equatable {
    public let jobId: Int
    public let ticker: String?
}

public struct ClaudeJob: Codable, Sendable, Equatable {
    public let id: Int
    public let kind: String
    public let status: String          // queued | running | done | failed
    public let error: String?
    public let result: JSONValue?
    public let createdAt: String
    public let finishedAt: String?

    public var isFinished: Bool { status == "done" || status == "failed" }
}

public struct Memo: Codable, Sendable, Equatable, Identifiable {
    public let id: Int
    public let ideaId: Int?
    public let ticker: String
    public let call: String            // buy | wait | avoid
    public let sizeMoney: String?
    public let bull: String?
    public let base: String?
    public let bear: String?
    public let catalysts: String?
    public let invalidation: String?
    public let whatWouldChange: String?
    public let confidenceLow: String?
    public let confidenceHigh: String?
    public let grounded: Bool
    public let struck: [String]?
    public let createdAt: String
}

public struct Memos: Codable, Sendable, Equatable {
    public let ticker: String
    public let memos: [Memo]
    public let running: RunningJob?
}

public struct RunningJob: Codable, Sendable, Equatable {
    public let id: Int
    public let status: String
    public let createdAt: String
}

public struct ChatMessage: Codable, Sendable, Equatable, Identifiable {
    public let id: Int
    public let ticker: String?
    public let role: String            // owner | ghostline
    public let content: String
    public let grounded: Bool?
    public let struck: [String]?
    public let createdAt: String
}

public struct ChatThread: Codable, Sendable, Equatable {
    public let ticker: String?
    public let messages: [ChatMessage]
    public let pendingJob: Int?
}

public struct Brief: Codable, Sendable, Equatable {
    public let id: Int
    public let forDate: String
    public let content: String
    public let grounded: Bool
    public let struck: [String]?
    public let pushedAt: String?
    public let createdAt: String
}

public struct BriefAnswer: Codable, Sendable, Equatable {
    public let brief: Brief?
    public let pendingJob: Int?
}

public struct ReadFields: Codable, Sendable, Equatable {
    public let ticker: String?
    public let asset: String?
    public let side: String?
    public let quantity: String?
    public let fill: String?
    public let at: String?
    public let fees: String?
    public let broker: String?
    public let optionSymbol: String?
    public let notes: String?
}

public extension APIClient {
    func deepDive(ticker: String) async throws -> JobTicket {
        struct Body: Encodable { let ticker: String }
        return try await post("/deep-dive", body: Body(ticker: ticker), authenticated: true)
    }

    func memos(ticker: String) async throws -> Memos {
        try await get("/deep-dive/\(ticker)", authenticated: true)
    }

    func job(id: Int) async throws -> ClaudeJob {
        try await get("/jobs/\(id)", authenticated: true)
    }

    func ask(_ message: String, ticker: String?) async throws -> JobTicket {
        struct Body: Encodable { let message: String; let ticker: String? }
        return try await post("/chat", body: Body(message: message, ticker: ticker), authenticated: true)
    }

    func thread(ticker: String?) async throws -> ChatThread {
        let q = ticker.map { [URLQueryItem(name: "ticker", value: $0)] } ?? []
        return try await get("/chat", query: q, authenticated: true)
    }

    func brief() async throws -> BriefAnswer {
        try await get("/brief", authenticated: true)
    }

    func writeBriefNow() async throws -> JobTicket {
        struct Empty: Encodable {}
        return try await post("/brief", body: Empty(), authenticated: true)
    }

    func readAttachment(id: Int) async throws -> JobTicket {
        struct Empty: Encodable {}
        return try await post("/attachments/\(id)/read", body: Empty(), authenticated: true)
    }

    /// Polls a job until it finishes or the deadline passes (every two seconds).
    func waitForJob(id: Int, seconds: Int = 600) async throws -> ClaudeJob {
        let deadline = Date().addingTimeInterval(TimeInterval(seconds))
        while true {
            let j = try await job(id: id)
            if j.isFinished || Date() > deadline { return j }
            try await Task.sleep(for: .seconds(2))
        }
    }
}

public extension APIClient {
    /// A confirmation read before its trade exists: multipart to /confirmations/read, answered with the job to poll.
    func readConfirmation(data: Data, filename: String, contentType: String) async throws -> JobTicket {
        let boundary = "ghostline-\(UUID().uuidString)"
        var body = Data()
        body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n".utf8))
        body.append(Data("Content-Type: \(contentType)\r\n\r\n".utf8))
        body.append(data)
        body.append(Data("\r\n--\(boundary)--\r\n".utf8))
        return try await postRaw("/confirmations/read", query: [], body: body, contentType: "multipart/form-data; boundary=\(boundary)")
    }
}
