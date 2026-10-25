import Foundation

/// One `/ws2` connection on `URLSessionWebSocketTask`. A client is single-use: after it closes, make a new one.
///
/// Every callback is delivered on the main queue, in order. `onClose` fires at most once, and only when the
/// connection ends by itself (failure or server close). Calling `close()` yourself stays silent.
///
/// `nonisolated` on purpose: URLSession calls the delegate on its own queue, not on the main actor, so this type
/// must not pick up an implicit `@MainActor` when the project's Default Actor Isolation is MainActor.
/// Mutable state is guarded by `lock`; every callback hops to the main queue explicitly.
nonisolated final class SocketClient: NSObject, URLSessionWebSocketDelegate, @unchecked Sendable {
    private let onOpen: () -> Void
    private let onMessage: (Data) -> Void
    private let onClose: () -> Void

    private let lock = NSLock()
    private var isClosed = false
    private var session: URLSession!
    private var task: URLSessionWebSocketTask!

    init(url: URL, onOpen: @escaping () -> Void, onMessage: @escaping (Data) -> Void,
         onClose: @escaping () -> Void) {
        self.onOpen = onOpen
        self.onMessage = onMessage
        self.onClose = onClose
        super.init()
        // Ephemeral: no cookies, cache or credentials are persisted.
        let session = URLSession(configuration: .ephemeral, delegate: self, delegateQueue: nil)
        let task = session.webSocketTask(with: url)
        self.session = session
        self.task = task
        task.resume()
    }

    /// Sends one JSON text frame (the server expects text, like the web client). `completion` runs on main.
    func send(_ data: Data, completion: @escaping (Bool) -> Void) {
        guard !closedFlag(), let text = String(data: data, encoding: .utf8) else {
            DispatchQueue.main.async { completion(false) }
            return
        }
        task.send(.string(text)) { error in
            DispatchQueue.main.async { completion(error == nil) }
        }
    }

    /// Owner-initiated close. Does not call `onClose`.
    func close() {
        lock.lock()
        isClosed = true
        lock.unlock()
        task.cancel(with: .goingAway, reason: nil)
        session.invalidateAndCancel()
    }

    // MARK: - Receive loop

    private func receiveNext() {
        task.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .failure:
                self.finish()
            case .success(let message):
                switch message {
                case .data(let data): self.deliverMessage(data)
                case .string(let text): self.deliverMessage(Data(text.utf8))
                @unknown default: break
                }
                self.receiveNext()
            }
        }
    }

    private func deliverMessage(_ data: Data) {
        DispatchQueue.main.async { [self] in
            if !closedFlag() { onMessage(data) }
        }
    }

    /// The connection ended without `close()`. Notify once.
    private func finish() {
        lock.lock()
        let alreadyClosed = isClosed
        isClosed = true
        lock.unlock()
        guard !alreadyClosed else { return }
        task.cancel(with: .goingAway, reason: nil)
        session.invalidateAndCancel()
        DispatchQueue.main.async { [self] in onClose() }
    }

    private func closedFlag() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return isClosed
    }

    // MARK: - URLSessionWebSocketDelegate

    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask,
                    didOpenWithProtocol protocol: String?) {
        guard !closedFlag() else { return }
        DispatchQueue.main.async { [self] in
            if !closedFlag() { onOpen() }
        }
        receiveNext()
    }

    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask,
                    didCloseWith closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?) {
        finish()
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        finish()
    }
}
