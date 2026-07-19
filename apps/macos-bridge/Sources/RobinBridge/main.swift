import ApplicationServices
import AppKit
import AudioToolbox
import AVFoundation
import CoreAudio
import CoreGraphics
import CoreMedia
import Foundation
import ScreenCaptureKit

struct BridgeCommand: Codable {
    let id: String
    let method: String
    let params: [String: String]?
}

struct BridgeResponse: Codable {
    let id: String
    let ok: Bool
    let result: [String: String]
    let error: String?
}

func boolString(_ value: Bool) -> String {
    value ? "true" : "false"
}

func microphoneGranted() -> Bool {
    AVCaptureDevice.authorizationStatus(for: .audio) == .authorized
}

func screenRecordingGranted() -> Bool {
    CGPreflightScreenCaptureAccess()
}

func accessibilityGranted() -> Bool {
    AXIsProcessTrusted()
}

func audioDevices() -> [AudioDeviceID] {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDevices,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var dataSize: UInt32 = 0
    let system = AudioObjectID(kAudioObjectSystemObject)
    guard AudioObjectGetPropertyDataSize(system, &address, 0, nil, &dataSize) == noErr else {
        return []
    }
    let count = Int(dataSize) / MemoryLayout<AudioDeviceID>.size
    var devices = [AudioDeviceID](repeating: 0, count: count)
    guard AudioObjectGetPropertyData(system, &address, 0, nil, &dataSize, &devices) == noErr else {
        return []
    }
    return devices
}

func matchingAudioDevice(_ needle: String = "BlackHole") -> (id: AudioDeviceID, name: String)? {
    for device in audioDevices() {
        let name = audioDeviceName(device)
        if name.localizedCaseInsensitiveContains(needle) {
            return (device, name)
        }
    }
    return nil
}

func matchingAudioDeviceName(_ needle: String = "BlackHole") -> String? {
    matchingAudioDevice(needle)?.name
}

func audioDeviceAvailable(matching needle: String = "BlackHole") -> Bool {
    matchingAudioDeviceName(needle) != nil
}

func defaultOutputDeviceName() -> String {
    guard let device = defaultOutputDeviceID() else { return "" }
    return audioDeviceName(device)
}

func defaultOutputDeviceID() -> AudioDeviceID? {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDefaultOutputDevice,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var device = AudioDeviceID()
    var dataSize = UInt32(MemoryLayout<AudioDeviceID>.size)
    let status = AudioObjectGetPropertyData(
        AudioObjectID(kAudioObjectSystemObject),
        &address,
        0,
        nil,
        &dataSize,
        &device
    )
    return status == noErr ? device : nil
}

func setDefaultOutputDevice(_ device: AudioDeviceID) -> OSStatus {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDefaultOutputDevice,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var mutableDevice = device
    return AudioObjectSetPropertyData(
        AudioObjectID(kAudioObjectSystemObject),
        &address,
        0,
        nil,
        UInt32(MemoryLayout<AudioDeviceID>.size),
        &mutableDevice
    )
}

func allAudioDeviceNames() -> [String] {
    audioDevices().map(audioDeviceName).filter { !$0.isEmpty }
}

func audioDeviceName(_ device: AudioDeviceID) -> String {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioObjectPropertyName,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var name: CFString = "" as CFString
    var dataSize = UInt32(MemoryLayout<CFString>.size)
    let status = withUnsafeMutablePointer(to: &name) { pointer in
        AudioObjectGetPropertyData(device, &address, 0, nil, &dataSize, pointer)
    }
    return status == noErr ? (name as String) : ""
}

func permissionsStatus(id: String) -> BridgeResponse {
    let matchedAudioDevice = matchingAudioDeviceName()
    return BridgeResponse(
        id: id,
        ok: true,
        result: [
            "screen_recording": boolString(screenRecordingGranted()),
            "accessibility": boolString(accessibilityGranted()),
            "microphone": boolString(microphoneGranted()),
            "audio_device_available": boolString(matchedAudioDevice != nil),
            "audio_device_name": matchedAudioDevice ?? "",
            "default_output_device": defaultOutputDeviceName(),
            "mode": "process"
        ],
        error: nil
    )
}

func playAudioFile(path: String, outputDevice: String = "BlackHole") -> BridgeResponse {
    guard FileManager.default.fileExists(atPath: path) else {
        return BridgeResponse(id: "unknown", ok: false, result: ["path": path, "played": "false"], error: "audio file not found")
    }
    if let routed = playAudioFileWithDefaultDeviceSwap(path: path, outputDevice: outputDevice) {
        return routed
    }
    do {
        let player = try AVAudioPlayer(contentsOf: URL(fileURLWithPath: path))
        player.prepareToPlay()
        let played = player.play()
        if played {
            let until = Date().addingTimeInterval(player.duration + 0.25)
            while player.isPlaying && Date() < until {
                RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
            }
        }
        return BridgeResponse(
            id: "unknown",
            ok: played,
            result: [
                "path": path,
                "played": boolString(played),
                "duration": String(format: "%.3f", player.duration),
                "output_device": defaultOutputDeviceName(),
                "route": "default"
            ],
            error: played ? nil : "audio playback did not start"
        )
    } catch {
        return BridgeResponse(id: "unknown", ok: false, result: ["path": path, "played": "false"], error: "\(error)")
    }
}

func playAudioFileWithDefaultDeviceSwap(path: String, outputDevice: String) -> BridgeResponse? {
    guard let target = matchingAudioDevice(outputDevice),
          let previousDevice = defaultOutputDeviceID() else {
        return nil
    }
    let switchStatus = setDefaultOutputDevice(target.id)
    guard switchStatus == noErr else {
        return BridgeResponse(
            id: "unknown",
            ok: false,
            result: ["path": path, "played": "false", "output_device": target.name, "route": "default_device_swap"],
            error: "failed to select default output device: \(switchStatus)"
        )
    }
    defer {
        _ = setDefaultOutputDevice(previousDevice)
    }
    do {
        RunLoop.current.run(until: Date().addingTimeInterval(0.35))
        let player = try AVAudioPlayer(contentsOf: URL(fileURLWithPath: path))
        player.prepareToPlay()
        let played = player.play()
        if played {
            let until = Date().addingTimeInterval(player.duration + 0.5)
            while player.isPlaying && Date() < until {
                RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
            }
            RunLoop.current.run(until: Date().addingTimeInterval(0.2))
        }
        return BridgeResponse(
            id: "unknown",
            ok: played,
            result: [
                "path": path,
                "played": boolString(played),
                "duration": String(format: "%.3f", player.duration),
                "output_device": target.name,
                "route": "default_device_swap"
            ],
            error: played ? nil : "audio playback did not start"
        )
    } catch {
        return BridgeResponse(
            id: "unknown",
            ok: false,
            result: ["path": path, "played": "false", "output_device": target.name, "route": "default_device_swap"],
            error: "\(error)"
        )
    }
}

func playAudioFileWithEngine(path: String, outputDevice: String) -> BridgeResponse? {
    guard let target = matchingAudioDevice(outputDevice) else {
        return nil
    }
    do {
        let file = try AVAudioFile(forReading: URL(fileURLWithPath: path))
        let engine = AVAudioEngine()
        let player = AVAudioPlayerNode()
        let outputNode = engine.outputNode
        var deviceID = target.id
        let status = AudioUnitSetProperty(
            outputNode.audioUnit!,
            kAudioOutputUnitProperty_CurrentDevice,
            kAudioUnitScope_Global,
            0,
            &deviceID,
            UInt32(MemoryLayout<AudioDeviceID>.size)
        )
        guard status == noErr else {
            return BridgeResponse(
                id: "unknown",
                ok: false,
                result: ["path": path, "played": "false", "output_device": target.name, "route": "engine"],
                error: "failed to select output device: \(status)"
            )
        }
        let outputFormat = outputNode.inputFormat(forBus: 0)
        engine.disconnectNodeOutput(engine.mainMixerNode)
        engine.connect(engine.mainMixerNode, to: outputNode, format: outputFormat)
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: file.processingFormat)
        player.scheduleFile(file, at: nil)
        engine.prepare()
        try engine.start()
        // Give the newly selected HAL device time to begin rendering before the
        // player node starts; otherwise BlackHole can accept the graph but drop
        // the first scheduled buffer during rapid consecutive utterances.
        RunLoop.current.run(until: Date().addingTimeInterval(0.2))
        player.play()
        let duration = Double(file.length) / file.fileFormat.sampleRate
        let until = Date().addingTimeInterval(duration + 0.25)
        while Date() < until {
            RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
        }
        player.stop()
        engine.stop()
        return BridgeResponse(
            id: "unknown",
            ok: true,
            result: [
                "path": path,
                "played": "true",
                "duration": String(format: "%.3f", duration),
                "output_device": target.name,
                "route": "engine"
            ],
            error: nil
        )
    } catch {
        return BridgeResponse(
            id: "unknown",
            ok: false,
            result: ["path": path, "played": "false", "output_device": target.name, "route": "engine"],
            error: "\(error)"
        )
    }
}

func captureScreen(application: String) -> BridgeResponse {
    guard let image = CGDisplayCreateImage(CGMainDisplayID()) else {
        return BridgeResponse(id: "unknown", ok: false, result: ["application": application], error: "screen capture failed")
    }
    let bitmap = NSBitmapImageRep(cgImage: image)
    guard let png = bitmap.representation(using: .png, properties: [:]) else {
        return BridgeResponse(id: "unknown", ok: false, result: ["application": application], error: "PNG encoding failed")
    }
    return BridgeResponse(
        id: "unknown",
        ok: true,
        result: ["application": application, "image_base64": png.base64EncodedString()],
        error: nil
    )
}

final class AudioSampleRecorder: NSObject, SCStreamOutput, SCStreamDelegate {
    private let outputURL: URL
    private var audioFile: AVAudioFile?
    private(set) var sampleCount = 0
    private(set) var byteCount = 0
    private(set) var errorMessage: String?
    private(set) var peakAmplitude: Float = 0
    private var squareSum: Double = 0
    private var amplitudeCount = 0

    var rmsAmplitude: Double {
        amplitudeCount > 0 ? sqrt(squareSum / Double(amplitudeCount)) : 0
    }

    init(outputPath: String) {
        self.outputURL = URL(fileURLWithPath: outputPath)
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of outputType: SCStreamOutputType) {
        guard outputType == .audio else {
            return
        }
        guard sampleBuffer.isValid, CMSampleBufferDataIsReady(sampleBuffer) else {
            return
        }
        guard let formatDescription = CMSampleBufferGetFormatDescription(sampleBuffer),
              let streamDescription = CMAudioFormatDescriptionGetStreamBasicDescription(formatDescription),
              let format = AVAudioFormat(streamDescription: streamDescription) else {
            errorMessage = "could not read audio format"
            return
        }
        let frames = AVAudioFrameCount(CMSampleBufferGetNumSamples(sampleBuffer))
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames) else {
            errorMessage = "could not allocate audio buffer"
            return
        }
        buffer.frameLength = frames
        let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(
            sampleBuffer,
            at: 0,
            frameCount: Int32(frames),
            into: buffer.mutableAudioBufferList
        )
        guard status == noErr else {
            errorMessage = "could not copy audio PCM data: \(status)"
            return
        }
        if let channels = buffer.floatChannelData {
            for channel in 0..<Int(format.channelCount) {
                let samples = channels[channel]
                for frame in 0..<Int(frames) {
                    let amplitude = abs(samples[frame])
                    peakAmplitude = max(peakAmplitude, amplitude)
                    squareSum += Double(amplitude * amplitude)
                    amplitudeCount += 1
                }
            }
        }
        do {
            if audioFile == nil {
                try FileManager.default.createDirectory(
                    at: outputURL.deletingLastPathComponent(),
                    withIntermediateDirectories: true
                )
                audioFile = try AVAudioFile(forWriting: outputURL, settings: format.settings)
            }
            try audioFile?.write(from: buffer)
            sampleCount += Int(frames)
            byteCount = (try? FileManager.default.attributesOfItem(atPath: outputURL.path)[.size] as? Int) ?? byteCount
        } catch {
            errorMessage = "\(error)"
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        errorMessage = "\(error)"
    }
}

func shareableApplications() async throws -> [SCRunningApplication] {
    let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
    return content.applications
}

func listCaptureApplications() async -> BridgeResponse {
    do {
        let apps = try await shareableApplications()
        let lines = apps
            .map { "\($0.bundleIdentifier):\($0.applicationName)" }
            .sorted()
        return BridgeResponse(id: "unknown", ok: true, result: ["applications": lines.joined(separator: "\n")], error: nil)
    } catch {
        return BridgeResponse(id: "unknown", ok: false, result: [:], error: "\(error)")
    }
}

func captureAudioSample(bundleID: String, outputPath: String, durationMs: Int) async -> BridgeResponse {
    do {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        guard let display = content.displays.first else {
            return BridgeResponse(id: "unknown", ok: false, result: [:], error: "no display available for capture filter")
        }
        let apps = content.applications.filter { $0.bundleIdentifier == bundleID }
        guard !apps.isEmpty else {
            return BridgeResponse(
                id: "unknown",
                ok: false,
                result: ["bundle_id": bundleID, "captured": "false"],
                error: "application not visible to ScreenCaptureKit"
            )
        }
        let filter = SCContentFilter(display: display, including: apps, exceptingWindows: [])
        let configuration = SCStreamConfiguration()
        configuration.capturesAudio = true
        configuration.excludesCurrentProcessAudio = true
        configuration.sampleRate = 48_000
        configuration.channelCount = 2
        configuration.width = 2
        configuration.height = 2
        configuration.minimumFrameInterval = CMTime(value: 1, timescale: 1)

        let recorder = AudioSampleRecorder(outputPath: outputPath)
        let stream = SCStream(filter: filter, configuration: configuration, delegate: recorder)
        try stream.addStreamOutput(recorder, type: .audio, sampleHandlerQueue: DispatchQueue(label: "robin.audio.capture"))
        try await stream.startCapture()
        try await Task.sleep(nanoseconds: UInt64(max(durationMs, 100)) * 1_000_000)
        try await stream.stopCapture()

        let ok = recorder.errorMessage == nil
        return BridgeResponse(
            id: "unknown",
            ok: ok,
            result: [
                "bundle_id": bundleID,
                "captured": boolString(ok),
                "path": outputPath,
                "samples": "\(recorder.sampleCount)",
                "bytes": "\(recorder.byteCount)",
                "peak": "\(recorder.peakAmplitude)",
                "rms": "\(recorder.rmsAmplitude)"
            ],
            error: recorder.errorMessage
        )
    } catch {
        return BridgeResponse(
            id: "unknown",
            ok: false,
            result: ["bundle_id": bundleID, "captured": "false", "path": outputPath],
            error: "\(error)"
        )
    }
}

func handle(_ command: BridgeCommand) async -> BridgeResponse {
    switch command.method {
    case "permissions.status":
        return permissionsStatus(id: command.id)
    case "audio.capture.start":
        return BridgeResponse(
            id: command.id,
            ok: true,
            result: [
                "capturing": "true",
                "bundle_id": command.params?["bundle_id"] ?? ""
            ],
            error: nil
        )
    case "audio.capture.stop":
        return BridgeResponse(id: command.id, ok: true, result: ["capturing": "false"], error: nil)
    case "audio.capture.sample":
        let bundleID = command.params?["bundle_id"] ?? "com.google.Chrome"
        let outputPath = command.params?["path"] ?? NSTemporaryDirectory() + "robin-capture.wav"
        let durationMs = Int(command.params?["duration_ms"] ?? "1500") ?? 1500
        let response = await captureAudioSample(bundleID: bundleID, outputPath: outputPath, durationMs: durationMs)
        return BridgeResponse(id: command.id, ok: response.ok, result: response.result, error: response.error)
    case "audio.output.play":
        let path = command.params?["path"] ?? command.params?["stream_id"] ?? ""
        let response = playAudioFile(path: path, outputDevice: command.params?["output_device"] ?? "BlackHole")
        return BridgeResponse(id: command.id, ok: response.ok, result: response.result, error: response.error)
    case "screen.capture":
        let response = captureScreen(application: command.params?["application"] ?? "")
        return BridgeResponse(id: command.id, ok: response.ok, result: response.result, error: response.error)
    case "audio.devices.list":
        return BridgeResponse(id: command.id, ok: true, result: ["devices": allAudioDeviceNames().joined(separator: "\n")], error: nil)
    case "audio.capture.apps":
        let response = await listCaptureApplications()
        return BridgeResponse(id: command.id, ok: response.ok, result: response.result, error: response.error)
    case "ui.find":
        return BridgeResponse(id: command.id, ok: true, result: ["elements": "[]"], error: nil)
    case "ui.press":
        return BridgeResponse(id: command.id, ok: true, result: ["pressed": command.params?["element_id"] ?? ""], error: nil)
    default:
        return BridgeResponse(id: command.id, ok: false, result: [:], error: "unknown method: \(command.method)")
    }
}

func write(_ response: BridgeResponse) throws {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    let data = try encoder.encode(response)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
}

@main
struct RobinBridgeMain {
    static func main() async {
        if CommandLine.arguments.contains("--json") {
            let input = FileHandle.standardInput.readDataToEndOfFile()
            do {
                let command = try JSONDecoder().decode(BridgeCommand.self, from: input)
                try write(await handle(command))
            } catch {
                let response = BridgeResponse(id: "unknown", ok: false, result: [:], error: "\(error)")
                try? write(response)
                exit(1)
            }
        } else {
            try? write(permissionsStatus(id: "health"))
        }
    }
}
