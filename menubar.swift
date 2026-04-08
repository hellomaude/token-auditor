// Token Auditor — native macOS menu bar app + frosted-glass floating widget.
// No install. /usr/bin/swift ships with Xcode Command Line Tools.
//
// Run with:    swift menubar.swift
// Or via:      ./start.sh
//
// Reads report.json + live.json from this script's directory.
// The menu bar shows score + live count. Click "Show Widget" for a
// translucent frosted-glass panel that floats above other windows.

import Cocoa
import Foundation
import QuartzCore

// MARK: - Paths

let scriptURL = URL(fileURLWithPath: CommandLine.arguments[0])
let scriptDir = scriptURL.deletingLastPathComponent()
let reportPath = scriptDir.appendingPathComponent("report.json")
let livePath = scriptDir.appendingPathComponent("live.json")
let settingsPath = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent(".tokentracker-settings.json")
let dashboardURL = "http://127.0.0.1:8787/dashboard.html"

// MARK: - Settings

final class Settings {
    static let shared = Settings()

    // Menu bar title fields
    var menuBarShowScore = true
    var menuBarShowCost = true
    var menuBarShowSessionCount = true

    // Widget sections
    var widgetShowToday = true
    var widgetShowCodex = true
    var widgetShowSessions = true
    var widgetSize: String = "medium"  // "small" | "medium" | "large"

    init() { load() }

    func load() {
        guard let data = try? Data(contentsOf: settingsPath),
              let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return }
        menuBarShowScore = (dict["menuBarShowScore"] as? Bool) ?? true
        menuBarShowCost = (dict["menuBarShowCost"] as? Bool) ?? true
        menuBarShowSessionCount = (dict["menuBarShowSessionCount"] as? Bool) ?? true
        widgetShowToday = (dict["widgetShowToday"] as? Bool) ?? true
        widgetShowCodex = (dict["widgetShowCodex"] as? Bool) ?? true
        widgetShowSessions = (dict["widgetShowSessions"] as? Bool) ?? true
        widgetSize = (dict["widgetSize"] as? String) ?? "medium"
    }

    func save() {
        let dict: [String: Any] = [
            "menuBarShowScore": menuBarShowScore,
            "menuBarShowCost": menuBarShowCost,
            "menuBarShowSessionCount": menuBarShowSessionCount,
            "widgetShowToday": widgetShowToday,
            "widgetShowCodex": widgetShowCodex,
            "widgetShowSessions": widgetShowSessions,
            "widgetSize": widgetSize,
        ]
        if let data = try? JSONSerialization.data(withJSONObject: dict, options: [.prettyPrinted]) {
            try? data.write(to: settingsPath)
        }
    }
}

// MARK: - Score formula (mirrors snapshot.py)

func computeScore(redundant: Double, idlePct: Double, avgTokens: Double) -> Int {
    let readPenalty = min(redundant / 50.0, 30.0)
    let idlePenalty = min(idlePct * 3.0, 25.0)
    let tokenPenalty = min(max(avgTokens - 15000, 0) / 2000.0, 20.0)
    return max(0, Int((100 - readPenalty - idlePenalty - tokenPenalty).rounded()))
}

// MARK: - Snapshot

struct LiveSession {
    var project: String
    var tokens: Int
    var cost: Double
    var burnRate: Int
    var idleSeconds: Int
    var warning: String?
    var model: String
}

struct Snapshot {
    var score: Int = 0
    var tokensToday: Int = 0
    var costToday: Double = 0
    var liveCount: Int = 0
    var sessions: [LiveSession] = []
    var codexPlan: String? = nil
    var codex5hPct: Double? = nil
    var codex7dPct: Double? = nil
}

func readJSON(_ url: URL) -> [String: Any]? {
    guard let data = try? Data(contentsOf: url),
          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    else { return nil }
    return obj
}

func loadSnapshot() -> Snapshot {
    var snap = Snapshot()
    if let report = readJSON(reportPath) {
        let waste = report["token_waste"] as? [String: Any] ?? [:]
        let summary = report["summary"] as? [String: Any] ?? [:]
        let redundant = (waste["redundant_file_reads"] as? Double) ?? Double(waste["redundant_file_reads"] as? Int ?? 0)
        let idlePct = (waste["idle_gap_pct_of_turns"] as? Double) ?? 0
        let avg = (summary["avg_tokens_per_session"] as? Double) ?? Double(summary["avg_tokens_per_session"] as? Int ?? 0)
        snap.score = computeScore(redundant: redundant, idlePct: idlePct, avgTokens: avg)

        if let daily = report["daily_usage"] as? [String: Any] {
            let sortedKeys = daily.keys.sorted()
            if let lastKey = sortedKeys.last, let today = daily[lastKey] as? [String: Any] {
                snap.tokensToday = (today["tokens"] as? Int) ?? Int((today["tokens"] as? Double) ?? 0)
                if let codex = report["codex"] as? [String: Any],
                   let codexByDay = codex["by_day"] as? [String: Any],
                   let codexToday = codexByDay[lastKey] as? [String: Any] {
                    snap.costToday = (codexToday["cost"] as? Double) ?? 0
                }
            }
        }
        if let codex = report["codex"] as? [String: Any],
           let rl = codex["rate_limits"] as? [String: Any] {
            snap.codexPlan = rl["plan_type"] as? String
            if let primary = rl["primary"] as? [String: Any] {
                snap.codex5hPct = primary["used_percent"] as? Double
            }
            if let secondary = rl["secondary"] as? [String: Any] {
                snap.codex7dPct = secondary["used_percent"] as? Double
            }
        }
    }
    if let live = readJSON(livePath),
       let sessions = live["active_sessions"] as? [[String: Any]] {
        snap.liveCount = sessions.count
        var liveCostSum = 0.0
        for s in sessions {
            let c = (s["cost"] as? Double) ?? 0
            liveCostSum += c
            snap.sessions.append(LiveSession(
                project: (s["project"] as? String) ?? "session",
                tokens: (s["tokens"] as? Int) ?? 0,
                cost: c,
                burnRate: (s["burn_rate_per_min"] as? Int) ?? 0,
                idleSeconds: (s["idle_seconds"] as? Int) ?? 0,
                warning: s["warning"] as? String,
                model: (s["model"] as? String) ?? ""
            ))
        }
        // Cost today = Codex today (from report.json) + live Claude sessions
        snap.costToday += liveCostSum
    }
    return snap
}

// MARK: - Helpers

func fmtTokens(_ n: Int) -> String {
    if n >= 1_000_000 { return String(format: "%.1fM", Double(n) / 1_000_000) }
    if n >= 1_000 { return String(format: "%.1fk", Double(n) / 1_000) }
    return "\(n)"
}

func emojiForScore(_ s: Int) -> String {
    if s >= 75 { return "🟢" }
    if s >= 50 { return "🟡" }
    return "🔴"
}

func colorForScore(_ s: Int) -> NSColor {
    if s >= 75 { return NSColor(red: 0.06, green: 0.73, blue: 0.51, alpha: 1) }   // green
    if s >= 50 { return NSColor(red: 0.96, green: 0.62, blue: 0.04, alpha: 1) }   // amber
    return NSColor(red: 0.94, green: 0.27, blue: 0.27, alpha: 1)                   // red
}

// MARK: - Score ring layer

final class ScoreRing: CALayer {
    let track = CAShapeLayer()
    let progress = CAShapeLayer()

    override init() {
        super.init()
        addSublayer(track)
        addSublayer(progress)
    }
    override init(layer: Any) { super.init(layer: layer) }
    required init?(coder: NSCoder) { fatalError() }

    override func layoutSublayers() {
        super.layoutSublayers()
        track.frame = bounds
        progress.frame = bounds
        let inset: CGFloat = 7
        let rect = bounds.insetBy(dx: inset, dy: inset)
        let path = NSBezierPath(ovalIn: rect).cgPath
        track.path = path
        progress.path = path
        track.fillColor = NSColor.clear.cgColor
        track.strokeColor = NSColor.white.withAlphaComponent(0.13).cgColor
        track.lineWidth = 6
        progress.fillColor = NSColor.clear.cgColor
        progress.lineWidth = 6
        progress.lineCap = .round
        // Rotate so the arc starts from top
        progress.transform = CATransform3DMakeRotation(-.pi / 2, 0, 0, 1)
    }

    func update(score: Int, animated: Bool) {
        let pct = CGFloat(max(0, min(100, score))) / 100.0
        progress.strokeColor = colorForScore(score).cgColor
        if animated {
            let anim = CABasicAnimation(keyPath: "strokeEnd")
            anim.fromValue = progress.strokeEnd
            anim.toValue = pct
            anim.duration = 0.55
            anim.timingFunction = CAMediaTimingFunction(name: .easeOut)
            progress.add(anim, forKey: "strokeEnd")
        }
        progress.strokeEnd = pct
    }
}

// MARK: - Bezier → CGPath bridge for older SDKs

extension NSBezierPath {
    var cgPath: CGPath {
        let path = CGMutablePath()
        var points = [CGPoint](repeating: .zero, count: 3)
        for i in 0..<elementCount {
            let type = element(at: i, associatedPoints: &points)
            switch type {
            case .moveTo: path.move(to: points[0])
            case .lineTo: path.addLine(to: points[0])
            case .cubicCurveTo: path.addCurve(to: points[2], control1: points[0], control2: points[1])
            case .quadraticCurveTo: path.addQuadCurve(to: points[1], control: points[0])
            case .closePath: path.closeSubpath()
            @unknown default: break
            }
        }
        return path
    }
}

// MARK: - Draggable visual-effect view

final class DraggableVisualEffectView: NSVisualEffectView {
    override var mouseDownCanMoveWindow: Bool { true }
}

// MARK: - Widget panel

let BRAND_ORANGE = NSColor(red: 0.91, green: 0.36, blue: 0.15, alpha: 1) // #e85d26
let BRAND_PURPLE = NSColor(red: 0.56, green: 0.40, blue: 0.94, alpha: 1) // #8f66f0

final class FlippedView: NSView {
    override var isFlipped: Bool { false }
}

final class ResizeGrip: NSView {
    weak var targetWindow: WidgetWindow?
    init(window: WidgetWindow) {
        self.targetWindow = window
        super.init(frame: .zero)
        wantsLayer = true
    }
    required init?(coder: NSCoder) { fatalError() }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override var mouseDownCanMoveWindow: Bool { false }
    override func resetCursorRects() {
        addCursorRect(bounds, cursor: .crosshair)
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.white.withAlphaComponent(0.55).setStroke()
        let p = NSBezierPath()
        p.lineWidth = 1.2
        p.lineCapStyle = .round
        for i in 0..<3 {
            let o = CGFloat(i) * 4 + 3
            p.move(to: NSPoint(x: bounds.maxX - o, y: bounds.minY + 2))
            p.line(to: NSPoint(x: bounds.maxX - 2, y: bounds.minY + o))
        }
        p.stroke()
    }
    private var startFrame: NSRect = .zero
    private var startMouse: NSPoint = .zero
    override func mouseDown(with event: NSEvent) {
        guard let win = targetWindow else { return }
        startFrame = win.frame
        startMouse = NSEvent.mouseLocation
    }
    override func mouseDragged(with event: NSEvent) {
        guard let win = targetWindow else { return }
        let cur = NSEvent.mouseLocation
        let dx = cur.x - startMouse.x
        let dy = cur.y - startMouse.y
        let newW = max(win.minSize.width, min(win.maxSize.width, startFrame.width + dx))
        let newH = max(win.minSize.height, min(win.maxSize.height, startFrame.height - dy))
        var f = startFrame
        f.size.width = newW
        f.size.height = newH
        f.origin.y = startFrame.maxY - newH
        win.setFrame(f, display: true)
        win.markUserResized()
    }
}

final class WidgetWindow: NSPanel, NSWindowDelegate {
    func markUserResized() { userResized = true }
    var sizeScale: CGFloat = 1.0
    func applySizePreset() {}
    private var userResized = false
    func windowDidResize(_ notification: Notification) {
        // Detect manual user resize (not our animated setFrame) via inLiveResize.
        if self.inLiveResize { userResized = true }
    }
    let ring = ScoreRing()
    var ringW: NSLayoutConstraint!
    var ringH: NSLayoutConstraint!
    weak var ringHostView: NSView?
    let scoreLabel = NSTextField(labelWithString: "—")
    let scoreOutOf = NSTextField(labelWithString: "/100")
    let tokensValue = NSTextField(labelWithString: "—")
    let tokensLabel = NSTextField(labelWithString: "TOKENS TODAY")
    let costValue = NSTextField(labelWithString: "$0.00")
    let costLabel = NSTextField(labelWithString: "COST TODAY")
    let brand = NSTextField(labelWithString: "TOKEN AUDITOR")
    let closeButton = NSButton()
    let themeButton = NSButton()
    var isDark: Bool = true

    // Codex usage bar
    let codexLabel = NSTextField(labelWithString: "CODEX · 5H")
    let codexValue = NSTextField(labelWithString: "—")
    let codexBarTrack = NSView()
    let codexBarFill = NSView()
    let codex7dTrack = NSView()
    let codex7dFill = NSView()
    let codex7dLabel = NSTextField(labelWithString: "7d —")

    // Sessions stack
    let sessionsTitle = NSTextField(labelWithString: "ACTIVE SESSIONS")
    let sessionsStack = NSStackView()

    // Height math — sessionsStack bottom = container bottom - 14
    // Fixed = header(14+14) + ring(10+84) + codex block(14+6+8+4) + sessions title(14) + stack top pad(8) ≈ 190
    private let fixedHeight: CGFloat = 200
    private let rowHeight: CGFloat = 50    // 44 card + 6 spacing
    private let emptyHeight: CGFloat = 24
    private let bottomPad: CGFloat = 14

    init() {
        let size = NSSize(width: 320, height: 420)
        super.init(
            contentRect: NSRect(origin: .zero, size: size),
            styleMask: [.borderless, .nonactivatingPanel, .fullSizeContentView, .resizable],
            backing: .buffered,
            defer: false
        )

        isFloatingPanel = true
        level = .floating
        isOpaque = false
        backgroundColor = .clear
        hasShadow = true
        isMovableByWindowBackground = true
        self.delegate = self
        hidesOnDeactivate = false
        animationBehavior = .utilityWindow
        minSize = NSSize(width: 280, height: 260)
        maxSize = NSSize(width: 640, height: 1200)
        collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary]

        // Frosted-glass background — use maskImage for proper rounded corners
        // on NSVisualEffectView (layer.cornerRadius alone doesn't clip the blur).
        let cornerRadius: CGFloat = 26
        let maskImage = NSImage(size: NSSize(width: cornerRadius * 2 + 1, height: cornerRadius * 2 + 1), flipped: false) { rect in
            NSColor.black.setFill()
            NSBezierPath(roundedRect: rect, xRadius: cornerRadius, yRadius: cornerRadius).fill()
            return true
        }
        maskImage.capInsets = NSEdgeInsets(top: cornerRadius, left: cornerRadius, bottom: cornerRadius, right: cornerRadius)
        maskImage.resizingMode = .stretch

        let blur = DraggableVisualEffectView(frame: NSRect(origin: .zero, size: size))
        blur.autoresizingMask = [.width, .height]
        blur.material = .fullScreenUI  // Notification Center / widget translucency
        blur.blendingMode = .behindWindow
        blur.state = .active
        blur.maskImage = maskImage
        blur.wantsLayer = true

        // Accent tint overlay for "color splash"
        let tintLayer = CALayer()
        tintLayer.frame = blur.bounds
        tintLayer.autoresizingMask = [.layerWidthSizable, .layerHeightSizable]
        let gradient = CAGradientLayer()
        gradient.frame = blur.bounds
        gradient.autoresizingMask = [.layerWidthSizable, .layerHeightSizable]
        gradient.colors = [
            BRAND_ORANGE.withAlphaComponent(0.14).cgColor,
            BRAND_PURPLE.withAlphaComponent(0.08).cgColor,
            NSColor.clear.cgColor
        ]
        gradient.locations = [0.0, 0.5, 1.0]
        gradient.startPoint = CGPoint(x: 0.0, y: 1.0)
        gradient.endPoint = CGPoint(x: 1.0, y: 0.0)
        blur.layer?.addSublayer(gradient)

        contentView = blur
        appearance = NSAppearance(named: .vibrantDark)

        buildLayout(in: blur)
        positionTopRight()
    }

    private func styleLabel(_ l: NSTextField, size: CGFloat, weight: NSFont.Weight, color: NSColor, mono: Bool = false, rounded: Bool = false) {
        var f: NSFont = mono
            ? NSFont.monospacedDigitSystemFont(ofSize: size, weight: weight)
            : NSFont.systemFont(ofSize: size, weight: weight)
        if rounded, let d = f.fontDescriptor.withDesign(.rounded) {
            f = NSFont(descriptor: d, size: size) ?? f
        }
        l.font = f
        l.textColor = color
        l.isEditable = false; l.isBordered = false; l.drawsBackground = false
        l.translatesAutoresizingMaskIntoConstraints = false
    }

    private func buildLayout(in container: NSView) {
        // Brand (top-left) — native SF rounded, like Control Center headers
        brand.font = NSFont.systemFont(ofSize: 13, weight: .semibold)
        if let desc = NSFont.systemFont(ofSize: 13, weight: .semibold).fontDescriptor.withDesign(.rounded) {
            brand.font = NSFont(descriptor: desc, size: 13)
        }
        brand.textColor = NSColor.labelColor
        brand.isEditable = false; brand.isBordered = false; brand.drawsBackground = false
        brand.translatesAutoresizingMaskIntoConstraints = false
        brand.stringValue = "Token Auditor"
        container.addSubview(brand)

        // Close × button
        closeButton.title = "×"
        closeButton.font = NSFont.systemFont(ofSize: 18, weight: .light)
        closeButton.isBordered = false
        closeButton.bezelStyle = .inline
        closeButton.contentTintColor = NSColor.tertiaryLabelColor
        closeButton.target = self
        closeButton.action = #selector(closeWidget)
        closeButton.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(closeButton)

        // Theme toggle (sun/moon)
        themeButton.title = "◐"
        themeButton.font = NSFont.systemFont(ofSize: 13, weight: .regular)
        themeButton.isBordered = false
        themeButton.bezelStyle = .inline
        themeButton.contentTintColor = NSColor.secondaryLabelColor
        themeButton.target = self
        themeButton.action = #selector(toggleTheme)
        themeButton.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(themeButton)

        // Score ring
        let ringHost = NSView()
        ringHost.wantsLayer = true
        ring.frame = CGRect(x: 0, y: 0, width: 84, height: 84)
        ringHost.layer = CALayer()
        ringHost.layer?.addSublayer(ring)
        ringHost.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(ringHost)
        self.ringHostView = ringHost

        styleLabel(scoreLabel, size: 30, weight: .semibold, color: .labelColor, mono: true, rounded: true)
        scoreLabel.alignment = .center
        container.addSubview(scoreLabel)

        styleLabel(scoreOutOf, size: 10, weight: .regular, color: .tertiaryLabelColor, mono: true, rounded: true)
        scoreOutOf.alignment = .center
        container.addSubview(scoreOutOf)

        // Today stats (right of ring) — sentence case, native weights
        styleLabel(tokensLabel, size: 11, weight: .regular, color: .secondaryLabelColor)
        tokensLabel.stringValue = "Tokens today"
        container.addSubview(tokensLabel)

        styleLabel(tokensValue, size: 19, weight: .semibold, color: .labelColor, mono: true, rounded: true)
        container.addSubview(tokensValue)

        styleLabel(costLabel, size: 11, weight: .regular, color: .secondaryLabelColor)
        costLabel.stringValue = "Total today"
        container.addSubview(costLabel)

        styleLabel(costValue, size: 19, weight: .semibold, color: BRAND_ORANGE, mono: true, rounded: true)
        container.addSubview(costValue)

        // Codex usage bars
        styleLabel(codexLabel, size: 11, weight: .medium, color: .secondaryLabelColor)
        codexLabel.stringValue = "Codex · 5h remaining"
        container.addSubview(codexLabel)

        styleLabel(codexValue, size: 12, weight: .semibold, color: .labelColor, mono: true, rounded: true)
        codexValue.alignment = .right
        container.addSubview(codexValue)

        codexBarTrack.wantsLayer = true
        codexBarTrack.layer = CALayer()
        codexBarTrack.layer?.backgroundColor = NSColor.white.withAlphaComponent(0.10).cgColor
        codexBarTrack.layer?.cornerRadius = 3
        codexBarTrack.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(codexBarTrack)

        codexBarFill.wantsLayer = true
        codexBarFill.layer = CALayer()
        codexBarFill.layer?.backgroundColor = BRAND_ORANGE.cgColor
        codexBarFill.layer?.cornerRadius = 3
        codexBarFill.translatesAutoresizingMaskIntoConstraints = false
        codexBarTrack.addSubview(codexBarFill)

        styleLabel(codex7dLabel, size: 10, weight: .regular, color: .tertiaryLabelColor, rounded: true)
        codex7dLabel.stringValue = "7d —"
        container.addSubview(codex7dLabel)

        codex7dTrack.wantsLayer = true
        codex7dTrack.layer = CALayer()
        codex7dTrack.layer?.backgroundColor = NSColor.white.withAlphaComponent(0.08).cgColor
        codex7dTrack.layer?.cornerRadius = 2
        codex7dTrack.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(codex7dTrack)

        codex7dFill.wantsLayer = true
        codex7dFill.layer = CALayer()
        codex7dFill.layer?.backgroundColor = BRAND_ORANGE.withAlphaComponent(0.55).cgColor
        codex7dFill.layer?.cornerRadius = 2
        codex7dFill.translatesAutoresizingMaskIntoConstraints = false
        codex7dTrack.addSubview(codex7dFill)

        // Sessions
        styleLabel(sessionsTitle, size: 11, weight: .medium, color: .secondaryLabelColor)
        sessionsTitle.stringValue = "Active sessions"
        container.addSubview(sessionsTitle)

        sessionsStack.orientation = .vertical
        sessionsStack.spacing = 6
        sessionsStack.alignment = .leading
        sessionsStack.distribution = .fill
        sessionsStack.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(sessionsStack)


        NSLayoutConstraint.activate([
            brand.topAnchor.constraint(equalTo: container.topAnchor, constant: 14),
            brand.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 18),

            closeButton.topAnchor.constraint(equalTo: container.topAnchor, constant: 6),
            closeButton.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -8),
            closeButton.widthAnchor.constraint(equalToConstant: 22),
            closeButton.heightAnchor.constraint(equalToConstant: 22),

            themeButton.topAnchor.constraint(equalTo: container.topAnchor, constant: 6),
            themeButton.trailingAnchor.constraint(equalTo: closeButton.leadingAnchor, constant: -2),
            themeButton.widthAnchor.constraint(equalToConstant: 22),
            themeButton.heightAnchor.constraint(equalToConstant: 22),

            ringHost.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 16),
            ringHost.topAnchor.constraint(equalTo: brand.bottomAnchor, constant: 10),
            { self.ringW = ringHost.widthAnchor.constraint(equalToConstant: 84); return self.ringW }(),
            { self.ringH = ringHost.heightAnchor.constraint(equalToConstant: 84); return self.ringH }(),

            scoreLabel.centerXAnchor.constraint(equalTo: ringHost.centerXAnchor),
            scoreLabel.centerYAnchor.constraint(equalTo: ringHost.centerYAnchor, constant: -3),
            scoreOutOf.centerXAnchor.constraint(equalTo: ringHost.centerXAnchor),
            scoreOutOf.topAnchor.constraint(equalTo: scoreLabel.bottomAnchor, constant: -2),

            tokensLabel.leadingAnchor.constraint(equalTo: ringHost.trailingAnchor, constant: 16),
            tokensLabel.topAnchor.constraint(equalTo: ringHost.topAnchor, constant: 2),
            tokensValue.leadingAnchor.constraint(equalTo: tokensLabel.leadingAnchor),
            tokensValue.topAnchor.constraint(equalTo: tokensLabel.bottomAnchor, constant: 2),

            costLabel.leadingAnchor.constraint(equalTo: tokensLabel.leadingAnchor),
            costLabel.topAnchor.constraint(equalTo: tokensValue.bottomAnchor, constant: 10),
            costValue.leadingAnchor.constraint(equalTo: costLabel.leadingAnchor),
            costValue.topAnchor.constraint(equalTo: costLabel.bottomAnchor, constant: 2),

            // Codex bars block
            codexLabel.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 18),
            codexLabel.topAnchor.constraint(equalTo: ringHost.bottomAnchor, constant: 14),
            codexValue.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -18),
            codexValue.centerYAnchor.constraint(equalTo: codexLabel.centerYAnchor),

            codexBarTrack.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 18),
            codexBarTrack.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -18),
            codexBarTrack.topAnchor.constraint(equalTo: codexLabel.bottomAnchor, constant: 6),
            codexBarTrack.heightAnchor.constraint(equalToConstant: 6),

            codex7dLabel.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 18),
            codex7dLabel.topAnchor.constraint(equalTo: codexBarTrack.bottomAnchor, constant: 8),

            codex7dTrack.leadingAnchor.constraint(equalTo: codex7dLabel.trailingAnchor, constant: 8),
            codex7dTrack.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -18),
            codex7dTrack.centerYAnchor.constraint(equalTo: codex7dLabel.centerYAnchor),
            codex7dTrack.heightAnchor.constraint(equalToConstant: 4),

            // Sessions
            sessionsTitle.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 18),
            sessionsTitle.topAnchor.constraint(equalTo: codex7dTrack.bottomAnchor, constant: 14),

            sessionsStack.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 16),
            sessionsStack.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -16),
            sessionsStack.topAnchor.constraint(equalTo: sessionsTitle.bottomAnchor, constant: 8),
            sessionsStack.bottomAnchor.constraint(lessThanOrEqualTo: container.bottomAnchor, constant: -14),
        ])
    }

    private func makeSessionRow(_ s: LiveSession) -> NSView {
        let card = NSView()
        card.wantsLayer = true
        card.layer = CALayer()
        card.layer?.backgroundColor = NSColor.white.withAlphaComponent(0.06).cgColor
        card.layer?.cornerRadius = 12
        card.layer?.cornerCurve = .continuous
        card.layer?.masksToBounds = true
        card.layer?.borderWidth = 0.5
        card.layer?.borderColor = NSColor.white.withAlphaComponent(0.10).cgColor
        card.translatesAutoresizingMaskIntoConstraints = false

        let dot = NSView()
        dot.wantsLayer = true
        dot.layer = CALayer()
        dot.layer?.cornerRadius = 3
        let active = s.idleSeconds < 60
        dot.layer?.backgroundColor = (s.warning != nil ? NSColor.systemOrange : (active ? NSColor.systemGreen : NSColor.tertiaryLabelColor)).cgColor
        dot.translatesAutoresizingMaskIntoConstraints = false
        card.addSubview(dot)

        let name = NSTextField(labelWithString: s.project)
        if let d = NSFont.systemFont(ofSize: 13, weight: .semibold).fontDescriptor.withDesign(.rounded) {
            name.font = NSFont(descriptor: d, size: 13)
        } else {
            name.font = NSFont.systemFont(ofSize: 13, weight: .semibold)
        }
        name.textColor = .labelColor
        name.isEditable = false; name.isBordered = false; name.drawsBackground = false
        name.lineBreakMode = .byTruncatingTail
        name.translatesAutoresizingMaskIntoConstraints = false
        card.addSubview(name)

        let tokens = NSTextField(labelWithString: fmtTokens(s.tokens))
        if let d = NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .semibold).fontDescriptor.withDesign(.rounded) {
            tokens.font = NSFont(descriptor: d, size: 12)
        } else {
            tokens.font = NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .semibold)
        }
        tokens.textColor = BRAND_ORANGE
        tokens.isEditable = false; tokens.isBordered = false; tokens.drawsBackground = false
        tokens.alignment = .right
        tokens.translatesAutoresizingMaskIntoConstraints = false
        card.addSubview(tokens)

        let subtext = String(format: "$%.2f · %@/min · idle %ds",
                             s.cost,
                             fmtTokens(s.burnRate),
                             s.idleSeconds)
        let sub = NSTextField(labelWithString: subtext)
        sub.font = NSFont.systemFont(ofSize: 11, weight: .regular)
        sub.textColor = .secondaryLabelColor
        sub.isEditable = false; sub.isBordered = false; sub.drawsBackground = false
        sub.lineBreakMode = .byTruncatingTail
        sub.translatesAutoresizingMaskIntoConstraints = false
        card.addSubview(sub)

        NSLayoutConstraint.activate([
            card.heightAnchor.constraint(equalToConstant: 44),
            card.widthAnchor.constraint(equalToConstant: 288),

            dot.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 10),
            dot.topAnchor.constraint(equalTo: card.topAnchor, constant: 10),
            dot.widthAnchor.constraint(equalToConstant: 6),
            dot.heightAnchor.constraint(equalToConstant: 6),

            name.leadingAnchor.constraint(equalTo: dot.trailingAnchor, constant: 8),
            name.topAnchor.constraint(equalTo: card.topAnchor, constant: 6),
            name.trailingAnchor.constraint(lessThanOrEqualTo: tokens.leadingAnchor, constant: -6),

            tokens.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -10),
            tokens.centerYAnchor.constraint(equalTo: name.centerYAnchor),

            sub.leadingAnchor.constraint(equalTo: name.leadingAnchor),
            sub.topAnchor.constraint(equalTo: name.bottomAnchor, constant: 2),
            sub.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -10),
        ])
        return card
    }

    private func positionTopRight() {
        guard let screen = NSScreen.main else { return }
        let v = screen.visibleFrame
        let margin: CGFloat = 18
        let origin = NSPoint(
            x: v.maxX - frame.width - margin,
            y: v.maxY - frame.height - margin
        )
        setFrameOrigin(origin)
    }

    @objc private func closeWidget() { orderOut(nil) }

    @objc private func toggleTheme() {
        isDark.toggle()
        appearance = isDark
            ? NSAppearance(named: .vibrantDark)
            : NSAppearance(named: .vibrantLight)
        if let blur = contentView as? NSVisualEffectView {
            blur.material = .fullScreenUI
        }
    }

    func update(_ snap: Snapshot, animated: Bool) {
        scoreLabel.stringValue = "\(snap.score)"
        scoreLabel.textColor = colorForScore(snap.score)
        ring.update(score: snap.score, animated: animated)
        tokensValue.stringValue = fmtTokens(snap.tokensToday)
        costValue.stringValue = String(format: "$%.2f", snap.costToday)

        // Codex usage bars
        let planPrefix = (snap.codexPlan?.uppercased() ?? "CODEX")
        codexLabel.stringValue = "\(planPrefix) · 5H REMAINING"
        if let used = snap.codex5hPct {
            let remaining = max(0, 100 - used)
            codexValue.stringValue = String(format: "%.0f%%", remaining)
            codexBarTrack.layoutSubtreeIfNeeded()
            let w = codexBarTrack.bounds.width * CGFloat(used / 100.0)
            codexBarFill.frame = CGRect(x: 0, y: 0, width: w, height: 6)
            codexBarFill.layer?.backgroundColor = (used > 80 ? NSColor.systemRed : (used > 50 ? NSColor.systemOrange : BRAND_ORANGE)).cgColor
        } else {
            codexValue.stringValue = "—"
            codexBarFill.frame = .zero
        }
        if let used7 = snap.codex7dPct {
            let remaining7 = max(0, 100 - used7)
            codex7dLabel.stringValue = String(format: "7D %.0f%%", remaining7)
            codex7dTrack.layoutSubtreeIfNeeded()
            let w = codex7dTrack.bounds.width * CGFloat(used7 / 100.0)
            codex7dFill.frame = CGRect(x: 0, y: 0, width: w, height: 4)
        } else {
            codex7dLabel.stringValue = "7D —"
            codex7dFill.frame = .zero
        }

        // Apply section visibility from settings
        let s = Settings.shared
        tokensLabel.isHidden = !s.widgetShowToday
        tokensValue.isHidden = !s.widgetShowToday
        costLabel.isHidden = !s.widgetShowToday
        costValue.isHidden = !s.widgetShowToday
        codexLabel.isHidden = !s.widgetShowCodex
        codexValue.isHidden = !s.widgetShowCodex
        codexBarTrack.isHidden = !s.widgetShowCodex
        codex7dLabel.isHidden = !s.widgetShowCodex
        codex7dTrack.isHidden = !s.widgetShowCodex
        sessionsTitle.isHidden = !s.widgetShowSessions
        sessionsStack.isHidden = !s.widgetShowSessions

        // Adaptive resize based on session count + visibility
        let sessionCount = s.widgetShowSessions ? min(snap.sessions.count, 6) : 0
        var contentHeight: CGFloat = 120 // header + ring minimum
        if s.widgetShowCodex { contentHeight += 60 }
        if s.widgetShowSessions {
            contentHeight += 28 // title
            contentHeight += (sessionCount == 0 ? emptyHeight : CGFloat(sessionCount) * rowHeight)
        }
        contentHeight += bottomPad
        if !userResized, abs(frame.height - contentHeight) > 1 {
            let oldTop = frame.maxY
            var newFrame = frame
            newFrame.size.height = contentHeight
            newFrame.origin.y = oldTop - contentHeight
            setFrame(newFrame, display: true, animate: true)
        }

        // Sessions stack
        sessionsStack.arrangedSubviews.forEach { $0.removeFromSuperview() }
        if snap.sessions.isEmpty {
            let empty = NSTextField(labelWithString: "No active sessions")
            empty.font = NSFont.systemFont(ofSize: 11, weight: .medium)
            empty.textColor = .tertiaryLabelColor
            empty.isEditable = false; empty.isBordered = false; empty.drawsBackground = false
            sessionsStack.addArrangedSubview(empty)
        } else {
            for s in snap.sessions.prefix(4) {
                sessionsStack.addArrangedSubview(makeSessionRow(s))
            }
        }
    }
}

// MARK: - App delegate

class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem!
    var widget: WidgetWindow?
    var refreshTimer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let btn = statusItem.button {
            if let img = NSImage(systemSymbolName: "bolt.circle.fill", accessibilityDescription: "Token Auditor") {
                let cfg = NSImage.SymbolConfiguration(pointSize: 14, weight: .semibold)
                btn.image = img.withSymbolConfiguration(cfg)
                btn.imagePosition = .imageLeading
                btn.imageHugsTitle = true
            }
            btn.title = " —"
        }

        let menu = NSMenu()
        menu.autoenablesItems = false
        statusItem.menu = menu

        // Create the widget eagerly so the first toggle is instant
        widget = WidgetWindow()
        widget?.update(loadSnapshot(), animated: false)
        widget?.makeKeyAndOrderFront(nil)

        refresh()
        refreshTimer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in
            self?.refresh()
        }
    }

    func refresh() {
        let snap = loadSnapshot()
        let s = Settings.shared
        var parts: [String] = []
        if s.menuBarShowScore { parts.append("\(snap.score)") }
        if s.menuBarShowCost { parts.append(String(format: "$%.2f", snap.costToday)) }
        if s.menuBarShowSessionCount && snap.liveCount > 0 { parts.append("·\(snap.liveCount)") }
        if let btn = statusItem.button {
            btn.title = parts.isEmpty ? "" : " " + parts.joined(separator: "  ")
            // Tint symbol to match score color
            if let img = NSImage(systemSymbolName: "bolt.circle.fill", accessibilityDescription: nil) {
                let cfg = NSImage.SymbolConfiguration(pointSize: 14, weight: .semibold)
                    .applying(NSImage.SymbolConfiguration(paletteColors: [colorForScore(snap.score)]))
                btn.image = img.withSymbolConfiguration(cfg)
            }
        }

        widget?.update(snap, animated: true)

        let menu = statusItem.menu!
        menu.removeAllItems()

        // Brand header
        let header = NSMenuItem(title: "Token Auditor", action: nil, keyEquivalent: "")
        header.attributedTitle = NSAttributedString(
            string: "Token Auditor",
            attributes: [
                .font: NSFont.systemFont(ofSize: 13, weight: .semibold),
                .foregroundColor: BRAND_ORANGE,
            ]
        )
        header.isEnabled = false
        menu.addItem(header)
        menu.addItem(NSMenuItem.separator())

        // Stats with SF Symbols
        menu.addItem(symbolItem("gauge.with.dots.needle.67percent", "Score", "\(snap.score) / 100"))
        menu.addItem(symbolItem("bolt.fill", "Tokens today", fmtTokens(snap.tokensToday)))
        menu.addItem(symbolItem("dollarsign.circle.fill", "Total today", String(format: "$%.2f", snap.costToday)))

        // Codex rate limits
        if let used5 = snap.codex5hPct {
            menu.addItem(symbolItem("timer", "Codex 5h remaining", String(format: "%.0f%%", max(0, 100 - used5))))
        }
        if let used7 = snap.codex7dPct {
            menu.addItem(symbolItem("calendar", "Codex 7d remaining", String(format: "%.0f%%", max(0, 100 - used7))))
        }

        if !snap.sessions.isEmpty {
            menu.addItem(NSMenuItem.separator())
            let sHdr = NSMenuItem(title: "Active sessions", action: nil, keyEquivalent: "")
            sHdr.attributedTitle = NSAttributedString(
                string: "ACTIVE SESSIONS",
                attributes: [
                    .font: NSFont.systemFont(ofSize: 10, weight: .semibold),
                    .foregroundColor: NSColor.tertiaryLabelColor,
                ]
            )
            sHdr.isEnabled = false
            menu.addItem(sHdr)
            for s in snap.sessions.prefix(5) {
                let active = s.idleSeconds < 60
                let dot = active ? "●" : "○"
                let title = "\(dot)  \(s.project)"
                let detail = "\(fmtTokens(s.tokens)) tok · $\(String(format: "%.2f", s.cost))"
                let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
                let attr = NSMutableAttributedString(
                    string: title,
                    attributes: [
                        .font: NSFont.systemFont(ofSize: 13, weight: .medium),
                        .foregroundColor: NSColor.labelColor,
                    ]
                )
                attr.append(NSAttributedString(
                    string: "   \(detail)",
                    attributes: [
                        .font: NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .regular),
                        .foregroundColor: NSColor.secondaryLabelColor,
                    ]
                ))
                item.attributedTitle = attr
                item.isEnabled = false
                menu.addItem(item)
            }
        }

        menu.addItem(NSMenuItem.separator())
        menu.addItem(symbolAction("Toggle Widget", "macwindow", #selector(toggleWidget), "w"))
        menu.addItem(symbolAction("Open Dashboard", "safari", #selector(openDashboard), "d"))
        menu.addItem(symbolAction("Refresh Now", "arrow.clockwise", #selector(refreshNow), "r"))

        // Settings submenu
        let settingsItem = NSMenuItem(title: "Settings", action: nil, keyEquivalent: "")
        let subMenu = NSMenu()
        subMenu.autoenablesItems = false
        let hdr1 = NSMenuItem(title: "Menu bar shows", action: nil, keyEquivalent: "")
        hdr1.isEnabled = false; subMenu.addItem(hdr1)
        subMenu.addItem(toggleItem("Score", Settings.shared.menuBarShowScore, #selector(toggleMBScore)))
        subMenu.addItem(toggleItem("Cost today", Settings.shared.menuBarShowCost, #selector(toggleMBCost)))
        subMenu.addItem(toggleItem("Session count", Settings.shared.menuBarShowSessionCount, #selector(toggleMBSessions)))
        subMenu.addItem(NSMenuItem.separator())
        let hdr2 = NSMenuItem(title: "Widget shows", action: nil, keyEquivalent: "")
        hdr2.isEnabled = false; subMenu.addItem(hdr2)
        subMenu.addItem(toggleItem("Today stats", Settings.shared.widgetShowToday, #selector(toggleWToday)))
        subMenu.addItem(toggleItem("Codex usage bars", Settings.shared.widgetShowCodex, #selector(toggleWCodex)))
        subMenu.addItem(toggleItem("Active sessions", Settings.shared.widgetShowSessions, #selector(toggleWSessions)))
        settingsItem.submenu = subMenu
        menu.addItem(settingsItem)

        menu.addItem(NSMenuItem.separator())
        menu.addItem(action("Quit", #selector(quit), "q"))
    }

    func symbolItem(_ symbol: String, _ label: String, _ value: String) -> NSMenuItem {
        let item = NSMenuItem(title: label, action: nil, keyEquivalent: "")
        if let img = NSImage(systemSymbolName: symbol, accessibilityDescription: nil) {
            let cfg = NSImage.SymbolConfiguration(pointSize: 12, weight: .regular)
            item.image = img.withSymbolConfiguration(cfg)
        }
        let attr = NSMutableAttributedString(
            string: label,
            attributes: [
                .font: NSFont.systemFont(ofSize: 13, weight: .regular),
                .foregroundColor: NSColor.labelColor,
            ]
        )
        // Pad so the value right-aligns visually
        let pad = String(repeating: " ", count: max(1, 22 - label.count))
        attr.append(NSAttributedString(
            string: "\(pad)\(value)",
            attributes: [
                .font: NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .semibold),
                .foregroundColor: BRAND_ORANGE,
            ]
        ))
        item.attributedTitle = attr
        item.isEnabled = false
        return item
    }

    func symbolAction(_ title: String, _ symbol: String, _ sel: Selector, _ key: String = "") -> NSMenuItem {
        let item = NSMenuItem(title: title, action: sel, keyEquivalent: key)
        if let img = NSImage(systemSymbolName: symbol, accessibilityDescription: nil) {
            let cfg = NSImage.SymbolConfiguration(pointSize: 12, weight: .regular)
            item.image = img.withSymbolConfiguration(cfg)
        }
        item.target = self
        return item
    }

    func toggleItem(_ title: String, _ on: Bool, _ sel: Selector) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: sel, keyEquivalent: "")
        item.state = on ? .on : .off
        item.target = self
        return item
    }

    @objc func toggleMBScore()    { Settings.shared.menuBarShowScore.toggle(); Settings.shared.save(); refresh() }
    @objc func toggleMBCost()     { Settings.shared.menuBarShowCost.toggle(); Settings.shared.save(); refresh() }
    @objc func toggleMBSessions() { Settings.shared.menuBarShowSessionCount.toggle(); Settings.shared.save(); refresh() }
    @objc func toggleWToday()     { Settings.shared.widgetShowToday.toggle(); Settings.shared.save(); refresh() }
    @objc func toggleWCodex()     { Settings.shared.widgetShowCodex.toggle(); Settings.shared.save(); refresh() }
    @objc func toggleWSessions()  { Settings.shared.widgetShowSessions.toggle(); Settings.shared.save(); refresh() }

    func plain(_ s: String) -> NSMenuItem {
        let item = NSMenuItem(title: s, action: nil, keyEquivalent: "")
        item.isEnabled = false
        return item
    }

    func action(_ s: String, _ sel: Selector, _ key: String = "") -> NSMenuItem {
        let item = NSMenuItem(title: s, action: sel, keyEquivalent: key)
        item.target = self
        return item
    }

    @objc func toggleWidget() {
        guard let w = widget else { return }
        if w.isVisible {
            w.orderOut(nil)
        } else {
            w.update(loadSnapshot(), animated: false)
            w.makeKeyAndOrderFront(nil)
        }
    }

    @objc func openDashboard() {
        if let url = URL(string: dashboardURL) { NSWorkspace.shared.open(url) }
    }

    @objc func refreshNow() {
        let task = Process()
        task.launchPath = "/usr/bin/env"
        task.arguments = ["python3", scriptDir.appendingPathComponent("analyze.py").path]
        task.currentDirectoryURL = scriptDir
        try? task.run()
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in self?.refresh() }
    }

    @objc func quit() { NSApp.terminate(nil) }
}

// MARK: - Run

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
